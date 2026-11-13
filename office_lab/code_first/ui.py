"""Notebook layout and evidence formatting; no agent or scoring implementation."""

import html
import json

from office_lab import visuals

CHAPTERS = [
    ("setup", "0–10", "Clock in. IT would like proof.", "Connect Forge and send a test trace"),
    ("tools", "10–35", "Equipment was not included.", "Build and trace the invoice lookup tool"),
    ("loop", "35–55", "Please acknowledge reality.", "Run the billing agent, one action at a time"),
    ("traces", "55–80", "The receipt says otherwise.", "Inspect tool calls and timeout recovery"),
    (
        "evals",
        "80–105",
        "Stop marking your own homework.",
        "Check the refund and judge the explanation",
    ),
    (
        "manual",
        "115–130",
        "The handbook has a new paragraph.",
        "Edit the harness and rerun the billing case",
    ),
    (
        "improve",
        "130–160",
        "Management would like to automate management.",
        "Use an editor agent to propose and test changes",
    ),
    ("final", "160–180", "Monday, again.", "Compare three harnesses on four fresh cases"),
]

FORGE_CHECKPOINTS = {
    "setup": (
        "Forge Notebooks",
        "Your workbench: edit Python here, run a cell, inspect its output. Agent Lens stores the execution evidence in your project.",
    ),
    "tools": (
        "Forge SDK + local tools",
        "Write the lookup and its tool span here. Register it with the gateway, then read the receipt: it identifies the executed source and that your tool ran in restricted local Python.",
    ),
    "loop": (
        "Agent Lens",
        "Let the billing run end, then open its conversation below the result. Find the lookup and the next model request. Does that request include the returned invoice?",
    ),
    "traces": (
        "Agent Lens",
        "Return to the same conversation. Read the arguments, observation and next action together. A failed tool call can be followed by a successful recovery.",
    ),
    "evals": (
        "Weave + EvalTables",
        "The trace shows what ran. Your checks ask whether it was correct. Publish the development row, then follow its conversation link back to the work being scored.",
    ),
    "manual": (
        "Agent Lens + harness files",
        "Find the event that prompted your edit. After the matched run, open its new conversation and check whether the employee used the changed procedure.",
    ),
    "improve": (
        "Isolated tools + Agent Lens",
        "Each probe starts with its own office state. Open the candidate's saved conversation from its phase result, then compare the trace with the proposed files.",
    ),
    "final": (
        "EvalTables + W&B Artifacts",
        "Compare the starting harness, your edit, and the frozen selected harness on the same case. Open a score's trace and the versioned files before explaining the difference.",
    ),
}


def heading(key, task, verify):
    _, minutes, title, subtitle = next(c for c in CHAPTERS if c[0] == key)
    return f'''<section class="quest" id="{key}"><div class="quest-time">{minutes} MIN</div>
<h2>{html.escape(subtitle)}</h2><p class="quest-aside">{html.escape(title)}</p><p><b>Your task.</b> {html.escape(task)}</p>
<p class="verify"><b>Check your work.</b> {html.escape(verify)}</p></section>'''


def forge_checkpoint(key, project=None):
    product, instruction = FORGE_CHECKPOINTS[key]
    link = (
        '<a target="_blank" rel="noopener" href="https://forge.coreweave.com/agent-lens/'
        + html.escape(project, quote=True)
        + '/conversations">Open my Agent Lens project ↗</a>'
        if project
        else "<span>Connect in the first chapter for your project link.</span>"
    )
    return (
        '<aside class="forge-checkpoint"><strong>'
        + html.escape(product)
        + "</strong><p>"
        + html.escape(instruction)
        + "</p>"
        + link
        + "</aside>"
    )


def trace_results(outcomes):
    from .support import execution_note

    cards = []
    for item in outcomes:
        label = html.escape(visuals.stage_label(item["stage"]) + " · " + item["title"])
        if item["status"] == "verified":
            detail = (
                f"{item['model_spans']} model calls · {item['tool_spans']} tool calls · stored spans verified"
                + '<br><a target="_blank" rel="noopener" href="'
                + html.escape(item["url"], quote=True)
                + '">Open this Agent Lens conversation ↗</a>'
            )
        elif item["status"] == "running":
            detail = "This run has not ended. Its calls are saved below. Continue it in the notebook; the full conversation exports when the run ends."
        else:
            detail = (
                "Execution saved; trace export needs attention. "
                + html.escape(item["error"])
                + " Use the export retry below. Do not rerun the employee to repair a trace."
            )
        cards.append(
            '<div class="forge-checkpoint"><strong>'
            + label
            + "</strong><p>"
            + detail
            + "<br>"
            + html.escape(execution_note(item.get("execution_mode")))
            + "</p></div>"
        )
    return "".join(cards) or "<p>No saved executions to export yet.</p>"


