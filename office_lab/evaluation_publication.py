"""Native EvalTables and Weave from saved office executions; Report after a conclusion."""

import json

from workshop.publication_lock import PUBLICATION_LOCK
from workshop.publish import native_table, preserve_native_workspace
from workshop.telemetry import calls_for

from .evaluation import score_review
from .harness import Harness, fingerprint

STAGES = {"eval_original": "Original", "eval_manual": "Manual", "eval_frozen": "RSI"}
TABLE = "pending_office_evaluations_v1"


def columns(runs, judgments):
    lookup = {j["execution_id"]: j for j in judgments}
    inputs = ["Case", "Task"]
    outputs = [
        "Employee explanation",
        "Account state",
        "Action receipts",
        "Judge status",
        "Judge reasons",
        "Harness fingerprint",
        "Agent Lens",
        "Judge trace",
        "Sandbox",
        "Execution status",
    ]
    scores = [
        "Task success",
        "State correct",
        "LLM grounding",
        "LLM coverage",
        "LLM usefulness",
        "Tool errors",
        "Seconds",
        "Actor USD",
        "Actor unknown USD",
        "Judge USD",
        "Judge unknown USD",
    ]
    rows = []
    for r in runs:
        j = lookup.get(r["id"], {})
        v = score_review(j.get("outcome"))
        rows.append(
            [
                r["case_id"],
                r["mission"],
                (r.get("draft") or {}).get("summary", ""),
                json.dumps(r["world"]["accounts"]),
                json.dumps(r["world"]["journal"]),
                j.get("status", "not_run"),
                json.dumps(j.get("outcome") or {"unavailable": j.get("error", "Not run")}),
                r["fingerprint"],
                r.get("trace", {}).get("url", ""),
                j.get("trace", {}).get("url", ""),
                json.dumps(r.get("sandbox", {})),
                r["status"],
                r["scores"].get("task_success"),
                r["scores"].get("state_correct"),
                v["grounding"],
                v["coverage"],
                v["usefulness"],
                r.get("tool_errors"),
                r.get("seconds"),
                r.get("observed_usd"),
                r.get("bounded_unknown_usd"),
                j.get("observed_usd"),
                j.get("bounded_unknown_usd"),
            ]
        )
    return inputs, outputs, scores, rows


def coverage(runs, judgments):
    valid = [r for r in runs if r.get("scores", {}).get("task_success") is not None]
    ids = {r["id"] for r in runs}
    return {
        "planned": len(runs),
        "state_checks_available": len(valid),
        "tasks_passed": sum(r["scores"]["task_success"] for r in valid),
        "judgments_available": sum(
            j["execution_id"] in ids and j["status"] == "complete" for j in judgments
        ),
    }


