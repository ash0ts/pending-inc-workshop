# %% scoring


def score_refund(initial, final):
    # Development scorer only. Compare the whole account state so a correct
    # refund cannot hide damage to another account. Do not edit initial in place.
    raise NotImplementedError("Construct the expected acme refund, then compare accounts")


EXPLANATION_RUBRIC = """Review the employee's explanation using only the supplied office state and events.
Does it describe completed actions accurately, and acknowledge unresolved work?
Return JSON: {"supported": true|false, "reason": "...", "event_ids": ["..."]}.
Cite exact event IDs. Do not reward confident wording without evidence."""


def validate_explanation(value, allowed_event_ids):
    # Task: require a real bool, a nonempty reason, and at least one exact event
    # citation. Reject unknown IDs. Invalid/unavailable judgments are missing.
    raise NotImplementedError("Validate the judge's structured response before using its score")


# %% row_mapping


def evaluation_row(run, state_score, explanation, trace_url):
    return {
        "input": {"case_id": run["case_id"], "question": run["mission"]},
        "output": {
            "answer": json.dumps(run.get("draft")),
            "execution_id": run["id"],
            "harness": run["fingerprint"],
            "agent_lens": trace_url,
        },
        "scores": {
            k: v
            for k, v in {
                "state_correct": state_score,
                "explanation_supported": explanation.get("supported") if explanation else None,
                "tool_errors": run.get("tool_errors"),
                "seconds": run.get("seconds"),
                "observed_usd": run.get("observed_usd"),
                "bounded_unknown_usd": run.get("bounded_unknown_usd"),
            }.items()
            if v is not None
        },
    }


# %% publication


def write_evaluation(project, scope_id, stage, rows, harness_files, suite_version):
    # These are native EvalTables V2 calls. case_id stays identical across versions.
    # The participant key comes from local/hosted authentication, never the gateway.
    from coreweave_evaluations import Client as EvaluationClient

    import wandb

    entity, name = project.split("/")
    identity = fingerprint([project, stage, rows, suite_version])[:16]
    publication_journal = support.Journal(".pending-code/publications", [project, identity])
    weave_result = publication_journal.once(
        "weave", lambda: write_weave_evaluation(project, stage, rows)
    )
    with wandb.init(
        entity=entity,
        project=name,
        id=identity,
        resume="allow",
        job_type="pending-evaluation",
        name=stage,
        config={"campaign": CAMPAIGN, "stage": stage, "suite": suite_version},
        settings=wandb.Settings(disable_git=True),
    ) as wb_run:
        native = {"status": "unavailable"}
        try:
            with EvaluationClient(
                base_url="https://evaluations.wandb.ai",
                api_key=wandb.Api().api_key,
                scope_id=scope_id,
            ) as evaluations:
                table = evaluations.eval_tables.create(
                    name="pending_cases", idempotency_key=identity + "-table"
                )
                evaluations.eval_tables.columns.create(
                    table.evaluation_id,
                    dataset_fields=[
                        {"source": role, "name": key, "value_type": "string"}
                        for role in ["input", "output"]
                        for key in rows[0][role]
                    ],
                    scorers=[
                        {
                            "name": key,
                            "value_type": "boolean"
                            if key in {"state_correct", "explanation_supported"}
                            else "number",
                        }
                        for key in [
                            "state_correct",
                            "explanation_supported",
                            "tool_errors",
                            "seconds",
                            "observed_usd",
                            "bounded_unknown_usd",
                            "native_grounding",
                            "native_coverage",
                            "native_usefulness",
                        ]
                    ],
                    idempotency_key=identity + "-columns",
                )
                evaluations.eval_tables.rows.add(
                    table.evaluation_id, rows=rows, idempotency_key=identity + "-rows"
                )
                version = evaluations.eval_tables.versions.create(
                    table.evaluation_id, idempotency_key=identity + "-version"
                )
            native = {"status": "available", "evaluation_id": table.evaluation_id}
        except Exception as error:
            native = {"status": "unavailable", "error": str(error)}
        if native["status"] == "available":
            wb_run.log(
                {
                    "cases": {
                        "_type": "eval-table-ces",
                        "schema_version": 1,
                        "ncols": len(rows[0]["input"]) + len(rows[0]["output"]) + 9,
                        "nrows": len(rows),
                        "evaluation_id": table.evaluation_id,
                        "evaluation_version_id": version.evaluation_version_id,
                        "dataset_id": table.dataset_id,
                        "dataset_version_id": version.dataset_version_id,
                        "log_mode": "IMMUTABLE",
                    }
                }
            )
        artifact = wandb.Artifact("harness-" + identity, type="agent-harness")
        for path, content in harness_files.items():
            with artifact.new_file(path) as file:
                file.write(content)
        with artifact.new_file("evaluation.json") as file:
            json.dump({"rows": rows, "suite": suite_version}, file, indent=2)
        wb_run.log_artifact(artifact).wait()
        wb_run.summary["native_publication"] = native
        run_url = wb_run.url
    return {
        "run_url": run_url,
        "dataset": weave_result["dataset"],
        "weave_url": weave_result["url"],
        "native": native,
        "evaluation_id": native.get("evaluation_id"),
        "project_url": f"https://forge.coreweave.com/wandb/{project}",
        "rows": len(rows),
    }


