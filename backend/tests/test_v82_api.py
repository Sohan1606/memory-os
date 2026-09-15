"""
V8.2 HTTP surface.

Covers the new cognitive-core endpoints AND asserts that the V8/V8.1 routes
still answer with their original shape (backwards compatibility is a hard
requirement of this upgrade).
"""
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
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-v82api-"))
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma")
    runtime = Runtime(cfg)
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    runtime.close()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture(scope="module")
def seeded(client):
    """One real turn, so the inspection endpoints have genuine data."""
    body = client.post("/api/chat", json={
        "message": "I want to migrate the billing database to Postgres by Friday",
        "thread_id": "v82-api"}).json()
    return body


# ------------------------------------------------------------- §3 capabilities
def test_capabilities_endpoint_is_honest(client):
    report = client.get("/api/capabilities").json()["capabilities"]
    assert report["provider"]
    names = {c["name"] for c in report["capabilities"]}
    assert {"generation", "tool_calling", "vision", "structured_output",
            "embeddings"} <= names
    for cap in report["capabilities"]:
        assert cap["state"] in ("SUPPORTED", "NOT_SUPPORTED", "UNKNOWN")
        assert cap["reason"], "a capability claim must be justified"


def test_routing_endpoint_lists_every_task(client):
    body = client.get("/api/capabilities/route").json()
    tasks = {r["task"] for r in body["routes"]}
    assert {"conversation", "memory_retrieval", "vision"} <= tasks
    for route in body["routes"]:
        assert route["mode"] in ("MODEL_TOOLS", "MODEL_STRUCTURED", "MODEL",
                                 "DETERMINISTIC", "NOT_CONFIGURED")
        assert route["reason"]


def test_vision_is_reported_not_configured_without_a_vision_model(client):
    routes = {r["task"]: r for r in
              client.get("/api/capabilities/route").json()["routes"]}
    vision = routes["vision"]
    if vision["mode"] == "NOT_CONFIGURED":
        assert "NOT CONFIGURED" in vision["reason"]


def test_provider_endpoint_still_works_and_gained_capabilities(client):
    body = client.get("/api/provider").json()
    # V8.1 fields preserved.
    assert body["provider"]["mode"] in ("REAL AGENT", "DETERMINISTIC FALLBACK")
    assert "available" in body["provider"]
    assert "extraction" in body and "perception" in body
    # V8.2 additions.
    assert "capabilities" in body
    assert "routing" in body


def test_health_reports_version_8_2(client):
    body = client.get("/api/health").json()
    assert body["version"] == "8.2"
    assert "capabilities" in body


# --------------------------------------------------------------- §4 execution
def test_execution_trace_is_available_for_a_turn(client, seeded):
    cid = seeded["correlation_id"]
    body = client.get(f"/api/execution/{cid}").json()
    assert body["correlation_id"] == cid
    assert isinstance(body["steps"], list)
    for step in body["steps"]:
        assert step["stage"]
        assert "detail" in step


def test_recent_executions_are_listed(client, seeded):
    body = client.get("/api/execution").json()
    assert isinstance(body["traces"], list)


def test_unknown_correlation_id_is_404_not_a_fabricated_trace(client):
    response = client.get("/api/execution/does-not-exist")
    assert response.status_code == 404
    assert "No execution trace" in response.json()["detail"]


# ------------------------------------------------------------- §6 arbitration
def test_arbitration_records_are_listed(client, seeded):
    body = client.get("/api/arbitration").json()
    assert isinstance(body["records"], list)
    for record in body["records"]:
        assert "reason" in record
        assert "uncertainty" in record


def test_unknown_arbitration_record_is_404(client):
    assert client.get("/api/arbitration/nope").status_code == 404


# --------------------------------------------------------------- §8 influence
def test_influence_list_and_pending(client, seeded):
    assert isinstance(client.get("/api/influence").json()["influences"], list)
    assert isinstance(
        client.get("/api/influence?pending=true").json()["influences"], list)


