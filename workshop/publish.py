"""Publish saved executions. This module never calls a model or reruns a task."""

from __future__ import annotations

import json
import time

from .model import fingerprint, harness_files
from .publication_lock import PUBLICATION_LOCK

LABELS = {
    "original": "Original / development",
    "candidate_a": "Candidate A / development",
    "candidate_b": "Candidate B / development",
    "composed": "Composed / development",
    "eval_original": "Original / fresh jobs",
    "eval_frozen": "Frozen harness / fresh jobs",
}


def preserve_native_workspace(url, entity, project, native_config):
    """Preserve native EvalTables fields that the workspace SDK does not model."""
    import wandb_workspaces.workspaces as ws

    import wandb

    sdk_url = url.replace("https://forge.coreweave.com/wandb/", "https://wandb.ai/")
    wire = ws.Workspace.from_url(sdk_url)._spec
    section = wire["spec"]["section"]
    section["runSets"][0].update(
        baselineRunId=native_config["referenceRunId"],
        pinnedRunIds=[native_config["referenceRunId"]],
    )
    bank = section["panelBankConfig"]["sections"][0]
    bank["panels"] = [
        {
            "__id__": "dispatch-final",
            "viewType": "Evaluation Tables",
            "isAuto": False,
            "config": native_config,
            "layout": {"x": 0, "y": 0, "w": 24, "h": 14},
        }
    ]
    bank["flowConfig"].update(columnsPerPage=1, rowsPerPage=1, boxHeight=1050)
    query = """mutation($id:ID,$entityName:String,$projectName:String,$type:String,
        $name:String,$displayName:String,$spec:String){upsertView(input:{id:$id,
        entityName:$entityName,projectName:$projectName,type:$type,name:$name,
        displayName:$displayName,spec:$spec}){view{id name}}}"""
    wandb.Api()._service_api.execute_graphql(
        query,
        {
            "id": wire["id"],
            "entityName": entity,
            "projectName": project,
            "type": "project-view",
            "name": wire["name"],
            "displayName": wire["displayName"],
            "spec": json.dumps(wire["spec"]),
        },
    )
    stored = ws.Workspace.from_url(sdk_url)._spec["spec"]["section"]
    assert stored["panelBankConfig"]["sections"][0]["panels"][0]["config"] == native_config


def comparison(snapshot):
    runs = [r for r in snapshot["runs"] if r["stage"].startswith("eval_")]
    case_ids = sorted({r["case_id"] for r in runs})
    output = []
    for cid in case_ids:
        versions = {r["stage"]: r for r in runs if r["case_id"] == cid}
        original, frozen = versions.get("eval_original"), versions.get("eval_frozen")
        a = (original or {}).get("scores", {}).get("correct")
        b = (frozen or {}).get("scores", {}).get("correct")
        output.append(
            {
                "case_id": cid,
                "title": (original or frozen)["title"],
                "original": a,
                "frozen": b,
                "difference": None if a is None or b is None else int(b) - int(a),
                "original_trace": (original or {}).get("trace", {}).get("url"),
                "frozen_trace": (frozen or {}).get("trace", {}).get("url"),
            }
        )
    paired = [r for r in output if r["difference"] is not None]
    return {
        "rows": output,
        "pairs_available": len(paired),
        "pairs_planned": 8,
        "wins": sum(r["difference"] > 0 for r in paired),
        "ties": sum(r["difference"] == 0 for r in paired),
        "losses": sum(r["difference"] < 0 for r in paired),
        "difference_percentage_points": 100 * sum(r["difference"] for r in paired) / len(paired)
        if paired
        else None,
    }


