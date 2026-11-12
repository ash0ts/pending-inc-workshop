# %% instructions
EMPLOYEE = Harness(
    instructions="Resolve the customer's request. For billing work, call lookup_invoice(account_id, invoice_id) to inspect the invoice before acting. Read the relevant account before changing it. Explain what you did and anything you could not verify.",
    verification_skill="Before saying resolved, inspect the affected account and check the requested result.",
    tool_skill="Use lookup_invoice(account_id, invoice_id) to inspect the invoice's payments.",
)

# %% invoice
INVOICE_SCHEMA = {
    "type": "object",
    "properties": {"account_id": {"type": "string"}, "invoice_id": {"type": "string"}},
    "required": ["account_id", "invoice_id"],
    "additionalProperties": False,
}


def lookup_invoice(state, account_id, invoice_id):
    # Task: select this account, then only payments for this invoice.
    # Return {"ok": True, "account_id": account_id, "payments": [...]}.
    raise NotImplementedError("Filter state['accounts'][account_id]['payments'] by invoice_id")


# %% dispatch


def dispatch_tool(client, execution_id, step, action):
    # Task: pass the model's action name AND arguments to client.tool.
    raise NotImplementedError("Return client.tool(execution_id, step, ..., ...)")


def append_observation(messages, action, result):
    # Task: preserve earlier messages. Append the assistant action, then a user
    # message containing 'Tool result: ' + json.dumps(result).
    raise NotImplementedError("The next model request needs the observation")


# %% executor


def initial_messages(run, harness, schema):
    return build_messages(run, harness) + [
        {
            "role": "system",
            "content": "Registered additional tool: lookup_invoice. Input schema: "
            + json.dumps(schema),
        }
    ]


def agent_step(client, run, messages, step, harness):
    action = client.model(run["id"], step, messages)
    result = dispatch_tool(client, run["id"], step, action)
    messages = append_observation(messages, action, result)
    # The employee can recover from a timeout by reading state on the next step.
    # A harness rule may also retry a READ once. Never automatically retry a write.
    if (
        harness.read_retries
        and action["action"]
        in {"lookup_invoice", "get_account", "list_payments", "onboarding_status"}
        and result.get("error") == "timeout"
        and step < 15
    ):
        step += 1
        result = dispatch_tool(client, run["id"], step, action)
        messages = append_observation(messages, action, result)
    return messages, step + 1, result


def run_employee(client, case_id, stage, harness, runtime, schema, *, one_step=False):
    run = client.open(case_id, stage, asdict(harness), runtime)
    if run["status"] != "running":
        return run
    messages = initial_messages(run, harness, schema)
    # Rebuild the conversation from durable tool events on refresh. Already-issued
    # requests are cached by the gateway. A changed request cannot reuse that slot.
    step = 0
    for event in run["events"]:
        action = {"action": event["name"], "arguments": event["arguments"]}
        messages = append_observation(messages, action, event["output"])
        step = max(step, int(event["id"].rsplit("-", 1)[1]) + 1)
    while step < 16:
        messages, step, result = agent_step(client, run, messages, step, harness)
        if result.get("accepted") or one_step:
            return {**client.result(run["id"]), "next_request_messages": messages}
    return client.close(run["id"], "agent_failure", "Step budget exhausted")
