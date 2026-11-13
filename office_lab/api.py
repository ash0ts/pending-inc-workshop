"""Separate office endpoint; legacy workshops retain their original API and records."""

import json
import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from workshop.model import config
from workshop.provider import Provider
from workshop.store import Store

from .gateway import Gateway


def lab_config():
    manifest = config()
    manifest.update(
        campaign="nj-hub-pending-v1",
        teaching_release="pending-v1",
        service_name="pending-inc",
        agent_name="office-employee",
        improver_name="process-improvement",
        max_steps=16,
        max_output_tokens=4096,
        request_timeout_seconds=180,
        development_cases=2,
        heldout_cases=4,
        final_executions=12,
        fixture_version="office-v1",
        judge_reasoning_effort="low",
        native_scores="independent final-state checks and separate LLM explanation review",
    )
    return manifest


class Request(BaseModel):
    method: str
    arguments: dict = Field(default_factory=dict)


def code_config():
    from .code_gateway import CAMPAIGN

    manifest = lab_config()
    manifest.update(
        campaign=CAMPAIGN,
        teaching_release=CAMPAIGN,
        prices_per_million={"input": 0.12, "cached": 0.02, "output": 0.38},
        prices_checked="2026-11-12",
        improver_reasoning_effort="low",
    )
    return manifest


def create_app(gateway=None, *, code_first=False):
    if gateway is None:
        store = Store(os.environ.get("WORKSHOP_STATE", "state"))
        manifest = lab_config()
        if code_first:
            from .code_gateway import CodeGateway

            manifest = code_config()
            backend = None
            mode = os.environ.get("WORKSHOP_TOOL_BACKEND", "sandbox")
            if mode == "local":
                from .local_backend import LocalToolsBackend

                backend = LocalToolsBackend(store.root / "local-tools")
            elif mode != "sandbox":
                raise ValueError("WORKSHOP_TOOL_BACKEND must be sandbox or local")
            gateway = CodeGateway(store, Provider(store, manifest), backend=backend)
        else:
            gateway = Gateway(store, Provider(store, manifest))
    app = FastAPI(title="Pending, Inc. workshop gateway")
    allowed = {
        "open",
        "model",
        "tool",
        "close",
        "result",
        "edit",
        "approve",
        "observe",
        "snapshot",
        "freeze",
        "grade",
        "retry_exports",
        "review",
        "evaluate",
        "publish_evaluation",
        "publish",
        "assess_review",
        "manual_candidate",
        "checkpoint",
        "conclude",
        "configure_tool",
        "tutorial",
        "mcp_practice",
        "practice_review",
        "setup",
        "register_runtime",
        "test_tool",
        "audit",
        "select_evidence",
        "judge_development",
    }

    @app.get("/health")
    def health():
        return {"lab": gateway.lab, "status": "ready", "tool_backend": gateway.backend.mode}

    @app.post("/office")
    def call(body: Request, authorization: str = Header(default="")):
        team = gateway.store.authenticate(authorization.removeprefix("Bearer "))
        if not team:
            raise HTTPException(401, "Use your workshop attendee token")
        if (
            body.method not in allowed
            or not hasattr(gateway, body.method)
            or "team" in body.arguments
            or len(json.dumps(body.arguments)) > 80000
        ):
            raise HTTPException(400, "Unknown action or oversized request")
        if body.method == "close" and body.arguments.get("status") != "agent_failure":
            raise HTTPException(400, "Only the recorded finish action accepts a submission")
        try:
            return getattr(gateway, body.method)(team["id"], **body.arguments)
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, str(exc)) from exc

    return app