def floor():
    desk = '<svg class="pixel-desk" viewBox="0 0 96 64" aria-hidden="true"><path fill="#ad7952" d="M4 36h58v8H4z"/><path fill="#605047" d="M9 44h5v17H9zm43 0h5v17h-5z"/><path fill="#243a4a" d="M18 12h27v22H18z"/><path fill="#9bd7bd" d="M22 16h19v14H22z"/><path fill="#467f79" d="M25 20h13v3H25zm0 5h9v2h-9z"/><path fill="#e8bc8a" d="M74 15h14v16H74z"/><path fill="#253445" d="M71 10h17v6H71zm-2 6h6v14h-6zM73 49h5v13h-5zm10 0h5v13h-5z"/><path fill="#7770b5" d="M68 31h23v19H68z"/><path fill="#fff3d5" d="M72 35h4v7h-4z"/></svg>'
    tiles = "".join(
        f'<a href="#{key}" class="desk">{desk}<strong>{html.escape(sub)}</strong><small>{minutes} min</small></a>'
        for key, minutes, _, sub in CHAPTERS
    )
    return f'<nav class="office-floor" aria-label="Workshop floor plan">{tiles}</nav>'


CSS = """<style>
:root{--pending-ink:#192938;--pending-accent:#146c65;--pending-paper:#f7f2e8;}
.quest{border-top:3px solid var(--pending-ink);padding:24px 0 12px;margin-top:30px}.quest h2{font-size:27px;letter-spacing:-.5px;line-height:1.2;margin:8px 0 14px}.quest-time{font:700 12px ui-monospace,monospace;color:var(--pending-accent);letter-spacing:1px}.quest-aside{font-style:italic;opacity:.85}.quest p{line-height:1.6;max-width:760px}.verify{border-left:3px solid #dcae4e;padding-left:14px}
.office-floor{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:18px;background:repeating-conic-gradient(#ebe5d9 0 25%,#f4efe5 0 50%) 0/24px 24px;border:3px solid #192938;box-shadow:5px 5px 0 #c7bda8}.desk{display:flex;flex-direction:column;min-height:118px;background:#fffaf1;border:2px solid #736b5f;padding:12px;color:#192938!important;text-decoration:none!important;box-shadow:3px 3px 0 #c7bda8}.desk:focus,.desk:hover{outline:3px solid #146c65;outline-offset:2px}.desk strong{font-size:13px;line-height:1.4}.desk small{color:#685f50;margin-top:auto}.person{font-size:24px;color:#146c65;line-height:1;margin-bottom:8px}.receipt{border:1px solid #c7c8ce;border-radius:6px;padding:14px}.receipt pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.pending-banner{font:700 13px ui-monospace,monospace;letter-spacing:1px;color:#146c65}
@media(max-width:700px){.office-floor{grid-template-columns:repeat(2,1fr)}.quest h2{font-size:23px}}
.pixel-desk{width:96px;height:64px;shape-rendering:crispEdges;margin-bottom:8px}.desk{min-height:160px}
.forge-checkpoint{border-left:3px solid #29877b;background:rgba(41,135,123,.09);padding:12px 16px;margin:12px 0;line-height:1.6;overflow-wrap:anywhere}.forge-checkpoint p{margin:5px 0}.forge-checkpoint a{font-weight:600}
.pending-diff{white-space:pre-wrap;overflow-wrap:anywhere;padding:14px;background:#8881;border:1px solid #8883;border-radius:6px;max-height:340px;overflow-y:auto;font:13px/1.55 ui-monospace,monospace}.pending-prerequisite{padding:10px 14px;background:rgba(220,174,78,.13);border-left:3px solid #bd8c27;line-height:1.5}.pending-workbench{border-top:2px solid #29877b;padding:12px 0}.pending-workbench h3{margin:0 0 12px}.pending-stepper{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));list-style:none;padding:0;gap:8px}.pending-stepper li{border:1px solid #aaa6;padding:10px;border-radius:5px;font-size:13px;line-height:1.35}.pending-stepper small{display:block;font-weight:700;margin-bottom:4px}.pending-now{border:2px solid #29877b!important;background:#29877b15}.pending-later{opacity:.65}.pending-done small{color:#29877b}@media(max-width:600px){.pending-stepper{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style>"""


def receipt(value):
    return (
        '<div class="receipt"><pre>'
        + html.escape(json.dumps(value, indent=2, ensure_ascii=False, default=str))
        + "</pre></div>"
    )


def event_table(run):
    return [
        {
            "Step": i + 1,
            "Action": visuals.tool_label(e["name"]),
            "Arguments": json.dumps(e["arguments"]),
            "Result": visuals.tool_result(e),
            "Event": e["id"],
        }
        for i, e in enumerate(run.get("events", []))
    ]


