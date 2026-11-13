"""An attendee-owned notebook and loopback service. Starting it never calls a model."""

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
import webbrowser
from contextlib import contextmanager
from pathlib import Path

import httpx

CAMPAIGN = "pending-self-service-v1"
ROOT = Path(__file__).resolve().parents[1]


def private_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        json.dump(value, f, indent=2)
    os.replace(temporary, path)


@contextmanager
def workspace_lock(root):
    """One service owns a workspace, including when a second port is requested."""
    path = Path(root) / "service.lock"
    with path.open("a+b") as handle:
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(
                "This workspace is already running. Stop its earlier terminal first."
            ) from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def configure(root, project, budget):
    from workshop.store import Store

    from .api import code_config

    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", project):
        raise ValueError("Use --project YOUR_ENTITY/pending-inc, not a URL")
    if not math.isfinite(budget) or budget <= 0:
        raise ValueError("--budget-usd must be a positive finite dollar amount")
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(root)
    with store.connect() as db:
        if db.execute("SELECT 1 FROM state WHERE key='ledger_owner'").fetchone():
            raise ValueError("This is a hosted ledger. Choose a new --state directory.")
    path = root / "workspace.json"
    if path.exists():
        workspace = json.loads(path.read_text())
        if workspace["project"] != project:
            raise ValueError(
                "This workspace belongs to "
                + workspace["project"]
                + ". Use a new --state for a different project."
            )
        if workspace["campaign"] != CAMPAIGN:
            raise ValueError("This workspace uses another campaign. Keep it and use a new --state.")
    else:
        if store.runs():
            raise ValueError("This directory contains older executions. Use a new --state.")
        workspace = {
            "project": project,
            "campaign": CAMPAIGN,
            "attendee": "local-" + uuid.uuid4().hex,
        }
        private_json(path, workspace)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else code_config()
    manifest.update(
        project=project,
        campaign=CAMPAIGN,
        teaching_release=CAMPAIGN,
        budget_usd=budget,
        team_workshop_usd=budget,
        buckets_usd={"workshop": budget, "rehearsal": 0, "recovery": 0},
        global_concurrency=2,
        team_concurrency=2,
        grading_visibility="public teaching fixtures; excluded from improvement context",
    )
    private_json(manifest_path, manifest)
    return store, workspace, manifest


def resolve_key():
    if os.environ.get("WANDB_API_KEY"):
        return os.environ["WANDB_API_KEY"]
    # This reads local login settings; it makes no API request.
    from wandb.sdk.lib.apikey import api_key

    import wandb

    return api_key(wandb.Settings()) or ""


def service(root, project, budget, port):
    import uvicorn

    from workshop.provider import Provider

    from .api import create_app
    from .code_gateway import CodeGateway
    from .local_backend import LocalToolsBackend

    class PersonalProvider(Provider):
        def complete(self, *args, **kwargs):
            if not os.environ.get("WANDB_API_KEY"):
                raise PermissionError(
                    "No W&B login. Run uv run wandb login, then restart start.py. Your progress is saved."
                )
            return super().complete(*args, **kwargs)

    class PersonalGateway(CodeGateway):
        lab = CAMPAIGN
        namespace = CAMPAIGN + "/"

        def setup(self, team):
            return {**super().setup(team), "project": self.manifest["project"]}

    with workspace_lock(root):
        store, workspace, manifest = configure(root, project, budget)
        access_path = Path(root) / "access.json"
        if access_path.exists():
            access = json.loads(access_path.read_text())
            if not store.authenticate(access["token"]):
                raise RuntimeError(
                    "Local connection receipt does not match this workspace. Preserve it and use a new --state."
                )
        else:
            access = {"token": store.create_team(workspace["attendee"])}
        access.update(gateway=f"http://127.0.0.1:{port}", project=project, campaign=CAMPAIGN)
        private_json(access_path, access)
        private_json(
            Path(root) / "service.json",
            {"key_hash": hashlib.sha256(os.environ.get("WANDB_API_KEY", "").encode()).hexdigest()},
        )
        gateway = PersonalGateway(
            store,
            PersonalProvider(store, manifest),
            backend=LocalToolsBackend(Path(root) / "tools"),
        )
        uvicorn.run(
            create_app(gateway=gateway, code_first=True),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )


