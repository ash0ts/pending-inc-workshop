"""Trusted side of the lab: model budget, task workspaces, evidence, and private grading."""

import copy
import threading
import time
from dataclasses import asdict

from workshop.budget import BudgetStop
from workshop.provider import ProviderUnavailable, json_output
from workshop.telemetry import calls_for, diagnostic, export_run

from .cases import CASES, case, public_development
from .editor import bounded_feedback, editor_messages, normalize_reflection
from .grading import grade as independent_grade
from .harness import Harness, fingerprint, finish_guard, harness_diff
from .sandbox import SandboxBackend


class Gateway:
    lab = "office-v1"
    namespace = "office/"
    feedback_bytes = 19000

    def __init__(self, store, provider, *, export=True, backend=None):
        self.store, self.provider, self.manifest = store, provider, provider.manifest
        self.export = export
        self.backend = backend or SandboxBackend()
        self.mutex, self.locks = threading.Lock(), {}

    def lock(self, team):
        with self.mutex:
            return self.locks.setdefault(team, threading.RLock())

    def get(self, team, key, default=None):
        return self.store.get(team, self.namespace + key, default)

    def put(self, team, key, value, **kwargs):
        return self.store.put(team, self.namespace + key, value, **kwargs)

    def runs(self, team):
        return [r for r in self.store.runs(team) if r.get("lab") == self.lab]

    def result(self, team, run_id):
        run = next((r for r in self.runs(team) if r["id"] == run_id), None)
        if run is None:
            raise ValueError("Execution does not belong to this team")
        run["trace"] = self.store.get(team, "trace/" + run_id, {})
        return run

    def _stage(self, team, stage):
        rows = [r for r in self.runs(team) if r["stage"] == stage]
        if len(rows) != 1 or rows[0]["status"] == "running":
            raise ValueError(f"Complete {stage} first")
        return rows[0]

    def _parent(self, team, number):
        if number == 1:
            return Harness(**self.get(team, "manual")["harness"])
        if number == 2 and self.get(team, "library/1"):
            return Harness(**self.get(team, "decision/r1_composed")["harness"])
        raise ValueError("Finish the first round and its saved lesson first")

    def open(self, team, case_id, stage, harness, runtime):
        with self.lock(team):
            self.store.team(team)
            if not isinstance(runtime, str) or not 10 <= len(runtime) <= 24000:
                raise ValueError("Include the loop, context builder and guard source")
            old_runtime = self.get(team, "runtime")
            if old_runtime is not None and old_runtime != runtime:
                raise ValueError("The runtime is frozen; use a new campaign for code changes")
            h, task = Harness(**harness), case(case_id)
            frozen = self.get(team, "freeze")
            for completed in self.runs(team):
                if (
                    completed["stage"] == stage
                    and completed["case_id"] == case_id
                    and completed.get("ended")
                    and completed.get("harness") == asdict(h)
                ):
                    return self.result(team, completed["id"])
            if stage in {"eval_original", "eval_manual", "eval_frozen"}:
                if not frozen or task.split != "heldout":
                    raise ValueError("Freeze before evaluating fresh cases")
                expected = (
                    self.get(team, "baseline")
                    if stage == "eval_original"
                    else self.get(team, "manual")["harness"]
                    if stage == "eval_manual"
                    else frozen["harness"]
                )
            else:
                if frozen:
                    raise ValueError("Improvement is closed after freeze")
                stages = {
                    "original": "double-charge",
                    "manual": "double-charge",
                    "repeat_original": "double-charge",
                    "r2_start": "missing-seat",
                    **{
                        f"r{n}_{b}": c
                        for n, c in [(1, "double-charge"), (2, "missing-seat")]
                        for b in ["a", "b", "composed"]
                    },
                }
                if stages.get(stage) != case_id:
                    raise ValueError("Unknown stage/task pair")
                if stage == "original":
                    if self.get(team, "baseline") is None:
                        self.put(team, "baseline", asdict(h), once=True)
                    expected = self.get(team, "baseline")
                elif stage == "repeat_original":
                    expected = self.get(team, "baseline")
                elif stage == "manual":
                    expected = self.get(team, "manual")["harness"]
                elif stage == "r2_start":
                    expected = asdict(self._parent(team, 2))
                else:
                    decision = self.get(team, "decision/" + stage)
                    if not decision or not decision.get("approved"):
                        raise ValueError("Review the exact saved harness before its probe")
                    expected = decision["harness"]
            if asdict(h) != expected:
                raise ValueError("This execution must use its saved harness")
            if old_runtime is None:
                self.put(team, "runtime", runtime, once=True)
            identity = dict(
                team=team,
                stage=stage,
                case_id=case_id,
                fingerprint=h.fingerprint,
                lab=self.lab,
                fixture_hash=fingerprint(task.public_files()),
                runtime_hash=fingerprint([runtime, self.get(team, "tool_source")])
                if self.get(team, "tool_source")
                else fingerprint(runtime),
            )
            run_id = fingerprint(identity)[:32]
            _, run = self.store.claim_run(
                {
                    **identity,
                    "id": run_id,
                    "title": task.title,
                    "mission": task.mission,
                    "harness": asdict(h),
                    "draft": None,
                    "world": copy.deepcopy(task.initial),
                    "initial_world": copy.deepcopy(task.initial),
                    "last_check": None,
                    "started": time.time(),
                    "queue_seconds": 0,
                    "execution_mode": self.backend.mode,
                }
            )
            return run

    def model(self, team, run_id, step, messages):
        with self.lock(team):
            run = self.result(team, run_id)
            if run["status"] != "running" or not 0 <= step < self.manifest["max_steps"]:
                raise ValueError("Execution is closed or step budget exhausted")
            key = f"model/{run_id}/{step}"
            old = self.get(team, key)
            if old:
                if old["input_hash"] != fingerprint(messages):
                    raise ValueError("Saved request has different inputs")
                return old["action"]
            claim = key + "/claim"
            previous = self.get(team, claim)
            if previous:
                calls = [
                    c
                    for c in calls_for(self.store, run_id)
                    if c.get("response", {}).get("choices")
                    and fingerprint(c["request"]["messages"]) == fingerprint(messages)
                ]
                if not calls:
                    self.close(
                        team,
                        run_id,
                        "interrupted",
                        "Provider claim has no durable response; paid inference will not be repeated",
                    )
                    raise ValueError("Inspect the interrupted request")
                record = calls[-1]
            else:
                self.put(team, claim, {"input_hash": fingerprint(messages)}, once=True)
                try:
                    record = self.provider.complete(team, run_id, step, messages)
                except Exception as exc:
                    self.close(team, run_id, "infrastructure_failure", str(exc))
                    raise
            try:
                action = json_output(record)
            except (ValueError, KeyError, TypeError) as exc:
                action = {"action": "invalid_action", "arguments": {"error": str(exc)}}
            self.put(team, key, {"input_hash": fingerprint(messages), "action": action}, once=True)
            return action

    def tool(self, team, run_id, step, name, arguments):
        with self.lock(team):
            run = self.result(team, run_id)
            key = f"tool/{run_id}/{step}"
            wanted = fingerprint([name, arguments])
            previous = self.get(team, key)
            if previous:
                if previous["input_hash"] != wanted:
                    raise ValueError("Saved tool step has different inputs")
                return previous["output"]
            if run["status"] != "running" or not 0 <= step < self.manifest["max_steps"]:
                raise ValueError("Execution is closed or step is invalid")
            claim = key + "/claim"
            if self.get(team, claim):
                # A saved event is authoritative if a crash occurred before its response cache.
                event = next((e for e in run["events"] if e["id"] == f"{run_id}/tool-{step}"), None)
                if event and fingerprint([event["name"], event["arguments"]]) == wanted:
                    self.put(
                        team, key, {"input_hash": wanted, "output": event["output"]}, once=True
                    )
                    return event["output"]
                self.close(
                    team,
                    run_id,
                    "interrupted",
                    "Uncertain tool execution; preserve it without replaying the mutation",
                )
                raise ValueError("Tool claim interrupted; inspect its Sandbox receipt")
            self.put(team, claim, {"input_hash": wanted}, once=True)
            task, harness = case(run["case_id"]), Harness(**run["harness"])
            started = time.time()
            if not isinstance(arguments, dict):
                output = {"ok": False, "error": "arguments_must_be_an_object"}
            elif name == "read_skill":
                skills = {
                    "verify-operation": harness.verification_skill,
                    "office-tools": harness.tool_skill,
                }
                output = {
                    "ok": arguments.get("name") in skills,
                    "content": skills.get(arguments.get("name"), "") or "No skill installed.",
                }
            elif name == "finish":
                reason = finish_guard(
                    harness, fingerprint(run["world"]["accounts"]), run["last_check"]
                )
                valid = (
                    isinstance(arguments.get("summary"), str)
                    and 10 <= len(arguments["summary"]) <= 4000
                    and arguments.get("resolution") in {"resolved", "needs_clarification"}
                    and isinstance(arguments.get("questions", []), list)
                    and all(isinstance(q, str) for q in arguments.get("questions", []))
                )
                if reason or not valid:
                    output = {
                        "ok": False,
                        "error": "finish_blocked",
                        "reason": reason or "Supply a summary, resolution and questions list",
                    }
                else:
                    run["draft"] = arguments
                    run["outcome"] = arguments
                    output = {"ok": True, "accepted": True}
            elif name == "guard":
                output = {
                    "ok": False,
                    "error": "finish_blocked",
                    "reason": str(arguments.get("reason", ""))[:1000],
                }
            elif name in {
                "lookup_invoice",
                "get_account",
                "list_payments",
                "refund_payment",
                "discover_onboarding",
                "onboarding_status",
                "provision_member",
                "check_work",
            }:
                try:
                    result = self.backend.call(self, run, step, name, arguments, task)
                    run["world"] = result["world"]
                    output = {
                        **result["output"],
                        "execution": result["receipt"],
                        "files_sha256": result["files_sha256"],
                        **(
                            {"tool_source_sha256": result["tool_source_sha256"]}
                            if result.get("tool_source_sha256")
                            else {}
                        ),
                    }
                    if name == "check_work":
                        run["last_check"] = {
                            "state_hash": fingerprint(run["world"]["accounts"]),
                            "passed": output["passed"],
                        }
                except Exception as exc:
                    self.close(team, run_id, "infrastructure_failure", str(exc))
                    raise
            else:
                output = {"ok": False, "error": "unknown_action"}
            output["state_hash"] = fingerprint(run["world"]["accounts"])
            run["events"].append(
                {
                    "id": f"{run_id}/tool-{step}",
                    "kind": "guard" if name == "guard" else "tool",
                    "name": str(name),
                    "arguments": arguments,
                    "output": output,
                    "started": started,
                    "ended": time.time(),
                    "origin": "harness" if name == "guard" else "agent",
                }
            )
            self.store.save_run(run)
            self.put(team, key, {"input_hash": wanted, "output": output}, once=True)
            if output.get("accepted"):
                self.close(team, run_id, "complete", "")
            return output

    def close(self, team, run_id, status, reason):
        run = self.result(team, run_id)
        if run.get("ended"):
            return run
        run.update(status=status, error=reason or None, ended=time.time())
        run["seconds"] = run["ended"] - run["started"]
        calls = calls_for(self.store, run_id)
        run["observed_usd"] = sum((c.get("accounting", {}).get("observed_usd") or 0) for c in calls)
        run["bounded_unknown_usd"] = sum(
            c.get("accounting", {}).get("bounded_unknown_usd", 0) for c in calls
        )
        run["tool_errors"] = sum(
            e["kind"] == "tool" and not e["output"].get("ok", True) for e in run["events"]
        )
        run["check_failures"] = sum(
            e["name"] == "check_work" and e["output"].get("passed") is False for e in run["events"]
        )
        run["sandbox"] = self.backend.stop(self, run)
        self.store.save_run(run)
        self.export_one(team, run_id)
        return self.result(team, run_id)

    def export_one(self, team, run_id):
        run = self.result(team, run_id)
        if self.export:
            try:
                export_run(
                    self.store, self.manifest, run, allow_tool_only=run["stage"] == "mcp_practice"
                )
                self.put(team, "export_error/" + run_id, None)
            except Exception as exc:
                self.put(team, "export_error/" + run_id, str(exc))

    def evidence(self, team, stages):
        records, ids = [], set()
        for stage in stages:
            run = self._stage(team, stage)
            if run["case_id"] == "decision" or run["stage"].startswith("eval_"):
                raise ValueError("Improvement reads development executions only")
            receipt = self.store.get(team, "trace/" + run["id"], {})
            if self.export and not receipt.get("verified"):
                raise ValueError("Retry the trace export before improving from this execution")
            events = run["events"]
            ids.update(e["id"] for e in events)
            ids.update(receipt.get("span_ids", {}).values())
            records.append(
                {
                    "stage": stage,
                    "conversation": receipt.get("url"),
                    "mission": run["mission"],
                    "answer": run.get("draft"),
                    "office_state": run.get("world"),
                    "events": events,
                    "diagnostic": diagnostic(self.store, run) if self.export else None,
                }
            )
        return records, ids

    def edit(self, team, number, phase, hypothesis=""):
        with self.lock(team):
            if (
                self.get(team, "freeze")
                or number not in {1, 2}
                or phase not in {"propose", "compose", "reflect"}
            ):
                raise ValueError("Unknown or frozen improvement stage")
            saved = self.get(team, f"editor/{number}/{phase}")
            if saved:
                return saved
            parent = self._parent(team, number)
            start = "manual" if number == 1 else "r2_start"
            stages = [start] + ([f"r{number}_a", f"r{number}_b"] if phase != "propose" else [])
            if phase == "reflect":
                stages.append(f"r{number}_composed")
            evidence, allowed = self.evidence(team, stages)
            candidates = {b: self.get(team, f"decision/r{number}_{b}") for b in ["a", "b"]}
            payload = bounded_feedback(
                {
                    "parent_harness": asdict(parent),
                    "raw_execution_feedback": evidence,
                    "participant_hypothesis": hypothesis[:1500],
                    "participant_observation": self.get(team, "observation"),
                    "experience_library": self.get(team, "library/1") if number == 2 else [],
                    "candidate_decisions": candidates if phase == "compose" else {},
                    "adopted_harness": self.get(team, f"decision/r{number}_composed")["harness"]
                    if phase == "reflect"
                    else None,
                },
                max_bytes=self.feedback_bytes,
            )
            allowed.update(self._auxiliary_citations(team, payload))
            identity = {
                "team": team,
                "stage": f"r{number}_{phase}",
                "case_id": "decision",
                "lab": self.lab,
                "fingerprint": parent.fingerprint,
            }
            run_id = fingerprint(identity)[:32]
            _, editor = self.store.claim_run(
                {
                    **identity,
                    "id": run_id,
                    "title": f"Round {number} / {phase}",
                    "mission": f"{phase.title()} the office harness using recorded development executions.",
                    "started": time.time(),
                }
            )
            if editor.get("ended"):
                if editor.get(
                    "error"
                ) == "Request input exceeds the workshop context bound" and not calls_for(
                    self.store, run_id
                ):
                    # The old preflight rejected this request before any inference. Preserve
                    # that run and give the corrected attempt its own identity.
                    identity.update(attempt=2, resumes_preflight=run_id)
                    run_id = fingerprint(identity)[:32]
                    _, editor = self.store.claim_run(
                        {
                            **identity,
                            "id": run_id,
                            "title": f"Round {number} / {phase} / Attempt 2",
                            "mission": f"{phase.title()} the office harness using recorded development executions.",
                            "started": time.time(),
                        }
                    )
                if editor.get("ended") and phase == "reflect":
                    for saved_call in reversed(calls_for(self.store, run_id)):
                        try:
                            raw = json_output(saved_call)
                            recovered = normalize_reflection(raw)
                            if recovered is raw or not all(
                                isinstance(recovered.get(k), str)
                                and 10 <= len(recovered[k]) <= 1400
                                for k in ["lesson", "when_to_use", "limit"]
                            ):
                                continue
                            self._cites(recovered, allowed)
                            recovered.update(
                                harness=self.get(team, f"decision/r{number}_composed")["harness"],
                                source_run=run_id,
                                receipt_recovery={
                                    "source_request": saved_call["id"],
                                    "method": "Unwrapped recorded save_lesson.arguments; content and citations unchanged",
                                    "original_status": editor["status"],
                                },
                            )
                            self.put(team, f"library/{number}", recovered, once=True)
                            self.put(team, f"editor/{number}/{phase}", recovered, once=True)
                            return recovered
                        except (ValueError, KeyError, TypeError):
                            continue
                if editor.get("ended"):
                    raise ValueError(
                        "This editor stopped without a valid receipt; its paid attempts are preserved"
                    )
            messages = editor_messages(phase, payload)
            try:
                for attempt in range(2):
                    record = self._editor_request(team, run_id, attempt, messages)
                    try:
                        value = json_output(record)
                        if phase == "reflect":
                            value = normalize_reflection(value)
                            if not all(
                                isinstance(value.get(k), str) and 10 <= len(value[k]) <= 1400
                                for k in ["lesson", "when_to_use", "limit"]
                            ):
                                raise ValueError(
                                    "Reflection needs a lesson, when_to_use, and limit"
                                )
                            self._cites(value, allowed)
                        else:
                            decisions = value if phase == "propose" else {"composed": value}
                            if phase == "propose" and set(decisions) != {"a", "b"}:
                                raise ValueError("Save both competing proposals")
                            for branch, decision in decisions.items():
                                base = decision.get("based_on")
                                if base not in (
                                    {"parent"} if phase == "propose" else {"parent", "a", "b"}
                                ):
                                    raise ValueError("Invalid based_on version")
                                before = (
                                    parent
                                    if base == "parent"
                                    else Harness(**candidates[base]["harness"])
                                )
                                after = Harness(**decision["harness"])
                                if decision.get("decision") != (
                                    "KEEP" if before == after else "CHANGE"
                                ):
                                    raise ValueError("CHANGE/KEEP must match the actual file diff")
                                if not all(
                                    isinstance(decision.get(k), str) and len(decision[k]) >= 8
                                    for k in ["title", "rationale"]
                                ):
                                    raise ValueError(
                                        "A completed decision needs a title and rationale"
                                    )
                                self._cites(decision, allowed)
                                decision.update(
                                    fingerprint=after.fingerprint,
                                    parent_fingerprint=before.fingerprint,
                                    diff=harness_diff(before, after),
                                    source_run=run_id,
                                    approved=False,
                                    affected_files=[
                                        p
                                        for p in before.files()
                                        if before.files()[p] != after.files()[p]
                                    ],
                                )
                        break
                    except (ValueError, TypeError, KeyError) as exc:
                        if attempt == 1:
                            raise
                        messages.append(
                            {
                                "role": "user",
                                "content": "The receipt is incomplete: "
                                + str(exc)
                                + ". Return a complete valid receipt.",
                            }
                        )
                if phase == "reflect":
                    value.update(
                        harness=self.get(team, f"decision/r{number}_composed")["harness"],
                        source_run=run_id,
                    )
                    self.put(team, f"library/{number}", value, once=True)
                else:
                    for branch, decision in decisions.items():
                        self.put(team, f"decision/r{number}_{branch}", decision, once=True)
                editor.update(outcome=value)
                self.store.save_run(editor)
                self.close(team, run_id, "complete", "")
                self.put(team, f"editor/{number}/{phase}", value, once=True)
                return value
            except Exception as exc:
                status = (
                    "infrastructure_failure"
                    if isinstance(exc, (BudgetStop, ProviderUnavailable))
                    else "agent_failure"
                )
                self.close(team, run_id, status, str(exc))
                raise

    def _editor_request(self, team, run_id, attempt, messages):
        key = f"editor-request/{run_id}/{attempt}"
        digest = fingerprint(messages)
        claim = self.get(team, key)
        if claim:
            if claim["input_hash"] != digest:
                raise ValueError("The saved editor request has different inputs")
            records = [
                r
                for r in calls_for(self.store, run_id)
                if r.get("response", {}).get("choices")
                and fingerprint(r["request"]["messages"]) == digest
            ]
            if not records:
                raise ValueError(
                    "Interrupted editor request has no durable response; do not repeat paid inference"
                )
            return records[-1]
        self.put(team, key, {"input_hash": digest}, once=True)
        return self.provider.complete(team, run_id, attempt, messages, role="improver")

    def _auxiliary_citations(self, team, payload):
        """Accept supplied observation/library IDs only when this team's traces verify them."""
        observation = payload.get("participant_observation") or {}
        library = payload.get("experience_library") or {}
        supplied = (
            set(library.get("supporting_event_ids", [])) if isinstance(library, dict) else set()
        )
        event_id = observation.get("event", {}).get("id")
        if event_id:
            supplied.add(event_id)
        supplied.update(observation.get("trace", {}).get("span_ids", {}).values())
        verified = set()
        for run in self.runs(team):
            if run["case_id"] == "decision" or run["stage"].startswith("eval_"):
                continue
            trace = self.store.get(team, "trace/" + run["id"], {})
            if not run.get("ended") or (self.export and not trace.get("verified")):
                continue
            verified.update(event["id"] for event in run["events"])
            verified.update(trace.get("span_ids", {}).values())
        return supplied & verified

    @staticmethod
    def _cites(value, allowed):
        cites = value.get("supporting_event_ids")
        if (
            not isinstance(cites, list)
            or not cites
            or not all(isinstance(x, str) and x in allowed for x in cites)
        ):
            raise ValueError("Cite at least one exact event/span ID from the observed executions")

    def approve(self, team, stage, expected_fingerprint):
        with self.lock(team):
            if self.get(team, "freeze"):
                raise ValueError("The harness is frozen")
            decision = self.get(team, "decision/" + stage)
            if not decision or decision["fingerprint"] != expected_fingerprint:
                raise ValueError("Review the saved version before approving")
            decision["approved"] = True
            self.put(team, "decision/" + stage, decision)
            return decision

    def observe(self, team, run_id, event_id, claim):
        run = self.result(team, run_id)
        if self.get(team, "freeze") or run["stage"].startswith("eval_"):
            raise ValueError("Use a development execution before freeze")
        event = next((e for e in run["events"] if e["id"] == event_id), None)
        if not event or not 10 <= len(claim) <= 1500:
            raise ValueError("Select an actual event and describe the hypothesis")
        observation = {
            "hypothesis": claim,
            "event": event,
            "execution": run_id,
            "trace": run["trace"],
        }
        self.put(team, "observation", observation)
        return observation

    def freeze(self, team, stage):
        with self.lock(team):
            old = self.get(team, "freeze")
            if old:
                if old["stage"] != stage:
                    raise ValueError("The frozen selection cannot change")
                return old
            self._stage(team, "r2_composed")
            run = self._stage(team, stage)
            if stage not in {
                "original",
                "manual",
                "r1_a",
                "r1_b",
                "r1_composed",
                "r2_start",
                "r2_a",
                "r2_b",
                "r2_composed",
            }:
                raise ValueError("Select a tested harness")
            value = {
                "stage": stage,
                "harness": run["harness"],
                "fingerprint": run["fingerprint"],
                "at": time.time(),
            }
            self.put(team, "freeze", value, once=True)
            return value

    def grade(self, team):
        with self.lock(team):
            if not self.get(team, "freeze"):
                raise ValueError("Freeze first")
            runs = [r for r in self.runs(team) if r["stage"].startswith("eval_")]
            expected = {
                (c.id, s)
                for c in CASES
                if c.split == "heldout"
                for s in ["eval_original", "eval_manual", "eval_frozen"]
            }
            if {(r["case_id"], r["stage"]) for r in runs} != expected or any(
                r["status"] == "running" for r in runs
            ):
                raise ValueError("Finish all 12 final executions before grading")
            for run in runs:
                if not run.get("scores"):
                    run["scores"] = independent_grade(case(run["case_id"]), run)
                    self.store.save_run(run)
            self.put(team, "graded", True)
            return self.snapshot(team)

    def snapshot(self, team):
        from .judging import judgments

        frozen = self.get(team, "freeze")
        return {
            "lab": self.lab,
            "team": team,
            "baseline": self.get(team, "baseline"),
            "development": public_development(),
            "heldout": [{"id": c.id, "title": c.title} for c in CASES if c.split == "heldout"]
            if frozen
            else [],
            "decisions": {
                s: self.get(team, "decision/" + s)
                for s in [f"r{n}_{b}" for n in [1, 2] for b in ["a", "b", "composed"]]
            },
            "library": [
                self.get(team, f"library/{n}") for n in [1, 2] if self.get(team, f"library/{n}")
            ],
            "runs": [self.result(team, r["id"]) for r in self.runs(team)],
            "freeze": frozen,
            "graded": self.get(team, "graded", False),
            "publication": self.get(team, "publication"),
            "evaluation_suite": self.get(team, "evaluation_suite"),
            "judgments": judgments(self, team),
            "evaluation_publication": self.get(team, "evaluation_publication"),
            "review_assessment": self.get(team, "review_assessment"),
            "budget": self.provider.budget.summary(team),
            "manual": self.get(team, "manual"),
            "checkpoints": self.get(team, "checkpoints", {}),
            "conclusion": self.get(team, "conclusion"),
            "execution_mode": self.backend.mode,
        }

    def sandbox_recheck(self, team, run_id):
        raise ValueError("Office tools already execute in Sandboxes; open an execution receipt")

    def retry_exports(self, team):
        from .judging import export_judge, judgments

        for run in self.runs(team):
            if run.get("ended"):
                self.export_one(team, run["id"])
        for row in judgments(self, team):
            if row.get("ended"):
                export_judge(self, row)
        return self.snapshot(team)

    def publish(self, team):
        from .evaluation_publication import publish_report

        return publish_report(self, team)

    def review(self, team, execution_id):
        from .judging import review_one

        return review_one(self, team, execution_id)

    def evaluate(self, team):
        from .judging import evaluate

        return evaluate(self, team)

    def publish_evaluation(self, team):
        from .evaluation_publication import publish_evaluation

        return publish_evaluation(self, team)

    def assess_review(self, team, judgment_id, criterion, agree, reason):
        from .evaluation import RUBRIC
        from .judging import judgments

        if not any(
            j["id"] == judgment_id and j["status"] == "complete" for j in judgments(self, team)
        ):
            raise ValueError("Select a completed judgment belonging to your workshop account")
        if (
            criterion not in RUBRIC["criteria"]
            or type(agree) is not bool
            or not isinstance(reason, str)
            or not 10 <= len(reason) <= 1500
        ):
            raise ValueError("Select a rubric criterion and explain your agreement or disagreement")
        value = {
            "judgment_id": judgment_id,
            "criterion": criterion,
            "agree": agree,
            "reason": reason,
            "authorship": "participant",
        }
        self.put(team, "review_assessment", value)
        return value

    def manual_candidate(self, team, harness, rationale, event_id):
        with self.lock(team):
            if self.get(team, "freeze"):
                raise ValueError("The comparison is frozen")
            original = self._stage(team, "original")
            if (
                event_id not in {e["id"] for e in original["events"]}
                or not 10 <= len(rationale) <= 1500
            ):
                raise ValueError("Cite an original execution event and explain your edit")
            before, after = Harness(**original["harness"]), Harness(**harness)
            decision = {
                "harness": asdict(after),
                "fingerprint": after.fingerprint,
                "parent_fingerprint": before.fingerprint,
                "decision": "KEEP" if before == after else "CHANGE",
                "authorship": "human",
                "title": "Participant revision",
                "rationale": rationale,
                "supporting_event_ids": [event_id],
                "diff": harness_diff(before, after),
                "affected_files": [
                    p for p in before.files() if before.files()[p] != after.files()[p]
                ],
                "approved": True,
            }
            old = self.get(team, "manual")
            if old and old != decision:
                raise ValueError("The submitted manual version is locked for its probes")
            if not old:
                self.put(team, "manual", decision, once=True)
            return decision

    def checkpoint(self, team, quest, answer):
        from .story import QUESTS

        if (
            quest not in {q["id"] for q in QUESTS}
            or not isinstance(answer, str)
            or not 5 <= len(answer) <= 4000
        ):
            raise ValueError("Choose a quest and save your observation")
        with self.lock(team):
            values = self.get(team, "checkpoints", {})
            values[quest] = {"answer": answer, "saved": time.time(), "authorship": "participant"}
            self.put(team, "checkpoints", values)
        return values

    def conclude(self, team, conclusion, next_test):
        if not self.get(team, "graded") or not all(
            isinstance(t, str) and 10 <= len(t) <= 4000 for t in [conclusion, next_test]
        ):
            raise ValueError("Evaluate first, then explain the result and next test")
        value = {"conclusion": conclusion, "next_test": next_test, "authorship": "participant"}
        self.put(team, "conclusion", value)
        return value

    def configure_tool(self, team, source):
        with self.lock(team):
            import ast

            if self.get(team, "baseline"):
                raise ValueError("Tool code is frozen after the baseline opens")
            if not isinstance(source, str) or len(source) > 6000:
                raise ValueError("Supply the get_account function source")
            nodes = ast.parse(source).body
            if (
                len(nodes) != 1
                or not isinstance(nodes[0], ast.FunctionDef)
                or nodes[0].name != "get_account"
            ):
                raise ValueError("Supply one function named get_account; no top-level statements")
            self.put(team, "tool_source", source)
            return {
                "saved": True,
                "source_hash": fingerprint(source),
                "note": "This function executes in the Sandbox. Test it on public development records before the baseline.",
            }

    def tutorial(self, team, instructions):
        with self.lock(team):
            if not isinstance(instructions, str) or not 20 <= len(instructions) <= 3500:
                raise ValueError("Write 20–3500 characters of instructions")
            existing = [r for r in self.runs(team) if r["case_id"] == "tutorial"]
            identity = {
                "team": team,
                "lab": self.lab,
                "stage": "tutorial",
                "case_id": "tutorial",
                "fingerprint": fingerprint(instructions),
            }
            rid = fingerprint(identity)[:32]
            if any(r["id"] == rid for r in existing):
                return self.result(team, rid)
            if len(existing) >= 3:
                raise ValueError(
                    "The tutorial allows three explicit requests; continue with the tool exercises"
                )
            mission = "A customer reports a duplicate charge. Explain what you can do right now. You have no tools or account records in this exercise."
            _, r = self.store.claim_run(
                {
                    **identity,
                    "id": rid,
                    "title": "First day, no tools",
                    "mission": mission,
                    "started": time.time(),
                }
            )
            try:
                response = self.provider.complete(
                    team,
                    rid,
                    0,
                    [
                        {
                            "role": "system",
                            "content": instructions
                            + " Return JSON with summary and missing_capabilities.",
                        },
                        {"role": "user", "content": mission},
                    ],
                )
                r["outcome"] = json_output(response)
                self.store.save_run(r)
                return self.close(team, rid, "complete", "")
            except Exception as exc:
                self.close(team, rid, "infrastructure_failure", str(exc))
                raise

    def mcp_practice(self, team):
        with self.lock(team):
            task = case("missing-seat")
            h = Harness()
            identity = {
                "team": team,
                "lab": self.lab,
                "stage": "mcp_practice",
                "case_id": task.id,
                "fingerprint": h.fingerprint,
            }
            rid = fingerprint(identity)[:32]
            _, r = self.store.claim_run(
                {
                    **identity,
                    "id": rid,
                    "title": "IT connection check",
                    "mission": "Discover the onboarding tools and inspect the Fern account through MCP. No model call or account mutation.",
                    "harness": asdict(h),
                    "world": copy.deepcopy(task.initial),
                    "initial_world": copy.deepcopy(task.initial),
                    "last_check": None,
                    "draft": None,
                    "started": time.time(),
                    "execution_mode": self.backend.mode,
                }
            )
            if r.get("ended"):
                return self.result(team, rid)
            self.tool(team, rid, 0, "discover_onboarding", {})
            self.tool(team, rid, 1, "onboarding_status", {"account_id": "fern"})
            r = self.result(team, rid)
            r["outcome"] = {
                "summary": "Discovered onboarding tools and read Fern through actual MCP.",
                "model_calls": 0,
            }
            for event in r["events"]:
                event["origin"] = "participant"
            self.store.save_run(r)
            return self.close(team, rid, "complete", "")

    def practice_review(self, team, execution_id, instructions):
        """Two development-only reviews with the participant's prompt; final rubric stays fixed."""
        from .evaluation import review_evidence, validate_review
        from .judging import export_judge

        with self.lock(team):
            run = self.result(team, execution_id)
            if (
                run["stage"].startswith("eval_")
                or run["case_id"] in {"decision", "tutorial"}
                or run["status"] != "complete"
            ):
                raise ValueError("Select a completed development execution")
            if not isinstance(instructions, str) or not 20 <= len(instructions) <= 3500:
                raise ValueError("Write a bounded judge instruction")
            evidence = review_evidence(run)
            identity = {
                "team": team,
                "lab": "office-practice-review-v1",
                "execution_id": execution_id,
                "instructions_hash": fingerprint(instructions),
                "input_hash": fingerprint(evidence),
            }
            rid = fingerprint(identity)[:32]
            rows = [r for r in self.store.runs(team) if r.get("lab") == "office-practice-review-v1"]
            old = next((r for r in rows if r["id"] == rid), None)
            if old:
                return old
            if len(rows) >= 2:
                raise ValueError(
                    "Practice allows two judge prompts; final evaluation uses the frozen suite"
                )
            _, row = self.store.claim_run(
                {
                    **identity,
                    "id": rid,
                    "case_id": run["case_id"],
                    "stage": "practice_judge",
                    "title": run["title"],
                    "mission": "Practice reviewing the employee explanation: " + run["mission"],
                    "fingerprint": run["fingerprint"],
                    "input": evidence,
                    "started": time.time(),
                }
            )
            try:
                response = self.provider.complete(
                    team,
                    rid,
                    0,
                    [
                        {"role": "system", "content": instructions},
                        {"role": "user", "content": __import__("json").dumps(evidence)},
                    ],
                    role="judge",
                )
                row.update(
                    outcome=validate_review(json_output(response), evidence), status="complete"
                )
            except Exception as exc:
                row.update(status="unavailable", error=str(exc))
            row["ended"] = time.time()
            row["seconds"] = row["ended"] - row["started"]
            self.store.save_run(row)
            export_judge(self, row)
            row["trace"] = self.store.get(team, "trace/" + rid, {})
            return row
