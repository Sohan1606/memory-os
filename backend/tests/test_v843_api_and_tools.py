"""
V8.4.3 — API routes, cognitive tools, explanation engine integration, and
legacy-regression checks for Connected Research.
"""
from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.cognition import net_security as ns
from app.main import app, rt
from app.runtime import Runtime


def _u(prefix: str = "rapi") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(
            b"<html><head><title>API Test Page</title></head><body>"
            b"<p>The release date is March 3, 2027 per the announcement.</p>"
            b"</body></html>")


@pytest.fixture(scope="module")
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield port
    srv.shutdown()


@pytest.fixture(autouse=True)
def allow_loopback(monkeypatch):
    real_classify = ns.classify_ip

    def _classify(ip):
        if str(ip) == "127.0.0.1":
            return None
        return real_classify(ip)

    monkeypatch.setattr(ns, "classify_ip", _classify)
    monkeypatch.setattr(ns, "ALLOWED_PORTS", frozenset(range(1, 65536)))
    yield


@pytest.fixture
def client(runtime: Runtime):
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _url(server, path="/"):
    return f"http://127.0.0.1:{server}{path}"


# ------------------------------------------------------------------- API v2
def test_research_v2_full_api_lifecycle(client: TestClient, server):
    user = _u()
    status = client.get("/api/research/v2/status", params={"user_id": user}).json()
    assert status["available"] is True

    created = client.post("/api/research/v2", json={
        "question": "What is the release date?", "user_id": user}).json()["session"]
    session_id = created["id"]
    assert created["state"] == "DRAFT"

    fetched = client.post(f"/api/research/v2/{session_id}/fetch", json={
        "url": _url(server), "user_id": user}).json()
    assert fetched["status"] == "COMPLETED"

    got = client.get(f"/api/research/v2/{session_id}", params={"user_id": user}).json()["session"]
    assert got["source_count"] == 1

    sources = client.get(f"/api/research/v2/{session_id}/sources", params={"user_id": user}).json()["sources"]
    assert len(sources) == 1
    fetches = client.get(f"/api/research/v2/{session_id}/fetches", params={"user_id": user}).json()["fetches"]
    assert len(fetches) == 1
    evidence = client.get(f"/api/research/v2/{session_id}/evidence", params={"user_id": user}).json()["evidence"]
    assert len(evidence) >= 1
    claims = client.get(f"/api/research/v2/{session_id}/claims", params={"user_id": user}).json()["claims"]
    assert len(claims) >= 1
    conflicts = client.get(f"/api/research/v2/{session_id}/conflicts", params={"user_id": user}).json()["conflicts"]
    assert conflicts == []

    finished = client.post(f"/api/research/v2/{session_id}/finish", params={"user_id": user}).json()["session"]
    assert finished["state"] == "COMPLETED"

    listed = client.get("/api/research/v2", params={"user_id": user}).json()["sessions"]
    assert any(s["id"] == session_id for s in listed)


def test_research_v2_world_update_api_requires_confirm(client: TestClient, server):
    user = _u()
    created = client.post("/api/research/v2", json={
        "question": "World bridge via API", "user_id": user}).json()["session"]
    session_id = created["id"]
    client.post(f"/api/research/v2/{session_id}/fetch", json={
        "url": _url(server), "user_id": user})
    claims = client.get(f"/api/research/v2/{session_id}/claims", params={"user_id": user}).json()["claims"]
    claim_id = claims[0]["id"]

    proposed = client.post(f"/api/research/v2/{session_id}/world-updates/propose", json={
        "claim_id": claim_id, "kind": "commitment", "label": "Release date",
        "user_id": user}).json()["world_update"]
    assert proposed["state"] == "PROPOSED"

    denied = client.post(f"/api/research/v2/world-updates/{proposed['id']}/apply", json={
        "confirm": False, "user_id": user})
    assert denied.status_code == 400

    applied = client.post(f"/api/research/v2/world-updates/{proposed['id']}/apply", json={
        "confirm": True, "user_id": user})
    assert applied.status_code == 200
    assert applied.json()["update"]["state"] == "APPLIED"


def test_research_v2_cross_user_404(client: TestClient, server):
    user_a, user_b = _u("a"), _u("b")
    created = client.post("/api/research/v2", json={
        "question": "Private to A", "user_id": user_a}).json()["session"]
    resp = client.get(f"/api/research/v2/{created['id']}", params={"user_id": user_b})
    assert resp.status_code == 404


