"""Read saved progress to explain the next action. Never execute a workshop stage."""

PHASES = [
    (
        "propose",
        "Ask the editor for two harness changes",
        "Ask the editor for two competing harness changes.",
    ),
    (
        "probe_a",
        "Test candidate A",
        "Review candidate A below, then test those files in a fresh office.",
    ),
    (
        "probe_b",
        "Test candidate B",
        "Review candidate B below, then test it on the same case from fresh state.",
    ),
    (
        "compose",
        "Ask the editor to select or combine changes",
        "Ask the editor to compare both probes and save a CHANGE or KEEP decision.",
    ),
    (
        "execute",
        "Test the selected harness",
        "Review the selected files, then execute them. A KEEP decision still needs a test.",
    ),
    (
        "reflect",
        "Ask the editor to save a lesson",
        "Ask the editor what the execution supports and where that lesson may not apply.",
    ),
]


def stage_run(snapshot, stage):
    runs = [r for r in snapshot.get("runs", []) if r.get("stage") == stage]
    return max(runs, key=lambda r: r.get("started", 0), default=None)


def completed(snapshot, stage):
    run = stage_run(snapshot, stage)
    return bool(run and run.get("ended"))


def lesson(snapshot, number):
    ids = {r["id"] for r in snapshot.get("runs", []) if r.get("stage") == f"r{number}_reflect"}
    return next(
        (item for item in snapshot.get("library", []) if item.get("source_run") in ids), None
    )


def phase_done(snapshot, number, phase):
    decisions = snapshot.get("decisions", {})
    if phase == "propose":
        return all(decisions.get(f"r{number}_{branch}") for branch in ["a", "b"])
    if phase == "compose":
        return bool(decisions.get(f"r{number}_composed"))
    if phase == "reflect":
        return bool(lesson(snapshot, number))
    suffix = "composed" if phase == "execute" else phase[-1]
    run = stage_run(snapshot, f"r{number}_{suffix}")
    decision = decisions.get(f"r{number}_{suffix}")
    return bool(
        run and run.get("ended") and decision and run["fingerprint"] == decision["fingerprint"]
    )


def improvement_step(snapshot):
    if not completed(snapshot, "manual"):
        return {
            "blocked": "Run the billing case with your edited harness first.",
            "anchor": "manual",
            "label": "Waiting for your edited-harness billing run",
        }
    for number in [1, 2]:
        if number == 2 and not completed(snapshot, "r2_start"):
            return {
                "round": 2,
                "phase": "start",
                "label": "Run the selected harness on onboarding",
                "description": "Run the billing harness on the onboarding case. Its new trace becomes round two’s starting evidence.",
            }
        for phase, label, description in PHASES:
            if not phase_done(snapshot, number, phase):
                suffix = (
                    "composed"
                    if phase == "execute"
                    else phase[-1]
                    if phase.startswith("probe_")
                    else None
                )
                run = stage_run(snapshot, f"r{number}_{suffix}") if suffix else None
                decision = (
                    snapshot.get("decisions", {}).get(f"r{number}_{suffix}") if suffix else None
                )
                if run and decision and run["fingerprint"] != decision["fingerprint"]:
                    return {
                        "blocked": "The saved test used different files from this decision. Ask the facilitator to inspect its receipt before continuing.",
                        "anchor": "improve",
                        "label": "Harness mismatch: inspect the saved test",
                    }
                return {"round": number, "phase": phase, "label": label, "description": description}
    return {"complete": True, "label": "Both improvement rounds are complete", "anchor": "final"}


def refresh(context):
    if not context:
        return {}
    snapshot = context["client"].snapshot()
    journal = context["journal"]
    for stage in ["original", "manual", "r2_start"]:
        run = stage_run(snapshot, stage)
        if run:
            journal.put(stage, run)
    original = stage_run(snapshot, "original")
    registration = journal.get("registration", {})
    tool_test = journal.get("tool_test", {})
    qualified = bool(
        tool_test.get("passed") and tool_test.get("registration") == registration.get("fingerprint")
    )
    if not qualified and registration:
        runs = [
            r
            for r in snapshot.get("runs", [])
            if r.get("stage") == "tool_test"
            and r.get("registration_fingerprint") == registration.get("fingerprint")
            and r.get("ended")
        ]
        for run in runs:
            outputs = [event["output"] for event in run.get("events", [])]
            if (
                len(outputs) == 3
                and all(item.get("ok") is True for item in outputs)
                and [len(item.get("payments", [])) for item in outputs] == [2, 0, 2]
                and run.get("world") == run.get("initial_world")
            ):
                qualified = True
    return {
        "connected": True,
        "tracing": "session" in context,
        "snapshot": snapshot,
        "registered": bool(qualified or snapshot.get("baseline")),
        "registration_saved": bool(registration),
        "judge_original": bool(original and journal.get("judge/" + original["id"])),
        "final_publications": bool(journal.get("final-publications")),
        "conclusion": bool(journal.get("conclusion")),
    }


