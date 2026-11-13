import json
from pathlib import Path

import httpx
import pytest

from office_lab.self_service import configure, workspace_lock
from workshop.budget import Budget, BudgetStop
from workshop.provider import Provider


def test_workspace_is_unique_bound_and_resumable(tmp_path):
    _, a, manifest = configure(tmp_path / "a", "learner/project", 8)
    _, again, resumed = configure(tmp_path / "a", "learner/project", 9)
    _, b, _ = configure(tmp_path / "b", "learner/project", 8)
    assert a == again and a["attendee"] != b["attendee"]
    assert manifest["project"] == "learner/project"
    assert resumed["budget_usd"] == resumed["team_workshop_usd"] == 9
    assert resumed["campaign"] == "pending-self-service-v1"
    with pytest.raises(ValueError, match="belongs to"):
        configure(tmp_path / "a", "someone/else", 8)
    assert json.loads((tmp_path / "a/workspace.json").read_text(encoding="utf-8")) == a


@pytest.mark.parametrize("budget", [0, -1, float("nan"), float("inf")])
def test_bad_allowance_rejected(tmp_path, budget):
    with pytest.raises(ValueError, match="finite"):
        configure(tmp_path, "learner/project", budget)


def test_hosted_ledger_is_not_reused(tmp_path):
    from workshop.store import Store

    store = Store(tmp_path)
    with store.transaction() as db:
        db.execute("INSERT INTO state VALUES('ledger_owner','old-host')")
    with pytest.raises(ValueError, match="hosted ledger"):
        configure(tmp_path, "learner/project", 8)


def test_only_one_service_per_workspace(tmp_path):
    with workspace_lock(tmp_path):
        with pytest.raises(RuntimeError, match="already running"):
            with workspace_lock(tmp_path):
                pass
    with workspace_lock(tmp_path):
        pass