def publish_evaluation(gateway, team):
    import wandb_workspaces.workspaces as ws
    import weave
    from wandb_workspaces.reports.v2.interface import UnknownPanel

    import wandb

    from .weave_scorers import ExecutionBehavior, ExplanationReview, OfficeState

    with PUBLICATION_LOCK:
        snapshot = gateway.snapshot(team)
        final = [r for r in snapshot["runs"] if r["stage"] in STAGES]
        judges = snapshot["judgments"]
        if (
            not snapshot["graded"]
            or len(final) != 12
            or {j["execution_id"] for j in judges} != {r["id"] for r in final}
            or any(not j.get("ended") for j in judges)
        ):
            raise ValueError("Account for all 12 executions and judge outcomes before publishing")
        if any(r.get("execution_mode") != "coreweave-sandbox" for r in final):
            raise ValueError("Local-test records cannot be published as a live workshop rehearsal")
        if gateway.export and any(not r.get("trace", {}).get("verified") for r in final):
            raise ValueError("Retry stored-span export before publication")
        if gateway.export and any(
            calls_for(gateway.store, j["id"]) and not j.get("trace", {}).get("verified")
            for j in judges
        ):
            raise ValueError("Retry judge exports before publication")
        version = fingerprint(
            [
                snapshot["evaluation_suite"],
                [(j["id"], j["status"], j.get("outcome")) for j in judges],
            ]
        )
        receipt = gateway.get(
            team, "evaluation_publication", {"version": version, "runs": {}, "weave": {}}
        )
        if receipt["version"] != version:
            raise ValueError("Preserve the published evaluation under its original identity")
        if receipt.get("publication_complete"):
            return receipt
        entity, project = gateway.manifest["project"].split("/")
        wc = weave.init(
            gateway.manifest["project"], settings={"implicitly_patch_integrations": False}
        )
        scorers = [OfficeState, ExplanationReview, ExecutionBehavior]
        data = weave.Dataset(
            name=f"{team}-office-cases",
            rows=[
                {"case_id": r["case_id"], "task": r["mission"]}
                for r in final
                if r["stage"] == "eval_original"
            ],
        )
        receipt["dataset"] = weave.publish(data).uri()
        receipt["scorers"] = {s.__name__: weave.publish(s()).uri() for s in scorers}
        for stage, label in STAGES.items():
            runs = sorted([r for r in final if r["stage"] == stage], key=lambda r: r["case_id"])
            if stage not in receipt["runs"]:
                rid = fingerprint([team, stage, version])[:20]
                with wandb.init(
                    entity=entity,
                    project=project,
                    id=rid,
                    resume="allow",
                    reinit="create_new",
                    name=f"{team} / {label} / Office outcomes",
                    group=team,
                    job_type="office-evaluation",
                    config={
                        "suite": snapshot["evaluation_suite"],
                        "harness": runs[0]["fingerprint"],
                    },
                    settings=wandb.Settings(disable_git=True),
                ) as wb:
                    artifact = wandb.Artifact(
                        f"{team}-{stage}-evidence", type="workshop-evaluation"
                    )
                    for p, t in Harness(**runs[0]["harness"]).files().items():
                        with artifact.new_file(p, mode="w") as f:
                            f.write(t)
                    with artifact.new_file("evidence.json", mode="w") as f:
                        json.dump(snapshot, f, indent=2)
                    wb.log_artifact(artifact).wait()
                    marker = native_table(
                        wb,
                        *columns(runs, judges),
                        table_name=TABLE,
                        boolean_scores=["Task success", "State correct"],
                    )
                    wb.log({TABLE: marker})
                    wb.summary.update(coverage(runs, judges))
                    receipt["runs"][stage] = {
                        "id": rid,
                        "url": wb.url,
                        "artifact": f"https://wandb.ai/{entity}/{project}/artifacts/workshop-evaluation/{artifact.name.split(':')[0]}/{artifact.version}",
                    }
                gateway.put(team, "evaluation_publication", receipt)
            if stage not in receipt["weave"]:
                logger = weave.EvaluationLogger(
                    name=f"{team} / {label}",
                    model={"name": "office-employee", "harness": runs[0]["fingerprint"]},
                    dataset=data,
                )
                for run in runs:
                    judge = next(j for j in judges if j["execution_id"] == run["id"])
                    with logger.log_prediction(
                        inputs={"case_id": run["case_id"], "task": run["mission"]},
                        example_id=run["case_id"],
                        trial_index=0,
                    ) as pred:
                        output = {"execution": run, "judgment": judge}
                        pred.output = output
                        for kind in scorers:
                            scorer = kind()
                            value = scorer.score(output=output)
                            pred.log_score(scorer, value)
                logger.log_summary(coverage(runs, judges))
                wc.flush()
                receipt["weave"][stage] = {"url": logger.ui_url}
                gateway.put(team, "evaluation_publication", receipt)
        ids = [receipt["runs"][s]["id"] for s in STAGES]
        api = wandb.Api()
        gids = [api.run(f"{entity}/{project}/{rid}")._attrs["id"] for rid in ids]
        native = {
            "evalName": TABLE,
            "evalTableType": "eval-table-ces",
            "referenceRunId": gids[0],
            "comparedEvals": [{"runId": gid, "step": 0} for gid in gids[1:]],
            "rowSize": "medium",
            "pageSize": 10,
            "aggregatedScoresCollapsed": False,
            "columnVisibility": {
                **{f"output.{n}": False for n in columns(final, judges)[1]},
                "output.Employee explanation": True,
                "output.Agent Lens": True,
                "score.Actor unknown USD": False,
                "score.Judge unknown USD": False,
            },
        }
        panel = UnknownPanel(
            viewType="Evaluation Tables", config=native, layout={"x": 0, "y": 0, "w": 24, "h": 14}
        )
        if not receipt.get("evaltables_url"):
            view = ws.Workspace(
                entity=entity,
                project=project,
                name=f"{team} / Pending, Inc.",
                sections=[
                    ws.Section(
                        name="Original, manual and RSI: same office cases",
                        is_open=True,
                        panels=[panel],
                    )
                ],
                runset_settings=ws.RunsetSettings(
                    filters=" or ".join(f"ID = '{rid}'" for rid in ids), baseline_run=ids[0]
                ),
            )
            view.save_as_new_view()
            receipt["evaltables_url"] = (
                f"https://forge.coreweave.com/wandb/{entity}/{project}?" + view.url.split("?", 1)[1]
            )
            gateway.put(team, "evaluation_publication", receipt)
        preserve_native_workspace(receipt["evaltables_url"], entity, project, native)
        receipt.update(
            native_panel=native,
            coverage={s: coverage([r for r in final if r["stage"] == s], judges) for s in STAGES},
            publication_complete=True,
        )
        gateway.put(team, "evaluation_publication", receipt)
        return receipt


