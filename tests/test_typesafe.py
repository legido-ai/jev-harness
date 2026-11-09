import httpx
import pytest

from jev_harness.answers import Answer
from jev_harness.deciders.base import AuthError, DeciderError
from jev_harness.deciders.typesafe import TypeSafeDecider

from conftest import make_questions

OK = {
    "model": "jev-1.13.0",
    "answers": {
        "category": {
            "type": "choice",
            "choice": "customer",
            "confidence": 0.93,
            "probabilities": {"customer": 0.93, "fraud": 0.05, "other": 0.02},
        },
        "mood": {
            "type": "score",
            "score": 0.67,
            "confidence": 0.5,
            "legend": {"0": "calm", "1": "annoyed", "2": "angry"},
            "probabilities": {"0": 0.33, "1": 0.67, "2": 0.0},
        },
        "urgent": {"type": "noul", "noul": 0.99},
        "category__fits": {"type": "noul", "noul": 0.97},
    },
    "usage": {"input_tokens": 428, "output_tokens": 73},
}


def decider(handler, **kwargs):
    return TypeSafeDecider(
        api_key="test-key",
        model="jev-1.13.0",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def test_payload_carries_state_questions_and_companions():
    sent = {}

    def handler(request):
        sent.update({"url": str(request.url), "body": __import__("json").loads(request.content),
                     "auth": request.headers.get("authorization")})
        return httpx.Response(200, json=OK)

    decider(handler).decide("the state", make_questions())
    assert sent["url"].endswith("/v1/systemone")
    assert sent["body"]["model"] == "jev-1.13.0"
    assert sent["body"]["state"] == "the state"
    assert "category" in sent["body"]["questions"]
    assert sent["body"]["questions"]["category"]["criteria"]["customer"]
    assert sent["auth"] == "Bearer test-key"


def test_response_parsing_and_cost():
    result = decider(lambda request: httpx.Response(200, json=OK)).decide("s", make_questions())
    assert result.answers["category"].choice == "customer"
    assert result.answers["category"].confidence == 0.93
    assert result.answers["urgent"].noul == 0.99
    assert result.usage.input_tokens == 428
    assert result.usage.cost_usd() == pytest.approx(428 / 1_000_000 * 0.042)


def test_rejected_credential_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"detail": {"error_type": "authentication_error"}})

    with pytest.raises(AuthError):
        decider(handler).decide("s", make_questions())
    assert len(calls) == 1


def test_retryable_status_is_retried(monkeypatch):
    monkeypatch.setattr("jev_harness.deciders.typesafe.time.sleep", lambda *_: None)
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="slow down")
        return httpx.Response(200, json=OK)

    result = decider(handler).decide("s", make_questions())
    assert len(calls) == 2
    assert result.answers["category"].choice == "customer"


def test_missing_choice_in_probabilities_is_rejected():
    payload = {"type": "choice", "choice": "ghost", "confidence": 0.9, "probabilities": {"customer": 1.0}}
    with pytest.raises(ValueError):
        Answer.from_payload("category", payload)


def test_exhausted_retries_raise():
    def handler(request):
        return httpx.Response(503, text="unavailable")

    with pytest.raises(DeciderError):
        decider(handler, max_retries=1).decide("s", make_questions())
