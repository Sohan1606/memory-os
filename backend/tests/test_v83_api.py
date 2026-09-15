"""V8.3 API surface. Every route is exercised against the real app."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client(runtime):
    import app.main as main

    main.get_runtime = lambda: runtime
    app.dependency_overrides[main.rt] = lambda: runtime
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _u(prefix: str = "api") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ------------------------------------------------------------------ missions
def test_create_and_list_missions(client):
    user = _u()
    created = client.post("/api/missions", json={
        "title": "Ship the new onboarding", "priority": 0.7,
        "success_criteria": ["Users complete signup"], "user_id": user})
    assert created.status_code == 200
    mission = created.json()["mission"]
    assert mission["state"] == "draft"

    listed = client.get("/api/missions", params={"user_id": user})
    assert [m["id"] for m in listed.json()["missions"]] == [mission["id"]]


def test_mission_state_and_history_routes(client):
    user = _u()
    mission = client.post("/api/missions", json={
        "title": "Mission with history", "user_id": user}).json()["mission"]

    moved = client.post(f"/api/missions/{mission['id']}/state", json={
        "state": "active", "reason": "User began work", "user_id": user})
    assert moved.json()["mission"]["state"] == "active"

    history = client.get(f"/api/missions/{mission['id']}/history",
                         params={"user_id": user}).json()["history"]
    assert any(h["reason"] == "User began work" for h in history)


def test_mission_steps_and_progress(client):
    user = _u()
    mission = client.post("/api/missions", json={
        "title": "Stepped mission", "user_id": user}).json()["mission"]
    step = client.post(f"/api/missions/{mission['id']}/steps", json={
        "summary": "Draft the outline", "user_id": user}).json()["step"]

    done = client.post(f"/api/missions/steps/{step['id']}/complete",
                       params={"user_id": user})
    assert done.json()["mission"]["progress"] == 1.0


def test_mission_brief_route(client):
    user = _u()
    assert client.get("/api/missions/brief", params={"user_id": user}
                      ).json()["brief"]["open"] == 0


def test_unknown_mission_is_404(client):
    assert client.get("/api/missions/ms_nope",
                      params={"user_id": _u()}).status_code == 404
    assert client.get("/api/missions/ms_nope/history",
                      params={"user_id": _u()}).status_code == 404


def test_invalid_mission_state_is_400(client):
    user = _u()
    mission = client.post("/api/missions", json={
        "title": "State validation", "user_id": user}).json()["mission"]
    bad = client.post(f"/api/missions/{mission['id']}/state", json={
        "state": "teleported", "reason": "nonsense", "user_id": user})
    assert bad.status_code == 400


# -------------------------------------------------------------- observations
def test_observation_routes(client):
    user = _u()
    created = client.post("/api/observations", json={
        "content": "User said the deadline moved", "source": "conversation",
        "origin": "msg_100", "user_id": user})
    observation = created.json()["observation"]
    assert observation["epistemic_status"] == "OBSERVED"

    listed = client.get("/api/observations", params={"user_id": user}).json()
    assert listed["stats"]["total"] == 1


def test_evidence_route_reports_insufficient(client):
    result = client.get("/api/observations/evidence/world/w_missing",
                        params={"user_id": _u()}).json()
    assert result["verdict"] == "INSUFFICIENT EVIDENCE"


def test_promotion_route_is_explicit(client):
    user = _u()
    observation = client.post("/api/observations", json={
        "content": "User prefers morning meetings", "source": "conversation",
        "origin": "msg_200", "user_id": user}).json()["observation"]
    promoted = client.post(f"/api/observations/{observation['id']}/promote",
                           params={"user_id": user}).json()
    assert promoted["promoted"] is True


def test_promoting_an_inference_is_refused(client):
    user = _u()
    observation = client.post("/api/observations", json={
        "content": "User seems stressed", "source": "conversation",
        "origin": "msg_300", "epistemic_status": "INFERRED",
        "user_id": user}).json()["observation"]
    result = client.post(f"/api/observations/{observation['id']}/promote",
                         params={"user_id": user}).json()
    assert result["promoted"] is False


def test_promote_unknown_observation_is_404(client):
    assert client.post("/api/observations/obs_nope/promote",
                       params={"user_id": _u()}).status_code == 404


# --------------------------------------------------------------------- world
def test_world_snapshot_and_changes(client):
    user = _u()
    client.post("/api/world/reconcile", json={
        "kind": "project", "label": "API world project", "user_id": user})
    snapshot = client.get("/api/world/snapshot", params={"user_id": user}).json()
    assert snapshot["count"] == 1
    assert snapshot["entities"][0]["freshness"]["freshness_class"] == "FRESH"

    changes = client.get("/api/world/changes", params={"user_id": user}).json()
    assert changes["changes"][0]["change"] == "created"


def test_reconcile_route_returns_a_verdict(client):
    user = _u()
    client.post("/api/world/reconcile", json={
        "kind": "project", "label": "Contested project", "confidence": 0.9,
        "user_id": user})
    result = client.post("/api/world/reconcile", json={
        "kind": "project", "label": "Contested project", "state": "abandoned",
        "confidence": 0.3, "user_id": user}).json()
    assert result["verdict"] == "downgrade"


def test_stale_route_explains_itself(client):
    result = client.get("/api/world/stale", params={"user_id": _u()}).json()
    assert result["stale"] == []
    assert "not treated as false" in result["note"]


# --------------------------------------------------------------- time machine
def test_history_routes(client):
    user = _u()
    assert client.get("/api/history/coverage",
                      params={"user_id": user}).json()["available"] is False

    client.post("/api/world/reconcile", json={
        "kind": "goal", "label": "History anchor goal", "user_id": user})
    coverage = client.get("/api/history/coverage",
                          params={"user_id": user}).json()
    assert coverage["available"] is True

    unavailable = client.get("/api/history/world", params={
        "user_id": user, "at": "1990-01-01T00:00:00+00:00"}).json()
    assert unavailable["available"] is False
    assert "HISTORY NOT AVAILABLE" in unavailable["reason"]


# ----------------------------------------------------------------- background
def test_background_status_and_control(client):
    user = _u()
    status = client.get("/api/background", params={"user_id": user}).json()
    assert status["state"] == "enabled"

    paused = client.post("/api/background/control", json={
        "state": "paused", "user_id": user}).json()
    assert paused["state"] == "paused"

    skipped = client.post("/api/background/run", json={
        "force": True, "user_id": user}).json()
    assert skipped["state"] == "skipped"

    client.post("/api/background/control", json={"state": "enabled",
                                                 "user_id": user})
    ran = client.post("/api/background/run", json={"force": True,
                                                   "user_id": user}).json()
    assert ran["state"] == "completed"
    assert ran["findings"] == []


def test_background_rejects_unknown_state(client):
    assert client.post("/api/background/control", json={
        "state": "nope", "user_id": _u()}).status_code == 400


# ------------------------------------------------------------------ attention
def test_attention_routes(client):
    user = _u()
    result = client.post("/api/attention/evaluate", json={
        "topic": "Something trivial", "importance": 0.05, "urgency": 0.05,
        "confidence": 0.5, "user_id": user}).json()
    assert result["level"] in ("IGNORE", "DO_NOTHING", "MONITOR")

    suppressions = client.get("/api/attention/suppressions",
                              params={"user_id": user}).json()
    assert len(suppressions["suppressions"]) == 1

    policy = client.get("/api/attention/policy", params={"user_id": user}).json()
    assert policy["verdict"] == "INSUFFICIENT EVIDENCE"


def test_attention_reaction_route(client):
    user = _u()
    result = client.post("/api/attention/evaluate", json={
        "topic": "Worth raising right now", "importance": 0.9, "urgency": 0.9,
        "confidence": 0.9, "user_id": user}).json()
    reaction = client.post(
        f"/api/attention/{result['intervention_id']}/reaction",
        json={"accepted": False, "detail": "Not now", "user_id": user}).json()
    assert reaction["recorded"] is True


# ----------------------------------------------------------------- simulation
def test_simulation_routes_and_commit_guard(client):
    user = _u()
    client.post("/api/world/reconcile", json={
        "kind": "project", "label": "Simulated project", "user_id": user})
    simulation = client.post("/api/simulation", json={
        "question": "What if I delayed this by a month?", "user_id": user}).json()
    assert simulation["epistemic_status"] == "SIMULATED"

    refused = client.post(f"/api/simulation/{simulation['id']}/commit", json={
        "confirm": False, "user_id": user}).json()
    assert refused["committed"] is False


# ------------------------------------------------------------------ documents
def test_document_upload_route(client):
    user = _u()
    response = client.post(
        "/api/documents", params={"user_id": user},
        files={"file": ("readme.txt", b"Real readable content", "text/plain")})
    document = response.json()["document"]
    assert document["understanding"] == "FULL"

    listed = client.get("/api/documents", params={"user_id": user}).json()
    assert ".pdf" in listed["capabilities"]["not_configured"]


def test_pdf_upload_is_metadata_only(client):
    user = _u()
    response = client.post(
        "/api/documents", params={"user_id": user},
        files={"file": ("report.pdf", b"%PDF-1.4 binary", "application/pdf")})
    assert response.json()["document"]["understanding"] == "METADATA ONLY"


# --------------------------------------------------------- connectors/research
def test_connector_routes_report_not_connected(client):
    user = _u()
    status = client.get("/api/connectors", params={"user_id": user}).json()
    assert status["connected"] == 0

    fetched = client.get("/api/connectors/calendar/fetch",
                         params={"user_id": user}).json()
    assert fetched["items"] is None


def test_research_routes_report_not_configured(client):
    user = _u()
    started = client.post("/api/research", json={
        "question": "Which vector database scales best?",
        "user_id": user}).json()["session"]
    assert started["provider_state"] == "RESEARCH PROVIDER NOT CONFIGURED"

    status = client.get("/api/research", params={"user_id": user}).json()
    assert status["status"]["available"] is False


# ----------------------------------------------------------------- maintenance
def test_maintenance_routes(client):
    user = _u()
    review = client.get("/api/maintenance/review", params={"user_id": user}).json()
    assert review["changes"] == 0

    remedies = client.get("/api/maintenance/remedies").json()
    assert remedies["non_destructive"] is True


# ----------------------------------------------------------------- predictions
def test_prediction_due_and_unresolved_routes(client):
    user = _u()
    due = client.get("/api/predictions/due", params={"user_id": user}).json()
    assert due["due"] == []
    assert "Silence resolves nothing" in due["note"]

    assert client.post("/api/predictions/p_nope/unresolved",
                       params={"user_id": user}).status_code == 404


# ------------------------------------------------------- v8.2 compatibility
def test_v82_routes_still_work(client):
    """V8.3 must not break the existing API contract."""
    user = _u()
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/capabilities").status_code == 200
    assert client.get("/api/provider").status_code == 200
    assert client.get("/api/needs", params={"user_id": user}).status_code == 200
    assert client.get("/api/trust", params={"user_id": user}).status_code == 200
    assert client.get("/api/cognition/self",
                      params={"user_id": user}).status_code == 200
