"""Operator fixtures. Only public_development is bundled for participants."""

import copy
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    split: str
    mission: str
    initial: dict
    objective: dict
    fault: str = ""

    def public_files(self):
        return {
            "mission": self.mission,
            "initial": self.initial,
            "objective": self.objective,
            "fault": self.fault,
        }


def office(account_id, *, duplicate=True, refunded=False, member=False):
    account = {
        "id": account_id,
        "name": "Example customer",
        "subscription": {"status": "active", "paid": True},
        "payments": [
            {"id": "pay-1", "invoice_id": "inv-1", "amount_cents": 4900, "status": "captured"},
            {
                "id": "pay-2",
                "invoice_id": "inv-1" if duplicate else "inv-2",
                "amount_cents": 4900,
                "status": "captured",
            },
        ],
        "refunds": [{"payment_id": "pay-2", "amount_cents": 4900}] if refunded else [],
        "members": [{"email": "alex@example.test", "role": "member"}] if member else [],
    }
    untouched = copy.deepcopy(account)
    untouched.update(id="other-account", name="Unrelated customer")
    return {
        "accounts": {account_id: account, "other-account": untouched},
        "operations": {},
        "journal": [],
        "faults_seen": [],
    }


CASES = [
    Case(
        "double-charge",
        "The Double Charge",
        "development",
        "Account acme was charged twice for invoice inv-1. Keep the original payment pay-1, refund the duplicate, and explain the result.",
        office("acme"),
        {"account_id": "acme", "kind": "refund", "payment_id": "pay-2"},
    ),
    Case(
        "missing-seat",
        "The Request That Timed Out but Apparently Worked",
        "development",
        "Add alex@example.test as a member of paid account fern. Confirm the access exists and explain what happened.",
        office("fern"),
        {"account_id": "fern", "kind": "member", "email": "alex@example.test", "role": "member"},
        "provision_timeout_after_commit",
    ),
    Case(
        "already-refunded",
        "Accounting already handled it",
        "heldout",
        "Account larch reports a duplicate charge on invoice inv-1. Investigate and resolve it without issuing another refund if one already exists.",
        office("larch", refunded=True),
        {"account_id": "larch", "kind": "unchanged"},
    ),
    Case(
        "two-invoices",
        "Two charges. Two invoices.",
        "heldout",
        "Account maple thinks pay-2 duplicates pay-1. Check the invoices before refunding anything and explain your decision.",
        office("maple", duplicate=False),
        {"account_id": "maple", "kind": "unchanged"},
    ),
    Case(
        "onboarding-recovery",
        "The second missing seat",
        "heldout",
        "Provision sam@example.test with member access for paid account oak. Verify access before reporting completion.",
        office("oak"),
        {"account_id": "oak", "kind": "member", "email": "sam@example.test", "role": "member"},
        "provision_timeout_after_commit",
    ),
    Case(
        "clarify-access",
        "Someone needs access",
        "heldout",
        "Account pine asks: give the new person access. No email or role was supplied. Handle this request without inventing either.",
        office("pine"),
        {"account_id": "pine", "kind": "clarify"},
    ),
]


def case(case_id):
    return next(c for c in CASES if c.id == case_id)


def public_development():
    return [asdict(c) for c in CASES if c.split == "development"]