def connection(url, state, project, budget, key):
    try:
        response = httpx.get(url + "/health", timeout=2, trust_env=False)
    except httpx.ConnectError:
        return False
    if response.status_code != 200 or response.json().get("lab") != CAMPAIGN:
        raise RuntimeError("The gateway port is occupied. Choose another --gateway-port.")
    access_path = state / "access.json"
    if not access_path.exists():
        raise RuntimeError(
            "That gateway belongs to another workspace. Choose another --gateway-port."
        )
    access = json.loads(access_path.read_text())
    response = httpx.post(
        url + "/office",
        json={"method": "setup"},
        headers={"Authorization": "Bearer " + access["token"]},
        timeout=5,
        trust_env=False,
    )
    response.raise_for_status()
    value = response.json()
    expected = hashlib.sha256(key.encode()).hexdigest()
    if value.get("project") != project:
        raise RuntimeError("The running workspace belongs to another project. Use a new --state.")
    if (
        value["model_allowance_usd"] != budget
        or json.loads((state / "service.json").read_text())["key_hash"] != expected
    ):
        raise RuntimeError(
            "Credentials or budget changed. Stop the previous launcher with Ctrl+C, then restart this command."
        )
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True, help="Your writable Forge entity/project")
    parser.add_argument("--state", default=str(ROOT / ".pending-local"))
    parser.add_argument(
        "--budget-usd",
        type=float,
        default=8,
        help="Total allowance for this workspace, including prior calls (default: 8)",
    )
    parser.add_argument("--port", type=int, default=2762)
    parser.add_argument("--gateway-port", type=int, default=8796)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.project):
        parser.error("Use --project YOUR_ENTITY/pending-inc")
    if not math.isfinite(args.budget_usd) or args.budget_usd <= 0:
        parser.error("--budget-usd must be positive and finite")
    state = Path(args.state).resolve()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.serve:
        service(state, args.project, args.budget_usd, args.gateway_port)
        return
    key = resolve_key()
    env = dict(os.environ)
    for name in [
        "OFFICE_GATEWAY",
        "OFFICE_GATEWAY_ACCESS_TOKEN",
        "OFFICE_PAIR_TOKEN",
        "WORKSHOP_ATTENDEE_TOKEN",
        "WORKSHOP_HOST_ID",
        "WORKSHOP_STATE",
    ]:
        env.pop(name, None)
    env.update(
        WANDB_API_KEY=key,
        WANDB_PROJECT_PATH=args.project,
        PENDING_CAMPAIGN=CAMPAIGN,
        PENDING_SELF_SERVICE="1",
        PYTHONUTF8="1",
        # Pinned marimo 0.25.2: initialize free cells; paid cells require run buttons.
        _MARIMO_CONFIG_OVERLOAD_RUNTIME_AUTO_INSTANTIATE="true",
        OFFICE_LOCAL_ACCESS_FILE=str(state / "access.json"),
    )
    if not key:
        print(
            "No W&B login found. Local practice works. For live calls, run uv run wandb login and restart this command.",
            flush=True,
        )
    children = []
    try:
        with socket.socket() as check:
            check.bind(("127.0.0.1", args.port))
        url = f"http://127.0.0.1:{args.gateway_port}"
        if not connection(url, state, args.project, args.budget_usd, key):
            with (state / "gateway.log").open("a") as log:
                (state / "gateway.log").chmod(0o600)
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(ROOT / "start.py"),
                        "--serve",
                        "--state",
                        str(state),
                        "--project",
                        args.project,
                        "--budget-usd",
                        str(args.budget_usd),
                        "--gateway-port",
                        str(args.gateway_port),
                    ],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            children.append(process)
            for _ in range(120):
                if process.poll() is not None:
                    raise RuntimeError(f"Local service stopped. See {state / 'gateway.log'}")
                if connection(url, state, args.project, args.budget_usd, key):
                    break
                time.sleep(0.25)
            else:
                raise RuntimeError(f"Local service did not start. See {state / 'gateway.log'}")
        notebook = state / "my_workshop.py"
        if not notebook.exists():
            shutil.copyfile(ROOT / "office_notebook.py", notebook)
        password = state / "notebook-password"
        if not password.exists():
            with os.fdopen(
                os.open(password, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w"
            ) as f:
                f.write(secrets.token_urlsafe(32))
        browser_url = f"http://127.0.0.1:{args.port}/?access_token={password.read_text().strip()}"
        with (state / "notebook.log").open("a") as log:
            (state / "notebook.log").chmod(0o600)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "marimo",
                    "edit",
                    str(notebook),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.port),
                    "--headless",
                    "--no-sandbox",
                    "--skip-update-check",
                    "--token-password-file",
                    str(password),
                ],
                cwd=state,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        children.append(process)
        for _ in range(120):
            if process.poll() is not None:
                raise RuntimeError(f"Notebook stopped. See {state / 'notebook.log'}")
            try:
                if (
                    httpx.get(
                        browser_url, timeout=2, follow_redirects=True, trust_env=False
                    ).status_code
                    == 200
                ):
                    break
            except httpx.ConnectError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError(f"Notebook did not start. See {state / 'notebook.log'}")
        print(
            f"Ready. Project: {args.project}; workspace allowance: ${args.budget_usd:g}.\nTools: local restricted Python and MCP. No remote Sandbox.\nYour editable notebook: {notebook}\nOpen: {browser_url}\nKeep this terminal open. Ctrl+C stops services started here; your work stays saved.",
            flush=True,
        )
        if not args.headless:
            webbrowser.open(browser_url)
        process.wait()
    except KeyboardInterrupt:
        print("Stopping. Your code and progress are saved.", flush=True)
    except (OSError, RuntimeError, ValueError, httpx.HTTPError) as error:
        parser.exit(1, f"Setup stopped: {error}\nYour saved work has not been deleted.\n")
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    print(f"Process {child.pid} is still finishing. It was not forcibly killed.")


if __name__ == "__main__":
    main()
