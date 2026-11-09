"""Typed questions for a System One decision model.

A decision model answers *typed* questions about a state. There are three primitives:

* :class:`Choice` - which of these options?
* :class:`Score`  - which level on this ordered rubric?
* :class:`Noul`   - is this statement true? (probability, no confidence)

The rules encoded here come from the model provider's documentation, because violating
them silently degrades accuracy instead of raising:

1. The question id is never sent to the model, so the meaning has to live in
   ``instructions``.
2. One judgment per question. Two judgments in one sentence produce an answer to
   neither.
3. A Choice always returns something, so when the option list may not cover the state
   you need a *companion Noul* asking whether any option fits at all
   (:meth:`Choice.companion_noul`). An explicit ``other`` option competes with the real
   options for probability; a companion question does not.
4. A Noul must be phrased so that a high number means *yes*.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = ["Question", "Choice", "Score", "Noul", "QuestionSet", "QuestionError"]


class QuestionError(ValueError):
    """Raised when a question is malformed."""


@dataclass(frozen=True)
class Question:
    """Base class for a typed question."""

    id: str
    instructions: str
    type: str = field(default="", init=False)

    def to_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def sub_questions(self) -> list["Question"]:
        """Extra questions the primitive needs in the same request, if any."""
        return []


@dataclass(frozen=True)
class Choice(Question):
    """Pick one option from a closed set."""

    options: Mapping[str, str]
    type: str = field(default="choice", init=False)

    def __post_init__(self) -> None:
        if not self.options:
            raise QuestionError(f"Choice {self.id!r} needs at least one option")
        if len(self.options) == 1:
            raise QuestionError(f"Choice {self.id!r} with one option is not a question")
        for key in self.options:
            if not isinstance(key, str) or not key:
                raise QuestionError(f"Choice {self.id!r} has an invalid option key")
            if key != key.strip():
                raise QuestionError(f"Choice {self.id!r} option {key!r} has stray whitespace")

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self.instructions,
            "criteria": dict(self.options),
        }

    def companion_noul(self) -> "Noul":
        """A Noul asking whether *any* option actually fits the state."""
        listed = "; ".join(f"{k}: {v}" for k, v in self.options.items())
        return Noul(
            id=f"{self.id}__fits",
            instructions=(
                "Does at least one of the options listed in `criteria` describe the "
                f"state, as opposed to none of them fitting? Options: {listed}"
            ),
            criteria=(
                "Yes means one of the listed options is a fair description of the state. "
                "No means the state is about something else entirely and the closest "
                "option would be a guess."
            ),
        )


@dataclass(frozen=True)
class Score(Question):
    """Place the state on an ordered rubric.

    Levels are judged independently, so every level must be understandable on its own.
    """

    levels: Sequence[str]
    type: str = field(default="score", init=False)

    def __post_init__(self) -> None:
        if len(self.levels) < 2:
            raise QuestionError(f"Score {self.id!r} needs at least two levels")
        for level in self.levels:
            if not isinstance(level, str) or not level.strip():
                raise QuestionError(f"Score {self.id!r} has an empty level")

    def to_payload(self) -> dict[str, Any]:
        return {
            "type": "score",
            "instructions": self.instructions,
            "criteria": list(self.levels),
        }

    def legend(self) -> dict[str, str]:
        return {str(i): level for i, level in enumerate(self.levels)}


@dataclass(frozen=True)
class Noul(Question):
    """A yes/no judgment where the probability is the answer."""

    criteria: str | None = None
    type: str = field(default="noul", init=False)

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"type": "noul", "instructions": self.instructions}
        if self.criteria:
            payload["criteria"] = self.criteria
        return payload


@dataclass
class QuestionSet:
    """A named, versioned bundle of questions sent in one request.

    Questions that share a state belong in one call: the state is sent once, which is
    where most of the cost and latency of a decision model is spent.
    """

    name: str
    version: int = 1
    questions: dict[str, Question] = field(default_factory=dict)
    add_companion_nouls: bool = True

    def add(self, question: Question) -> "QuestionSet":
        if question.id in self.questions:
            raise QuestionError(f"duplicate question id {question.id!r}")
        self.questions[question.id] = question
        return self

    def extend(self, questions: Iterable[Question]) -> "QuestionSet":
        for question in questions:
            self.add(question)
        return self

    def with_companions(self) -> "QuestionSet":
        """Return a copy where every Choice carries its companion Noul."""
        clone = QuestionSet(self.name, self.version, dict(self.questions), self.add_companion_nouls)
        if self.add_companion_nouls:
            for question in list(self.questions.values()):
                if isinstance(question, Choice):
                    companion = question.companion_noul()
                    if companion.id not in clone.questions:
                        clone.questions[companion.id] = companion
        return clone

    def ids(self) -> list[str]:
        return list(self.with_companions().questions)

    def payload(self) -> dict[str, dict[str, Any]]:
        return {qid: q.to_payload() for qid, q in self.with_companions().questions.items()}

    def bump(self) -> "QuestionSet":
        """New version, same questions.

        Changing criteria changes production behaviour, so the version is part of the
        ledger record: thresholds tuned against version N mean nothing for version N+1.
        """
        return QuestionSet(self.name, self.version + 1, dict(self.questions), self.add_companion_nouls)