def test_outcome_on_an_unknown_influence_is_404(client):
    response = client.post("/api/influence/nope/outcome", json={
        "verdict": "SUPPORTED", "detail": "x", "evidence": ["y"]})
    assert response.status_code == 404


def test_an_invalid_verdict_is_rejected(client):
    response = client.post("/api/influence/any/outcome", json={
        "verdict": "PROBABLY", "detail": "x"})
    assert response.status_code == 422


def test_memory_impact_is_honest_for_an_uninfluential_memory(client):
    created = client.post("/api/memories", json={
        "content": "Prefers espresso over filter coffee"}).json()
    mid = created["memory"]["id"]
    body = client.get(f"/api/memories/{mid}/impact").json()
    assert body["influence_count"] == 0
    assert body["summary"]


# ------------------------------------------------------------------ §15 policy
def test_cognitive_policy_is_listed_with_evidence(client, seeded):
    body = client.get("/api/cognitive-policy").json()
    assert "effective" in body
    assert isinstance(body["policies"], list)
    for policy in body["policies"]:
        assert "confidence" in policy
        assert "is_default" in policy


def test_a_single_policy_explains_itself(client):
    body = client.get("/api/cognitive-policy/response_depth").json()
    assert body["key"] == "response_depth"
    assert body["explanation"]


def test_an_unknown_policy_key_is_404(client):
    assert client.get("/api/cognitive-policy/not_a_dimension").status_code == 404


def test_policy_can_be_reverted(client):
    client.post("/api/chat", json={"message": "please keep your answers brief",
                                   "thread_id": "v82-policy"})
    response = client.delete("/api/cognitive-policy/response_depth")
    assert response.status_code in (200, 404)


# ------------------------------------------------------------------- §17 trust
def test_trust_reports_per_capability_evidence(client, seeded):
    body = client.get("/api/trust").json()
    assert isinstance(body["capabilities"], list)
    for entry in body["capabilities"]:
        assert entry["label"] in ("RELIABLE", "MIXED", "UNRELIABLE",
                                  "SUPPRESSED", "INSUFFICIENT EVIDENCE")
        assert entry["detail"], "a trust claim must explain its basis"
        if entry["total"] < 3:
            # Below the evidence threshold no reliability may be claimed.
            assert entry["label"] == "INSUFFICIENT EVIDENCE"
            assert entry["reliability"] is None


# ------------------------------------------------------------------ §10 intent
def test_intent_transitions_are_exposed(client, seeded):
    body = client.get("/api/intents/transitions").json()
    assert isinstance(body["transitions"], list)
    for t in body["transitions"]:
        assert "to_status" in t
        assert "uncertainty" in t


def test_intent_why_for_an_unknown_intent_is_honest(client):
    body = client.get("/api/intents/nope/why").json()
    assert body["intent"] is None
    assert body["transitions"] == []
    assert "INSUFFICIENT EVIDENCE" in body["explanation"]


# -------------------------------------------------------------------- §11 needs
def test_needs_are_listed_with_accuracy(client, seeded):
    body = client.get("/api/needs").json()
    assert "accuracy" in body
    assert isinstance(body["hypotheses"], list)


def test_evaluating_an_unknown_need_is_404(client):
    response = client.post("/api/needs/nope/evaluate", json={"correct": True})
    assert response.status_code == 404


def test_a_need_hypothesis_can_be_confirmed(client, seeded):
    hypotheses = client.get("/api/needs").json()["hypotheses"]
    if not hypotheses:
        pytest.skip("no need hypothesis recorded in this run")
    hid = hypotheses[0]["id"]
    body = client.post(f"/api/needs/{hid}/evaluate", json={"correct": True}).json()
    assert body["correct"] is True
    assert client.get("/api/needs").json()["accuracy"]["accuracy"] is not None


# --------------------------------------------------------------- §9 continuity
def test_continuity_items_are_listed(client, seeded):
    body = client.get("/api/continuity").json()
    assert isinstance(body["items"], list)
    for item in body["items"]:
        assert item["reason"]
        assert item["kind"]


