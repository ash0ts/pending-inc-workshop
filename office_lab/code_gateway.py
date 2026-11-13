"""Code-first campaign, isolated from older rehearsals, using the same budget ledger."""

import ast
import copy
import hashlib
import json
import time
from dataclasses import asdict

from workshop.provider import json_output
from workshop.telemetry import calls_for

from .cases import case
from .gateway import Gateway
from .harness import Harness, fingerprint

CAMPAIGN = "pending-v4-code-r2"


class CodeGateway(Gateway):
    lab = CAMPAIGN
    namespace = CAMPAIGN + "/"
    feedback_bytes = 15000

    def setup(self, team):
        return {
            "campaign": self.lab,
            "tool_backend": self.backend.mode,
            "team": team,
            "attendee": team,
            "model_allowance_usd": self.provider.budget.limit_usd(team),
            "model": self.manifest["model"],
            "budget": self.provider.budget.summary(team),
            "registration": self.get(team, "registration"),
            "frozen_runtime": self.get(team, "runtime") is not None,
        }

    def register_runtime(self, team, executor_source, tool_source, schema):
        """Store source, never execute participant Python on the gateway host."""
        with self.lock(team):
            if not isinstance(executor_source, str) or not 20 <= len(executor_source) <= 24000:
                raise ValueError("Include your executor, dispatcher and observation builder")
            ast.parse(executor_source)
            if not isinstance(tool_source, str) or len(tool_source) > 6000:
                raise ValueError("Include the lookup_invoice function")
            tree = ast.parse(tool_source)
            if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
                raise ValueError("Register one function, without top-level statements")
            fn = tree.body[0]
            if fn.name != "lookup_invoice" or [a.arg for a in fn.args.args] != [
                "state",
                "account_id",
                "invoice_id",
            ]:
                raise ValueError("Use lookup_invoice(state, account_id, invoice_id)")
            if fn.decorator_list or fn.args.defaults or fn.args.kw_defaults:
                raise ValueError("Tool source must not execute decorators or default expressions")
            expected = {
                "type": "object",
                "properties": {"account_id": {"type": "string"}, "invoice_id": {"type": "string"}},
                "required": ["account_id", "invoice_id"],
                "additionalProperties": False,
            }
            if schema != expected:
                raise ValueError(
                    "Schema must require string account_id and invoice_id, with no extra arguments"
                )
            record = {
                "campaign": self.lab,
                "executor_source": executor_source,
                "tool_source": tool_source,
                "schema": schema,
                "executor_fingerprint": fingerprint(executor_source),
                "tool_fingerprint": hashlib.sha256(tool_source.encode()).hexdigest(),
                "fingerprint": fingerprint([executor_source, tool_source, schema]),
            }
            old = self.get(team, "registration")
            if self.get(team, "runtime") is not None and old != record:
                raise ValueError("Runtime is frozen for this comparison; edit only the harness")
            self.put(team, "registration", record)
            return record

    def open(self, team, case_id, stage, harness, runtime):
        with self.lock(team):
            registration = self.get(team, "registration")
            if not registration or runtime != registration["executor_source"]:
                raise ValueError("Register these exact executor functions before running")
            test = self.get(team, "tool_test/" + registration["fingerprint"])
            if not test or not test["passed"]:
                raise ValueError("Pass the registered invoice-tool checks before freezing the baseline")
            run = super().open(team, case_id, stage, harness, runtime)
            run["registration_fingerprint"] = registration["fingerprint"]
            run["runtime_hash"] = registration["fingerprint"]
            run["tool_fingerprint"] = registration["tool_fingerprint"]
            self.store.save_run(run)
            return run

    def test_tool(self, team):
        """Three public checks, one Sandbox. Cached by exact registered source."""
        with self.lock(team):
            reg = self.get(team, "registration")
            if not reg:
                raise ValueError("Register the tool first")
            key = "tool_test/" + reg["fingerprint"]
            if self.get(team, key):
                return self.get(team, key)
            attempts = [r for r in self.runs(team) if r["stage"] == "tool_test"]
            if len(attempts) >= 3:
                raise ValueError("Three remote tool tests used; continue testing edits locally")
            task = case("double-charge")
            rid = fingerprint([team, self.lab, key])[:32]
            claimed, run = self.store.claim_run(
                {
                    "id": rid,
                    "team": team,
                    "lab": self.lab,
                    "stage": "tool_test",
                    "case_id": task.id,
                    "title": "Invoice tool qualification",
                    "mission": "Test the installed lookup_invoice against public account and invoice inputs.",
                    "fingerprint": Harness().fingerprint,
                    "harness": asdict(Harness()),
                    "world": copy.deepcopy(task.initial),
                    "initial_world": copy.deepcopy(task.initial),
                    "last_check": None,
                    "started": time.time(),
                    "draft": None,
                    "registration_fingerprint": reg["fingerprint"],
                    "tool_fingerprint": reg["tool_fingerprint"],
                    "execution_mode": self.backend.mode,
                }
            )
            if not claimed:
                raise ValueError(
                    "Interrupted tool qualification retained; inspect receipts before retry"
                )
            checks = []
            try:
                for step, (account, invoice, count) in enumerate(
                    [
                        ("acme", "inv-1", 2),
                        ("acme", "does-not-exist", 0),
                        ("other-account", "inv-1", 2),
                    ]
                ):
                    out = self.tool(
                        team,
                        rid,
                        step,
                        "lookup_invoice",
                        {"account_id": account, "invoice_id": invoice},
                    )
                    checks.append(
                        {
                            "account": account,
                            "invoice": invoice,
                            "passed": out.get("ok") is True
                            and out.get("account_id") == account
                            and len(out.get("payments", [])) == count,
                        }
                    )
                after = self.result(team, rid)
                checks.append({"name": "read-only", "passed": after["world"] == task.initial})
                self.close(team, rid, "complete", "")
                result = {
                    "execution_id": rid,
                    "passed": all(c["passed"] for c in checks),
                    "checks": checks,
                    "registration": reg["fingerprint"],
                }
                self.put(team, key, result, once=True)
                return result
            except Exception:
                self.close(team, rid, "infrastructure_failure", "Invoice tool test interrupted")
                raise

    def audit(self, team, execution_id):
        """Allowlisted provider data; no tokens, authorization headers or grader fixtures."""
        run = self.result(team, execution_id)
        calls = []
        for c in calls_for(self.store, execution_id):
            row = {k: c[k] for k in ("id", "started", "ended", "error", "accounting") if k in c}
            row["request"] = {
                k: v
                for k, v in c.get("request", {}).items()
                if k in {"model", "messages", "max_tokens", "temperature", "reasoning_effort"}
            }
            row["response"] = {
                k: v
                for k, v in (c.get("response") or {}).items()
                if k in {"id", "model", "choices", "usage"}
            }
            row["headers"] = {
                k: v
                for k, v in c.get("headers", {}).items()
                if k.lower() in {"x-request-id", "request-id", "x-ratelimit-remaining-requests"}
            }
            calls.append(row)
        return {
            "campaign": self.lab,
            "run": run,
            "calls": calls,
            "events": run.get("events", []),
            "audit_fingerprint": fingerprint([calls, run.get("events", [])]),
        }

    def select_evidence(self, team, execution_id, event_ids, hypothesis, expected_behavior):
        with self.lock(team):
            run = self.result(team, execution_id)
            if (
                case(run["case_id"]).split != "development"
                or run["stage"].startswith("eval_")
                or not run.get("ended")
            ):
                raise ValueError("Choose a completed development execution")
            allowed = {e["id"] for e in run["events"]}
            if (
                not isinstance(event_ids, list)
                or len(event_ids) != 1
                or not set(event_ids) <= allowed
            ):
                raise ValueError("Cite one exact event from this execution")
            if not all(
                isinstance(s, str) and 10 <= len(s) <= 1400 for s in [hypothesis, expected_behavior]
            ):
                raise ValueError("Describe your hypothesis and expected behavior")
            value = {
                "execution_id": execution_id,
                "event_ids": event_ids,
                "event": next(e for e in run["events"] if e["id"] == event_ids[0]),
                "trace": run.get("trace", {}),
                "hypothesis": hypothesis,
                "expected_behavior": expected_behavior,
                "authorship": "participant hypothesis",
                "source": "development only",
            }
            self.put(team, "observation", value)
            return value

    def judge_development(self, team, execution_id, rubric):
        """Return raw structured response. The notebook implements development validation."""
        with self.lock(team):
            run = self.result(team, execution_id)
            if (
                run["stage"].startswith("eval_")
                or case(run["case_id"]).split != "development"
                or not run.get("ended")
            ):
                raise ValueError("Use a completed development run, never a held-out result")
            if not isinstance(rubric, str) or not 20 <= len(rubric) <= 3500:
                raise ValueError("Write a bounded development rubric")
            rid = fingerprint([team, self.lab, execution_id, rubric, "learner-judge"])[:32]
            key = "development_judge/" + rid
            if self.get(team, key):
                return self.get(team, key)
            count = self.get(team, "development_judge_ids", [])
            if rid not in count and len(count) >= 2:
                raise ValueError("Two development judgments used; edit and test validators locally")
            if rid in count:
                calls = calls_for(self.store, rid)
                good = [c for c in calls if (c.get("response") or {}).get("choices")]
                if good:
                    result = {
                        "id": rid,
                        "status": "returned",
                        "value": json_output(good[-1]),
                        "execution_id": execution_id,
                    }
                    self.put(team, key, result, once=True)
                    return result
                raise ValueError("Interrupted judge request retained without duplicate inference")
            self.put(team, "development_judge_ids", count + [rid])
            evidence = {k: run.get(k) for k in ["mission", "draft", "world", "events"]}
            try:
                call = self.provider.complete(
                    team,
                    rid,
                    0,
                    [
                        {"role": "system", "content": rubric},
                        {"role": "user", "content": json.dumps(evidence)},
                    ],
                    role="judge",
                )
                result = {
                    "id": rid,
                    "status": "returned",
                    "value": json_output(call),
                    "execution_id": execution_id,
                }
            except Exception as exc:
                result = {
                    "id": rid,
                    "status": "unavailable",
                    "error": str(exc),
                    "value": None,
                    "execution_id": execution_id,
                }
            self.put(team, key, result, once=True)
            return result
