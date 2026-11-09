"""HTTP service around the cascade.

Endpoints are thin on purpose; all the decision logic lives in :mod:`jev_harness.cascade`.
Authorization is a single bearer token, because the service is meant to sit behind the
caller's own login rather than re-implement one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from . import __version__
from .answers import Decision
from .cascade import Cascade
from .deciders.base import AuthError, DeciderError
from .deciders.llm import LLMDecider
from .deciders.mock import KeywordDecider
from .deciders.typesafe import TypeSafeDecider
from .evals import band_report
from .ledger import Ledger
from .policy import Policy
from .primitives import QuestionError, QuestionSet
from .spec import load_question_set

__all__ = ["app", "build_app", "build_cascade"]


class DecideRequest(BaseModel):
    state: Any = Field(..., description="text or structured state the questions are about")
    action: str | None = Field(None, description="policy key overriding the model's own choice")
    meta: dict[str, Any] | None = None


class FeedbackRequest(BaseModel):
    ledger_id: str
    outcome: str | bool
    note: str = ""


def _policy_from_env(policy_path: str | None) -> Policy:
    if policy_path and Path(policy_path).exists():
        import json

        raw = json.loads(Path(policy_path).read_text(encoding="utf-8"))
        return Policy(
            thresholds={str(k): float(v) for k, v in (raw.get("thresholds") or {}).items()},
            default_threshold=float(raw.get("default_threshold", 0.90)),
            review_floor=float(raw.get("review_floor", 0.50)),
            destructive=raw.get("destructive") or (),
            destructive_threshold=float(raw.get("destructive_threshold", 0.95)),
        )
    raw_thresholds = os.environ.get("POLICY_THRESHOLDS")
    thresholds: dict[str, float] = {}
    if raw_thresholds:
        import json

        thresholds = {str(k): float(v) for k, v in json.loads(raw_thresholds).items()}
    return Policy(
        thresholds=thresholds,
        default_threshold=float(os.environ.get("POLICY_DEFAULT_THRESHOLD", 0.90)),
        destructive=os.environ.get("POLICY_DESTRUCTIVE", "").split(",") if os.environ.get("POLICY_DESTRUCTIVE") else (),
    )


def build_cascade(
    questions: QuestionSet | None = None,
    primary: str | None = None,
    *,
    ledger: Ledger | None = None,
    policy: Policy | None = None,
    shadow: bool | None = None,
) -> Cascade:
    """Assemble a cascade from the environment, tolerating a missing decision model."""
    spec_path = os.environ.get("QUESTIONS_PATH")
    if questions is None:
        if not spec_path:
            raise QuestionError("QUESTIONS_PATH must point at a question-set spec")
        questions, primary = load_question_set(spec_path)
    if ledger is None and os.environ.get("LEDGER_PATH"):
        ledger = Ledger(os.environ["LEDGER_PATH"])
    decision_model = None
    if os.environ.get("TYPESAFE_API_KEY"):
        try:
            decision_model = TypeSafeDecider(model=os.environ.get("TYPESAFE_MODEL"))
        except AuthError:
            decision_model = None
    elif os.environ.get("DECIDER") == "keyword":
        decision_model = KeywordDecider()
    fallback = None
    if os.environ.get("ENABLE_LLM_FALLBACK", "1") == "1":
        try:
            fallback = LLMDecider()
        except DeciderError:
            fallback = None
    return Cascade(
        questions=questions,
        policy=policy or _policy_from_env(os.environ.get("POLICY_PATH")),
        decision_model=decision_model,
        fallback_model=fallback,
        ledger=ledger,
        primary_question=primary,
        shadow=bool(shadow if shadow is not None else os.environ.get("SHADOW") == "1"),
    )


def build_app(cascade: Cascade | None = None) -> FastAPI:
    app = FastAPI(title="jev-harness", version=__version__,
                  description="Typed decisions with calibrated confidence, in a cascade.")
    state: dict[str, Any] = {"cascade": cascade}

    def get_cascade() -> Cascade:
        if state["cascade"] is None:
            state["cascade"] = build_cascade()
        return state["cascade"]

    def authorise(authorization: str | None) -> None:
        expected = os.environ.get("HARNESS_TOKEN")
        if not expected:
            return
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        import hmac

        if not hmac.compare_digest(authorization.split(" ", 1)[1].strip(), expected):
            raise HTTPException(status_code=403, detail="bad token")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        """Report the wiring, not just liveness: a service with no layers is healthy and useless."""
        try:
            cascade_obj = get_cascade()
        except Exception as exc:  # misconfiguration must be visible, not a 500
            return {"status": "degraded", "version": __version__, "error": f"{type(exc).__name__}: {exc}"}
        questions = getattr(cascade_obj, "questions", None)
        return {
            "status": "ok",
            "version": __version__,
            "question_set": getattr(questions, "name", None),
            "question_set_version": getattr(questions, "version", None),
            "decision_model": getattr(getattr(cascade_obj, "decision_model", None), "name", None),
            "fallback_model": getattr(getattr(cascade_obj, "fallback_model", None), "name", None),
            "shadow": getattr(cascade_obj, "shadow", None),
        }

    @app.get("/v1/questions")
    def questions(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorise(authorization)
        cascade_obj = get_cascade()
        return {
            "name": cascade_obj.questions.name,
            "version": cascade_obj.questions.version,
            "primary": cascade_obj.primary_question,
            "questions": cascade_obj.questions.payload(),
        }

    @app.post("/v1/decide")
    def decide(request: DecideRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorise(authorization)
        cascade_obj = get_cascade()
        decision: Decision = cascade_obj.decide(request.state, action=request.action, meta=request.meta)
        return decision.to_dict()

    @app.post("/v1/feedback")
    def feedback(request: FeedbackRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorise(authorization)
        cascade_obj = get_cascade()
        if cascade_obj.ledger is None:
            raise HTTPException(status_code=400, detail="ledger is not configured")
        return cascade_obj.ledger.payoff(request.ledger_id, request.outcome, note=request.note)

    @app.get("/v1/stats")
    def stats(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorise(authorization)
        cascade_obj = get_cascade()
        if cascade_obj.ledger is None:
            raise HTTPException(status_code=400, detail="ledger is not configured")
        return cascade_obj.ledger.stats()

    @app.get("/v1/bands")
    def bands(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorise(authorization)
        cascade_obj = get_cascade()
        if cascade_obj.ledger is None:
            raise HTTPException(status_code=400, detail="ledger is not configured")
        return {"bands": band_report(cascade_obj.ledger.decisions())}

    return app


app = build_app()
