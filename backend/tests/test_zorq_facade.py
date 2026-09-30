"""ZORQ state facade tests (Phase Z-UI.1, WP-UI-1 — DQ-16).

The facade is GET-only and fail-closed. When zroq-core is importable (the
canonical repository layout: `pip install -e .` at the repo root) these tests
assert the real state it exposes. When it is not importable, they assert the
truthful UNAVAILABLE payload — never fabricated state.
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.runtime import Runtime
from app import zorq_facade
from app.main import app, rt

ZORQ_IMPORTABLE = importlib.util.find_spec("zroq") is not None

FACADE_PATHS = (
    "/api/zorq/status", "/api/zorq/capabilities", "/api/zorq/actions",
    "/api/zorq/audit", "/api/zorq/devices",
)


@pytest.fixture(scope="module")
def client():
    """Isolated runtime + isolated facade data dir (house fixture pattern)."""
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-zorqfacade-"))
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma")
    runtime = Runtime(cfg)
    original_settings = zorq_facade.settings
    zorq_facade.settings = cfg
    zorq_facade._CORE_STATE = None
    app.dependency_overrides[rt] = lambda: runtime
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    zorq_facade._CORE_STATE = None
    zorq_facade.settings = original_settings
    runtime.close()
    shutil.rmtree(tmp, ignore_errors=True)


def test_facade_status_truthful(client):
    res = client.get("/api/zorq/status")
    assert res.status_code == 200
    body = res.json()
    assert body["identity"] == "ZORQ"
    if ZORQ_IMPORTABLE:
        assert body["available"] is True
        core = body["core"]
        assert core["active_sessions"] == 0
        assert core["audit_integrity"] is True
        assert core["filesystem_posture"] in {"SUPPORTED", "DEGRADED", "UNAVAILABLE"}
        assert body["runtime"]["connection"] == "LOCAL"
        assert body["runtime"]["sync"] == "NOT-CONFIGURED"
        # Phase 3F-min: the browser voice transport exists (input + tracked
        # output + local barge-in); 3F-full generation control does not.
        # The token remains truthful: partial, never simulated-complete.
        assert body["runtime"]["voice"]["state"] == "PARTIALLY-IMPLEMENTED"
        assert "3F-min" in body["runtime"]["voice"]["note"]
        assert "not implemented" in body["runtime"]["voice"]["note"]
        assert body["runtime"]["multimodal"]["state"] == "NOT-IMPLEMENTED"
        assert body["memory_integration"]["canonical_authority"] == "MEMORY//OS"
        assert len(body["planes"]) == 5
        plane_names = {p["plane"] for p in body["planes"]}
        assert plane_names == {
            "Intelligence", "Continuity", "Interaction", "Action", "Evolution",
        }
    else:
        assert body["available"] is False
        assert body["status"] == "UNAVAILABLE"
        assert "reason" in body


def test_facade_capabilities_truthful(client):
    res = client.get("/api/zorq/capabilities")
    assert res.status_code == 200
    body = res.json()
    if ZORQ_IMPORTABLE:
        assert body["available"] is True
        assert body["sealed"] is True
        assert "visibility_note" in body and "not permission" in body["visibility_note"].lower()
        caps = body["capabilities"]
        assert {c["capability_id"] for c in caps} >= {
            "local.time", "device.metadata", "filesystem.approved",
        }
        for cap in caps:
            ladder = cap["ladder"]
            # VISIBLE and AVAILABLE are true; AUTHORIZED is session-bound;
            # EXECUTABLE is false through the facade — the ladder is never
            # collapsed into a single "enabled".
            assert ladder["visible"] is True
            assert ladder["available"] is True
            assert ladder["authorized"] == "SESSION-BOUND"
            assert ladder["executable"] is False
            assert cap["confirmation_mode"]  # real manifest field
            assert cap["risk"]  # real manifest field
    else:
        assert body["available"] is False


def test_facade_actions_truthful_empty(client):
    res = client.get("/api/zorq/actions")
    assert res.status_code == 200
    body = res.json()
    if ZORQ_IMPORTABLE:
        assert body["available"] is True
        phases = [step["phase"] for step in body["lifecycle"]]
        assert phases == [
            "PROPOSED", "AUTHORIZATION", "SNAPSHOT", "LEASE",
            "EXECUTION", "VERIFICATION", "AUDIT",
        ]
        assert body["records"] == []
        assert "not yet exposed" in body["records_note"]
        assert "VERIFIED" in body["status_vocabulary"]
        assert body["execution_origins"]["REMOTE"] == "NOT-IMPLEMENTED"
    else:
        assert body["available"] is False


def test_facade_audit_real_hash_chained_events(client):
    res = client.get("/api/zorq/audit")
    assert res.status_code == 200
    body = res.json()
    if ZORQ_IMPORTABLE:
        assert body["available"] is True
        assert body["integrity_verified"] is True
        events = body["events"]
        assert len(events) >= 1
        boot = [e for e in events if e["event_type"] == "core.boot"]
        assert boot, "the facade core must have real boot audit events"
        for e in events:
            assert e["event_hash"]
            assert e["sequence"] >= 1
        # Hash chain: each event commits to the previous hash.
        for prev, cur in zip(events, events[1:]):
            assert cur["sequence"] == prev["sequence"] + 1
    else:
        assert body["available"] is False


def test_facade_devices_truthful(client):
    res = client.get("/api/zorq/devices")
    assert res.status_code == 200
    body = res.json()
    if ZORQ_IMPORTABLE:
        assert body["available"] is True
        assert body["current_device"]["device_class"] == "local"
        assert body["connection"] == "LOCAL"
        assert body["sync"] == "NOT-CONFIGURED"
        assert body["authorized_devices"] == []
        assert body["available_devices"] == []
        distinctions = set(body["distinctions"])
        assert "DEVICE TRUST ≠ USER AUTHORIZATION" in distinctions
        assert "SYNC ≠ AUTHORIZATION" in distinctions
    else:
        assert body["available"] is False


def test_facade_is_get_only(client):
    """No mutating verb is accepted on any facade route (DQ-16: GET-only)."""
    for path in FACADE_PATHS:
        res = client.post(path)
        assert res.status_code == 405, f"POST {path} must be rejected (405), got {res.status_code}"


def test_facade_leaks_no_secrets(client):
    """No secret material crosses the facade boundary.

    Prose may legitimately mention the word "secrets" (e.g. the Evolution
    plane's authority boundary); what must never appear is a secret-bearing
    FIELD or value.
    """
    secret_like_keys = ("owner_secret", "secret", "password", "api_key", "token")

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                assert str(key).lower() not in secret_like_keys, (
                    f"{path} leaked secret-like key: {key}"
                )
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for idx, item in enumerate(node):
                walk(item, f"{path}[{idx}]")

    for path in FACADE_PATHS:
        walk(client.get(path).json(), path)
