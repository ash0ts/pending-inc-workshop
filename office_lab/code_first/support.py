"""Local receipts, source capture and readback. The editable algorithm lives in notebook cells."""

import inspect
import json
import os
import platform
import re
import tempfile
import textwrap
import time
from datetime import UTC, datetime
from pathlib import Path

from office_lab.harness import fingerprint

CAMPAIGN = "pending-v4-code-r2"


def default_project():
    explicit = os.getenv("WANDB_PROJECT_PATH")
    if explicit:
        return explicit
    entity, project = os.getenv("WANDB_ENTITY"), os.getenv("WANDB_PROJECT")
    if entity and project:
        return f"{entity}/{project}"
    return ""


def source(function):
    return textwrap.dedent(inspect.getsource(function)).strip()


def runtime_source(*functions):
    return "\n\n".join(source(f) for f in functions)


class Journal:
    def __init__(self, root, identity):
        self.root = Path(root) / fingerprint(identity)[:16]
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, key):
        return self.root / (fingerprint(key) + ".json")

    def get(self, key, default=None):
        p = self.path(key)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def put(self, key, value):
        encoded = json.dumps(value, ensure_ascii=False, indent=2)
        # Never persist notebook credentials, even in an error or accidental UI output.
        for secret in [
            os.getenv("WANDB_API_KEY"),
            os.getenv("OFFICE_PAIR_TOKEN"),
            os.getenv("OFFICE_GATEWAY_ACCESS_TOKEN"),
        ]:
            if secret and len(secret) > 8 and secret in encoded:
                raise ValueError("Refusing to save a credential in a notebook receipt")
        fd, name = tempfile.mkstemp(dir=self.root, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(encoded)
            os.replace(name, self.path(key))
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return value

    def once(self, key, operation):
        old = self.get(key)
        if old is not None:
            return old
        return self.put(key, operation())


def publish_checkpoint(journal, key, operation):
    """Reuse complete publication; retry unavailable native writes without inference."""
    old = journal.get(key)
    if old and old.get("evaluation_id"):
        return old
    return journal.put(key, operation())


def verify_project(project):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", project):
        raise ValueError("Use entity/project, without a URL")
    import wandb

    api = wandb.Api()
    entity, name = project.split("/")
    row = api._service_api.execute_graphql(
        "query($e:String!,$p:String!){project(entityName:$e,name:$p){internalId}}",
        {"e": entity, "p": name},
    ).get("project")
    if not row:
        raise ValueError("Create this Forge project and grant your account access first")
    return row["internalId"]


def doctor(client, project):
    import importlib.metadata as md

    versions = {name: md.version(name) for name in ["marimo", "coreweave", "wandb", "weave"]}
    if versions["coreweave"] != "0.1.0b0":
        raise ValueError("Use the locked Forge SDK: coreweave==0.1.0b0")
    setup = client.setup()
    if setup["campaign"] != CAMPAIGN:
        raise ValueError("This is the old workshop gateway; ask for the code-first endpoint")
    return {
        "python": platform.python_version(),
        "platform": platform.system(),
        "versions": versions,
        "scope_id": verify_project(project),
        "gateway": setup,
        "publication": "Read access verified. Trace/evaluation write checks still required.",
    }


def dt(value):
    return datetime.fromtimestamp(value, UTC)


def stored_timestamp(value):
    parsed = datetime.fromisoformat(value)
    # Agent Lens readback may omit the zone on UTC timestamps. Never interpret
    # those strings in the participant laptop's local timezone.
    return (parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)).timestamp()


def check_evaluation_access(project, scope_id, journal):
    """Write one unscored setup row, outside the participant comparison."""
    from coreweave_evaluations import Client

    import wandb

    identity = fingerprint([project, journal.root.name, "setup-evaluation"])[:40]

    def write():
        with Client(
            base_url="https://evaluations.wandb.ai", api_key=wandb.Api().api_key, scope_id=scope_id
        ) as client:
            table = client.eval_tables.create(
                name="pending_setup", idempotency_key=identity + "-table"
            )
            client.eval_tables.columns.create(
                table.evaluation_id,
                dataset_fields=[{"source": "input", "name": "check", "value_type": "string"}],
                scorers=[],
                idempotency_key=identity + "-columns",
            )
            client.eval_tables.rows.add(
                table.evaluation_id,
                rows=[
                    {
                        "input": {"check": "Participant evaluation write permission"},
                        "output": {},
                        "scores": {},
                    }
                ],
                idempotency_key=identity + "-row",
            )
        return {
            "evaluation_id": table.evaluation_id,
            "write_access": True,
            "unscored_setup_only": True,
        }

    return journal.once(["setup-evaluation", identity], write)


