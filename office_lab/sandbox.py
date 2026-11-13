"""Sandbox lifecycle and receipts. Local execution is reserved for tests and labeled replay."""

import hashlib
import json
import time
from pathlib import Path

from .harness import Harness

IMAGE = "python@sha256:4766d8b510c428e595d74b9cc5bbb2fae8e26316fffb4adc89908d79aacd58a2"
LIFETIME = 600


class LocalBackend:
    mode = "local-test"

    def __init__(self, root):
        self.root = Path(root)

    def call(self, gateway, run, step, name, arguments, task):
        import os
        import subprocess
        import sys

        root = self.root / run["id"]
        root.mkdir(parents=True, exist_ok=True)
        files = Harness(**run["harness"]).files()
        for p, content in files.items():
            path = root / "harness" / p
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        package = root / "office_lab"
        package.mkdir(exist_ok=True)
        for file in ["__init__.py", "runner.py", "mcp_server.py"]:
            (package / file).write_bytes(Path(__file__).with_name(file).read_bytes())
        (package / "simulator.py").write_text(tool_code(gateway, run))
        source = root / f"step-{step}.json"
        source.write_text(json.dumps(payload(run, name, arguments, task)))
        proc = subprocess.run(
            [sys.executable, "-m", "office_lab.runner", str(source.resolve())],
            cwd=root,
            env={**os.environ, "PYTHONPATH": str(root.resolve())},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode:
            raise RuntimeError(proc.stderr)
        result = json.loads(proc.stdout)
        return {
            **result,
            "receipt": {"mode": self.mode, "exit_code": proc.returncode, "stderr": proc.stderr},
        }

    def stop(self, gateway, run):
        return {"mode": self.mode, "cleanup": "local_test_no_remote_resource"}


def tool_code(gateway, run):
    import inspect

    from .simulator import get_account

    code = Path(__file__).with_name("simulator.py").read_text()
    source = (
        gateway.get(run["team"], "tool_source")
        if hasattr(gateway, "get") and "team" in run
        else None
    )
    code = code.replace(inspect.getsource(get_account), source + "\n") if source else code
    registered = gateway.get(run["team"], "registration") if "team" in run else None
    if registered:
        code += "\n\n" + registered["tool_source"] + "\n"
    return code


def payload(run, name, arguments, task):
    return {
        "world": run["world"],
        "name": name,
        "arguments": arguments,
        "fault": task.fault,
        "objective": task.objective,
        "harness_files": list(Harness(**run["harness"]).files()),
    }


class SandboxBackend:
    mode = "coreweave-sandbox"

    def __init__(self):
        self.active = {}

    def start(self, gateway, run):
        from cwsandbox import AuthStrategy, NetworkOptions, ResourceOptions, Sandbox

        key = "sandbox/" + run["id"]
        receipt = gateway.get(run["team"], key)
        if run["id"] in self.active:
            return self.active[run["id"]]
        if receipt:
            raise RuntimeError(
                "Sandbox ownership was interrupted. Preserve the run; operator recovery is required."
            )
        archive = Path(__file__).resolve().parents[1] / ".context/office-mcp-wheels.zip"
        if not archive.exists():
            raise RuntimeError("Prepare the pinned offline MCP wheels before running a Sandbox")
        prices = gateway.manifest["sandbox_prices_hourly"]
        reserve = (prices["cpu"] + prices["memory_gib"]) * LIFETIME / 3600
        gateway.provider.budget.charge(
            key,
            gateway.store.team(run["team"])["bucket"],
            "Office sandbox maximum 600s, 1 CPU / 1 GiB",
            reserve,
        )
        receipt = {
            "mode": self.mode,
            "status": "creating",
            "image": IMAGE,
            "reserved_usd": reserve,
            "lifetime_seconds": LIFETIME,
            "created": time.time(),
            "cleanup": "pending",
        }
        gateway.put(run["team"], key, receipt, once=True)
        sandbox = Sandbox.run(
            "sleep",
            "infinity",
            auth=AuthStrategy.WANDB,
            container_image=IMAGE,
            max_lifetime_seconds=LIFETIME,
            resources=ResourceOptions(requests={"cpu": "1", "memory": "1Gi"}),
            network=NetworkOptions(deny_egress=True, deny_ingress=True),
            environment_variables={},
            tags=["nj-hub-workshop", "pending-inc"],
        )
        self.active[run["id"]] = sandbox
        receipt.update(sandbox_id=sandbox.sandbox_id)
        gateway.put(run["team"], key, receipt)
        sandbox.wait(timeout=60)
        sandbox.exec(
            ["mkdir", "-p", "/workspace/office_lab", "/workspace/harness/skills"],
            timeout_seconds=10,
        ).result()
        sandbox.write_file(
            "/workspace/wheels.zip", archive.read_bytes(), timeout_seconds=30
        ).result()
        install = sandbox.exec(
            [
                "sh",
                "-c",
                "python -m zipfile -e /workspace/wheels.zip /workspace/wheels && pip install --no-index --find-links /workspace/wheels mcp",
            ],
            timeout_seconds=60,
        ).result()
        if install.returncode:
            raise RuntimeError("Offline MCP installation failed: " + install.stderr[-1000:])
        root = Path(__file__).parent
        for file in ["__init__.py", "simulator.py", "runner.py", "mcp_server.py"]:
            sandbox.write_file(
                "/workspace/office_lab/" + file,
                (
                    tool_code(gateway, run).encode()
                    if file == "simulator.py"
                    else (root / file).read_bytes()
                ),
                timeout_seconds=10,
            ).result()
        for file, content in Harness(**run["harness"]).files().items():
            sandbox.write_file(
                "/workspace/harness/" + file, content.encode(), timeout_seconds=10
            ).result()
        receipt.update(
            status="ready",
            wheels_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            tool_sha256=hashlib.sha256(tool_code(gateway, run).encode()).hexdigest(),
        )
        gateway.put(run["team"], key, receipt)
        return sandbox

    def call(self, gateway, run, step, name, arguments, task):
        sandbox = self.start(gateway, run)
        stem = f"/workspace/step-{step}"
        request = json.dumps(payload(run, name, arguments, task))
        sandbox.write_file(stem + ".json", request.encode(), timeout_seconds=10).result()
        proc = sandbox.exec(
            ["python", "-m", "office_lab.runner", stem + ".json"],
            cwd="/workspace",
            timeout_seconds=30,
        ).result()
        receipt = {
            "mode": self.mode,
            "sandbox_id": sandbox.sandbox_id,
            "exit_code": proc.returncode,
            "stderr": proc.stderr,
            "stdout": proc.stdout,
            "request_sha256": hashlib.sha256(request.encode()).hexdigest(),
        }
        gateway.put(run["team"], f"sandbox-process/{run['id']}/{step}", receipt)
        if proc.returncode:
            raise RuntimeError("Office tool process failed; inspect its saved stderr")
        result = json.loads(sandbox.read_file(stem + ".result.json", timeout_seconds=10).result())
        expected = {
            p: hashlib.sha256(t.encode()).hexdigest()
            for p, t in Harness(**run["harness"]).files().items()
        }
        if result["files_sha256"] != expected:
            raise RuntimeError("Executed harness files differ from the selected version")
        return {**result, "receipt": {k: v for k, v in receipt.items() if k != "stdout"}}

    def stop(self, gateway, run):
        key = "sandbox/" + run["id"]
        receipt = gateway.get(run["team"], key, {"mode": self.mode, "cleanup": "not_created"})
        sandbox = self.active.pop(run["id"], None)
        if sandbox:
            try:
                sandbox.stop().result()
                receipt["cleanup"] = "stop_acknowledged"
            except Exception as exc:
                receipt.update(cleanup="stop_unconfirmed", cleanup_error=str(exc))
            gateway.put(run["team"], key, receipt)
        return receipt
