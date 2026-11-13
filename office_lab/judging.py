"""Durable post-execution LLM reviews. No solver, improver, or hidden grader calls."""

import inspect
import time

from workshop.budget import BudgetStop
from workshop.provider import ProviderUnavailable, json_output
from workshop.telemetry import calls_for, export_run

from .evaluation import RUBRIC, judge_messages, review_evidence, validate_review
from .harness import fingerprint


def suite_config(manifest):
    return {
        "id": "office-evaluation-v1",
        "rubric": RUBRIC,
        "judge_model": manifest["model"],
        "temperature": manifest["temperature"],
        "reasoning_effort": manifest["judge_reasoning_effort"],
        "max_output_tokens": manifest["improver_max_output_tokens"],
        "prompt_source": inspect.getsource(judge_messages),
        "validation_source": inspect.getsource(validate_review),
        "input_source": inspect.getsource(review_evidence),
        "transport_retries": manifest["transport_retries"],
        "primary_measure": "Independent final-state correctness; separate LLM explanation scores",
        "review_is_primary": False,
    }


def judgments(gateway, team):
    suite = gateway.get(team, "evaluation_suite")
    if not suite:
        return []
    active = gateway.get(team, "review_validation_revision")
    lab = active["lab"] if active else "office-evaluation-v1"
    rows = [r for r in gateway.store.runs(team) if r.get("lab") == lab]
    for row in rows:
        row["trace"] = gateway.store.get(
            team, "trace/" + row.get("source_judgment_id", row["id"]), {}
        )
    return rows


def export_judge(gateway, row):
    if row.get("source_judgment_id"):
        source = next(
            r for r in gateway.store.runs(row["team"]) if r["id"] == row["source_judgment_id"]
        )
        return export_judge(gateway, source)
    if gateway.export and calls_for(gateway.store, row["id"]):
        try:
            export_run(
                gateway.store, {**gateway.manifest, "agent_name": "office-review-judge"}, row
            )
            gateway.put(row["team"], "export_error/" + row["id"], None)
        except Exception as exc:
            gateway.put(row["team"], "export_error/" + row["id"], str(exc))


def review_one(gateway, team, execution_id):
    with gateway.lock(team):
        if gateway.get(team, "review_validation_revision"):
            return next(r for r in judgments(gateway, team) if r["execution_id"] == execution_id)
        if not gateway.get(team, "graded"):
            raise ValueError(
                "Finish all twelve executions and independent grading before LLM review"
            )
        run = gateway.result(team, execution_id)
        if not run["stage"].startswith("eval_"):
            raise ValueError("This suite reviews fresh-task executions only")
        config = suite_config(gateway.manifest)
        suite = {"fingerprint": fingerprint(config), "config": config}
        old = gateway.get(team, "evaluation_suite")
        if old and old != suite:
            raise ValueError("Evaluation settings changed; use a new evaluation identity")
        if not old:
            gateway.put(team, "evaluation_suite", suite, once=True)
        evidence = review_evidence(run)
        messages = judge_messages(evidence)
        identity = {
            "team": team,
            "lab": "office-evaluation-v1",
            "execution_id": execution_id,
            "suite": suite["fingerprint"],
            "input_hash": fingerprint(evidence),
        }
        run_id = fingerprint(identity)[:32]
        claimed, row = gateway.store.claim_run(
            {
                **identity,
                "id": run_id,
                "case_id": run["case_id"],
                "stage": "judge_" + run["stage"],
                "fingerprint": run["fingerprint"],
                "title": run["title"],
                "started": time.time(),
                "attempt": 1,
                "mission": "Review the employee explanation against its office actions:\n"
                + run["mission"],
                "input": evidence,
                "messages": messages,
                "trace_attributes": {
                    "workshop.execution": execution_id,
                    "workshop.evaluation_suite": suite["fingerprint"],
                    "workshop.role": "judge",
                },
            }
        )
        if row.get("ended"):
            export_judge(gateway, row)
            return next(r for r in judgments(gateway, team) if r["id"] == run_id)
        if run["status"] != "complete":
            row.update(status="unavailable", error="No completed submission to review")
        else:
            try:
                calls = calls_for(gateway.store, run_id)
                # A durable successful response can be parsed after a process restart.
                completed = [
                    c
                    for c in calls
                    if not c.get("error") and (c.get("response") or {}).get("choices")
                ]
                if completed:
                    response = completed[-1]
                elif calls:
                    raise ProviderUnavailable(
                        "An earlier provider attempt has no reusable response; preserved without another paid call"
                    )
                elif not claimed:
                    raise ProviderUnavailable(
                        "Interrupted judge claim; preserved without another paid call"
                    )
                else:
                    response = gateway.provider.complete(team, run_id, 0, messages, role="judge")
                row.update(
                    outcome=validate_review(json_output(response), evidence), status="complete"
                )
            except Exception as exc:
                row.update(
                    status="unavailable",
                    error=str(exc),
                    failure_kind="infrastructure"
                    if isinstance(exc, (BudgetStop, ProviderUnavailable))
                    else "invalid_judgment",
                )
        row["ended"] = time.time()
        row["seconds"] = row["ended"] - row["started"]
        calls = calls_for(gateway.store, run_id)
        row["observed_usd"] = sum(c.get("accounting", {}).get("observed_usd") or 0 for c in calls)
        row["bounded_unknown_usd"] = sum(
            c.get("accounting", {}).get("bounded_unknown_usd", 0) for c in calls
        )
        gateway.store.save_run(row)
        export_judge(gateway, row)
        return next(r for r in judgments(gateway, team) if r["id"] == run_id)


def evaluate(gateway, team):
    if not gateway.get(team, "graded"):
        raise ValueError("Finish independent grading before LLM review")
    for run in gateway.runs(team):
        if run["stage"].startswith("eval_"):
            review_one(gateway, team, run["id"])
    return gateway.snapshot(team)
