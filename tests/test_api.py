import pytest
from fastapi.testclient import TestClient

from jev_harness import Cascade, Ledger, Policy
from jev_harness.api import build_app
from jev_harness.deciders.mock import KeywordDecider
from jev_harness.spec import load_question_set

from pathlib import Path

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def cascade():
    questions, primary = load_question_set(EXAMPLES / "email_triage.questions.json")
    return Cascade(
        questions=questions,
        policy=Policy(thresholds={"customer": 0.5, "fraud": 0.95}, default_threshold=0.9),
        decision_model=KeywordDecider(),
        ledger=Ledger(),
        primary_question=primary,
    )


@pytest.fixture
def client(cascade, monkeypatch):
    monkeypatch.delenv("HARNESS_TOKEN", raising=False)
    return TestClient(build_app(cascade))


def test_healthz_reports_wiring(client, cascade):
    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["question_set"] == "email_triage"
    assert payload["decision_model"] == "keyword-offline"
    assert cascade.shadow is False


def test_questions_endpoint_exposes_companions(client):
    payload = client.get("/v1/questions").json()
    assert payload["version"] == 3
    assert "category__fits" in payload["questions"]


def test_decide_returns_a_typed_decision(client):
    response = client.post("/v1/decide", json={"state": "I want a refund, my invoice is charged twice"})
    assert response.status_code == 200
    body = response.json()
    assert body["ledger_id"]
    assert body["layer"] in {"jev", "llm", "human"}
    assert body["answers"]["category"]["choice"]
    assert "may_act" in body and "threshold" in body


def test_feedback_then_stats_and_bands(client):
    body = client.post("/v1/decide", json={"state": "I want a refund"}).json()
    feedback = client.post(
        "/v1/feedback", json={"ledger_id": body["ledger_id"], "outcome": "correct", "note": "checked"}
    )
    assert feedback.status_code == 200
    stats = client.get("/v1/stats").json()
    assert stats["decisions"] == 1 and stats["reviewed"] == 1
    bands = client.get("/v1/bands").json()["bands"]
    assert sum(row["decisions"] for row in bands) <= 1


def test_token_is_required_when_configured(monkeypatch, cascade):
    monkeypatch.setenv("HARNESS_TOKEN", "secret-token")
    client = TestClient(build_app(cascade))
    assert client.get("/v1/questions").status_code == 401
    assert client.get("/v1/questions", headers={"Authorization": "Bearer nope"}).status_code == 403
    ok = client.get("/v1/questions", headers={"Authorization": "Bearer secret-token"})
    assert ok.status_code == 200


def test_healthz_reports_degraded_wiring_instead_of_500(monkeypatch):
    monkeypatch.delenv("QUESTIONS_PATH", raising=False)
    client = TestClient(build_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"


def test_healthz_stays_open_with_token(monkeypatch, cascade):
    monkeypatch.setenv("HARNESS_TOKEN", "secret-token")
    client = TestClient(build_app(cascade))
    assert client.get("/healthz").status_code == 200
