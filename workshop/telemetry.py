"""Forge SDK export from durable records. Export retries never call the model."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import UTC, datetime

import httpx

LOCK = threading.Lock()
SESSION = None


def dt(value):
    return datetime.fromtimestamp(value, UTC)


def export_tool_event(tracing, event):
    """Turn one durable tool event into an inspectable Agent Lens span."""
    from opentelemetry import trace

    with tracing.Tool(
        name=event["name"],
        arguments=event.get("arguments", {}),
        result=event["output"],
        tool_call_id=event["id"],
        started_at=dt(event["started"]),
        ended_at=dt(event["ended"]),
    ) as tool:
        span_id = format(trace.get_current_span().get_span_context().span_id, "016x")
        tool.set_attributes({"workshop.origin": event.get("origin", event["kind"])})
        if not event["output"].get("ok", True):
            tool.record_error(
                RuntimeError(str(event["output"].get("error", "guard blocked action")))
            )
    return span_id


def calls_for(store, run_id):
    with store.connect() as db:
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT value FROM requests WHERE run_id=? ORDER BY created,id", (run_id,)
            )
        ]


def readback(project, trace_id, expected, timeout=25):
    end = time.monotonic() + timeout
    rows = []
    while time.monotonic() < end:
        r = httpx.post(
            "https://trace.wandb.ai/agents/spans/query",
            auth=("api", os.environ["WANDB_API_KEY"]),
            timeout=20,
            json={
                "project_id": project,
                "query": {"$expr": {"$eq": [{"$getField": "trace_id"}, {"$literal": trace_id}]}},
                "include_details": True,
                "include_costs": True,
                "limit": 200,
            },
        )
        r.raise_for_status()
        rows = r.json().get("spans", [])
        if len(rows) == expected:
            return rows
        if len(rows) > expected:
            raise RuntimeError("Stored trace has unexpected duplicate spans")
        time.sleep(1)
    raise RuntimeError(f"Stored trace incomplete: {len(rows)}/{expected} spans")


def verify(rows, calls, events):
    llms = [r for r in rows if r.get("operation_name") == "chat"]
    tools = [r for r in rows if r.get("operation_name") == "execute_tool"]
    if len(llms) != len(calls) or len(tools) != len(events):
        raise RuntimeError("Stored span coverage differs from the independent event record")
    for call in calls:
        response = call.get("response") or {}
        matched = (
            [r for r in llms if r.get("response_id") == response.get("id")]
            if response.get("id")
            else []
        )
        if not matched:
            matched = [
                r
                for r in llms
                if abs(
                    datetime.fromisoformat(r["started_at"]).replace(tzinfo=UTC).timestamp()
                    - call["started"]
                )
                < 0.001
            ]
        if len(matched) != 1:
            raise RuntimeError("Provider attempt missing or ambiguous in stored spans")
        row = matched[0]
        if row.get("request_model") != call["request"]["model"]:
            raise RuntimeError("Stored requested model differs")
        usage = response.get("usage")
        if isinstance(usage, dict):
            for source, dest in [
                ("prompt_tokens", "input_tokens"),
                ("completion_tokens", "output_tokens"),
            ]:
                if source in usage and row.get(dest) != usage[source]:
                    raise RuntimeError("Stored usage differs")
        if row.get("request_max_tokens") != call["request"]["max_tokens"]:
            raise RuntimeError("Stored model request limit differs")
    for event in events:
        matched = [r for r in tools if r.get("tool_call_id") == event["id"]]
        if len(matched) != 1:
            raise RuntimeError("Tool event missing or ambiguous in stored spans")
        row = matched[0]
        for key, expected in [
            ("tool_call_arguments", event.get("arguments", {})),
            ("tool_call_result", event["output"]),
        ]:
            actual = row.get(key)
            actual = {} if actual == "" and expected == {} else json.loads(actual or "null")
            if actual != expected:
                raise RuntimeError(f"Stored {key} differs")
        for key, source in [("started_at", "started"), ("ended_at", "ended")]:
            stamp = datetime.fromisoformat(row[key]).replace(tzinfo=UTC).timestamp()
            if abs(stamp - event[source]) > 0.001:
                raise RuntimeError("Stored tool timestamp differs")


def export_run(store, manifest, run, *, reexport=False, allow_tool_only=False):
    global SESSION
    from coreweave.forge.agentlens import tracing as t
    from opentelemetry import trace

    calls = calls_for(store, run["id"])
    events = [e for e in run.get("events", []) if e["kind"] in {"tool", "guard"}]
    if not calls and not (allow_tool_only and events):
        return {"verified": False, "reason": "No provider request occurred"}
    key, project = "trace/" + run["id"], manifest["project"]
    receipt = store.get(run["team"], key)
    with LOCK:
        if reexport and receipt:
            store.put(run["team"], key + f"/attempt-{time.time_ns()}", receipt)
            receipt = None
        if receipt and receipt.get("verified"):
            return receipt
        if not receipt:
            if SESSION is None:
                SESSION = t.init(
                    project,
                    service_name=manifest.get("service_name", "nj-hub-dispatch"),
                    autopatch_integrations=False,
                )
            with t.Conversation(
                conversation_id=run["id"],
                conversation_name=f"{run['team']} / {run['stage'].replace('_', ' ').title()} / {run['title']}",
                attributes={
                    "workshop.campaign": manifest["campaign"],
                    "workshop.team": run["team"],
                    "workshop.stage": run["stage"],
                    "workshop.case": run["case_id"],
                    "workshop.harness": run["fingerprint"],
                    "workshop.attempt": run.get("attempt", 1),
                    **run.get("trace_attributes", {}),
                },
            ):
                with t.Turn(
                    agent_name=manifest.get("improver_name", "dispatch-improver")
                    if run["case_id"] == "decision"
                    else manifest.get("agent_name", "dispatch-agent"),
                    agent_version=run["fingerprint"],
                    messages=[t.Message(role="user", content=run["mission"])],
                    output_messages=[
                        t.Message(role="assistant", content=json.dumps(run.get("outcome")))
                    ],
                    started_at=dt(run["started"]),
                    ended_at=dt(run["ended"]),
                ):
                    tid = format(trace.get_current_span().get_span_context().trace_id, "032x")
                    receipt = {
                        "verified": False,
                        "conversation_id": run["id"],
                        "trace_id": tid,
                        "expected": len(calls) + len(events) + 1,
                        "span_ids": {},
                        "url": f"https://forge.coreweave.com/agent-lens/{project}/conversations/{run['id']}",
                    }
                    store.put(run["team"], key, receipt)
                    for c in calls:
                        response, req = c.get("response") or {}, c["request"]
                        choice = (response.get("choices") or [{}])[0]
                        llm = t.LLM(
                            model=req["model"],
                            provider_name="wandb",
                            response_id=response.get("id", ""),
                            response_model=response.get("model", ""),
                            request_max_tokens=req["max_tokens"],
                            request_temperature=req["temperature"],
                            system_instructions=[
                                m["content"] for m in req["messages"] if m["role"] == "system"
                            ],
                            input_messages=[
                                t.Message(role=m["role"], content=m["content"])
                                for m in req["messages"]
                                if m["role"] != "system"
                            ],
                            output_messages=[
                                t.Message(
                                    role="assistant", content=choice["message"].get("content") or ""
                                )
                            ]
                            if choice.get("message")
                            else [],
                            finish_reasons=[choice["finish_reason"]]
                            if choice.get("finish_reason")
                            else [],
                            started_at=dt(c["started"]),
                            ended_at=dt(c["ended"]),
                        )
                        usage = response.get("usage") or {}
                        if all(
                            type(usage.get(k)) is int
                            for k in ["prompt_tokens", "completion_tokens"]
                        ):
                            llm.usage = t.Usage(
                                input_tokens=usage["prompt_tokens"],
                                output_tokens=usage["completion_tokens"],
                                cache_read_input_tokens=(
                                    usage.get("prompt_tokens_details") or {}
                                ).get("cached_tokens", 0),
                                reasoning_tokens=(usage.get("completion_tokens_details") or {}).get(
                                    "reasoning_tokens", 0
                                ),
                            )
                        with llm:
                            llm.set_attributes(
                                {
                                    "workshop.request_id": c["id"],
                                    "workshop.usage_available": bool(usage),
                                    "workshop.provider_headers": json.dumps(c.get("headers", {})),
                                }
                            )
                            if c.get("error"):
                                llm.record_error(RuntimeError(json.dumps(c["error"])))
                    for e in events:
                        receipt["span_ids"][e["id"]] = export_tool_event(t, e)
                    store.put(run["team"], key, receipt)
            if not SESSION.force_flush():
                raise RuntimeError("Forge SDK export did not flush")
    rows = readback(project, receipt["trace_id"], receipt["expected"])
    verify(rows, calls, events)
    receipt.update(verified=True, stored_spans=len(rows), verified_at=time.time())
    store.put(run["team"], "stored_spans/" + run["id"], rows)
    store.put(run["team"], key, receipt)
    return receipt


def diagnostic(store, run):
    receipt = store.get(run["team"], "trace/" + run["id"], {})
    if not receipt.get("verified"):
        raise RuntimeError("Stored spans have not been verified; summary generation is paused")
    rows = store.get(run["team"], "stored_spans/" + run["id"])
    tools = [r for r in rows if r.get("operation_name") == "execute_tool"]
    observations, seen = [], {}
    for row in tools:
        key = (row.get("tool_name"), row.get("tool_call_arguments"))
        seen[key] = seen.get(key, 0) + 1
        result = row.get("tool_call_result") or "{}"
        try:
            value = json.loads(result)
        except ValueError:
            value = {}
        if row.get("is_error") or value.get("ok") is False or value.get("valid") is False:
            observations.append(
                {
                    "tool": row.get("tool_name"),
                    "span_id": row.get("span_id"),
                    "observation": result[:900],
                }
            )
    summary = {
        "execution": run["id"],
        "conversation": receipt["url"],
        "observations": observations[:8],
        "repeated_calls": [dict(tool=k[0], count=n) for k, n in seen.items() if n > 1],
        "seconds": run.get("seconds"),
        "submitted_decision": (run.get("outcome") or {}).get("decision"),
        "source": "verified stored Agent Lens spans; no independent grades included",
    }
    encoded = json.dumps(summary, ensure_ascii=False)
    if len(encoded.encode()) > 8192:
        raise RuntimeError("Diagnostic exceeded its 8 KiB bound")
    return summary