def eval_columns(runs):
    inputs = ["Case", "Task"]
    outputs = ["Schedule or deferral", "Execution status", "Grade reason", "Harness", "Agent Lens"]
    scores = [
        "Correct scheduling",
        "Constraint compliance",
        "Job coverage",
        "Tool errors",
        "Execution seconds",
        "Queue seconds",
        "Observed USD",
        "Bounded unknown USD",
    ]
    rows = []
    for r in runs:
        g = r.get("scores", {})
        rows.append(
            [
                r["case_id"],
                r["title"],
                json.dumps(r.get("outcome"), ensure_ascii=False),
                r["status"],
                g.get("reason", "Unavailable"),
                r["fingerprint"],
                r.get("trace", {}).get("url", ""),
                g.get("correct"),
                g.get("compliance"),
                g.get("completion"),
                r.get("tool_errors"),
                r.get("seconds"),
                r.get("queue_seconds"),
                r.get("observed_usd"),
                r.get("bounded_unknown_usd"),
            ]
        )
    return inputs, outputs, scores, rows


def native_table(
    run, inputs, outputs, scores, rows, *, table_name="dispatch_cases", boolean_scores=None
):
    import wandb

    columns = inputs + outputs + scores
    boolean_scores = scores[:2] if boolean_scores is None else boolean_scores
    # Use the Evaluations SDK explicitly: wandb.EvalTable may select the legacy
    # Weave-backed writer. Missing CES scores remain absent observations.
    from coreweave_evaluations import Client

    api = wandb.Api()
    scope = api._service_api.execute_graphql(
        "query($e:String!,$p:String!){project(entityName:$e,name:$p){internalId}}",
        {"e": run.entity, "p": run.project},
    )["project"]["internalId"]

    def key(operation):
        return fingerprint(
            [
                run.id,
                "dispatch-evaluation-v1" if table_name == "dispatch_cases" else table_name,
                operation,
            ]
        )

    with Client(
        base_url="https://evaluations.wandb.ai", api_key=api.api_key, scope_id=scope
    ) as client:
        table = client.eval_tables.create(name=table_name, idempotency_key=key("create"))
        client.eval_tables.columns.create(
            table.evaluation_id,
            dataset_fields=[
                {"source": role, "name": col, "value_type": "string"}
                for role, fields in [("input", inputs), ("output", outputs)]
                for col in fields
            ],
            scorers=[
                {"name": col, "value_type": "boolean" if col in boolean_scores else "number"}
                for col in scores
            ],
            idempotency_key=key("columns"),
        )
        records = [dict(zip(columns, row)) for row in rows]
        client.eval_tables.rows.add(
            table.evaluation_id,
            rows=[
                {
                    "input": {k: r[k] for k in inputs},
                    "output": {k: r[k] for k in outputs},
                    "scores": {k: r[k] for k in scores if r[k] is not None},
                }
                for r in records
            ],
            idempotency_key=key("rows"),
        )
        version = client.eval_tables.versions.create(
            table.evaluation_id, idempotency_key=key("version")
        )
    return {
        "_type": "eval-table-ces",
        "schema_version": 1,
        "ncols": len(columns),
        "nrows": len(rows),
        "evaluation_id": table.evaluation_id,
        "evaluation_version_id": version.evaluation_version_id,
        "dataset_id": table.dataset_id,
        "dataset_version_id": version.dataset_version_id,
        "log_mode": "IMMUTABLE",
    }


def participant_conclusion(snapshot):
    answers = snapshot.get("journal", {}).get("conclusion", {}).get("answers", {})
    labels = {
        "observed": "We observed",
        "changed": "We changed",
        "result": "On fresh jobs",
        "next_test": "Our next test",
        "own_workflow": "A workflow in our product",
        "exit_a": "Participant A's explanation",
        "exit_b": "Participant B's explanation",
    }
    if not answers:
        return ""
    # Quotes preserve authorship; numeric results remain in the independently generated table.
    lead = (
        "Technical rehearsal notes entered by automation, not participant feedback"
        if snapshot.get("technical_rehearsal")
        else "Participant-written interpretation"
    )
    return (
        "\n## The pair's explanation\n\n"
        + lead
        + ":\n\n"
        + "\n\n".join(
            f"**{label}:**\n\n"
            + "\n".join("> " + line for line in str(answers.get(key, "")).splitlines())
            for key, label in labels.items()
            if answers.get(key)
        )
    )


