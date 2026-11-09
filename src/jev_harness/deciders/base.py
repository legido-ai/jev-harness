"""Decider interface: anything that can answer typed questions about a state."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..answers import Answer, Usage
from ..primitives import QuestionSet

__all__ = ["DeciderResult", "Decider", "DeciderError", "AuthError"]


class DeciderError(RuntimeError):
    """A decider could not produce answers (bad status, unparseable output)."""


class AuthError(DeciderError):
    """Credentials were rejected. Not retryable."""


@dataclass
class DeciderResult:
    answers: dict[str, Answer] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_ms: float = 0.0


@runtime_checkable
class Decider(Protocol):
    name: str

    def decide(
        self, state: Any, questions: QuestionSet, hint: str | None = None
    ) -> DeciderResult:
        """Answer every question about ``state``.

        ``hint`` carries an earlier layer's guess so the fallback can second-guess it
        instead of starting blind.
        """
        ...