def publish_report(gateway, team):
    import wandb_workspaces.reports.v2 as wr
    from wandb_workspaces.reports.v2.interface import UnknownPanel

    snapshot = gateway.snapshot(team)
    conclusion = snapshot.get("conclusion")
    if not conclusion:
        raise ValueError("Save your conclusion and next experiment before publishing the Report")
    receipt = publish_evaluation(gateway, team)
    entity, project = gateway.manifest["project"].split("/")
    old = gateway.get(team, "publication")
    report = (
        wr.Report.from_url(
            old["report_url"].replace("https://forge.coreweave.com/wandb/", "https://wandb.ai/")
        )
        if old
        else wr.Report(
            entity=entity,
            project=project,
            title=f"Please Improve Yourself / {team}",
            description="A working office agent, its harness revisions, and what the fresh cases showed.",
        )
    )
    table = (
        "| Harness | Tasks passed | State checks | LLM reviews |\n|---|---:|---:|---:|\n"
        + "\n".join(
            f"| {STAGES[s]} | {v['tasks_passed']}/4 | {v['state_checks_available']}/4 | {v['judgments_available']}/4 |"
            for s, v in receipt["coverage"].items()
        )
    )
    text = (
        "The employee handles simulated billing and onboarding. We compared the first working harness, a manual revision, and a version selected after two Harness²-inspired refinement rounds. The model and tool implementation stayed fixed. These are four tasks, each attempted under three harnesses, in one teaching run.\n\n"
        + table
        + "\n\nState checks score the resulting accounts. An LLM separately reviews the employee's explanation. Missing results remain missing; the comparison does not establish repeatability.\n\n"
        + f"[Open native EvalTables]({receipt['evaltables_url']})\n\n"
        + "\n".join(
            f"- {STAGES[s]}: [evidence]({v['artifact']}) · [Weave evaluation]({receipt['weave'][s]['url']})"
            for s, v in receipt["runs"].items()
        )
    )
    report.blocks = [
        wr.MarkdownBlock(text=text),
        wr.PanelGrid(
            runsets=[
                wr.Runset(
                    entity=entity,
                    project=project,
                    filters=" or ".join(f"ID = '{v['id']}'" for v in receipt["runs"].values()),
                )
            ],
            panels=[
                UnknownPanel(
                    viewType="Evaluation Tables",
                    config=receipt["native_panel"],
                    layout={"x": 0, "y": 0, "w": 24, "h": 14},
                )
            ],
        ),
        wr.MarkdownBlock(
            text="### What we found\n\n"
            + conclusion["conclusion"]
            + "\n\n### What we would test next\n\n"
            + conclusion["next_test"]
            + "\n\n### Follow an execution\n\n"
            + "\n".join(
                f"- {r['title']} / {r['stage']}: [Agent Lens]({r.get('trace', {}).get('url', '')})"
                for r in snapshot["runs"]
                if r["case_id"] != "decision"
            )
        ),
    ]
    report.save()
    value = {
        "report_url": f"https://forge.coreweave.com/wandb/{entity}/{project}/reports/"
        + report.url.rsplit("/", 1)[1],
        "conclusion": conclusion,
    }
    gateway.put(team, "publication", value)
    return value
