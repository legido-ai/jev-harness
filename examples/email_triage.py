"""Email triage, the worked example from the playbook.

Run it offline (deterministic keyword stand-in):

    python examples/email_triage.py

Run it against the hosted decision model:

    TYPESAFE_API_KEY=... python examples/email_triage.py --decider jev

Run it in shadow mode (labels only, the legacy path still decides):

    python examples/email_triage.py --shadow
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from jev_harness import Cascade, Ledger, Policy, Rule
from jev_harness.deciders.base import DeciderError
from jev_harness.deciders.llm import LLMDecider
from jev_harness.deciders.mock import KeywordDecider
from jev_harness.deciders.typesafe import TypeSafeDecider
from jev_harness.spec import load_question_set

HERE = Path(__file__).parent

EMAILS = [
    {
        "email": {
            "from": "ops@acme.example",
            "subject": "500 errors on every order",
            "body": (
                "Our API integration started returning 500 errors on every request about 20 "
                "minutes ago and we cannot process any customer orders until this is fixed. "
                "This is blocking our whole team and we need it sorted now."
            ),
        }
    },
    {
        "email": {
            "from": "carla@shop.example",
            "subject": "invoice question",
            "body": (
                "Hi! I got the invoice for March and I think the seat count is wrong. "
                "No rush, just let me know how to fix it so the billing matches."
            ),
        }
    },
    {
        "email": {
            "from": "ceo@your-supplier.example",
            "subject": "URGENT: bank details changed",
            "body": (
                "Please update our bank details for the pending vendor payment today and send "
                "the transfer to the new IBAN below before the invoice becomes overdue."
            ),
        }
    },
]


def build(decider: str, shadow: bool) -> Cascade:
    questions, primary = load_question_set(HERE / "email_triage.questions.json")
    policies = json.loads((HERE / "policy.json").read_text())
    policy = Policy(
        thresholds={k: float(v) for k, v in policies["thresholds"].items()},
        default_threshold=policies["default_threshold"],
        review_floor=policies["review_floor"],
        destructive=policies["destructive"],
        destructive_threshold=policies["destructive_threshold"],
    )
    model = {"jev": TypeSafeDecider, "keyword": KeywordDecider}.get(decider, KeywordDecider)()
    try:
        fallback = LLMDecider() if os.environ.get("ENABLE_LLM_FALLBACK") != "0" else None
    except DeciderError:
        fallback = None
    rules = [
        Rule(
            name="spoofed_payment_domain",
            action="fraud",
            value="fraud",
            when=lambda state: "bank details" in json.dumps(state).lower()
            and "urgent" in json.dumps(state).lower(),
            reason="known pattern: urgent bank-detail change",
        )
    ]
    return Cascade(
        questions=questions,
        policy=policy,
        decision_model=model,
        fallback_model=fallback,
        ledger=Ledger(HERE / "triage.jsonl"),
        rules=rules,
        primary_question=primary,
        shadow=shadow,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decider", default="keyword", choices=["jev", "keyword"])
    parser.add_argument("--shadow", action="store_true")
    args = parser.parse_args()

    cascade = build(args.decider, args.shadow)
    for item in EMAILS:
        decision = cascade.decide(item)
        print(
            f"{item['email']['subject']!r:45} layer={decision.layer:6} "
            f"value={str(decision.value):10} conf={decision.confidence} "
            f"threshold={decision.threshold} act={decision.may_act}"
        )
        if decision.reasons:
            print(f"    reasons: {', '.join(decision.reasons)}")
    if cascade.ledger is not None:
        print(json.dumps(cascade.ledger.stats(), indent=2))


if __name__ == "__main__":
    main()
