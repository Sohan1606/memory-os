"""Typed HTTP surface for the v8 cognitive layer."""
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, rt
from app.runtime import Runtime


@pytest.fixture(scope="module")
def client():
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-cogapi-"))
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma")
    runtime = Runtime(cfg)
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    runtime.close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_chat_returns_a_cognitive_trace(client):
    body = client.post("/api/chat", json={
        "message": "I need to finish the billing migration by Friday",
        "thread_id": "cog-1"}).json()
    assert body["correlation_id"]
    cognition = body["cognition"]
    assert cognition["need"]
    assert cognition["autonomy"] in ("observe", "assist", "prepare",
                                     "act_low_risk", "act")


def test_turn_can_be_replayed(client):
    body = client.post("/api/chat", json={"message": "I want to launch in March",
                                          "thread_id": "cog-2"}).json()
    replay = client.get(f"/api/cognition/turn/{body['correlation_id']}").json()
    assert replay["count"] >= 2
    assert any(e["type"] == "conversation.message" for e in replay["events"])


def test_unknown_turn_is_404(client):
    assert client.get("/api/cognition/turn/nope").status_code == 404


def test_status_endpoint_is_complete(client):
    status = client.get("/api/cognition/status").json()
    for key in ("autonomy", "world", "predictions", "attention", "self", "events"):
        assert key in status


def test_self_report_does_not_claim_integrations(client):
    report = client.get("/api/cognition/self").json()
    assert "external_context" in report["not_configured"]
    assert "external_actions" in report["not_configured"]


def test_events_feed_is_incrementally_pollable(client):
    first = client.get("/api/cognition/events", params={"limit": 5}).json()
    latest = first["latest_id"]
    client.post("/api/chat", json={"message": "One more thing", "thread_id": "cog-3"})
    second = client.get("/api/cognition/events", params={"since": latest}).json()
    assert all(e["id"] > latest for e in second["events"])


def test_world_endpoint_exposes_summary(client):
    body = client.get("/api/world").json()
    assert "entities" in body and "summary" in body


def test_autonomy_can_be_changed_and_validated(client):
    assert client.post("/api/autonomy", json={"level": "observe",
                                              "reason": "test"}).json()["level"] == "observe"
    assert client.post("/api/autonomy", json={"level": "nonsense"}).status_code == 400
    client.post("/api/autonomy", json={"level": "assist", "reason": "restore"})


def test_sandbox_is_labelled_and_non_mutating(client):
    before = client.get("/api/world").json()["summary"]
    result = client.post("/api/sandbox",
                         json={"question": "What if I drop the migration?"}).json()
    assert result["simulation"] is True
    assert "SIMULATION ONLY" in result["banner"]
    assert client.get("/api/world").json()["summary"] == before


def test_predictions_refuse_to_guess_calibration(client):
    body = client.get("/api/predictions").json()
    assert body["accuracy"]["calibration"] in (
        "INSUFFICIENT EVIDENCE", "WELL CALIBRATED", "REASONABLE",
        "POORLY CALIBRATED")


def test_resolving_unknown_prediction_is_404(client):
    assert client.post("/api/predictions/nope/resolve",
                       json={"correct": True}).status_code == 404


def test_memory_reputation_starts_without_evidence(client):
    body = client.get("/api/memories/mem_not_real/reputation").json()
    assert body["reputation"] == "INSUFFICIENT EVIDENCE"


def test_learning_surface_is_honest(client):
    body = client.get("/api/learning").json()
    assert "policies" in body and "self_evaluation" in body


def test_why_requires_a_subject(client):
    body = client.get("/api/cognition/why",
                      params={"subject_kind": "memory", "subject_id": "ghost"}).json()
    assert "INSUFFICIENT EVIDENCE" in body["explanation"]