def span_identity():
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    return {
        "trace_id": format(context.trace_id, "032x"),
        "span_id": format(context.span_id, "016x"),
    }


def stored_spans(project, trace_id, expected, *, timeout=30):
    import httpx

    import wandb

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = httpx.post(
            "https://trace.wandb.ai/agents/spans/query",
            auth=("api", wandb.Api().api_key),
            timeout=20,
            json={
                "project_id": project,
                "query": {"$expr": {"$eq": [{"$getField": "trace_id"}, {"$literal": trace_id}]}},
                "include_details": True,
                "limit": 1000,
            },
        )
        result.raise_for_status()
        rows = result.json().get("spans", [])
        if len(rows) >= expected:
            return rows
        time.sleep(1)
    raise RuntimeError("Trace not fully stored yet. Retry readback; do not rerun the employee.")


def validate_spans(rows, audit):
    """Independent equality checks. Missing usage is not zero usage."""
    tools = [r for r in rows if r.get("operation_name") == "execute_tool"]
    models = [r for r in rows if r.get("operation_name") == "chat"]
    if len(tools) != len(audit["events"]) or len(models) != len(audit["calls"]):
        raise ValueError("Stored tool/model coverage does not match the gateway audit")
    for event in audit["events"]:
        matches = [r for r in tools if r.get("tool_call_id") == event["id"]]
        if len(matches) != 1:
            raise ValueError("Missing or duplicate tool event: " + event["id"])
        row = matches[0]
        for key, expected in [
            ("tool_call_arguments", event["arguments"]),
            ("tool_call_result", event["output"]),
        ]:
            actual = row.get(key)
            actual = json.loads(actual or "{}") if isinstance(actual, str) else actual
            if actual != expected:
                raise ValueError("Stored " + key + " differs from the execution")
        for key, saved in [("started_at", "started"), ("ended_at", "ended")]:
            if abs(stored_timestamp(row[key]) - event[saved]) > 0.001:
                raise ValueError("Tool timestamp differs")
    for call in audit["calls"]:
        response = call.get("response") or {}
        matches = (
            [r for r in models if r.get("response_id") == response.get("id")]
            if response.get("id")
            else [
                r
                for r in models
                if abs(stored_timestamp(r["started_at"]) - call["started"]) < 0.001
            ]
        )
        if len(matches) != 1:
            raise ValueError("Missing or duplicate provider request")
        row = matches[0]
        if row.get("request_model") != call["request"]["model"]:
            raise ValueError("Requested model differs")
        if response.get("model") and row.get("response_model") != response["model"]:
            raise ValueError("Returned model differs")
        for source_key, dest in [
            ("prompt_tokens", "input_tokens"),
            ("completion_tokens", "output_tokens"),
        ]:
            value = response.get("usage", {}).get(source_key)
            if value is not None and row.get(dest) != value:
                raise ValueError("Usage differs")
    return {"verified": True, "model_spans": len(models), "tool_spans": len(tools)}


def capture_trace(audit, project, session, instrument, journal, *, version=None):
    """Export retries never invoke the actor. Preserve partial exports for investigation."""
    version = version or fingerprint(source(instrument))
    key = ["trace", project, audit["run"]["id"], audit["audit_fingerprint"], version]
    old = journal.get(key)
    if not old:
        # The learner instrumentation saves span identities before the scopes close.
        receipt = instrument(
            {**audit, "export_id": fingerprint(key)[:32]},
            project,
            lambda r: journal.put(key, {**r, "instrumentation": version}),
        )
        old = journal.put(key, {**receipt, "instrumentation": version})
    if old.get("verified"):
        return old
    session.force_flush()
    rows = stored_spans(project, old["trace_id"], len(audit["calls"]) + len(audit["events"]) + 1)
    verified = validate_spans(rows, audit)
    links = {
        eid: old["url"] + "?trace=" + old["trace_id"] + "&span=" + sid
        for eid, sid in old.get("span_ids", {}).items()
    }
    return journal.put(key, {**old, **verified, "span_links": links})