def test_provider_uses_attendee_project_and_reserves_unknown_usage(tmp_path, monkeypatch):
    store, workspace, manifest = configure(tmp_path, "learner/project", 8)
    store.create_team(workspace["attendee"])
    monkeypatch.setenv("WANDB_API_KEY", "unit-test-placeholder")
    headers = []

    def reply(request):
        headers.append(dict(request.headers))
        return httpx.Response(
            200,
            json={
                "model": manifest["model"],
                "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            },
        )

    provider = Provider(store, manifest, transport=httpx.MockTransport(reply))
    record = provider.complete(
        workspace["attendee"], "example", 0, [{"role": "user", "content": "hello"}]
    )
    assert headers[0]["openai-project"] == "learner/project"
    assert record["accounting"]["observed_usd"] is None
    assert record["accounting"]["bounded_unknown_usd"] > 0
    before = provider.budget.summary()
    with pytest.raises(BudgetStop, match="already exists"):
        provider.complete(
            workspace["attendee"], "example", 0, [{"role": "user", "content": "hello"}]
        )
    assert len(headers) == 1 and provider.budget.summary() == before


def test_no_hidden_shared_ceiling(tmp_path):
    store, workspace, manifest = configure(tmp_path, "learner/project", 0.000001)
    store.create_team(workspace["attendee"])
    budget = Budget(store, manifest)
    with pytest.raises(BudgetStop, match="ceiling"):
        budget.reserve(
            "attempt",
            workspace["attendee"],
            "run",
            {
                "model": manifest["model"],
                "messages": [],
                "max_tokens": manifest["max_output_tokens"],
            },
        )
    assert budget.summary()["attempts"] == 0


def test_two_callers_share_one_reservation_limit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    store, workspace, manifest = configure(tmp_path, "learner/project", 0.005)
    store.create_team(workspace["attendee"])
    budget = Budget(store, manifest)

    def reserve(i):
        try:
            budget.reserve(
                str(i),
                workspace["attendee"],
                str(i),
                {
                    "model": manifest["model"],
                    "messages": [],
                    "max_tokens": manifest["max_output_tokens"],
                },
            )
            return True
        except BudgetStop:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(results) == 1
    assert budget.summary()["accounted_usd"] <= 0.005


def test_recorded_success_survives_restart(tmp_path, monkeypatch):
    store, workspace, manifest = configure(tmp_path, "learner/project", 8)
    store.create_team(workspace["attendee"])
    monkeypatch.setenv("WANDB_API_KEY", "unit-test-placeholder")

    def reply(request):
        return httpx.Response(
            200,
            json={
                "model": manifest["model"],
                "usage": {"prompt_tokens": 4, "completion_tokens": 3},
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
            },
        )

    first = Provider(store, manifest, transport=httpx.MockTransport(reply))
    first.complete(workspace["attendee"], "run", 0, [])
    again, _, config = configure(tmp_path, "learner/project", 8)
    assert Budget(again, config).summary() == first.budget.summary()


def test_packaged_notebook_has_no_host_token_form():
    import ast
    import base64
    import zlib

    tree = ast.parse(Path("office_notebook.py").read_text(encoding="utf-8"))
    strings = [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]
    source = Path("office_notebook.py").read_text(encoding="utf-8")
    assert "Check my setup" in source and "Attendee token" not in source
    assert "cwsandbox.com" not in source
    payloads = []
    for s in strings:
        if len(s) > 10000:
            try:
                payloads.append(json.loads(zlib.decompress(base64.b64decode(s))))
            except (ValueError, zlib.error):
                pass
    assert len(payloads) == 1
    assert "office_lab/grading.py" not in payloads[0]["modules"]
    assert "office_lab/cases.py" not in payloads[0]["modules"]
    assert len(payloads[0]["development"]) == 2


def test_service_authentication_real_lookup_and_mcp_without_provider(tmp_path, monkeypatch):
    import copy
    from dataclasses import asdict

    import uvicorn
    from fastapi.testclient import TestClient

    from office_lab.cases import case
    from office_lab.code_first.solutions import ANSWERS
    from office_lab.gateway import Gateway
    from office_lab.harness import Harness
    from office_lab.self_service import service

    captured = []
    monkeypatch.delenv("WANDB_API_KEY", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: captured.append(app))
    monkeypatch.setattr(Gateway, "export_one", lambda *a, **k: None)
    monkeypatch.setattr(Provider, "complete", lambda *a, **k: pytest.fail("Unexpected model call"))
    service(tmp_path, "learner/project", 8, 9876)
    app = TestClient(captured[0])
    assert app.get("/health").json()["tool_backend"] == "local-restricted-python"
    assert app.post("/office", json={"method": "setup"}).status_code == 401
    access = json.loads((tmp_path / "access.json").read_text(encoding="utf-8"))

    def call(method, **args):
        response = app.post(
            "/office",
            headers={"Authorization": "Bearer " + access["token"]},
            json={"method": method, "arguments": args},
        )
        assert response.status_code == 200, response.text
        return response.json()

    assert call("setup")["project"] == "learner/project"
    schema = {
        "type": "object",
        "properties": {"account_id": {"type": "string"}, "invoice_id": {"type": "string"}},
        "required": ["account_id", "invoice_id"],
        "additionalProperties": False,
    }
    call(
        "register_runtime",
        executor_source="def execute():\n    return None",
        tool_source=ANSWERS["invoice"],
        schema=schema,
    )
    first = call("test_tool")
    assert first["passed"] and call("test_tool") == first
    assert call("snapshot")["budget"]["attempts"] == 0
    from office_lab.local_backend import LocalToolsBackend
    from workshop.store import Store

    store = Store(tmp_path)
    workspace = json.loads((tmp_path / "workspace.json").read_text(encoding="utf-8"))

    # Use the actual backend and MCP transport on a development fixture.
    class Saved:
        def get(self, team, key):
            return store.get(team, "pending-self-service-v1/" + key)

        def put(self, team, key, value, **kw):
            return store.put(team, "pending-self-service-v1/" + key, value, **kw)

    task = case("missing-seat")
    run = {
        "id": "mcp-check",
        "team": workspace["attendee"],
        "world": copy.deepcopy(task.initial),
        "harness": asdict(Harness()),
    }
    backend = LocalToolsBackend(tmp_path / "mcp")
    result = backend.call(Saved(), run, 0, "discover_onboarding", {}, task)
    assert result["receipt"]["mcp_transport"] == "stdio"
    assert result["output"]["ok"]
