"""Offline setup check. No provider, gateway, or Sandbox connection is made."""

import ast
import importlib.metadata
import json
import platform
from pathlib import Path

from office_lab.code_first import support
from office_lab.code_first.solutions import ANSWERS

ast.parse(Path("office_notebook.py").read_text())
namespace = {"json": json}
exec(ANSWERS["invoice"], namespace)
exec(ANSWERS["dispatch"], namespace)
public = json.loads(Path("assets/office-development.json").read_text())
assert all(row["passed"] for row in support.tool_checks(namespace["lookup_invoice"], public))
assert support.loop_checks(namespace["dispatch_tool"], namespace["append_observation"])
print(
    json.dumps(
        {
            "platform": platform.system(),
            "python": platform.python_version(),
            "marimo": importlib.metadata.version("marimo"),
            "public_worked_answer_checks": "passed",
            "paid_calls": 0,
            "live_access": "not checked",
        },
        indent=2,
    )
)
