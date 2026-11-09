"""Shared fixtures: a small question set plus programmable stand-in deciders."""

from __future__ import annotations

from typing import Any

import pytest

from jev_harness import Answer, Choice, Noul, QuestionSet, Score
from jev_harness.deciders.base import DeciderError, DeciderResult


def make_questions() -> QuestionSet:
    questions = QuestionSet(name="triage", version=2)
    questions.add(
        Choice(
            id="category",
            instructions="What does the sender want?",
            options={"customer": "asking about an order", "fraud": "payment scam", "other": "none of the above"},
        )
    )
    questions.add(Score(id="mood", instructions="How annoyed is the sender?", levels=["calm", "annoyed", "angry"]))
    questions.add(Noul(id="urgent", instructions="Is this urgent?"))
    return questions


class FakeDecider:
    """Returns scripted answers so cascade routing can be tested in isolation."""

    def __init__(self, answers: dict[str, Answer], model: str = "fake", error: Exception | None = None):
        self.answers = answers
        self.name = model
        self.model = model
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def decide(self, state: Any, questions: QuestionSet, hint: str | None = None) -> DeciderResult:
        self.calls.append({"state": state, "hint": hint})
        if self.error is not None:
            raise self.error
        return DeciderResult(answers=dict(self.answers), model=self.model)


def choice_answer(qid: str, value: str, confidence: float, fits: float | None = None) -> dict[str, Answer]:
    answers = {
        qid: Answer(
            question_id=qid,
            type="choice",
            choice=value,
            confidence=confidence,
            probabilities={value: confidence, "other": max(0.0, 1 - confidence)},
        )
    }
    if fits is not None:
        answers[f"{qid}__fits"] = Answer(question_id=f"{qid}__fits", type="noul", noul=fits)
    return answers


@pytest.fixture
def questions() -> QuestionSet:
    return make_questions()