def test_research_v2_fetch_of_unsafe_url_is_blocked_via_api(client: TestClient):
    user = _u()
    created = client.post("/api/research/v2", json={
        "question": "SSRF via API", "user_id": user}).json()["session"]
    result = client.post(f"/api/research/v2/{created['id']}/fetch", json={
        "url": "http://169.254.169.254/latest/meta-data/", "user_id": user}).json()
    assert result["status"] == "BLOCKED"


def test_legacy_research_route_unaffected(client: TestClient):
    """The V8.3 /api/research honest-BLOCKED contract must remain intact."""
    user = _u()
    started = client.post("/api/research", json={
        "question": "Legacy contract check", "user_id": user}).json()["session"]
    assert started["provider_state"] == "RESEARCH PROVIDER NOT CONFIGURED"
    assert started["state"] == "BLOCKED"


# ------------------------------------------------------------- cognitive tools
def test_cognitive_tools_include_research(runtime: Runtime, server):
    from app.agent.cognitive_tools import build_cognitive_tools
    user = _u()
    tools = build_cognitive_tools(runtime.cognition, user, thread_id="t1")
    names = {t.name for t in tools}
    for expected in ("start_research", "fetch_research_source", "list_research",
                     "inspect_research", "inspect_research_evidence",
                     "inspect_research_claims"):
        assert expected in names


def test_cognitive_tool_start_and_fetch_flow(runtime: Runtime, server):
    from app.agent.cognitive_tools import build_cognitive_tools
    user = _u()
    tools = build_cognitive_tools(runtime.cognition, user, thread_id="t1")
    by_name = {t.name: t for t in tools}

    started = json.loads(by_name["start_research"].func(question="What is the release date?"))
    assert started["status"] == "CREATED"
    session_id = started["session"]["id"]

    fetched = json.loads(by_name["fetch_research_source"].func(
        url=_url(server), session_id=session_id))
    assert fetched["status"] == "COMPLETED"

    claims = json.loads(by_name["inspect_research_claims"].func(session_id=session_id))
    assert claims["status"] == "OK"
    assert "capped" in claims["note"].lower()

    evidence = json.loads(by_name["inspect_research_evidence"].func(session_id=session_id))
    assert evidence["status"] == "OK"


def test_cognitive_tool_never_invents_url_is_documented(runtime: Runtime):
    from app.agent.cognitive_tools import build_cognitive_tools
    user = _u()
    tools = build_cognitive_tools(runtime.cognition, user, thread_id="t1")
    by_name = {t.name: t for t in tools}
    assert "never invent" in by_name["fetch_research_source"].description.lower()
    assert "no search engine" in by_name["start_research"].description.lower() or \
           "search engine" in by_name["start_research"].description.lower()


# --------------------------------------------------------- explanation engine
def test_explanation_engine_explains_research_claim(runtime: Runtime, server):
    user = _u()
    engine = runtime.cognition.research_engine
    session = engine.start(user, "Explainable research")
    engine.fetch(user, session["id"], _url(server))
    claim = engine.claims(user, session["id"])[0]

    exp = runtime.cognition.explanation_engine.explain(
        user, subject_kind="research_claim", subject_id=claim["id"], query_intent="what_evidence")
    assert exp["explanation_type"] == "RESEARCH_EVIDENCE"
    assert exp["supporting_evidence"]
    factor_names = {f["name"] for f in exp["decisive_factors"]}
    assert "claim_confidence" in factor_names
    capped_factor = next(f for f in exp["decisive_factors"] if f["name"] == "claim_confidence")
    assert "capped" in capped_factor["description"].lower()


def test_explanation_engine_why_not_on_uncontested_claim(runtime: Runtime, server):
    """A claim with no conflicts should still explain honestly under why_not
    (no fabricated competing claim)."""
    user = _u()
    engine = runtime.cognition.research_engine
    session = engine.start(user, "Explainable claim without conflict")
    engine.fetch(user, session["id"], _url(server))
    claim = engine.claims(user, session["id"])[0]
    exp = runtime.cognition.explanation_engine.explain(
        user, subject_kind="research_claim", subject_id=claim["id"], query_intent="why_not")
    assert exp["explanation_type"] == "RESEARCH_EVIDENCE"


def test_explanation_engine_reports_insufficient_evidence_for_unknown_research(runtime: Runtime):
    user = _u()
    exp = runtime.cognition.explanation_engine.explain(
        user, subject_kind="research", subject_id="rsess_doesnotexist", query_intent="why")
    assert "INSUFFICIENT EVIDENCE" in exp["summary"]
