"""Office tools without the CoreWeave Sandbox service.

Only the registered lookup runs in Monty's bounded Python interpreter. The
other tools and the stdio MCP server are fixed workshop code. No learner Python
is executed in the gateway's interpreter.
"""

import asyncio
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

from .harness import Harness, fingerprint
from .runner import mcp_action
from .simulator import check_work, execute


class LocalToolsBackend:
    mode = "local-restricted-python"

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def call(self, gateway, run, step, name, arguments, task):
        key = f"local-tool/{run['id']}/{step}"
        digest = fingerprint([name, arguments, run["world"], run["harness"]])
        saved = gateway.get(run["team"], key)
        if saved:
            if saved["input_hash"] != digest:
                raise ValueError("Saved local tool request has different inputs")
            return saved["result"]
        if gateway.get(run["team"], "tool_source"):
            raise ValueError("Local mode supports the code-first lookup exercise only")
        root = self.root / run["id"]
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        files = Harness(**run["harness"]).files()
        for name_, content in files.items():
            path = root / "harness" / name_
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode("utf-8"))
        world = copy.deepcopy(run["world"])
        source = (gateway.get(run["team"], "registration") or {}).get("tool_source", "")
        if name == "lookup_invoice":
            from pydantic_monty import Monty, MontyRuntimeError

            if not source:
                raise ValueError("Register lookup_invoice first")
            # No host functions, mounts, credentials, or network capabilities.
            with Monty(
                binary_path=Path(sys.executable).parent
                / ("monty.exe" if os.name == "nt" else "monty"),
                max_processes=1,
                request_timeout=5,
            ) as pool:
                with pool.checkout(
                    limits={"max_feed_duration_secs": 2, "max_memory": 32 * 1024 * 1024}
                ) as session:
                    try:
                        world, output = session.feed_run(
                            source
                            + "\nresult = lookup_invoice(world, **arguments)\n(world, result)",
                            inputs={"world": world, "arguments": arguments},
                        )
                    except MontyRuntimeError as error:
                        output = {"ok": False, "error": str(error)}
        elif name in {"onboarding_status", "provision_member", "discover_onboarding"}:
            state = root / f"step-{step}.state.json"
            state.write_text(json.dumps({"world": world, "fault": task.fault}))
            output = asyncio.run(mcp_action(state, name, arguments, clean_environment=True))
            world = json.loads(state.read_text())["world"]
        elif name == "check_work":
            output = {"ok": True, **check_work(world, task.objective)}
        else:
            world, output = execute(world, name, arguments, task.fault)
        result = {
            "world": world,
            "output": output,
            "files_sha256": {
                name_: hashlib.sha256((root / "harness" / name_).read_bytes()).hexdigest()
                for name_ in files
            },
            "tool_source_sha256": hashlib.sha256(source.encode()).hexdigest(),
            "receipt": {
                "mode": self.mode,
                "remote_sandbox": False,
                "request_sha256": digest,
                "lookup_runtime": "pydantic-monty==1.6.0",
                "mcp_transport": "stdio"
                if name in {"onboarding_status", "provision_member", "discover_onboarding"}
                else None,
            },
        }
        # Persist the completed mutation before handing it back to the gateway.
        gateway.put(run["team"], key, {"input_hash": digest, "result": result}, once=True)
        return result

    def stop(self, gateway, run):
        return {"mode": self.mode, "remote_sandbox": False, "cleanup": "no_remote_resource"}