def blocked(action, progress):
    """Return the unmet prerequisite and its chapter, before a button is enabled."""
    live = {
        "setup_trace",
        "install_runtime",
        "export_sandbox",
        "step_employee",
        "finish_employee",
        "export_original",
        "judge_original",
        "publish_original",
        "run_manual",
        "publish_manual",
        "improvement_step",
        "inspect_branches",
        "freeze_runtime",
        "final_runs",
        "publish_final",
        "save_conclusion",
        "publish_report",
    }
    if action not in live:
        return None
    if not progress.get("connected"):
        return {"text": "Connect to your project in the first chapter.", "anchor": "setup"}
    if action == "setup_trace":
        return None
    if not progress.get("tracing"):
        return {"text": "Click Send my first Forge trace in the first chapter.", "anchor": "setup"}
    snapshot = progress.get("snapshot", {})
    if action in {"step_employee", "finish_employee"} and not progress.get("registered"):
        return {"text": "Register and test your tool with the gateway above.", "anchor": "loop"}
    if action in {
        "export_original",
        "judge_original",
        "publish_original",
        "run_manual",
    } and not completed(snapshot, "original"):
        return {
            "text": "Use Continue this billing run until it stops in the billing chapter first.",
            "anchor": "loop",
        }
    if action == "export_sandbox" and not progress.get("registration_saved"):
        return {
            "text": "Register and test your tool above to create its execution record.",
            "anchor": "loop",
        }
    if action == "publish_original" and not progress.get("judge_original"):
        return {
            "text": "Click Ask the judge to assess the billing explanation above. An unavailable judgment can still be published as missing.",
            "anchor": "evals",
        }
    if action in {"publish_manual", "improvement_step", "inspect_branches"} and not completed(
        snapshot, "manual"
    ):
        return {
            "text": "Save your harness edit and rerun the billing case first.",
            "anchor": "manual",
        }
    if action == "freeze_runtime" and not improvement_step(snapshot).get("complete"):
        return {"text": "Complete both guided improvement rounds first.", "anchor": "improve"}
    if action == "final_runs" and not snapshot.get("freeze"):
        return {"text": "Freeze a tested harness above.", "anchor": "final"}
    if action == "publish_final" and not snapshot.get("graded"):
        return {"text": "Finish the 12 fresh evaluations and grading above.", "anchor": "final"}
    if action in {"save_conclusion", "publish_report"} and not progress.get("final_publications"):
        return {"text": "Publish the final comparison above.", "anchor": "final"}
    if action == "publish_report" and not progress.get("conclusion"):
        return {"text": "Write and save your conclusion above.", "anchor": "final"}
    return None


def evidence_rows(snapshot):
    run = stage_run(snapshot, "original")
    if not run or not run.get("ended"):
        return []
    import json

    from office_lab import visuals

    return [
        {
            "Step": i + 1,
            "Tool": visuals.tool_label(e["name"]),
            "Arguments": json.dumps(e["arguments"]),
            "Result": visuals.tool_result(e),
            "event_id": e["id"],
        }
        for i, e in enumerate(run.get("events", []))
    ]


def selected_event(snapshot, selection):
    if not selection or len(selection) != 1:
        return None
    run = stage_run(snapshot, "original")
    return next(
        (e for e in (run or {}).get("events", []) if e["id"] == selection[0]["event_id"]), None
    )


def manual_ready(snapshot, selection, hypothesis, expected):
    if not selected_event(snapshot, selection):
        return "Select one event in the table above."
    if not 10 <= len(hypothesis.strip()) <= 1400:
        return "Describe what you observed in 10–1,400 characters."
    if not 10 <= len(expected.strip()) <= 1400:
        return "Describe the behavior you expect in 10–1,400 characters."
    return None


def freeze_options(snapshot):
    labels = {
        "r2_composed": "Selected after onboarding · round two",
        "r1_composed": "Selected after billing · round one",
        "manual": "Your edited harness",
    }
    return {label: stage for stage, label in labels.items() if completed(snapshot, stage)}