def write_weave_evaluation(project, stage, rows):
    import weave

    weave_client = weave.init(project, settings={"implicitly_patch_integrations": False})
    dataset = weave.Dataset(name="pending-" + stage, rows=[r["input"] for r in rows])
    dataset_ref = weave.publish(dataset).uri()
    logger = weave.EvaluationLogger(
        name=stage, model={"name": stage, "harness": rows[0]["output"]["harness"]}, dataset=dataset
    )
    for row in rows:
        with logger.log_prediction(
            inputs=row["input"], example_id=row["input"]["case_id"], trial_index=0
        ) as prediction:
            prediction.output = row["output"]
            for scorer, value in row["scores"].items():
                prediction.log_score(scorer, value)
    logger.log_summary({"coverage": len(rows)})
    weave_client.flush()
    return {"dataset": dataset_ref, "url": logger.ui_url, "rows": len(rows)}


# %% comparison


def write_comparison(project, publications):
    import wandb_workspaces.workspaces as ws
    from wandb_workspaces.reports.v2.interface import UnknownPanel

    import wandb

    unavailable = [item for item in publications if not item.get("evaluation_id")]
    if unavailable:
        return {
            "status": "unavailable",
            "reason": "Native EvalTables publication is incomplete. Saved Weave evaluations and artifacts remain available.",
            "unavailable_runs": [item["run_url"] for item in unavailable],
        }

    entity, name = project.split("/")
    ids = [item["run_url"].rstrip("/").split("/")[-1] for item in publications]
    api = wandb.Api()
    gids = [api.run(f"{project}/{rid}")._attrs["id"] for rid in ids]
    native = {
        "evalName": "cases",
        "evalTableType": "eval-table-ces",
        "referenceRunId": gids[0],
        "comparedEvals": [{"runId": gid, "step": 0} for gid in gids[1:]],
        "rowSize": "medium",
        "pageSize": 10,
        "aggregatedScoresCollapsed": False,
    }
    view = ws.Workspace(
        entity=entity,
        project=name,
        name="Pending / " + " / ".join(ids),
        sections=[
            ws.Section(
                name="Same cases, different harnesses",
                is_open=True,
                panels=[
                    UnknownPanel(
                        viewType="Evaluation Tables",
                        config=native,
                        layout={"x": 0, "y": 0, "w": 24, "h": 14},
                    )
                ],
            )
        ],
        runset_settings=ws.RunsetSettings(
            filters=" or ".join(f"ID = '{rid}'" for rid in ids), baseline_run=ids[0]
        ),
    )
    view.save_as_new_view()
    url = f"https://forge.coreweave.com/wandb/{project}?" + view.url.split("?", 1)[1]
    # Preserve native fields absent from the workspace SDK, then read them back.
    support.preserve_native_workspace(url, entity, name, native)
    return {"status": "available", "url": url, "run_ids": ids}
