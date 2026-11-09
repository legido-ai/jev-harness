"""The four-layer cascade.

    code rule  ->  decision model  ->  chat model  ->  human

Each layer is cheaper and more certain than the next, so a decision stops at the first
layer that is sure. Two invariants are enforced here rather than trusted:

* an answer whose companion Noul says "no option really fits" never acts, however
  confident the Choice looked;
* a decision without a confidence never acts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Sequence

from .answers import Answer, Decision
from .deciders.base import Decider, DeciderError
from .ledger import Ledger
from .policy import Policy
from .primitives import QuestionSet
from .rules import Rule

__all__ = ["Cascade", "CascadeResult"]

FITS_SUFFIX = "__fits"
FITS_MIN = 0.5


@dataclass
class CascadeResult:
    decision: Decision
    state: Any = None
    questions: QuestionSet | None = None


@dataclass
class Cascade:
    questions: QuestionSet
    policy: Policy = field(default_factory=Policy)
    decision_model: Decider | None = None
    fallback_model: Decider | None = None
    ledger: Ledger | None = None
    rules: Sequence[Rule] = ()
    primary_question: str | None = None
    shadow: bool = False

    # -- helpers ----------------------------------------------------------
    def _primary_id(self, questions: QuestionSet) -> str:
        if self.primary_question:
            return self.primary_question
        ids = [qid for qid in questions.with_companions().questions if not qid.endswith(FITS_SUFFIX)]
        if not ids:
            raise ValueError("question set is empty")
        return ids[0]

    @staticmethod
    def _value_of(answer: Answer) -> str | None:
        if answer.type == "choice":
            return answer.choice
        if answer.type == "score":
            return f"{answer.score:.3f}" if answer.score is not None else None
        if answer.type == "noul":
            return f"{answer.noul:.4f}" if answer.noul is not None else None
        return None

    def _fits_noul(self, answers: dict[str, Answer], primary_id: str) -> Answer | None:
        return answers.get(f"{primary_id}{FITS_SUFFIX}")

    # -- run --------------------------------------------------------------
    def decide(self, state: Any, *, action: str | None = None, meta: dict[str, Any] | None = None) -> Decision:
        questions = self.questions.with_companions()
        started = time.perf_counter()
        value: str | None = None
        confidence: float | None = None
        action_key = action or "default"
        reasons: list[str] = []
        collected: dict[str, Answer] = {}

        # Layer 0 - code.
        for rule in self.rules:
            if rule.matches(state):
                decision = Decision(
                    action=action or rule.action,
                    value=rule.value,
                    confidence=1.0,
                    threshold=self.policy.threshold_for(action or rule.action),
                    layer="code",
                    reasons=[f"rule:{rule.name}"] + ([rule.reason] if rule.reason else []),
                    model="rule",
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
                self._log(decision, state, questions, meta)
                return decision

        # Layer 1 - decision model.
        if self.decision_model is not None:
            primary_id = self._primary_id(questions)
            try:
                result = self.decision_model.decide(state, questions)
            except DeciderError as exc:
                return self._escalate_unavailable(state, questions, action, exc, meta)
            answer = result.answers.get(primary_id)
            if answer is None:
                return self._human(state, questions, action, answers=result.answers,
                                   reasons=["decision model did not answer the primary question"],
                                   meta=meta, started=started, model=result.model)
            confidence = answer.confidence
            action_key = action or (answer.choice if answer.type == "choice" else None) or "default"
            threshold = self.policy.threshold_for(action_key)
            value = self._value_of(answer)
            reasons: list[str] = []

            fits = self._fits_noul(result.answers, primary_id)
            fits_ok = True
            if fits is not None and fits.noul is not None and fits.noul < FITS_MIN:
                fits_ok = False
                reasons.append(f"no option fits (p={fits.noul:.2f})")
            if confidence is None:
                reasons.append("no confidence reported")

            decision = Decision(
                action=action_key,
                value=value,
                confidence=confidence,
                threshold=threshold,
                layer="jev",
                answers=dict(result.answers),
                model=result.model,
                latency_ms=result.latency_ms,
                reasons=reasons,
            )
            decision.usage = result.usage
            collected.update(result.answers)

            if self.shadow:
                decision.layer = "shadow"
                decision.authoritative = False
                decision.reasons.append("shadow mode: label only, the legacy path decides")
                self._log(decision, state, questions, meta, shadow=True)
                return decision

            if fits_ok and confidence is not None and confidence >= threshold:
                decision.reasons.append("above threshold")
                self._log(decision, state, questions, meta)
                return decision

            reasons.append(f"below threshold {threshold:.2f}")

        # Layer 2 - chat model on the hard cases.
        if self.fallback_model is not None:
            primary_id = self._primary_id(questions)
            hint = None
            if self.decision_model is not None:
                hint = f"'{value}' with confidence {confidence:.2f}" if confidence is not None else None
            try:
                result = self.fallback_model.decide(state, questions, hint=hint)
            except DeciderError as exc:
                return self._human(
                    state, questions, action,
                    answers={},
                    reasons=reasons + [f"fallback unavailable: {exc}"],
                    meta=meta, started=started, model=None,
                )
            answer = result.answers.get(primary_id)
            if answer is not None:
                action_key = action or (answer.choice if answer.type == "choice" else None) or "default"
                threshold = self.policy.threshold_for(action_key)
                confidence_llm = answer.confidence
                value_llm = self._value_of(answer)
                collected.update(result.answers)
                secondary = {
                    "llm": {"value": value_llm, "confidence": confidence_llm, "model": result.model},
                }
                if value is not None:
                    secondary["jev"] = {"value": value, "confidence": confidence}
                decision = Decision(
                    action=action_key,
                    value=value_llm,
                    confidence=confidence_llm,
                    threshold=threshold,
                    layer="llm",
                    answers=dict(collected),
                    model=result.model,
                    latency_ms=result.latency_ms,
                    reasons=reasons,
                    secondary=secondary,
                )
                decision.usage = result.usage
                if confidence_llm is not None and confidence_llm >= threshold:
                    decision.reasons.append("llm above threshold")
                    self._log(decision, state, questions, meta)
                    return decision
                # Both models are unsure: hand the reviewer both answers side by side,
                # which is the fastest review there is.
                decision.reasons.append("llm also below threshold")
                decision.escalated = True
                decision.layer = "human"
                decision.value = None
                decision.authoritative = False
                self._log(decision, state, questions, meta)
                return decision

        return self._human(
            state, questions, action,
            answers=collected,
            reasons=reasons or ["no layer could decide"],
            meta=meta, started=started, model=None,
        )

    # -- outcomes ---------------------------------------------------------
    def _human(
        self,
        state: Any,
        questions: QuestionSet,
        action: str | None,
        *,
        answers: dict[str, Answer],
        reasons: list[str],
        meta: dict[str, Any] | None,
        started: float,
        model: str | None,
    ) -> Decision:
        decision = Decision(
            action=action or "review",
            value=None,
            confidence=None,
            threshold=self.policy.threshold_for(action or "review"),
            layer="human",
            escalated=True,
            authoritative=False,
            reasons=reasons,
            answers=answers,
            model=model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )
        self._log(decision, state, questions, meta)
        return decision

    def _escalate_unavailable(
        self,
        state: Any,
        questions: QuestionSet,
        action: str | None,
        exc: Exception,
        meta: dict[str, Any] | None,
    ) -> Decision:
        return self._human(
            state, questions, action,
            answers={},
            reasons=[f"decision model unavailable: {type(exc).__name__}: {exc}"],
            meta=meta, started=time.perf_counter(), model=None,
        )

    def _log(
        self,
        decision: Decision,
        state: Any,
        questions: QuestionSet,
        meta: dict[str, Any] | None,
        shadow: bool = False,
    ) -> None:
        if self.ledger is not None:
            decision.ledger_id = self.ledger.record(
                decision=decision, state=state, questions=questions, shadow=shadow, meta=meta
            )
