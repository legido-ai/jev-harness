"""Typed answers and the shape of a decision."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

__all__ = ["Answer", "AnswerError", "Usage", "Decision", "PRICE_PER_MILLION_INPUT_USD"]

# Decision models are priced per input token and generate no text.
PRICE_PER_MILLION_INPUT_USD = 0.042


class AnswerError(ValueError):
    """Raised when a model returns an answer that does not fit the question."""


@dataclass
class Answer:
    """One answer to one typed question."""

    question_id: str
    type: str
    choice: str | None = None
    score: float | None = None
    noul: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)
    legend: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """Short human label for logs and review queues."""
        if self.type == "choice":
            return str(self.choice)
        if self.type == "score":
            return f"{self.score:.2f}"
        if self.type == "noul":
            return f"p={self.noul:.3f}" if self.noul is not None else "p=?"
        return self.type

    def top_two(self) -> list[tuple[str, float]]:
        return sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)[:2]

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "choice": self.choice,
            "score": self.score,
            "noul": self.noul,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
            "legend": dict(self.legend),
        }

    @classmethod
    def from_payload(cls, question_id: str, payload: Mapping[str, Any]) -> "Answer":
        qtype = str(payload.get("type") or "")
        probabilities = {
            str(k): float(v) for k, v in (payload.get("probabilities") or {}).items()
        }
        confidence = payload.get("confidence")
        answer = cls(
            question_id=question_id,
            type=qtype,
            choice=payload.get("choice"),
            score=payload.get("score"),
            noul=payload.get("noul"),
            confidence=float(confidence) if confidence is not None else None,
            probabilities=probabilities,
            legend={str(k): str(v) for k, v in (payload.get("legend") or {}).items()},
            raw=dict(payload),
        )
        answer.validate()
        return answer

    def validate(self) -> None:
        if self.type == "choice":
            if not self.choice:
                raise AnswerError(f"{self.question_id}: choice answer without a choice")
            if self.probabilities and self.choice not in self.probabilities:
                raise AnswerError(
                    f"{self.question_id}: choice {self.choice!r} missing from probabilities"
                )
        elif self.type == "score":
            if self.score is None:
                raise AnswerError(f"{self.question_id}: score answer without a score")
        elif self.type == "noul":
            if self.noul is None:
                raise AnswerError(f"{self.question_id}: noul answer without a probability")
        # An unknown type is tolerated: a newer provider type must not crash a caller.


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0

    def cost_usd(self) -> float:
        return self.input_tokens / 1_000_000 * PRICE_PER_MILLION_INPUT_USD

    def to_dict(self) -> dict[str, int]:
        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}


@dataclass
class Decision:
    """The outcome of a cascade run."""

    action: str
    value: str | None
    confidence: float | None
    threshold: float
    layer: str
    authoritative: bool = True
    escalated: bool = False
    reasons: list[str] = field(default_factory=list)
    answers: dict[str, Answer] = field(default_factory=dict)
    secondary: dict[str, Any] | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: float = 0.0
    model: str | None = None
    ledger_id: str | None = None

    @property
    def may_act(self) -> bool:
        return (
            self.authoritative
            and not self.escalated
            and self.confidence is not None
            and self.confidence >= self.threshold
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "action": self.action,
            "value": self.value,
            "confidence": self.confidence,
            "threshold": self.threshold,
            "layer": self.layer,
            "authoritative": self.authoritative,
            "escalated": self.escalated,
            "may_act": self.may_act,
            "reasons": list(self.reasons),
            "model": self.model,
            "latency_ms": round(self.latency_ms, 1),
            "cost_usd": round(self.usage.cost_usd(), 8),
            "usage": self.usage.to_dict(),
            "answers": {qid: a.to_dict() for qid, a in self.answers.items()},
            "secondary": self.secondary,
        }