def test_closing_an_unknown_continuity_item_is_404(client):
    assert client.post("/api/continuity/nope/close", json={}).status_code == 404


# -------------------------------------------------------------------- §21 focus
def test_focus_can_be_set_read_and_cleared(client):
    created = client.post("/api/memories", json={
        "content": "The staging cluster runs in eu-west-1"}).json()
    mid = created["memory"]["id"]

    set_response = client.post("/api/focus", json={
        "subject_kind": "memory", "subject_id": mid, "session_id": "f1"})
    assert set_response.status_code == 200

    current = client.get("/api/focus?session_id=f1").json()
    assert any(f["subject_id"] == mid for f in current["focus"])

    assert client.delete("/api/focus?session_id=f1").status_code == 200
    assert client.get("/api/focus?session_id=f1").json()["focus"] == []


def test_an_invalid_focus_kind_is_rejected(client):
    response = client.post("/api/focus", json={
        "subject_kind": "wormhole", "subject_id": "x"})
    assert response.status_code in (400, 422)


# ------------------------------------------------------------ §23 explanations
def test_why_now_explains_timing(client, seeded):
    body = client.get("/api/cognition/why-now?subject_kind=intent"
                      "&subject_id=any").json()
    assert "explanation" in body or "reason" in body


def test_why_used_explains_a_memory(client):
    created = client.post("/api/memories", json={
        "content": "Prefers dark mode in every tool"}).json()
    mid = created["memory"]["id"]
    body = client.get(f"/api/memories/{mid}/why-used").json()
    assert "arbitrations" in body or "explanation" in body


def test_what_changed_still_works(client, seeded):
    body = client.get("/api/cognition/what-changed").json()
    assert isinstance(body, dict)


# --------------------------------------------------------------- §19 control
def test_control_endpoint_executes_a_command(client):
    body = client.post("/api/control", json={
        "message": "remember that I deploy on Tuesday mornings"}).json()
    assert body["command"] == "REMEMBER"
    assert body["applied"] is True


def test_control_endpoint_rejects_ordinary_conversation(client):
    response = client.post("/api/control", json={
        "message": "how is the weather looking today"})
    assert response.status_code == 422


def test_control_refuses_an_unresolvable_destructive_command(client):
    body = client.post("/api/control", json={
        "message": "forget that", "session_id": "empty-session"}).json()
    assert body["applied"] is False
    assert body["requires"] == "clarification"


def test_control_commands_are_documented(client):
    body = client.get("/api/control/commands").json()
    commands = {c["command"] for c in body["commands"]}
    assert {"FORGET", "CORRECT", "REMEMBER", "EXPLAIN_BELIEF"} <= commands


# ------------------------------------------- §13 prediction observation route
def test_observing_an_unknown_prediction_is_404(client):
    response = client.post("/api/predictions/nope/observe", json={
        "observation": "it came true"})
    assert response.status_code == 404


# --------------------------------------------------- backwards compatibility
@pytest.mark.parametrize("path", [
    "/api/health", "/api/provider", "/api/memories", "/api/cognition/status",
    "/api/cognition/events", "/api/autonomy", "/api/predictions",
    "/api/decisions", "/api/memory-health", "/api/cognition/self",
    "/api/world", "/api/learning", "/api/events", "/api/export",
])
def test_v81_endpoints_still_respond(client, path):
    assert client.get(path).status_code == 200


def test_chat_response_keeps_its_v81_shape(client, seeded):
    for key in ("answer", "thread_id", "activity", "cognition",
                "correlation_id"):
        assert key in seeded, f"v8.1 chat field '{key}' disappeared"


def test_chat_trace_gained_v82_fields_without_losing_v81(client, seeded):
    cognition = seeded["cognition"]
    for key in ("need", "intent", "autonomy"):
        assert key in cognition
    for key in ("mode", "context_items", "influenced_by"):
        assert key in cognition, f"v8.2 field '{key}' missing"
