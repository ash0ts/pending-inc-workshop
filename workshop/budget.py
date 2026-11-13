"""The trusted service reserves cost before every provider attempt."""

import json
import os
import time
from decimal import ROUND_CEILING, Decimal


class BudgetStop(RuntimeError):
    pass


def micros(value):
    return int((Decimal(str(value)) * 1_000_000).to_integral_value(rounding=ROUND_CEILING))


class Budget:
    def __init__(self, store, manifest):
        self.store, self.manifest = store, manifest

    def limit_usd(self, team):
        """Operator-set allowance; older accounts retain the manifest default."""
        limit = Decimal(
            str(self.store.get(team, "workshop_budget_usd", self.manifest["team_workshop_usd"]))
        )
        if not limit.is_finite() or not 0 < limit <= Decimal(
            str(self.manifest["team_workshop_usd"])
        ):
            raise BudgetStop("Invalid workshop account allowance")
        return float(limit)

    @staticmethod
    def _used(db, clause="", args=()):
        return db.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='pending' THEN reserved "
            "ELSE COALESCE(observed,0)+COALESCE(unknown,0) END),0) FROM requests " + clause,
            args,
        ).fetchone()[0]

    def reserve(self, request_id, team, run_id, request):
        m = self.manifest
        if request["model"] != m["model"]:
            raise BudgetStop("Requested model is not the frozen model")
        # UTF-8 bytes plus generous chat overhead bound tokenization without assuming a cache hit.
        input_limit = m["max_input_tokens"]
        if len(json.dumps(request["messages"], ensure_ascii=False).encode()) > input_limit - 2048:
            raise BudgetStop("Request input exceeds the workshop context bound")
        if request["max_tokens"] not in {
            m["max_output_tokens"],
            m.get("improver_max_output_tokens", m["max_output_tokens"]),
        }:
            raise BudgetStop("Output bound changed")
        price = m["prices_per_million"]
        reserved = micros(
            (
                Decimal(str(price["input"])) * input_limit
                + Decimal(str(price["output"])) * request["max_tokens"]
            )
            / 1_000_000
        )
        bucket = self.store.team(team)["bucket"]
        with self.store.transaction() as db:
            owner = db.execute("SELECT value FROM state WHERE key='ledger_owner'").fetchone()
            if owner and owner[0] != os.environ.get("WORKSHOP_HOST_ID"):
                raise BudgetStop(
                    "This ledger belongs to the hosted service. This local copy is read-only for inference."
                )
            stop = db.execute("SELECT value FROM state WHERE key='accounting_stop'").fetchone()
            if stop:
                raise BudgetStop(stop[0])
            if db.execute("SELECT 1 FROM requests WHERE id=?", (request_id,)).fetchone():
                raise BudgetStop(
                    "A provider attempt with this identity already exists; never rerun it"
                )
            fees = db.execute("SELECT COALESCE(SUM(micros),0) FROM charges").fetchone()[0]
            if self._used(db) + fees + reserved > micros(m["budget_usd"]):
                raise BudgetStop("Workshop ceiling reached, including compute and in-flight calls")
            fees_bucket = db.execute(
                "SELECT COALESCE(SUM(micros),0) FROM charges WHERE bucket=?", (bucket,)
            ).fetchone()[0]
            if self._used(db, "WHERE bucket=?", (bucket,)) + fees_bucket + reserved > micros(
                m["buckets_usd"][bucket]
            ):
                raise BudgetStop(f"{bucket} allocation exhausted")
            if bucket == "workshop" and self._used(db, "WHERE team=?", (team,)) + reserved > micros(
                self.limit_usd(team)
            ):
                raise BudgetStop("Your workshop model-call allowance is exhausted")
            db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    request_id,
                    team,
                    run_id,
                    bucket,
                    "pending",
                    reserved,
                    None,
                    None,
                    json.dumps({"request": request}),
                    time.time(),
                ),
            )

    def finish(self, request_id, record):
        m = self.manifest
        with self.store.transaction() as db:
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if not row or row["status"] != "pending":
                raise BudgetStop("Unknown or already-accounted provider attempt")
            stored = json.loads(row["value"])
            stored.update(record)
            usage = (record.get("response") or {}).get("usage")
            observed, unknown, status = None, row["reserved"], "bounded_unknown"
            stop_reason = None
            response = record.get("response") or {}
            returned = response.get("model")
            # An error envelope with no model is a provider error, not a model mismatch.
            if returned is not None and returned != m["model"]:
                stop_reason = f"Provider returned unexpected model {returned}"
            if response.get("choices") and returned is None:
                stop_reason = "Successful provider response lacks a model identity"
            if isinstance(usage, dict) and any(
                k in usage and usage[k] is not None and (type(usage[k]) is not int or usage[k] < 0)
                for k in ("prompt_tokens", "completion_tokens")
            ):
                stop_reason = "Contradictory provider usage; full reservation retained"
            if isinstance(usage, dict) and all(
                type(usage.get(k)) is int and usage[k] >= 0
                for k in ("prompt_tokens", "completion_tokens")
            ):
                inp, out = usage["prompt_tokens"], usage["completion_tokens"]
                cache = (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
                if type(cache) is not int or not 0 <= cache <= inp:
                    stop_reason = "Contradictory cached usage"
                else:
                    p = m["prices_per_million"]
                    observed = micros(
                        (
                            Decimal(str(p["input"])) * (inp - cache)
                            + Decimal(str(p["cached"])) * cache
                            + Decimal(str(p["output"])) * out
                        )
                        / 1_000_000
                    )
                    unknown, status = 0, "observed"
                    if (
                        inp > m["max_input_tokens"]
                        or out > stored["request"]["max_tokens"]
                        or observed > row["reserved"]
                    ):
                        stop_reason = "Returned usage exceeds the proven reservation"
            stored["accounting"] = {
                "observed_usd": None if observed is None else observed / 1e6,
                "bounded_unknown_usd": unknown / 1e6,
                "reserved_usd": row["reserved"] / 1e6,
            }
            db.execute(
                "UPDATE requests SET status=?,observed=?,unknown=?,value=? WHERE id=?",
                (status, observed, unknown, json.dumps(stored), request_id),
            )
            if stop_reason:
                db.execute(
                    "INSERT OR REPLACE INTO state VALUES('accounting_stop',?)", (stop_reason,)
                )
        if stop_reason:
            raise BudgetStop(stop_reason)
        return stored

    def charge(self, charge_id, bucket, description, usd):
        amount = micros(usd)
        if amount < 0 or bucket not in self.manifest["buckets_usd"]:
            raise ValueError("Invalid compute reservation")
        with self.store.transaction() as db:
            owner = db.execute("SELECT value FROM state WHERE key='ledger_owner'").fetchone()
            if owner and owner[0] != os.environ.get("WORKSHOP_HOST_ID"):
                raise BudgetStop(
                    "This ledger is owned by the hosted gateway; local spending is disabled"
                )
            if db.execute("SELECT 1 FROM charges WHERE id=?", (charge_id,)).fetchone():
                return
            fees = db.execute("SELECT COALESCE(SUM(micros),0) FROM charges").fetchone()[0]
            if self._used(db) + fees + amount > micros(self.manifest["budget_usd"]):
                raise BudgetStop("Compute would exceed the workshop ceiling")
            bucket_fees = db.execute(
                "SELECT COALESCE(SUM(micros),0) FROM charges WHERE bucket=?", (bucket,)
            ).fetchone()[0]
            if self._used(db, "WHERE bucket=?", (bucket,)) + bucket_fees + amount > micros(
                self.manifest["buckets_usd"][bucket]
            ):
                raise BudgetStop("Compute would exceed the phase ceiling")
            db.execute(
                "INSERT INTO charges VALUES(?,?,?,?)", (charge_id, bucket, description, amount)
            )

    def summary(self, team=None):
        clause, args = ("WHERE team=?", (team,)) if team else ("", ())
        with self.store.connect() as db:
            row = db.execute(
                "SELECT COUNT(*),COALESCE(SUM(observed),0),COALESCE(SUM(unknown),0),"
                "COALESCE(SUM(CASE WHEN status='pending' THEN reserved ELSE 0 END),0) "
                "FROM requests " + clause,
                args,
            ).fetchone()
            charges = (
                db.execute("SELECT COALESCE(SUM(micros),0) FROM charges").fetchone()[0]
                if not team
                else 0
            )
        return dict(
            attempts=row[0],
            observed_usd=row[1] / 1e6,
            bounded_unknown_usd=row[2] / 1e6,
            inflight_usd=row[3] / 1e6,
            compute_reserved_usd=charges / 1e6,
            accounted_usd=(sum(row[1:]) + charges) / 1e6,
        )
