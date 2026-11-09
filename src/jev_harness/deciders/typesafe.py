"""Client for a TypeSafe / Jev System One endpoint (plain HTTP, no SDK required)."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from ..answers import Answer, Usage
from ..primitives import QuestionSet
from .base import AuthError, DeciderError, DeciderResult

__all__ = ["TypeSafeDecider"]

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-1.13.0"
RETRY_STATUS = {408, 425, 429, 500, 502, 503, 504}


class TypeSafeDecider:
    """Calls ``POST {base_url}/v1/systemone``.

    The model id is pinned on purpose: pinning is what keeps a confidence threshold
    meaningful between weeks, and a floating alias re-tunes the system behind your back.
    """

    name = "jev"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ) -> None:
        key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        if not key:
            raise AuthError("TYPESAFE_API_KEY is not set")
        self.api_key = key
        self.base_url = (base_url or os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.model = model or os.environ.get("TYPESAFE_MODEL") or DEFAULT_MODEL
        self.timeout = timeout
        self.max_retries = max_retries
        self._client = client or httpx.Client(timeout=timeout)

    def payload(self, state: Any, questions: QuestionSet) -> dict[str, Any]:
        return {"model": self.model, "state": state, "questions": questions.payload()}

    def decide(self, state: Any, questions: QuestionSet) -> DeciderResult:
        body = self.payload(state, questions)
        started = time.perf_counter()
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._client.post(
                    f"{self.base_url}/v1/systemone",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                        "User-Agent": "jev-harness/0.1",
                    },
                    json=body,
                )
            except httpx.HTTPError as exc:  # network, DNS, timeout
                last_error = exc
                if attempt == self.max_retries:
                    raise DeciderError(f"transport error: {exc}") from exc
                time.sleep(0.4 * (attempt + 1))
                continue

            if response.status_code in (401, 403):
                raise AuthError(
                    "the decision-model endpoint rejected the credential "
                    f"({response.status_code}); check TYPESAFE_API_KEY"
                )
            if response.status_code in RETRY_STATUS and attempt < self.max_retries:
                last_error = DeciderError(f"retryable status {response.status_code}")
                time.sleep(0.4 * (attempt + 1))
                continue
            if response.status_code >= 400:
                raise DeciderError(
                    f"decision-model error {response.status_code}: {response.text[:300]}"
                )

            data = response.json()
            raw_answers = data.get("answers") or {}
            answers = {
                qid: Answer.from_payload(qid, payload) for qid, payload in raw_answers.items()
            }
            usage_payload = data.get("usage") or {}
            usage = Usage(
                input_tokens=int(usage_payload.get("input_tokens") or 0),
                output_tokens=int(usage_payload.get("output_tokens") or 0),
            )
            return DeciderResult(
                answers=answers,
                usage=usage,
                model=str(data.get("model") or self.model),
                latency_ms=(time.perf_counter() - started) * 1000,
            )

        raise DeciderError(f"no answer after retries: {last_error}")
