import json

import httpx
import pytest

from jev_harness.deciders.base import DeciderError
from jev_harness.deciders.llm import LLMDecider

from conftest import make_questions


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def completion(content: str, reasoning: str = "", prompt_tokens: int = 120, completion_tokens: int = 30):
    return httpx.Response(
        200,
        json={
            "choices": [
                {
                    "message": {"role": "assistant", "content": content, "reasoning_content": reasoning},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
        },
    )


def make_decider(handler):
    return LLMDecider(api_key="test", model="test-model", client=client(handler))


def test_parses_plain_json():
    body = json.dumps({"answers": {"category": {"choice": "customer", "confidence": 0.8}}})
    decider = make_decider(lambda request: completion(body))
    result = decider.decide("hi", make_questions())
    assert result.answers["category"].choice == "customer"
    assert result.answers["category"].confidence == 0.8
    assert result.model == "test-model"


def test_parses_fenced_json():
    body = "```json\n" + json.dumps({"answers": {"urgent": {"noul": 0.9}}}) + "\n```"
    decider = make_decider(lambda request: completion(body))
    answers = decider.parse(body, make_questions())
    assert answers["urgent"].noul == 0.9


def test_rejects_option_outside_the_closed_set():
    body = json.dumps({"answers": {"category": {"choice": "spam", "confidence": 0.9}}})
    with pytest.raises(DeciderError, match="not an option"):
        LLMDecider.parse(body, make_questions())


def test_noul_percentages_are_normalised():
    body = json.dumps({"answers": {"urgent": {"noul": 85}}})
    answers = LLMDecider.parse(body, make_questions())
    assert answers["urgent"].noul == pytest.approx(0.85)


def test_score_is_clamped_to_the_rubric():
    body = json.dumps({"answers": {"mood": {"score": 9.5, "confidence": 0.7}}})
    answers = LLMDecider.parse(body, make_questions())
    assert answers["mood"].score == 2.0


def test_empty_content_is_retried_with_a_bigger_budget():
    """A reasoning model can spend the whole budget thinking and emit nothing."""
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload["max_tokens"])
        if len(seen) == 1:
            return completion("", reasoning="Let me think about this for a while...")
        return completion(json.dumps({"answers": {"category": {"choice": "fraud", "confidence": 0.9}}}))

    decider = make_decider(handler)
    result = decider.decide("state", make_questions())
    assert result.answers["category"].choice == "fraud"
    assert seen[0] < seen[1]


def test_reasoning_only_twice_raises_instead_of_returning_silence():
    decider = make_decider(lambda request: completion("", reasoning="thinking..."))
    with pytest.raises(DeciderError, match="no visible answer"):
        decider.decide("state", make_questions())


def test_http_error_becomes_decider_error():
    decider = make_decider(lambda request: httpx.Response(500, text="boom"))
    with pytest.raises(DeciderError, match="LLM error 500"):
        decider.decide("state", make_questions())


def test_hint_is_included_in_the_prompt():
    captured = {}

    def handler(request):
        captured["body"] = json.loads(request.content)
        return completion(json.dumps({"answers": {"category": {"choice": "customer", "confidence": 0.9}}}))

    decider = make_decider(handler)
    decider.decide("state", make_questions(), hint="'fraud' with confidence 0.55")
    user_prompt = captured["body"]["messages"][1]["content"]
    assert "'fraud' with confidence 0.55" in user_prompt
    assert captured["body"]["response_format"] == {"type": "json_object"}
