"""V9 conversation, surface, voice parity and portability integration."""
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, rt
from app.runtime import Runtime


@pytest.fixture(scope="module")
def v9_client():
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-v9-"))
    runtime = Runtime(Settings(
        data_dir=tmp, sqlite_path=tmp / "m.db",
        checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma",
        disable_embeddings=True))
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as client:
        yield client, runtime
    app.dependency_overrides.clear()
    runtime.close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_chat_runs_meaning_kernel_and_backend_surface(v9_client):
    client, runtime = v9_client
    response = client.post("/api/chat", json={
        "message": "I prefer careful evidence over speed.",
        "thread_id": "v9-surface"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["surface"]["cognitive_stage"] == "WAITING_FOR_USER"
    assert body["surface"]["active_objects"][0]["type"] == "PREFERENCE"
    stages = [a["stage"] for a in body["surface"]["activities"]]
    assert "UNDERSTANDING" in stages
    assert "FORMING_RESPONSE" in stages
    assert all(a["source"]["id"] for a in body["surface"]["activities"])
    event_types = [e.type for e in runtime.cognition.bus.recent(
        runtime.settings.demo_user_id, 100)]
    assert "meaning.compiled" in event_types
    assert "surface.selected" in event_types
    assert "cognitive_response.generated" in event_types


def test_text_and_voice_share_semantic_pipeline(v9_client):
    client, runtime = v9_client
    text = client.post("/api/chat", json={
        "message": "I value honesty.", "thread_id": "v9-text",
        "interaction_mode": "text"}).json()
    voice = client.post("/api/chat", json={
        "message": "I value honesty.", "thread_id": "v9-voice",
        "interaction_mode": "voice"}).json()
    assert text["surface"]["schema_version"] == voice["surface"]["schema_version"] == "9.0.1"
    assert text["cognition"] is not None and voice["cognition"] is not None
    # Duplicate semantic content does not create a second long-term object.
    rows = runtime.db.query(
        "SELECT * FROM cognitive_objects WHERE user_id=? AND type='VALUE'"
        " AND content='I value honesty.'",
        (runtime.settings.demo_user_id,))
    assert len(rows) == 1
    types = [e.type for e in runtime.cognition.bus.recent(
        runtime.settings.demo_user_id, 200)]
    assert "voice.session_started" in types
    assert "voice.session_ended" in types


def test_focused_correction_supersedes_without_guessing(v9_client):
    client, _ = v9_client
    first = client.post("/api/chat", json={
        "message": "I prefer meetings on Monday.",
        "thread_id": "v9-correction"}).json()
    old_id = first["surface"]["active_objects"][0]["id"]
    correction = client.post("/api/chat", json={
        "message": "Actually, change that to Tuesday.",
        "thread_id": "v9-correction"})
    assert correction.status_code == 200, correction.text
    body = correction.json()
    assert body["surface"]["active_objects"][0]["type"] == "CORRECTION"
    old = client.get(f"/api/v9/cognitive-objects/{old_id}").json()
    assert old["status"] == "SUPERSEDED"
    assert old["superseded_by"] == body["surface"]["active_objects"][0]["id"]


def test_v9_api_crud_diff_and_unknown(v9_client):
    client, _ = v9_client
    made = client.post("/api/v9/cognitive-objects", json={
        "type": "GOAL", "content": "Publish the report.",
        "modality": "DESIRED", "confidence": .8,
        "provenance": "USER_STATED"})
    assert made.status_code == 201, made.text
    obj = made.json()
    first_version = client.get("/api/v9/personal-state").json()["version"]
    changed = client.patch(f"/api/v9/cognitive-objects/{obj['id']}", json={
        "confidence": .9, "reason": "User confirmed it"})
    assert changed.status_code == 200
    second_version = client.get("/api/v9/personal-state").json()["version"]
    diff = client.get("/api/v9/personal-state/diff", params={
        "from_version": first_version, "to_version": second_version})
    assert diff.status_code == 200
    assert diff.json()["confirmed"]["changed"]
    assert client.get("/api/v9/cognitive-objects/not-real").status_code == 404


def test_v9_tables_are_in_portability_allowlist(v9_client):
    _, runtime = v9_client
    from app.portability import TABLES, TABLE_DOMAINS, SCHEMA_VERSION
    expected = {"meaning_compilations", "cognitive_objects",
                "cognitive_object_versions", "cognitive_relationships",
                "personal_state_versions"}
    assert expected.issubset(TABLES)
    assert all("semantic_state" in TABLE_DOMAINS[t] for t in expected)
    assert SCHEMA_VERSION == "9.0"
    selected = runtime.portability._selected_rows(
        runtime.settings.demo_user_id, ["semantic_state"])
    assert selected["cognitive_objects"]