def export_completed_traces(context, code, runs):
    """Export saved executions; a telemetry failure must not hide completed work."""
    outcomes = []
    for run in runs:
        item = {"execution": run["id"], "stage": run["stage"], "title": run["title"]}
        if not run.get("ended"):
            outcomes.append({**item, "status": "running"})
            continue
        try:
            if "session" not in context:
                raise ValueError("Click Send my first Forge trace to initialize your project.")
            if "instrument" not in code:
                raise ValueError("Run the free instrumentation cells first.")
            audit = context["client"].audit(run["id"])
            trace = capture_trace(
                audit,
                context["project"],
                context["session"],
                code["instrument"],
                context["journal"],
                version=code["instrumentation_version"],
            )
            context["journal"].put("trace/" + run["id"], trace)
            outcomes.append({**item, **trace, "status": "verified"})
        except Exception as error:
            outcomes.append({**item, "status": "export_pending", "error": str(error)})
    return outcomes


def tool_checks(lookup, development):
    import copy

    world = development[0]["initial"]
    rows = []
    for aid, iid in [("acme", "inv-1"), ("acme", "other-invoice"), ("other-account", "inv-1")]:
        before = copy.deepcopy(world)
        try:
            value = lookup(before, aid, iid)
            expected = [p for p in world["accounts"][aid]["payments"] if p["invoice_id"] == iid]
            passed = (
                value.get("account_id") == aid
                and value.get("payments") == expected
                and before == world
            )
            rows.append({"account": aid, "invoice": iid, "passed": passed, "returned": value})
        except Exception as exc:
            rows.append({"account": aid, "invoice": iid, "passed": False, "error": str(exc)})
    return rows


def loop_checks(dispatch, append_observation):
    class Fake:
        def tool(self, run_id, step, name, arguments):
            return {"ok": True, "echo": arguments, "name": name}

    action = {
        "action": "lookup_invoice",
        "arguments": {"account_id": "acme", "invoice_id": "inv-1"},
    }
    result = dispatch(Fake(), "practice", 0, action)
    messages = append_observation([], action, result)
    if (
        len(messages) != 2
        or messages[-1]["role"] != "user"
        or result["echo"] != action["arguments"]
    ):
        raise ValueError(
            "Dispatch the selected arguments, then append the assistant action and user observation"
        )
    if json.loads(messages[-1]["content"].removeprefix("Tool result: ")) != result:
        raise ValueError("The next request must contain the actual tool result")
    return messages


def preserve_native_workspace(url, entity, project, native_config):
    """Preserve native EvalTables fields that the workspace SDK does not model."""
    import wandb_workspaces.workspaces as ws

    import wandb

    sdk_url = url.replace("https://forge.coreweave.com/wandb/", "https://wandb.ai/")
    wire = ws.Workspace.from_url(sdk_url)._spec
    section = wire["spec"]["section"]
    section["runSets"][0].update(
        baselineRunId=native_config["referenceRunId"],
        pinnedRunIds=[native_config["referenceRunId"]],
    )
    bank = section["panelBankConfig"]["sections"][0]
    bank["panels"] = [
        {
            "__id__": "dispatch-final",
            "viewType": "Evaluation Tables",
            "isAuto": False,
            "config": native_config,
            "layout": {"x": 0, "y": 0, "w": 24, "h": 14},
        }
    ]
    bank["flowConfig"].update(columnsPerPage=1, rowsPerPage=1, boxHeight=1050)
    query = """mutation($id:ID,$entityName:String,$projectName:String,$type:String,
        $name:String,$displayName:String,$spec:String){upsertView(input:{id:$id,
        entityName:$entityName,projectName:$projectName,type:$type,name:$name,
        displayName:$displayName,spec:$spec}){view{id name}}}"""
    wandb.Api()._service_api.execute_graphql(
        query,
        {
            "id": wire["id"],
            "entityName": entity,
            "projectName": project,
            "type": "project-view",
            "name": wire["name"],
            "displayName": wire["displayName"],
            "spec": json.dumps(wire["spec"]),
        },
    )
    stored = ws.Workspace.from_url(sdk_url)._spec["spec"]["section"]
    assert stored["panelBankConfig"]["sections"][0]["panels"][0]["config"] == native_config
