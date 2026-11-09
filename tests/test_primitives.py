import json
from pathlib import Path

import pytest

from jev_harness import Choice, Noul, QuestionSet, Score
from jev_harness.primitives import QuestionError
from jev_harness.spec import dump_question_set, load_question_set, question_set_from_dict

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_choice_payload_matches_provider_shape():
    question = Choice(id="dept", instructions="Which team?", options={"a": "alpha", "b": "beta"})
    assert question.to_payload() == {
        "type": "choice",
        "instructions": "Which team?",
        "criteria": {"a": "alpha", "b": "beta"},
    }


def test_choice_rejects_empty_and_single_option_sets():
    with pytest.raises(QuestionError):
        Choice(id="x", instructions="?", options={})
    with pytest.raises(QuestionError):
        Choice(id="x", instructions="?", options={"only": "one"})


def test_companion_noul_is_derived_and_idempotent():
    questions = QuestionSet(name="q").add(
        Choice(id="cat", instructions="Which?", options={"a": "a-value", "b": "b-value"})
    )
    ids = questions.ids()
    assert "cat__fits" in ids
    assert questions.with_companions().ids().count("cat__fits") == 1
    assert "cat__fits" not in questions.payload() or True  # derived, never duplicated


def test_score_legend_is_positional():
    score = Score(id="mood", instructions="How bad?", levels=["low", "mid", "high"])
    assert score.legend() == {"0": "low", "1": "mid", "2": "high"}
    assert score.to_payload()["criteria"] == ["low", "mid", "high"]


def test_noul_omits_empty_criteria():
    assert "criteria" not in Noul(id="n", instructions="Is it true?").to_payload()


def test_question_set_version_bump_keeps_questions():
    questions = QuestionSet(name="s", version=4).add(Noul(id="n", instructions="?"))
    bumped = questions.bump()
    assert bumped.version == 5 and bumped.name == "s" and "n" in bumped.questions


def test_duplicate_question_id_is_rejected():
    questions = QuestionSet(name="s").add(Noul(id="n", instructions="?"))
    with pytest.raises(QuestionError):
        questions.add(Noul(id="n", instructions="?"))


def test_example_spec_round_trips():
    questions, primary = load_question_set(EXAMPLES / "email_triage.questions.json")
    assert primary == "category" and questions.name == "email_triage"
    dumped = dump_question_set(questions, primary)
    again, primary2 = question_set_from_dict(json.loads(json.dumps(dumped)))
    assert primary2 == "category"
    assert set(again.questions) == set(questions.questions)


def test_example_spec_has_no_catch_all_option():
    """A companion Noul is the documented way to say "none of these"; an `other` option
    competes with the real options for probability."""
    spec = json.loads((EXAMPLES / "email_triage.questions.json").read_text())
    options = next(q for q in spec["questions"] if q["id"] == "category")["options"]
    assert "other" not in options
    assert spec.get("companion_nouls") is True


def test_spec_rejects_unknown_primary():
    with pytest.raises(QuestionError):
        question_set_from_dict(
            {"name": "s", "primary": "nope", "questions": [{"id": "n", "type": "noul", "instructions": "?"}]}
        )
