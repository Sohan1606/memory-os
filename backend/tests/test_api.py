"""FastAPI surface tests via TestClient against an isolated runtime."""
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
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-api-"))
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma")
    runtime = Runtime(cfg)
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    runtime.close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_health_reports_real_stack(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok"
    assert h["agent"]["framework"] == "langgraph"
    assert h["provider"]["name"] in {"demo", "ollama", "openai"}
    assert h["vector"]["mode"] in {"semantic", "keyword"}
    assert "voice" in h


def test_memories_seeded(client):
    data = client.get("/api/memories").json()
    assert len(data["memories"]) >= 20
    assert data["stats"]["total"] >= 20


def test_chat_roundtrip(client):
    r = client.post("/api/chat", json={"message": "I prefer concise answers.",
                                       "thread_id": "api-t1"})
    assert r.status_code == 200
    body = r.json()
    assert body["answer"]
    assert body["thread_id"] == "api-t1"


def test_chat_validates_blank_message(client):
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422


def test_crud_cycle(client):
    created = client.post("/api/memories", json={
        "content": "Enjoys long distance cycling on Sundays.", "category": "HABIT"}).json()
    mid = created["memory"]["id"]

    got = client.get(f"/api/memories/{mid}").json()
    assert got["memory"]["id"] == mid
    assert got["versions"]

    patched = client.patch(f"/api/memories/{mid}", json={
        "content": "Enjoys long distance running on Sundays.", "reason": "corrected"}).json()
    assert patched["memory"]["version"] == 2

    assert client.delete(f"/api/memories/{mid}").status_code == 200
    assert client.get(f"/api/memories/{mid}").status_code == 404


def test_search_states(client):
    strong = client.post("/api/memories/search",
                         json={"query": "what are my projects?"}).json()
    assert strong["state"] in {"STRONG", "WEAK"}
    assert strong["results"] and strong["path"]

    none = client.post("/api/memories/search",
                       json={"query": "zzzz qqqq vvvv nonsense"}).json()
    assert none["state"] == "NO_STRONG_MATCH"
    assert none["results"] == []

    empty = client.post("/api/memories/search", json={"query": ""}).json()
    assert empty["state"] == "EMPTY"


def test_graph_and_timeline(client):
    graph = client.get("/api/memories/graph").json()
    ids = {n["id"] for n in graph["nodes"]}
    assert graph["edges"]
    assert all(e["source"] in ids and e["target"] in ids for e in graph["edges"])

    timeline = client.get("/api/memories/timeline").json()
    assert timeline["events"]


def test_export_import_reset(client):
    exported = client.get("/api/export").json()
    assert exported["memories"] and "events" in exported

    bad = client.post("/api/import", json={"payload": {"nope": []}})
    assert bad.status_code == 400

    ok = client.post("/api/import", json={
        "payload": {"memories": [{"content": "Imported test memory about sailing.",
                                  "category": "FACT"}]}}).json()
    assert ok["imported"] == 1

    reset = client.post("/api/reset").json()
    assert reset["reset"] is True and reset["seeded"] >= 20


def test_delete_all_requires_confirmation(client):
    assert client.delete("/api/memories").status_code == 400


def test_voice_status_is_honest(client):
    v = client.get("/api/voice/status").json()
    assert v["mode"] in {"whisper", "browser"}
    assert v["browser_fallback"] is True


def test_transcribe_without_whisper_returns_503(client):
    r = client.post("/api/voice/transcribe",
                    files={"file": ("a.webm", b"not-audio", "audio/webm")})
    assert r.status_code in {503, 500}
