# %% instrumentation


def instrument_execution(audit, project, save_receipt):
    run = audit["run"]
    conversation_id = audit["export_id"]
    # This exporter uses the recorded operation timestamps, not export time.
    # It is independent of inference: fixing a span never repeats a model request.
    with tracing.Conversation(
        conversation_id=conversation_id,
        conversation_name=f"{run['team']} / Trace exercise / {run['stage']} / {run['title']}",
        attributes={
            "workshop.campaign": audit["campaign"],
            "workshop.execution": run["id"],
            "workshop.harness": run["fingerprint"],
            "workshop.stage": run["stage"],
            "workshop.tool_backend": run.get("execution_mode", "unknown"),
        },
    ):
        with tracing.Turn(
            agent_name="pending-employee",
            agent_version=run["fingerprint"],
            messages=[tracing.Message(role="user", content=run["mission"])],
            started_at=dt(run["started"]),
            ended_at=dt(run["ended"]),
        ):
            receipt = {
                **span_identity(),
                "verified": False,
                "url": f"https://forge.coreweave.com/agent-lens/{project}/conversations/{conversation_id}",
                "span_ids": {},
            }
            save_receipt(receipt)
            for call in audit["calls"]:
                record_model_span(call)
            for event in audit["events"]:
                # Task: add a tracing.Tool scope with name, arguments, result,
                # tool_call_id, started_at and ended_at from THIS event.
                # Record an error when output.ok is False, then save its span ID.
                record_tool_span(event, receipt)
            save_receipt(receipt)
    return receipt


# %% tool_span


def record_tool_span(event, receipt):
    raise NotImplementedError("Wrap this event in tracing.Tool and save span_identity()['span_id']")


# %% model_span


def record_model_span(call):
    request, response = call["request"], call.get("response") or {}
    choice = (response.get("choices") or [{}])[0]
    span = tracing.LLM(
        model=request["model"],
        provider_name="wandb",
        response_model=response.get("model", ""),
        response_id=response.get("id", ""),
        request_max_tokens=request["max_tokens"],
        request_temperature=request.get("temperature"),
        system_instructions=[m["content"] for m in request["messages"] if m["role"] == "system"],
        input_messages=[
            tracing.Message(role=m["role"], content=m["content"])
            for m in request["messages"]
            if m["role"] != "system"
        ],
        output_messages=[
            tracing.Message(
                role="assistant", content=choice.get("message", {}).get("content") or ""
            )
        ],
        finish_reasons=[choice["finish_reason"]] if choice.get("finish_reason") else [],
        started_at=dt(call["started"]),
        ended_at=dt(call["ended"]),
    )
    usage = response.get("usage") or {}
    if "prompt_tokens" in usage and "completion_tokens" in usage:
        span.usage = tracing.Usage(
            input_tokens=usage["prompt_tokens"], output_tokens=usage["completion_tokens"]
        )
    with span:
        span.set_attributes(
            {"workshop.request_id": call["id"], "workshop.usage_available": bool(usage)}
        )
        if call.get("error"):
            span.record_error(RuntimeError(str(call["error"])))
