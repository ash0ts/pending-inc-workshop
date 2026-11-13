from __future__ import annotations

import json
import os
import threading
import time

import httpx

from .budget import Budget


class ProviderUnavailable(RuntimeError):
    pass


class Provider:
    def __init__(self, store, manifest, *, transport=None, sleeper=time.sleep):
        self.store, self.manifest = store, manifest
        self.budget = Budget(store, manifest)
        self.client = httpx.Client(timeout=manifest["request_timeout_seconds"], transport=transport)
        self.lock = threading.Lock()
        self.failures, self.cool_until = 0, 0
        self.sleeper = sleeper
        self.semaphore = threading.BoundedSemaphore(manifest["global_concurrency"])

    def complete(self, team, run_id, step, messages, *, role="solver"):
        m = self.manifest
        request = {
            "model": m["model"],
            "messages": messages,
            "max_tokens": m["improver_max_output_tokens"]
            if role in {"improver", "judge"}
            else m["max_output_tokens"],
            "temperature": m["temperature"],
            "response_format": {"type": "json_object"},
        }
        if role == "judge":
            request["reasoning_effort"] = m["judge_reasoning_effort"]
        elif role == "improver" and m.get("improver_reasoning_effort"):
            request["reasoning_effort"] = m["improver_reasoning_effort"]
        with self.semaphore:
            for attempt in range(m["transport_retries"] + 1):
                with self.lock:
                    if time.time() < self.cool_until:
                        raise ProviderUnavailable(
                            "Provider circuit is cooling down; no request sent"
                        )
                call_id = f"{run_id}/{step}/{attempt}"
                self.budget.reserve(call_id, team, run_id, request)
                started = time.time()
                response, error, headers, status = None, None, {}, None
                try:
                    r = self.client.post(
                        m["base_url"] + "/chat/completions",
                        json=request,
                        timeout=m.get("improver_timeout_seconds", 180)
                        if role in {"improver", "judge"}
                        else m["request_timeout_seconds"],
                        headers={
                            "Authorization": "Bearer " + os.environ["WANDB_API_KEY"],
                            "OpenAI-Project": m["project"],
                        },
                    )
                    status = r.status_code
                    headers = {
                        k: v
                        for k, v in r.headers.items()
                        if k.lower()
                        in {
                            "x-request-id",
                            "request-id",
                            "retry-after",
                            "x-ratelimit-remaining-requests",
                        }
                    }
                    try:
                        response = r.json()
                    except ValueError:
                        error = {"type": "invalid_provider_response", "http_status": status}
                    if status >= 400 or (isinstance(response, dict) and response.get("error")):
                        error = {
                            "type": "provider_error",
                            "http_status": status,
                            "message": str((response or {}).get("error", "HTTP error"))[:1000],
                        }
                except (httpx.HTTPError, KeyError) as exc:
                    error = {"type": type(exc).__name__, "message": str(exc)[:500]}
                if not isinstance(response, dict):
                    response = None
                record = self.budget.finish(
                    call_id,
                    {
                        "id": call_id,
                        "started": started,
                        "ended": time.time(),
                        "request": request,
                        "response": response,
                        "headers": headers,
                        "http_status": status,
                        "error": error,
                        "attempt": attempt,
                        "role": role,
                    },
                )
                if not error and response and response.get("choices"):
                    with self.lock:
                        self.failures = 0
                    return record
                with self.lock:
                    self.failures += 1
                    if self.failures >= 3:
                        self.cool_until = time.time() + 30
                        self.failures = 0
                retryable = status is None or status == 429 or status >= 500
                if not retryable or attempt == m["transport_retries"]:
                    raise ProviderUnavailable(
                        json.dumps(error or {"type": "empty_provider_response"})
                    )
                self.sleeper(min(2**attempt, 4))
        raise ProviderUnavailable("Provider did not return a response")


def json_output(record):
    choice = record["response"]["choices"][0]
    if choice.get("finish_reason") == "length":
        raise ValueError("Model output reached its token limit")
    content = choice.get("message", {}).get("content") or ""
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0]
    value = json.loads(content)
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value
