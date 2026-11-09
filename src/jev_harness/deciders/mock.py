"""Deterministic offline decider.

This is **not a model**. It is a keyword-overlap stand-in used by the test suite, by CI
and by the offline demo so the harness can be exercised without credentials. It returns
answers in the same shape as a real decision model, with probabilities derived from word
overlap between the state and each option's description, which makes its behaviour
reproducible and its confidence interpretable ("how much of the option's wording is
present in the state").

Never report its numbers as model quality.
"""

from __future__ import annotations

import re
import time
from typing import Any

from ..answers import Answer, Usage
from ..primitives import Choice, Noul, QuestionSet, Score
from .base import DeciderResult

__all__ = ["KeywordDecider"]

WORD = re.compile(r"[a-záéíóúüñ0-9]+", re.IGNORECASE)
STOP = {
    "the", "a", "an", "of", "to", "and", "or", "is", "are", "be", "in", "on", "for",
    "that", "this", "with", "as", "at", "by", "it", "not", "does", "do", "what",
    "which", "el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "es",
}


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in WORD.findall(text) if w.lower() not in STOP and len(w) > 2}


class KeywordDecider:
    name = "keyword-offline"

    def __init__(self, threshold: float = 0.6) -> None:
        self.threshold = threshold

    def _score_option(self, state_tokens: set[str], description: str) -> float:
        option_tokens = _tokens(description)
        if not option_tokens:
            return 0.0
        overlap = len(state_tokens & option_tokens) / len(option_tokens)
        return round(overlap, 4)

    def decide(self, state: Any, questions: QuestionSet, hint: str | None = None) -> DeciderResult:
        started = time.perf_counter()
        text = state if isinstance(state, str) else str(state)
        state_tokens = _tokens(text)
        answers: dict[str, Answer] = {}

        for qid, question in questions.with_companions().questions.items():
            if isinstance(question, Choice):
                raw = {k: self._score_option(state_tokens, v) for k, v in question.options.items()}
                total = sum(raw.values())
                if total <= 0:
                    probabilities = {k: 1.0 / len(raw) for k in raw}
                else:
                    probabilities = {k: v / total for k, v in raw.items()}
                best = max(probabilities, key=lambda k: probabilities[k])
                size = len(probabilities)
                confidence = round(max(0.0, (size * probabilities[best] - 1) / (size - 1)), 4) if size > 1 else 1.0
                answers[qid] = Answer(
                    question_id=qid, type="choice", choice=best,
                    confidence=confidence, probabilities=probabilities,
                )
            elif isinstance(question, Score):
                levels = [
                    self._score_option(state_tokens, level) for level in question.levels
                ]
                total = sum(levels)
                probabilities = (
                    {str(i): v / total for i, v in enumerate(levels)}
                    if total > 0
                    else {str(i): 1.0 / len(levels) for i in range(len(levels))}
                )
                position = sum(i * p for i, p in ((int(k), v) for k, v in probabilities.items()))
                size = len(levels)
                confidence = round(max(0.0, (size * max(probabilities.values()) - 1) / (size - 1)), 4)
                answers[qid] = Answer(
                    question_id=qid, type="score", score=round(position, 4),
                    confidence=confidence, probabilities=probabilities, legend=question.legend(),
                )
            elif isinstance(question, Noul):
                description = question.criteria or question.instructions
                probability = self._score_option(state_tokens, description)
                answers[qid] = Answer(
                    question_id=qid, type="noul", noul=round(min(1.0, probability), 4)
                )

        return DeciderResult(
            answers=answers,
            usage=Usage(input_tokens=max(1, len(state_tokens))),
            model=self.name,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