def publish_team(workshop, team, *, report=True):
    with PUBLICATION_LOCK:
        return _publish_team(workshop, team, report=report)


def _publish_team(workshop, team, *, report=True):
    import wandb_workspaces.reports.v2 as wr
    import wandb_workspaces.workspaces as ws
    import weave
    from wandb_workspaces.reports.v2.interface import UnknownPanel

    import wandb

    s = workshop.store
    snapshot = workshop.snapshot(team)
    if (
        report
        and snapshot.get("teaching_release") == "hands-on-v2"
        and not snapshot.get("journal", {}).get("conclusion")
    ):
        raise ValueError("Save your conclusion before publishing the Report")
    if not snapshot["evaluation_complete"]:
        raise ValueError("Finish all sixteen final executions before publishing the comparison")
    if any(
        r["status"] == "complete" and not r.get("trace", {}).get("verified")
        for r in snapshot["runs"]
    ):
        raise ValueError(
            "Trace export is still pending. Retry export before publishing the final comparison."
        )
    final = [r for r in snapshot["runs"] if r["stage"].startswith("eval_")]
    if len(final) != 16 or len({(r["stage"], r["case_id"]) for r in final}) != 16:
        raise ValueError("Expected all sixteen distinct final execution records")
    entity, project = workshop.manifest["project"].split("/")
    receipt = s.get(team, "publication", {"runs": {}, "weave": {}, "artifacts": {}})
    export_root = s.root / "publications" / team
    export_root.mkdir(parents=True, exist_ok=True)
    (export_root / "evidence.json").write_text(json.dumps(snapshot, indent=2))
    result = comparison(snapshot)
    (export_root / "comparison.json").write_text(json.dumps(result, indent=2))
    weave_client = weave.init(
        workshop.manifest["project"], settings={"implicitly_patch_integrations": False}
    )
    for stage, label in LABELS.items():
        # Fresh-case comparisons are needed in class; development records can wait for the Report.
        if not report and not stage.startswith("eval_"):
            continue
        runs = [r for r in snapshot["runs"] if r["stage"] == stage]
        if not runs:
            continue
        publication_key = fingerprint(
            [{k: r.get(k) for k in ["id", "scores", "trace"]} for r in runs]
        )
        native_format = "eval-table-ces-v1"
        run_id = fingerprint(
            [workshop.manifest["campaign"], team, stage, publication_key, native_format]
        )[:20]
        inputs, outputs, scores, rows = eval_columns(runs)
        if (
            receipt["runs"].get(stage, {}).get("source_hash") != publication_key
            or receipt["runs"].get(stage, {}).get("format") != native_format
        ):
            with wandb.init(
                entity=entity,
                project=project,
                id=run_id,
                resume="allow",
                reinit="create_new",
                name=f"{team} / {label}",
                group=team,
                job_type="heldout" if stage.startswith("eval_") else "adaptation",
                config={
                    "team": team,
                    "stage": stage,
                    "model": workshop.manifest["model"],
                    "harness_fingerprint": runs[0]["fingerprint"],
                    "fixture_version": workshop.manifest["fixture_version"],
                },
                settings=wandb.Settings(disable_git=True),
            ) as run:
                table = native_table(run, inputs, outputs, scores, rows)
                run.log({"dispatch_cases": table})
                available = [r for r in runs if r["scores"].get("correct") is not None]
                run.summary.update(
                    {
                        "correct_rate": sum(r["scores"]["correct"] for r in available)
                        / len(available)
                        if available
                        else None,
                        "graded_cases": len(available),
                        "planned_cases": len(runs),
                        "tool_errors": sum(r.get("tool_errors", 0) for r in runs),
                        "observed_usd": sum(r.get("observed_usd", 0) for r in runs),
                        "bounded_unknown_usd": sum(r.get("bounded_unknown_usd", 0) for r in runs),
                    }
                )
                art = wandb.Artifact(
                    f"{team}-{stage}-evidence",
                    type="workshop-evidence",
                    metadata={
                        "harness_fingerprint": runs[0]["fingerprint"],
                        "source_hash": publication_key,
                    },
                )
                with art.new_file("executions.json", mode="w") as f:
                    json.dump(runs, f, indent=2)
                for name, value in harness_files(runs[0]["harness"]).items():
                    with art.new_file(name, mode="w") as f:
                        f.write(value)
                if stage in snapshot["decisions"] and snapshot["decisions"][stage]:
                    d = snapshot["decisions"][stage]
                    with art.new_file("decision.json", mode="w") as f:
                        json.dump(d, f, indent=2)
                    with art.new_file("harness.diff", mode="w") as f:
                        f.write(d["diff"])
                run.log_artifact(art).wait()
                receipt["runs"][stage] = {
                    "id": run_id,
                    "url": run.url,
                    "source_hash": publication_key,
                    "format": native_format,
                    "marker": table,
                }
                receipt["artifacts"][stage] = (
                    f"https://wandb.ai/{entity}/{project}/artifacts/workshop-evidence/{art.name.split(':')[0]}/{art.version}"
                )
            s.put(team, "publication", receipt)
        if receipt["weave"].get(stage, {}).get("source_hash") != publication_key:
            data = weave.Dataset(
                name=f"{team}-{stage}-cases",
                rows=[
                    {
                        "case_id": r["case_id"],
                        "task": r["mission"],
                        "records": r.get("fixture_display"),
                    }
                    for r in runs
                ],
            )
            ref = weave.publish(data)
            ev = weave.EvaluationLogger(
                name=f"{team} / {label}",
                model={
                    "name": "dispatch",
                    "harness": runs[0]["fingerprint"],
                    "model": snapshot["model"],
                },
                dataset=data,
            )
            # Start records must reach storage before the summary ends the root call.
            weave_client.flush()
            for r in runs:
                with ev.log_prediction(
                    inputs={"case_id": r["case_id"], "task": r["mission"]},
                    example_id=r["case_id"],
                    trial_index=0,
                ) as prediction:
                    prediction.output = {
                        "decision": r.get("outcome"),
                        "agent_lens": r.get("trace", {}).get("url"),
                        "status": r["status"],
                    }
                    for key in ["correct", "compliance", "completion"]:
                        if r["scores"].get(key) is not None:
                            prediction.log_score(key, r["scores"][key])
            weave_client.flush()
            ev.log_summary()
            weave_client.flush()
            call_id = ev.ui_url.rstrip("/").rsplit("/", 1)[-1]
            stored_evaluation = None
            for _ in range(10):
                stored_evaluation = weave_client.get_call(call_id)
                if stored_evaluation.ended_at is not None:
                    break
                time.sleep(1)
            if stored_evaluation.ended_at is None or stored_evaluation.exception:
                raise RuntimeError(
                    "Weave evaluation summary is not stored completely; retry publication from the saved executions"
                )
            receipt["weave"][stage] = {
                "dataset": ref.uri(),
                "status": "logged",
                "source_hash": publication_key,
                "evaluation_url": ev.ui_url,
                "verified_summary": True,
            }
            s.put(team, "publication", receipt)
    weave_client.flush()
    ids = [receipt["runs"][stage]["id"] for stage in ["eval_original", "eval_frozen"]]
    api = wandb.Api()
    global_ids = [api.run(f"{entity}/{project}/{i}")._attrs["id"] for i in ids]
    native_config = {
        "evalName": "dispatch_cases",
        "evalTableType": "eval-table-ces",
        "referenceRunId": global_ids[0],
        "comparedEvals": [{"runId": global_ids[1], "step": 0}],
        "aggregatedScoresCollapsed": False,
        "rowSize": "medium",
        "pageSize": 8,
        "columnVisibility": {
            "output.Schedule or deferral": False,
            "output.Harness": False,
            "output.Agent Lens": False,
            "output.Execution status": False,
            "output.Grade reason": False,
        },
    }
    if receipt.get("evaltables_config") != native_config:
        existing_view = receipt.get("evaltables_url")
        view = ws.Workspace(
            entity=entity,
            project=project,
            name=f"{team} / Original versus frozen",
            sections=[
                ws.Section(
                    name="Same fresh cases, two harnesses",
                    is_open=True,
                    panels=[
                        UnknownPanel(
                            viewType="Evaluation Tables",
                            config=native_config,
                            layout={"x": 0, "y": 0, "w": 24, "h": 14},
                        )
                    ],
                )
            ],
            runset_settings=ws.RunsetSettings(
                filters=" or ".join(f"ID = '{i}'" for i in ids), baseline_run=ids[0]
            ),
        )
        if existing_view:
            old_view = ws.Workspace.from_url(existing_view)
            old_view.sections = view.sections
            old_view.runset_settings = view.runset_settings
            old_view.save()
            view = old_view
        else:
            view.save_as_new_view()
        receipt["evaltables_url"] = (
            f"https://wandb.ai/{entity}/{project}?" + view.url.split("?", 1)[1]
        )
        receipt["evaltables_config"] = native_config
        s.put(team, "publication", receipt)
    preserve_native_workspace(receipt["evaltables_url"], entity, project, native_config)
    receipt.update(
        project_url=f"https://wandb.ai/{entity}/{project}",
        agent_lens_url=f"https://forge.coreweave.com/agent-lens/{entity}/{project}/conversations",
    )
    s.put(team, "publication", receipt)
    (export_root / "publication.json").write_text(json.dumps(receipt, indent=2))
    if not report:
        return receipt
    rows_md = "\n".join(
        f"| {r['title']} | {r['original'] if r['original'] is not None else 'Unavailable'} | {r['frozen'] if r['frozen'] is not None else 'Unavailable'} |"
        for r in result["rows"]
    )
    method = (
        "Candidate A was submitted through the manual instruction editor. The improver proposed Candidate B and a composition."
        if snapshot.get("teaching_release") == "hands-on-v2"
        else "The agent proposed harness changes."
    )
    summary = f"""# What changed in our scheduling agent?

This is a saved workshop run by **{team}**. The model stayed fixed at `{snapshot["model"]}`.
{method} We tested the harnesses on four development days, then ran the frozen version on eight fresh days.
These are fictional operations cases and one execution per version per case. They demonstrate the workflow, not a general performance claim.

## The final comparison

We have **{result["pairs_available"]} of 8 paired grades**: **{result["wins"]} wins, {result["ties"]} ties, and {result["losses"]} losses** for the frozen harness.
Correctness means a complete constraint-compliant schedule, or a justified deferral when the independent checker finds no feasible complete schedule.

| Dispatch day | Original correct | Frozen correct |
|---|---|---|
{rows_md}

Open [the native EvalTables comparison]({receipt["evaltables_url"]}). Original is the reference, with the same eight case identities aligned across versions.
Every row includes the delivered schedule, grade reason, harness fingerprint, and Agent Lens conversation.

## Follow the work

"""
    for stage in ["candidate_a", "candidate_b", "composed"]:
        d = snapshot["decisions"].get(stage)
        if d:
            summary += f"**{stage.replace('_', ' ').title()} — {d['title']}.** Decision: {d['decision']}. [Inspect the actual diff and full rationale]({receipt['artifacts'][stage]}).\n\n"
    summary += "| Fresh-case execution | Original | Frozen |\n|---|---:|---:|\n"
    arms = [[r for r in final if r["stage"] == stage] for stage in ["eval_original", "eval_frozen"]]
    for label, key, fmt in [
        ("Tool errors (including recovered errors)", "tool_errors", ".0f"),
        ("Mean execution seconds", "seconds", ".1f"),
        ("Observed inference USD", "observed_usd", ".5f"),
    ]:
        values = [
            sum(r.get(key, 0) for r in arm) / (len(arm) if key == "seconds" else 1) for arm in arms
        ]
        summary += f"| {label} | {values[0]:{fmt}} | {values[1]:{fmt}} |\n"
    summary += "\nThese timings come from one small rehearsal. They are descriptive, not a repeatability claim.\n\n"
    if snapshot.get("curation"):
        c = snapshot["curation"]
        summary += f"The inspected development failure is saved as a [versioned regression case]({c['artifact_url']}) beside its [Agent Lens conversation]({c['agent_lens']}). The participant's observation is separate from the agent's rationale.\n\n"
    summary += (
        "## Costs and interpretation\n\n"
        + f"Recorded inference: **${snapshot['budget']['observed_usd']:.4f} observed**, "
        f"**${snapshot['budget']['bounded_unknown_usd']:.4f} bounded unknown**. This includes adaptation and probes. Hosting is accounted separately in the workshop ledger.\n\n"
        + "A tool error can recover without harming the final schedule. A successful submission can still violate a requirement. Compare the delivered work before attributing a score change to a harness edit.\n\n"
        + "## Apply this to your product\n\nWhat operation would you delegate? What would count as a wrong result? Which trace would let a teammate verify the problem? Add those answers and your next test to this Report.\n"
    )
    if receipt.get("workshop_materials_url"):
        summary += "\n## Workshop materials\n\n" + (
            f"[Download the participant notebook, replay pack, and narrated walkthrough]({receipt['workshop_materials_url']}). "
            "The recording follows actual saved executions through marimo, Agent Lens, Weave, the branch view, and EvalTables. "
            "It uses computer narration. The replay pack includes the three-hour plan and facilitator guide.\n"
        )
    summary += participant_conclusion(snapshot)
    (export_root / "report.md").write_text(summary)
    report = (
        wr.Report.from_url(receipt["report_url"])
        if receipt.get("report_url")
        else wr.Report(
            entity=entity,
            project=project,
            title=f"{team}: Improve a scheduling agent with Forge",
            description="Saved executions, harness changes, native evaluations, and Agent Lens evidence.",
        )
    )
    ids = [receipt["runs"][s]["id"] for s in ["eval_original", "eval_frozen"]]
    report.blocks = [
        wr.MarkdownBlock(text=summary),
        wr.PanelGrid(
            runsets=[
                wr.Runset(
                    entity=entity, project=project, filters=" or ".join(f"ID = '{i}'" for i in ids)
                )
            ],
            panels=[
                UnknownPanel(
                    **{
                        "__id__": "dispatch-final",
                        "isAuto": False,
                        "viewType": "Evaluation Tables",
                        "config": native_config,
                        "layout": {"x": 0, "y": 8, "w": 24, "h": 14},
                    }
                ),
                wr.BarPlot(
                    title="Correct scheduling decisions on fresh jobs",
                    metrics=["correct_rate"],
                    range_x=(0, 1),
                    title_x="Fraction correct",
                    layout=wr.Layout(w=24, h=8),
                ),
            ],
        ),
    ]
    report.save()
    receipt.update(
        report_url=f"https://wandb.ai/{entity}/{project}/reports/"
        + report.url.split("/reports/", 1)[1],
        project_url=f"https://wandb.ai/{entity}/{project}",
        agent_lens_url=f"https://forge.coreweave.com/agent-lens/{entity}/{project}/conversations",
    )
    s.put(team, "publication", receipt)
    (export_root / "publication.json").write_text(json.dumps(receipt, indent=2))
    return receipt
