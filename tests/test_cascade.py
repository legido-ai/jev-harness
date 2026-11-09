from pathlib import Path

import pytest

from jev_harness import Cascade, Ledger, Policy, Rule
from jev_harness.deciders.base import DeciderError
from jev_harness.deciders.mock import KeywordDecider

from conftest import FakeDecider, choice_answer


def build(questions, decision_model=None, fallback=None, ledger=None, policy=None, shadow=False, rules=()):
    return Cascade(
        questions=questions,
        rules=rules,
        policy=policy or Policy(thresholds={"customer": 0.9}, default_threshold=0.9),
        decision_model=decision_model,
        fallback_model=fallback,
        ledger=ledger if ledger is not None else Ledger(),
        primary_question="category",
        shadow=shadow,
    )


def test_rule_layer_wins_without_calling_any_model(questions):
    jev = FakeDecider(choice_answer("category", "customer", 0.99))
    cascade = build(
        questions,
        decision_model=jev,
        rules=[Rule(name="always", action="fraud", value="fraud", when=lambda s: True)],
    )
    decision = cascade.decide("anything")
    assert decision.layer == "code" and decision.value == "fraud"
    assert decision.may_act is True
    assert jev.calls == []


def test_confident_decision_model_acts(questions):
    jev = FakeDecider(choice_answer("category", "customer", 0.97, fits=0.99))
    decision = build(questions, decision_model=jev).decide({"email": "hi"})
    assert decision.layer == "jev"
    assert decision.value == "customer"
    assert decision.threshold == 0.9
    assert decision.may_act is True


def test_low_confidence_escalates_to_fallback_with_hint(questions):
    jev = FakeDecider(choice_answer("category", "customer", 0.55, fits=0.9))
    llm = FakeDecider(choice_answer("category", "fraud", 0.96), model="llm")
    decision = build(questions, decision_model=jev, fallback=llm).decide("state")
    assert decision.layer == "llm" and decision.value == "fraud"
    assert decision.may_act is True
    assert "'customer' with confidence 0.55" in llm.calls[0]["hint"]


def test_both_layers_unsure_goes_to_human(questions):
    jev = FakeDecider(choice_answer("category", "customer", 0.4, fits=0.9))
    llm = FakeDecider({"category": choice_answer("category", "customer", 0.6)["category"]}, model="llm")
    decision = build(questions, decision_model=jev, fallback=llm).decide("state")
    assert decision.layer == "human" and decision.escalated is True
    assert decision.may_act is False


def test_companion_noul_blocks_a_confident_choice(questions):
    """A Choice always returns something; the fits question is what stops a guess."""
    jev = FakeDecider(choice_answer("category", "customer", 0.99, fits=0.2))
    llm = FakeDecider(choice_answer("category", "other", 0.99), model="llm")
    decision = build(questions, decision_model=jev, fallback=llm).decide("state")
    assert decision.layer == "llm"
    assert any("no option fits" in reason for reason in decision.reasons)


def test_shadow_mode_labels_but_never_authorises(questions):
    jev = FakeDecider(choice_answer("category", "customer", 0.99, fits=0.99))
    ledger = Ledger()
    decision = build(questions, decision_model=jev, ledger=ledger, shadow=True).decide("state")
    assert decision.layer == "shadow"
    assert decision.authoritative is False
    assert decision.may_act is False
    records = ledger.records()
    assert records[0]["shadow"] is True and records[0]["value"] == "customer"


def test_decider_failure_becomes_review_not_crash(questions):
    jev = FakeDecider({}, error=DeciderError("upstream 500"))
    decision = build(questions, decision_model=jev).decide("state")
    assert decision.layer == "human" and decision.escalated is True
    assert "decision model unavailable" in decision.reasons[0]


def test_escalation_keeps_the_model_confidence_for_band_analysis(questions):
    """A review record without the model's confidence cannot be banded afterwards."""
    jev = FakeDecider(choice_answer("category", "customer", 0.72, fits=0.9))
    decision = build(questions, decision_model=jev, fallback=None).decide("state")
    assert decision.layer == "human" and decision.escalated is True
    assert decision.confidence == 0.72
    assert decision.may_act is False
    assert decision.answers["category"].choice == "customer"


def test_no_models_means_review(questions):
    decision = build(questions).decide("state")
    assert decision.layer == "human" and decision.escalated is True


def test_ledger_records_cost_and_answers(questions):
    questions_file = Path(__file__).resolve().parents[1] / "examples" / "email_triage.questions.json"
    ledger = Ledger()
    cascade = Cascade(
        questions=questions,
        policy=Policy(),
        decision_model=KeywordDecider(),
        ledger=ledger,
        primary_question="category",
    )
    decision = cascade.decide("I am being charged twice on my invoice and I want a refund")
    assert decision.ledger_id
    record = ledger.decisions()[0]
    assert record["question_set_version"] == 2
    assert record["answers"]["category"]["probabilities"]
    assert record["cost_usd"] >= 0
    assert questions_file.exists()


def test_score_answer_value_is_formatted(questions):
    from jev_harness import Answer

    jev = FakeDecider({"mood": Answer(question_id="mood", type="score", score=1.5, confidence=0.99)})
    cascade = Cascade(questions=questions, decision_model=jev, primary_question="mood", ledger=Ledger())
    decision = cascade.decide("state")
    assert decision.value == "1.500"
