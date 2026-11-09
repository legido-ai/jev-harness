import pytest

from jev_harness import Answer, Decision, Ledger, QuestionSet
from jev_harness.evals import band_report, suggest_band
from jev_harness.answers import Usage


def make_decision(layer="jev", confidence=0.99, value="customer", may_act=True) -> Decision:
    return Decision(
        action="customer",
        value=value,
        confidence=confidence,
        threshold=0.9,
        layer=layer,
        answers={"category": Answer(question_id="category", type="choice", choice=value, confidence=confidence)},
        usage=Usage(input_tokens=1000),
        latency_ms=120.0,
        model="jev-1.13.0",
    )


def test_ledger_appends_jsonl(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    questions = QuestionSet(name="triage", version=3)
    ledger.record(decision=make_decision(), state="email text", questions=questions)
    assert path.exists()
    assert len(path.read_text().strip().splitlines()) == 1
    reloaded = Ledger(path)
    assert reloaded.records()[0]["question_set_version"] == 3


def test_outcome_joins_on_latest_record(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    questions = QuestionSet(name="triage", version=1)
    did = ledger.record(decision=make_decision(), state="s", questions=questions)
    ledger.payoff(did, "wrong", note="reviewer disagreed")
    ledger.payoff(did, True)
    decisions = ledger.decisions()
    assert len(decisions) == 1
    assert decisions[0]["outcome"] == "correct"


def test_stats_report_layers_cost_and_accuracy(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    questions = QuestionSet(name="q", version=1)
    for confidence in (0.99, 0.6, 0.4):
        did = ledger.record(decision=make_decision(confidence=confidence), state="s", questions=questions)
        if confidence == 0.99:
            ledger.payoff(did, "correct")
        if confidence == 0.4:
            ledger.payoff(did, "wrong")
    stats = ledger.stats()
    assert stats["decisions"] == 3
    assert stats["reviewed"] == 2
    assert stats["accuracy_on_reviewed"] == 0.5
    assert stats["total_cost_usd"] == pytest.approx(3 * 1000 / 1_000_000 * 0.042)


def test_band_report_buckets_by_confidence(tmp_path):
    ledger = Ledger(tmp_path / "l.jsonl")
    questions = QuestionSet(name="q", version=1)
    for confidence, outcome in [(0.99, "correct"), (0.97, "correct"), (0.6, "wrong"), (0.3, "wrong")]:
        did = ledger.record(decision=make_decision(confidence=confidence), state="s", questions=questions)
        ledger.payoff(did, outcome)
    rows = band_report(ledger.decisions())
    by_band = {row["band"]: row for row in rows}
    assert by_band["[0.95, 1.0)"]["accuracy"] == 1.0
    assert by_band["[0.5, 0.9)"]["accuracy"] == 0.0
    assert by_band["[0, 0.5)"]["decisions"] == 1


def test_suggest_band_needs_samples():
    records = [
        {"kind": "decision", "confidence": 0.99, "outcome": "correct", "may_act": True} for _ in range(25)
    ]
    assert suggest_band(records, min_accuracy=0.98, min_samples=20) == 0.95
    assert suggest_band(records[:5], min_accuracy=0.98, min_samples=20) is None


def test_torn_last_line_does_not_break_reads(tmp_path):
    path = tmp_path / "l.jsonl"
    ledger = Ledger(path)
    ledger.record(decision=make_decision(), state="s", questions=QuestionSet(name="q", version=1))
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{not json")
    assert len(Ledger(path).decisions()) == 1
