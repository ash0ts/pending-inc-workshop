"""Notebook layout and evidence formatting; no agent or scoring implementation."""

import html
import json

CHAPTERS = [
    ("setup", "0–10", "Clock in", "Send your first trace"),
    ("tools", "10–35", "Equipment was not included", "Build the invoice tool"),
    ("loop", "35–55", "Please acknowledge reality", "Connect action to observation"),
    ("traces", "55–80", "The receipt says otherwise", "Instrument the employee"),
    ("evals", "80–105", "Stop marking your own homework", "Write checks and a judge"),
    ("manual", "115–130", "The handbook has a new paragraph", "Test your manual edit"),
    (
        "improve",
        "130–160",
        "Management would like to automate management",
        "Build Propose → Probe → Compose",
    ),
    ("final", "160–180", "Monday, again", "Compare on fresh cases"),
]


def heading(key, task, verify):
    _, minutes, title, subtitle = next(c for c in CHAPTERS if c[0] == key)
    return f'''<section class="quest" id="{key}"><div class="quest-time">{minutes} MIN · {html.escape(subtitle)}</div>
<h2>{html.escape(title)}</h2><p><b>Your task.</b> {html.escape(task)}</p>
<p class="verify"><b>Check your work.</b> {html.escape(verify)}</p></section>'''


def floor():
    desk = '<svg class="pixel-desk" viewBox="0 0 96 64" aria-hidden="true"><path fill="#ad7952" d="M4 36h58v8H4z"/><path fill="#605047" d="M9 44h5v17H9zm43 0h5v17h-5z"/><path fill="#243a4a" d="M18 12h27v22H18z"/><path fill="#9bd7bd" d="M22 16h19v14H22z"/><path fill="#467f79" d="M25 20h13v3H25zm0 5h9v2h-9z"/><path fill="#e8bc8a" d="M74 15h14v16H74z"/><path fill="#253445" d="M71 10h17v6H71zm-2 6h6v14h-6zM73 49h5v13h-5zm10 0h5v13h-5z"/><path fill="#7770b5" d="M68 31h23v19H68z"/><path fill="#fff3d5" d="M72 35h4v7h-4z"/></svg>'
    tiles = "".join(
        f'<a href="#{key}" class="desk">{desk}<strong>{html.escape(sub)}</strong><small>{minutes} min</small></a>'
        for key, minutes, _, sub in CHAPTERS
    )
    return f'<nav class="office-floor" aria-label="Workshop floor plan">{tiles}</nav>'


CSS = """<style>
:root{--pending-ink:#192938;--pending-accent:#146c65;--pending-paper:#f7f2e8;}
.quest{border-top:3px solid var(--pending-ink);padding:24px 0 12px;margin-top:30px}.quest h2{font-size:27px;letter-spacing:-.5px;line-height:1.2;margin:8px 0 14px}.quest-time{font:700 12px ui-monospace,monospace;color:var(--pending-accent);letter-spacing:1px}.quest p{line-height:1.6;max-width:760px}.verify{border-left:3px solid #dcae4e;padding-left:14px}
.office-floor{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:18px;background:repeating-conic-gradient(#ebe5d9 0 25%,#f4efe5 0 50%) 0/24px 24px;border:3px solid #192938;box-shadow:5px 5px 0 #c7bda8}.desk{display:flex;flex-direction:column;min-height:118px;background:#fffaf1;border:2px solid #736b5f;padding:12px;color:#192938!important;text-decoration:none!important;box-shadow:3px 3px 0 #c7bda8}.desk:focus,.desk:hover{outline:3px solid #146c65;outline-offset:2px}.desk strong{font-size:13px;line-height:1.4}.desk small{color:#685f50;margin-top:auto}.person{font-size:24px;color:#146c65;line-height:1;margin-bottom:8px}.receipt{border:1px solid #c7c8ce;border-radius:6px;padding:14px}.receipt pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}.pending-banner{font:700 13px ui-monospace,monospace;letter-spacing:1px;color:#146c65}
@media(max-width:700px){.office-floor{grid-template-columns:repeat(2,1fr)}.quest h2{font-size:23px}}
.pixel-desk{width:96px;height:64px;shape-rendering:crispEdges;margin-bottom:8px}.desk{min-height:160px}
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
            "Step": i,
            "Action": e["name"],
            "Arguments": json.dumps(e["arguments"]),
            "Result": e["output"].get("error")
            or ("accepted" if e["output"].get("accepted") else "returned"),
            "Event": e["id"],
        }
        for i, e in enumerate(run.get("events", []))
    ]