def billing_status(snapshot):
    from office_lab.code_first import flow

    run = flow.stage_run(snapshot, "original")
    if not run:
        text = "No billing run yet. Run one model action to inspect the handoff, or let the agent continue until it stops. Both controls use the starting harness."
    elif run.get("ended"):
        text = (
            "This billing run has ended. Saved status: "
            + html.escape(run["status"])
            + ". The controls below will not start another run. "
            + '<a href="#evals">Continue to the state checks and explanation judge ↓</a>.'
        )
    else:
        count = len(run.get("events", []))
        calls = "tool call" if count == 1 else "tool calls"
        text = f"{count} {calls} saved in this billing run. The next action continues from that state; it does not reset the account."
    return (
        '<div class="forge-checkpoint" role="status"><strong>Starting harness · Billing case</strong><p>'
        + text
        + "</p></div>"
    )


def timeout_status(actions, compared=False):
    read = next((a for a in actions if a["name"] == "onboarding_status"), None)
    stage = 3 if read and compared else 2 if read else 1 if actions else 0
    steps = []
    for i, title in enumerate(["Request access", "Check actual access", "Compare two retries"]):
        status = "Done" if i < stage else "Now" if i == stage else "Next"
        steps.append(
            f'<li class="pending-{status.lower()}"><small>{status}</small>{i + 1}. {title}</li>'
        )
    if not actions:
        text = "Start by asking the office tool to add Alex. Before you click, predict what a timeout would tell you about whether the work happened."
    elif not read:
        output = actions[-1]["output"]
        text = (
            "<strong>Tool response: timeout.</strong> No acknowledgement arrived. "
            "That alone does not tell you whether Alex was added. "
            "Use the next button to read the account before sending another write."
            "<p><em>IT has confirmed that confirmation is unavailable.</em></p>"
            if output.get("error") == "timeout"
            else "Tool response: " + html.escape(json.dumps(output)) + ". Check the account next."
        )
    else:
        output = read["output"]
        count = sum(
            m == {"email": "alex@example.test", "role": "member"} for m in output.get("members", [])
        )
        if not output.get("ok"):
            text = "The access check failed: " + html.escape(json.dumps(output))
        elif count == 1:
            text = "<strong>Alex has access: one member record.</strong> The request committed before its acknowledgement timed out. No second write is needed. <p><em>The door opened. The confirmation email is still in a meeting.</em></p>"
        else:
            text = f"<strong>Access check: {count} matching member records.</strong> The request requires exactly one."
    return (
        '<div class="pending-workbench"><ol class="pending-stepper">'
        + "".join(steps)
        + '</ol><div class="forge-checkpoint" role="status">'
        + text
        + "</div></div>"
    )


def timeout_comparison(branches):
    from office_lab import practice

    rows = []
    for key, title in [("same", "Retry the same request"), ("new", "Send a new request")]:
        if key not in branches:
            continue
        events = practice.attempt_actions(branches[key], "missing-seat")
        write, read = events[-2:]
        output = read["output"]
        count = sum(
            m == {"email": "alex@example.test", "role": "member"} for m in output.get("members", [])
        )
        result = (
            "Saved receipt returned; no extra write"
            if write["output"].get("replayed")
            else "A new write was recorded"
            if write["output"].get("ok")
            else "Write failed: " + str(write["output"].get("error"))
        )
        rows.append(
            {
                "Retry": title,
                "Tool response": result,
                "Alex’s member records": count if output.get("ok") else None,
            }
        )
    return rows


def publication_summary(publications, comparison):
    if comparison.get("url"):
        heading = (
            '<a href="'
            + html.escape(comparison["url"], quote=True)
            + '">Open native EvalTables comparison ↗</a>'
        )
    else:
        heading = "<strong>Native EvalTables is unavailable.</strong> Your Weave evaluations and W&B files were saved. Retry publication after service recovery; do not rerun the employee."
    links = "".join(
        '<li><a href="'
        + html.escape(item["weave_url"], quote=True)
        + '">Weave evaluation ↗</a> · <a href="'
        + html.escape(item["run_url"], quote=True)
        + '">W&B run and harness files ↗</a></li>'
        for item in publications
    )
    return '<div class="receipt"><p>' + heading + "</p><ul>" + links + "</ul></div>"


def prerequisite(block):
    if not block:
        return ""
    return (
        '<p class="pending-prerequisite" role="status">'
        + html.escape(block["text"])
        + ' <a href="#'
        + html.escape(block["anchor"], quote=True)
        + '">Go to this step ↑</a></p>'
    )


