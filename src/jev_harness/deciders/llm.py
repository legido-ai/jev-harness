"""LLM fallback decider: the second layer of the cascade.

The point of the cascade is that a hosted chat model only sees the cases the decision
model was unsure about, so this decider is deliberately boring: one request, JSON out,
validate before use.

Two failure modes are handled explicitly because they are silent otherwise:

* reasoning models emit thinking tokens first and can spend the whole token budget
  there, returning an empty ``content``. An empty answer is a failed turn, not a
  low-confidence one, so it is retried once with a bigger budget and then raised.
* a chat model will happily name an option that is not in the list. Anything outside
  the closed set is rejected here rather than downstream.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx

from ..answers import Answer, Usage
from ..primitives import Choice, Noul, QuestionSet, Score
from .base import DeciderError, DeciderResult

__all__ = ["LLMDecider"]

SYSTEM_PROMPT = (
    "You are a decision layer inside a larger program. You answer typed questions about "
    "a state. You never write prose: reply with one JSON object and nothing else. "
    "Answer only with the labels exactly as given, never invent one. Set confidence "
    "honestly between 0 and 1: it is used to decide whether a human must look."
)


class LLMDecider:
    """OpenAI-compatible chat endpoint used only for escalated decisions."""

    name = "llm"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        max_tokens: int = 900,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_key = api_key or (
            os.environ.get("LLM_API_KEY")
            or os.environ.get("OPENCODE_GO_API_KEY")
            or os.environ.get("OPENAI_API_KEY")
            or ""
        )
        if not self.api_key:
            raise DeciderError("no LLM credential: set LLM_API_KEY or OPENCODE_GO_API_KEY")
        self.base_url = (
            base_url
            or os.environ.get("LLM_BASE_URL")
            or "https://opencode.ai/zen/go/v1"
        ).rstrip("/")
        self.model = model or os.environ.get("LLM_MODEL") or "deepseek-v4.1-flash"
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._client = client or httpx.Client(timeout=timeout)

    # -- prompt -----------------------------------------------------------
    def _describe(self, questions: QuestionSet) -> str:
        lines: list[str] = []
        for qid, question in questions.with_companions().questions.items():
            lines.append(f'- id "{qid}": {question.instructions}')
            if isinstance(question, Choice):
                options = "; ".join(f'"{k}" = {v}' for k, v in question.options.items())
                lines.append(f"  allowed values: {options}")
                lines.append('  reply field: "choice"')
            elif isinstance(question, Score):
                levels = " | ".join(f"{i}: {lv}" for i, lv in enumerate(question.levels))
                lines.append(f"  ordered levels: {levels}")
                lines.append('  reply field: "score" as a number between 0 and the last level')
            elif isinstance(question, Noul):
                if question.criteria:
                    lines.append(f"  yes/no meaning: {question.criteria}")
                lines.append('  reply field: "noul" as a number between 0 and 1')
        return "\n".join(lines)

    def build_messages(self, state: Any, questions: QuestionSet, hint: str | None = None) -> list[dict[str, str]]:
        state_text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
        user = (
            "STATE:\n<<<\n" + state_text + "\n>>>\n\n"
            "QUESTIONS:\n" + self._describe(questions) + "\n\n"
        )
        if hint:
            user += f"A fast classifier guessed {hint} with low confidence. Do not anchor on it.\n\n"
        user += (
            'Reply with exactly: {"answers": {"<question id>": {"choice"|"score"|"noul": ..., '
            '"confidence": 0.0}}}. Add a short "reason" string per answer only if asked.'
        )
        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ]

    # -- request ----------------------------------------------------------
    def _post(self, messages: list[dict[str, str]], max_tokens: int) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }

    def decide(self, state: Any, questions: QuestionSet, hint: str | None = None) -> DeciderResult:
        started = time.perf_counter()
        messages = self.build_messages(state, questions, hint)
        budget = self.max_tokens
        content = ""
        reasoning_only = False
        usage = Usage()

        for attempt in range(2):
            body = self._post(messages, budget)
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "User-Agent": "OpenAI/Python 1.40.0",
                    "x-opencode-session": "jev-harness",
                },
                json=body,
            )
            if response.status_code >= 400:
                raise DeciderError(
                    f"LLM error {response.status_code}: {response.text[:300]}"
                )
            data = response.json()
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = (message.get("content") or "").strip()
            reasoning_only = bool(message.get("reasoning_content")) and not content
            usage_payload = data.get("usage") or {}
            usage = Usage(
                input_tokens=int(usage_payload.get("prompt_tokens") or 0),
                output_tokens=int(usage_payload.get("completion_tokens") or 0),
            )
            if content:
                break
            # An empty visible answer is a failed turn: reasoning ate the budget.
            budget = max(budget * 2, 1600)

        if not content:
            detail = "only reasoning output" if reasoning_only else "empty content"
            raise DeciderError(f"LLM returned no visible answer ({detail})")

        answers = self.parse(content, questions)
        return DeciderResult(
            answers=answers,
            usage=usage,
            model=self.model,
            latency_ms=(time.perf_counter() - started) * 1000,
        )

    # -- parsing ----------------------------------------------------------
    @staticmethod
    def _loads(content: str) -> dict[str, Any]:
        text = content.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not match:
                raise DeciderError(f"LLM answer is not JSON: {text[:200]}")
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise DeciderError(f"LLM answer is invalid JSON: {exc}") from exc

    @classmethod
    def parse(cls, content: str, questions: QuestionSet) -> dict[str, Answer]:
        payload = cls._loads(content)
        raw = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):
            raise DeciderError("LLM answer is missing the 'answers' object")
        wanted = questions.with_companions().questions
        answers: dict[str, Answer] = {}
        for qid, question in wanted.items():
            entry = raw.get(qid)
            if not isinstance(entry, dict):
                continue
            confidence = entry.get("confidence")
            confidence = float(confidence) if isinstance(confidence, (int, float)) else None
            if isinstance(question, Choice):
                value = entry.get("choice")
                if value is None:
                    continue
                value = str(value).strip()
                if value not in question.options:
                    lowered = {k.lower(): k for k in question.options}
                    value = lowered.get(value.lower(), "")
                if not value:
                    raise DeciderError(
                        f"LLM chose {entry.get('choice')!r} for {qid!r}, which is not an option"
                    )
                answers[qid] = Answer(
                    question_id=qid,
                    type="choice",
                    choice=value,
                    confidence=confidence,
                    probabilities={value: confidence if confidence is not None else 0.0},
                    raw=entry,
                )
            elif isinstance(question, Score):
                value = entry.get("score")
                if value is None:
                    continue
                last = len(question.levels) - 1
                answers[qid] = Answer(
                    question_id=qid,
                    type="score",
                    score=max(0.0, min(float(value), float(last))),
                    confidence=confidence,
                    legend=question.legend(),
                    raw=entry,
                )
            elif isinstance(question, Noul):
                value = entry.get("noul")
                if value is None:
                    continue
                probability = float(value)
                if probability > 1.0:  # tolerate percentages
                    probability = probability / 100.0
                answers[qid] = Answer(
                    question_id=qid,
                    type="noul",
                    noul=max(0.0, min(probability, 1.0)),
                    raw=entry,
                )
        if not answers:
            raise DeciderError("LLM answered none of the questions")
        return answers
