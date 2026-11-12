"""Tables, diagrams and evidence links for the chamber notebook. No widgets."""

import html
import json


def stage_label(stage):
    labels = {
        "original": "Starting harness",
        "manual": "Your edited harness",
        "repeat_original": "Starting harness / repeat run",
        "tool_test": "Sandbox test / invoice lookup",
        "r2_start": "Round 2 / Test the selected billing harness on onboarding",
        "mcp_practice": "MCP connection practice",
        "eval_original": "Fresh case / Starting harness",
        "eval_manual": "Fresh case / Your edited harness",
        "eval_frozen": "Fresh case / Frozen selected harness",
    }
    if stage in labels:
        return labels[stage]
    parts = stage.split("_")
    if len(parts) < 2 or parts[0] not in {"r1", "r2"}:
        return stage
    return f"Round {parts[0][1:]} / " + {
        "a": "Test candidate A",
        "b": "Test candidate B",
        "composed": "Test the selected harness",
        "propose": "Editor proposes two changes",
        "compose": "Editor selects or combines changes",
        "reflect": "Editor saves a lesson",
    }.get(parts[-1], parts[-1])


def tool_label(name):
    label = {
        "lookup_invoice": "Look up invoice",
        "get_account": "Read account",
        "list_payments": "Read payments and refunds",
        "refund_payment": "Issue refund",
        "onboarding_status": "Read account access",
        "provision_member": "Grant account access",
        "read_skill": "Load a skill",
        "check_work": "Run the development check",
        "finish": "Submit final response",
    }.get(name)
    return f"{label} ({name})" if label else name


def tool_result(event):
    output = event["output"]
    if output.get("error"):
        return output["error"]
    if event["name"] == "finish" and output.get("accepted"):
        return "Response recorded; correctness not graded here"
    return "Tool returned successfully" if output.get("ok") else "Response returned"


def account_rows(run):
    rows = []
    for aid, account in run.get("world", {}).get("accounts", {}).items():
        before = run["initial_world"]["accounts"][aid]
        rows.append(
            {
                "Account": account["name"],
                "Changed": account != before,
                "Payments": len(account["payments"]),
                "Refunds before": len(before["refunds"]),
                "Refunds after": len(account["refunds"]),
                "Refunded USD": sum(r["amount_cents"] for r in account["refunds"]) / 100,
                "Access": ", ".join(m["email"] + " (" + m["role"] + ")" for m in account["members"])
                or "No members",
            }
        )
    return rows


def event_rows(snapshot):
    return [
        {
            "Case": r["title"],
            "Version / stage": stage_label(r["stage"]),
            "Execution": r["status"],
            "Task check": r.get("scores", {}).get("task_success", "Not graded"),
            "Seconds": round(r["seconds"], 1) if r.get("seconds") is not None else None,
            "Model USD": round(r["observed_usd"], 6) if r.get("observed_usd") is not None else None,
            "Unknown cost bound USD": r.get("bounded_unknown_usd"),
            "Harness": r["fingerprint"][:12],
            "Agent Lens": r.get("trace", {}).get("url", ""),
        }
        for r in snapshot.get("runs", [])
        if r["case_id"] not in {"decision", "tutorial"}
    ]


def branch_diagram(snapshot, round_number=1, *, compact=False):
    """One readable round; decisions and executions only appear when recorded."""
    import textwrap

    n = int(round_number)
    if n not in (1, 2):
        raise ValueError("Choose refinement round 1 or 2")
    runs = {r["stage"]: r for r in snapshot.get("runs", []) if not r["stage"].startswith("eval_")}
    lines = ["flowchart TB"]
    parent = "parent"
    parent_title = (
        "Your edited harness" if n == 1 and snapshot.get("manual") else "Starting harness"
    )
    if n == 2:
        if not snapshot.get("decisions", {}).get("r1_composed"):
            return 'flowchart TB\nwaiting["Round two has no saved decisions yet"]'
        parent_title = "Harness adopted in round 1"
    lines.append(f'parent["{parent_title}"]')
    start = "manual" if n == 1 else "r2_start"
    if start in runs:
        title = "Test of your edit" if n == 1 else "Same harness, onboarding task"
        shape = f'start_run("Execution<br/>{title}")' if compact else f'start_run(("{title}"))'
        lines += [shape, "parent -->|files executed| start_run"]
        parent = "start_run"
    for branch, label in [
        ("a", "Candidate A"),
        ("b", "Candidate B"),
        ("composed", "Selected harness"),
    ]:
        stage = f"r{n}_{branch}"
        d = snapshot.get("decisions", {}).get(stage)
        if not d:
            continue
        title = "Retain this harness" if d["decision"] == "KEEP" else d["title"]

        def escape_label(text):
            if not compact:
                return html.escape(text, quote=True)
            for char, entity in [("&", "#38;"), ("<", "#60;"), (">", "#62;"), ('"', "#quot;")]:
                text = text.replace(char, entity)
            return text

        title = "<br/>".join(escape_label(t) for t in textwrap.wrap(title, 34 if compact else 30))
        if compact:
            lines.append(f'{stage}["Decision · {label}<br/>{title}"]')
        else:
            lines.append(f'{stage}{{"{label}<br/>{title}"}}')
        based_on = d.get("based_on", "parent")
        base = parent if based_on == "parent" else f"r{n}_{based_on}"
        lines.append(f"{base} -->|based on its files| {stage}")
        if branch == "composed":
            for candidate in ["a", "b"]:
                if f"r{n}_{candidate}" in runs:
                    lines.append(f"r{n}_{candidate}_run -. reviewed .-> {stage}")
        if stage in runs:
            status = html.escape(runs[stage]["status"])
            shape = (
                f'{stage}_run("{label} execution<br/>{status}")'
                if compact
                else f'{stage}_run(("{label} execution<br/>{status}"))'
            )
            lines += [
                shape,
                f"{stage} -->|selected files executed| {stage}_run",
            ]
    if len(snapshot.get("library", [])) >= n:
        lines += ['lesson["Saved lesson + limits"]', f"r{n}_composed_run -. reflection .-> lesson"]
    frozen = snapshot.get("freeze")
    if frozen and frozen["stage"].startswith(f"r{n}_"):
        lines += ['freeze["Frozen selection"]', f"{frozen['stage']}_run ==>|selected| freeze"]
    if compact:
        lines.insert(
            0,
            '%%{init: {"theme":"base","themeVariables":{"fontSize":"12px","primaryColor":"#f4f4f4","primaryTextColor":"#192938","primaryBorderColor":"#9ca3af","lineColor":"#6b7280"},"flowchart":{"nodeSpacing":20,"rankSpacing":12,"padding":6}}}%%',
        )
    return "\n".join(lines)


def evidence_html(run):
    url = run.get("trace", {}).get("url", "")
    link = (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">Show what happened in Agent Lens ↗</a>'
        if url.startswith("https://forge.coreweave.com/")
        else "Trace unavailable. Saved local events remain below."
    )
    return f'<div class="office-evidence">{link}<pre>{html.escape(json.dumps({"stage": run["stage"], "harness": run["fingerprint"], "sandbox": run.get("sandbox"), "result": run.get("draft")}, indent=2))}</pre></div>'