def improvement_panel(snapshot, button, mission, context=None):
    import marimo as mo

    from office_lab import visuals
    from office_lab.code_first import flow

    current = flow.improvement_step(snapshot)
    if current.get("blocked"):
        return mo.vstack(
            [
                mo.Html(prerequisite({"text": current["blocked"], "anchor": current["anchor"]})),
                button,
            ]
        )
    number = current.get("round", 2)
    phase = current.get("phase")
    name = "Billing" if number == 1 else "Onboarding"
    steps = []
    for key, label, _ in flow.PHASES:
        done = flow.phase_done(snapshot, number, key)
        state = "Done" if done else "Now" if key == phase else "Later"
        steps.append(
            '<li class="pending-'
            + state.lower()
            + '"><small>'
            + state
            + "</small>"
            + html.escape(label)
            + "</li>"
        )
    heading = (
        "Both rounds are complete" if current.get("complete") else f"Round {number} of 2 · {name}"
    )
    views = [
        mo.Html(
            '<section class="pending-workbench" id="improvement-workbench"><h3>'
            + heading
            + '</h3><ol class="pending-stepper">'
            + "".join(steps)
            + "</ol></section>"
        )
    ]
    if current.get("complete"):
        views.append(
            mo.md(
                "The employee has tested the selected harness on both development cases and saved its lessons. [Continue to the fresh-case comparison ↓](#final)."
            )
        )
    else:
        views.append(mo.md("**Next: " + current["label"] + ".** " + current["description"]))
    if phase == "propose":
        views.append(mission)
    if phase in {"probe_a", "probe_b", "execute"}:
        suffix = "composed" if phase == "execute" else phase[-1]
        decision = snapshot["decisions"][f"r{number}_{suffix}"]
        verb = "Retain this harness" if decision["decision"] == "KEEP" else decision["title"]
        views.extend(
            [
                mo.md("**" + verb + "**\n\n**Editor’s rationale:** " + decision["rationale"]),
                mo.Html(diff_block(decision["diff"]))
                if decision["diff"]
                else mo.md(
                    "The executable files are unchanged. This test still records how that harness behaves."
                ),
            ]
        )
    round_runs = [
        r
        for r in snapshot.get("runs", [])
        if r["stage"].startswith(f"r{number}_") and r.get("case_id") != "decision"
    ]
    if phase == "compose" or current.get("complete"):
        rows = [
            {
                "Run": visuals.stage_label(r["stage"]),
                "Status": r.get("status"),
                "Seconds": round(r["seconds"], 2) if r.get("seconds") is not None else None,
                "Model USD": r.get("observed_usd"),
                "Tools": len(r.get("events", [])),
            }
            for r in round_runs
        ]
        if rows:
            views.extend(
                [
                    mo.md(
                        "**What the probes recorded** · Execution status and cost are separate from correctness."
                    ),
                    mo.ui.table(
                        rows,
                        selection=None,
                        show_download=False,
                        show_column_summaries=False,
                        show_search=False,
                    ),
                ]
            )
    views.append(button)
    if context:
        links = []
        for run in round_runs:
            trace = context["journal"].get("trace/" + run["id"], {})
            if trace.get("verified") and trace.get("url"):
                links.append(
                    "["
                    + visuals.stage_label(run["stage"])
                    + " in Agent Lens]("
                    + trace["url"]
                    + ")"
                )
        if links:
            views.append(mo.md("**Inspect the work:** " + " · ".join(links)))
    details = {}
    for n in [1, 2]:
        decisions = [
            d for key, d in snapshot.get("decisions", {}).items() if key.startswith(f"r{n}_") and d
        ]
        if decisions:
            parts = [mo.mermaid(visuals.branch_diagram(snapshot, n, compact=True))]
            parts.extend(
                mo.vstack(
                    [
                        mo.md(
                            "**" + d["title"] + "**\n\n**Editor’s rationale:** " + d["rationale"]
                        ),
                        mo.Html(diff_block(d["diff"])),
                    ]
                )
                for d in decisions
            )
            saved_lesson = flow.lesson(snapshot, n)
            if saved_lesson:
                parts.append(mo.Html(receipt(saved_lesson)))
            details[f"Round {n}: saved branches, decisions and lesson"] = mo.vstack(parts)
    if details:
        views.append(mo.accordion(details))
    return mo.vstack(views)


def event_summary(event):
    output = event["output"]
    if output.get("error"):
        result = "Reported error: " + json.dumps(output["error"], ensure_ascii=False)
    else:
        result = visuals.tool_result(event)
        lists = [
            f"{name}: {len(value)} records"
            for name, value in output.items()
            if isinstance(value, list)
        ]
        if lists:
            result += " · " + "; ".join(lists)
    return (
        '<div class="forge-checkpoint"><code>'
        + html.escape(json.dumps(event["arguments"], ensure_ascii=False))
        + "</code><p>"
        + html.escape(result)
        + "</p></div>"
    )


def diff_block(diff):
    return (
        '<pre class="pending-diff" aria-label="Harness file changes">'
        + html.escape(diff)
        + "</pre>"
    )
