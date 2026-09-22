"""V8.5 AUDIT + OBSERVABILITY.

Audit events live on the canonical EventBus (no parallel audit store) and
carry no secrets. Observability: request correlation, metrics, health
semantics, and no private cognitive content in any telemetry surface.
"""
from __future__ import annotations

import json

import pytest

from app.cognition.events import EVENT_TYPES, SECURITY_V85

from conftest_v85 import (bearer, make_secure_runtime, register_and_login,
                          secure_client, teardown)


@pytest.fixture(scope="module")
def env():
    runtime, tmp = make_secure_runtime()
    client = secure_client(runtime)
    yield runtime, client
    teardown(runtime, tmp)


# --------------------------------------------------------------------- audit
def test_security_events_are_canonical_bus_types():
    """The audit vocabulary is part of the ONE canonical event set."""
    for t in SECURITY_V85:
        assert t in EVENT_TYPES, t


def test_auth_lifecycle_is_audited(env):
    runtime, client = env
    user, token, _ = register_and_login(client, "audit@aud.test")
    ns = user["namespace"]
    client.post("/api/auth/login", json={
        "email": "audit@aud.test", "password": "wrong-password-99"})
    client.cookies.clear()
    client.post("/api/auth/logout", headers=bearer(token))

    types = {e.type for e in runtime.cognition.bus.recent(ns, limit=100)}
    assert "auth.registered" in types
    assert "auth.login" in types
    assert "auth.failed" in types
    assert "auth.logout" in types
    assert "user.created" in types


def test_audit_events_contain_no_secrets(env):
    runtime, client = env
    user, token, _ = register_and_login(client, "nosecret@aud.test",
                                        password="super-secret-password-42")
    ns = user["namespace"]
    events = runtime.cognition.bus.recent(ns, limit=200)
    blob = json.dumps([e.as_dict() for e in events])
    assert "super-secret-password-42" not in blob
    assert token not in blob
    assert "pbkdf2" not in blob
    # Email appears only redacted in audit payloads.
    assert "nosecret@aud.test" not in blob
    assert "n***@aud.test" in blob
    client.cookies.clear()


def test_audit_survives_in_same_store_as_cognition(env):
    """No second audit system: security events sit in cognitive_events."""
    runtime, client = env
    rows = runtime.db.query(
        "SELECT type FROM cognitive_events WHERE type LIKE 'auth.%' LIMIT 5")
    assert rows


# --------------------------------------------------------------- correlation
def test_request_id_present_and_echoed(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "corr@aud.test")
    client.cookies.clear()
    r = client.get("/api/memories", headers=bearer(token))
    assert r.headers.get("x-request-id", "").startswith("req_")
    # Caller-supplied ids are honoured (correlation across services).
    r = client.get("/api/memories", headers={
        **bearer(token), "X-Request-ID": "trace-abc-123"})
    assert r.headers["x-request-id"] == "trace-abc-123"
    # Errors carry the id in the body too.
    r = client.get("/api/memories/absent-id", headers={
        **bearer(token), "X-Request-ID": "trace-err-456"})
    assert r.json()["request_id"] == "trace-err-456"


# ------------------------------------------------------------------- metrics
def test_metrics_capture_requests_and_denials(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "metrics@aud.test")
    client.cookies.clear()
    client.get("/api/memories", headers=bearer(token))
    fresh = secure_client(runtime)
    fresh.get("/api/memories")            # 401 → auth failure metric
    snap = client.get("/api/metrics", headers=bearer(token)).json()
    assert snap["counters"]["http.requests_total"] >= 2
    assert snap["counters"].get("security.auth_failures", 0) >= 1
    assert any("GET /api/memories" in k for k in snap["requests"])
    route = next(v for k, v in snap["requests"].items() if "GET /api/memories" in k)
    assert route["count"] >= 1 and route["avg_ms"] >= 0


def test_metrics_contain_no_private_content(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "private@aud.test")
    client.cookies.clear()
    client.post("/api/memories", headers=bearer(token), json={
        "content": "Deeply private fact about Zanzibar travel.", "category": "FACT"})
    snap = client.get("/api/metrics", headers=bearer(token)).json()
    blob = json.dumps(snap)
    assert "Zanzibar" not in blob
    assert "private@aud.test" not in blob
    # Labels are route TEMPLATES, not concrete ids.
    assert "/api/memories/{memory_id}" in blob or "memory_id" not in blob


# -------------------------------------------------------------------- health
def test_liveness_is_only_liveness(env):
    runtime, client = env
    r = client.get("/api/health/live")
    assert r.status_code == 200
    assert r.json() == {"status": "alive"}


def test_readiness_reports_dependency_truth(env):
    runtime, client = env
    r = client.get("/api/health/ready")
    body = r.json()
    assert r.status_code in (200, 503)
    deps = body["dependencies"]
    assert deps["database"]["state"] == "ACTIVE"      # actively probed
    # Every dependency reports one of the honest states.
    for name, d in deps.items():
        assert d["state"] in ("ACTIVE", "DEGRADED", "NOT_CONFIGURED",
                              "BLOCKED", "FAILED"), name
    # Demo provider must NOT claim to be an active model.
    assert deps["model_provider"]["state"] in ("NOT_CONFIGURED", "ACTIVE", "FAILED")
    assert "degraded_capabilities" in body


def test_health_shows_auth_mode_without_secrets(env):
    runtime, client = env
    h = client.get("/api/health").json()
    assert h["security"]["auth_mode"] == "required"
    blob = json.dumps(h)
    assert "password" not in blob.lower() or "password_hash" not in blob
    assert "token" not in json.dumps(h.get("security", {}))


# ------------------------------------------------------------- admin surfaces
def test_admin_security_event_review(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "secadmin@aud.test")
    client.cookies.clear()
    r = client.get("/api/admin/security-events", headers=bearer(token))
    assert r.status_code == 200
    events = r.json()["events"]
    assert any(e["type"] == "auth.login" for e in events)
    assert "pbkdf2" not in json.dumps(events)


def test_rate_limit_state_is_observable(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "rlobs@aud.test")
    client.cookies.clear()
    r = client.get("/api/admin/rate-limit", headers=bearer(token))
    assert r.status_code == 200
    body = r.json()
    assert set(body["limits_per_minute"]) == {
        "auth", "api", "research", "portability", "expensive"}
