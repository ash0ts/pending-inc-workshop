"""Participant client with individual authentication and optional Forge endpoint access."""

import httpx


class GatewayError(RuntimeError):
    """The gateway answered with an error. status is the HTTP code; detail is its message."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


class Client:
    def __init__(self, url, token, *, access_token=None):
        self.url, self.token = url.rstrip("/"), token
        if access_token and not self.url.startswith("https://"):
            raise ValueError("Forge endpoint access requires HTTPS")
        self.access_token = access_token

    def call(self, method, **arguments):
        headers = {"Authorization": "Bearer " + self.token}
        if self.access_token:
            headers["X-Sandbox-Share-Token"] = self.access_token
        result = httpx.post(
            self.url + "/office",
            json={"method": method, "arguments": arguments},
            headers=headers,
            timeout=240,
        )
        if result.is_error:
            try:
                detail = result.json().get("detail", result.text)
            except ValueError:
                detail = result.text
            if result.status_code == 503 and "Permission denied: access denied" in str(detail):
                detail = (
                    str(detail)
                    + ". The gateway could not access CoreWeave Sandboxes. Your saved work is retained. "
                    "The host must restore access or provide the local-tool gateway. "
                    "Do not rerun completed stages or change your Forge project to fix this."
                )
            raise GatewayError(result.status_code, str(detail))
        return result.json()

    def open(self, case_id, stage, harness, runtime):
        return self.call("open", case_id=case_id, stage=stage, harness=harness, runtime=runtime)

    def model(self, run_id, step, messages):
        return self.call("model", run_id=run_id, step=step, messages=messages)

    def tool(self, run_id, step, name, arguments):
        return self.call("tool", run_id=run_id, step=step, name=name, arguments=arguments)

    def close(self, run_id, status, reason):
        return self.call("close", run_id=run_id, status=status, reason=reason)

    def result(self, run_id):
        return self.call("result", run_id=run_id)

    def edit(self, number, phase, hypothesis=""):
        return self.call("edit", number=number, phase=phase, hypothesis=hypothesis)

    def approve(self, stage, expected_fingerprint):
        return self.call("approve", stage=stage, expected_fingerprint=expected_fingerprint)

    def observe(self, run_id, event_id, claim):
        return self.call("observe", run_id=run_id, event_id=event_id, claim=claim)

    def snapshot(self):
        return self.call("snapshot")

    def freeze(self, stage):
        return self.call("freeze", stage=stage)

    def grade(self):
        return self.call("grade")

    def publish(self):
        return self.call("publish")

    def review(self, execution_id):
        return self.call("review", execution_id=execution_id)

    def evaluate(self):
        return self.call("evaluate")

    def publish_evaluation(self):
        return self.call("publish_evaluation")

    def assess_review(self, judgment_id, criterion, agree, reason):
        return self.call(
            "assess_review",
            judgment_id=judgment_id,
            criterion=criterion,
            agree=agree,
            reason=reason,
        )

    def retry_exports(self):
        return self.call("retry_exports")

    def configure_tool(self, source):
        return self.call("configure_tool", source=source)

    def practice_review(self, execution_id, instructions):
        return self.call("practice_review", execution_id=execution_id, instructions=instructions)

    def mcp_practice(self):
        return self.call("mcp_practice")

    def tutorial(self, instructions):
        return self.call("tutorial", instructions=instructions)

    def manual_candidate(self, harness, rationale, event_id):
        return self.call(
            "manual_candidate", harness=harness, rationale=rationale, event_id=event_id
        )

    def checkpoint(self, quest, answer):
        return self.call("checkpoint", quest=quest, answer=answer)

    def conclude(self, conclusion, next_test):
        return self.call("conclude", conclusion=conclusion, next_test=next_test)

    def setup(self):
        return self.call("setup")

    def register_runtime(self, executor_source, tool_source, schema):
        return self.call(
            "register_runtime",
            executor_source=executor_source,
            tool_source=tool_source,
            schema=schema,
        )

    def test_tool(self):
        return self.call("test_tool")

    def audit(self, execution_id):
        return self.call("audit", execution_id=execution_id)

    def select_evidence(self, execution_id, event_ids, hypothesis, expected_behavior):
        return self.call(
            "select_evidence",
            execution_id=execution_id,
            event_ids=event_ids,
            hypothesis=hypothesis,
            expected_behavior=expected_behavior,
        )

    def judge_development(self, execution_id, rubric):
        return self.call("judge_development", execution_id=execution_id, rubric=rubric)


class DirectClient:
    """Operator-only adapter for tests and rehearsal; not in participant bundles."""

    def __init__(self, gateway, team):
        self.gateway, self.team = gateway, team

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda *a, **kw: getattr(self.gateway, name)(self.team, *a, **kw)
