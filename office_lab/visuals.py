"""Tables, diagrams and evidence links for the chamber notebook. No widgets."""

import html
import json


def stage_label(stage):
    labels = {
        "original": "Original employee",
        "manual": "Your revision",
        "repeat_original": "Original repeated",
        "r2_start": "Next task with adopted harness",
        "mcp_practice": "MCP connection practice",
        "eval_original": "Fresh case / Original",
        "eval_manual": "Fresh case / Manual",
        "eval_frozen": "Fresh case / RSI",
    }
    if stage in labels:
        return labels[stage]
    parts = stage.split("_")
    return f"Round {parts[0][1:]} / " + {
        "a": "Candidate A",
        "b": "Candidate B",
        "composed": "Composition",
        "propose": "Proposal session",
        "compose": "Composition decision",
        "reflect": "Reflection",
    }.get(parts[-1], parts[-1])


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


def branch_diagram(snapshot, round_number=1):
    """One readable round; decisions and executions only appear when recorded."""
    import textwrap

    n = int(round_number)
    if n not in (1, 2):
        raise ValueError("Choose refinement round 1 or 2")
    runs = {r["stage"]: r for r in snapshot.get("runs", []) if not r["stage"].startswith("eval_")}
    lines = ["flowchart TB"]
    parent = "parent"
    parent_title = "Manual harness" if n == 1 and snapshot.get("manual") else "Original harness"
    if n == 2:
        if not snapshot.get("decisions", {}).get("r1_composed"):
            return 'flowchart TB\nwaiting["Round two has no saved decisions yet"]'
        parent_title = "Harness adopted in round 1"
    lines.append(f'parent["{parent_title}"]')
    start = "manual" if n == 1 else "r2_start"
    if start in runs:
        title = "Manual probe" if n == 1 else "Same harness, onboarding task"
        lines += [f'start_run(("{title}"))', "parent -->|files executed| start_run"]
        parent = "start_run"
    for branch, label in [("a", "Candidate A"), ("b", "Candidate B"), ("composed", "Composition")]:
        stage = f"r{n}_{branch}"
        d = snapshot.get("decisions", {}).get(stage)
        if not d:
            continue
        title = "Retain this harness" if d["decision"] == "KEEP" else d["title"]
        title = "<br/>".join(html.escape(t, quote=True) for t in textwrap.wrap(title, 30))
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
            lines += [
                f'{stage}_run(("{label} execution<br/>{status}"))',
                f"{stage} -->|selected files executed| {stage}_run",
            ]
    if len(snapshot.get("library", [])) >= n:
        lines += ['lesson["Saved lesson + limits"]', f"r{n}_composed_run -. reflection .-> lesson"]
    frozen = snapshot.get("freeze")
    if frozen and frozen["stage"].startswith(f"r{n}_"):
        lines += ['freeze["Frozen selection"]', f"{frozen['stage']}_run ==>|selected| freeze"]
    return "\n".join(lines)


def evidence_html(run):
    url = run.get("trace", {}).get("url", "")
    link = (
        f'<a href="{html.escape(url, quote=True)}" target="_blank" rel="noopener">Show what happened in Agent Lens ↗</a>'
        if url.startswith("https://forge.coreweave.com/")
        else "Trace unavailable. Saved local events remain below."
    )
    return f'<div class="office-evidence">{link}<pre>{html.escape(json.dumps({"stage": run["stage"], "harness": run["fingerprint"], "sandbox": run.get("sandbox"), "result": run.get("draft")}, indent=2))}</pre></div>'
