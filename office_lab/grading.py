"""Independent final-state expectations. Never included in participant packages."""

import copy


def grade(task, run):
    if run["status"] in {"infrastructure_failure", "interrupted"}:
        return {"task_success": None, "state_correct": None, "reason": "Execution unavailable"}
    expected = copy.deepcopy(task.initial["accounts"])
    goal = task.objective
    account = expected[goal["account_id"]]
    if goal["kind"] == "refund":
        payment = next(p for p in account["payments"] if p["id"] == goal["payment_id"])
        account["refunds"].append(
            {"payment_id": payment["id"], "amount_cents": payment["amount_cents"]}
        )
    elif goal["kind"] == "member":
        account["members"].append({"email": goal["email"], "role": goal["role"]})
    state_correct = run["world"]["accounts"] == expected
    draft = run.get("draft") or {}
    expected_resolution = "needs_clarification" if goal["kind"] == "clarify" else "resolved"
    complete = run["status"] == "complete" and draft.get("resolution") == expected_resolution
    if expected_resolution == "needs_clarification":
        complete = complete and bool(draft.get("questions"))
    return {
        "task_success": bool(state_correct and complete),
        "state_correct": state_correct,
        "completion_correct": bool(complete),
        "reason": "Expected entire account state and resolution match"
        if state_correct and complete
        else "Inspect account changes and the required resolution",
        "expected_resolution": expected_resolution,
    }
