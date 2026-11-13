from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parents[1]


def config():
    return json.loads((ROOT / "manifest.json").read_text())


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Harness(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    instructions: str = Field(min_length=20, max_length=5000)
    validate_before_submit: bool = False
    calendar_retries: int = Field(default=0, ge=0, le=1)

    @property
    def fingerprint(self):
        return fingerprint(self.model_dump())


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["CHANGE", "KEEP"]
    title: str = Field(min_length=5, max_length=130)
    category: Literal["Task rules", "Verification", "Workflow", "Tool access"]
    rationale: str = Field(min_length=15, max_length=2000)
    supporting_event_ids: list[str] = Field(default_factory=list, max_length=12)
    based_on: Literal["original", "candidate_a", "candidate_b"] = "original"
    harness: Harness


BASELINE = Harness(
    instructions=(
        "Schedule all four requested jobs using the supplied tools. Respect each job's required "
        "qualification, appointment window, duration, technician availability, and travel time "
        "between consecutive jobs. Use times as integer minutes after midnight on a 15-minute "
        "grid. Choose any complete valid schedule. If no complete schedule exists, explicitly "
        "defer and explain the conflicting requirements. Do not invent availability or claim "
        "a booking succeeded without a submission receipt."
    )
)


def harness_files(harness):
    h = Harness.model_validate(harness)
    return {
        "instructions.md": h.instructions + "\n",
        "workflow.json": json.dumps(
            {
                "validate_before_submit": h.validate_before_submit,
                "calendar_retries": h.calendar_retries,
            },
            indent=2,
        )
        + "\n",
    }


def harness_diff(before, after):
    import difflib

    a, b = harness_files(before), harness_files(after)
    return "".join(
        "".join(
            difflib.unified_diff(
                a[p].splitlines(True),
                b[p].splitlines(True),
                fromfile="before/" + p,
                tofile="after/" + p,
            )
        )
        for p in a
    )
