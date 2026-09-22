"""V8.5 MIGRATION: a real V8.4.4 single-user database opens under V8.5 with
every cognitive row preserved, and the legacy namespace is adopted
deterministically and idempotently by the bootstrap owner.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.runtime import Runtime

from conftest_v85 import bearer, secure_client, teardown


def _build_v844_style_database(tmp: Path):
    """Create a populated single-user database using DISABLED auth mode —
    i.e. exactly the V8.4.4 behavior path (demo namespace, no login)."""
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma",
                   auth_mode="disabled")
    rt_legacy = Runtime(cfg)
    user = cfg.demo_user_id
    rt_legacy.memory.create(user, "Legacy fact: prefers metric units.",
                            category="PREFERENCE", allow_duplicate=True)
    rt_legacy.cognition.world.upsert(user, "project", "Legacy project Delta")
    rt_legacy.cognition.missions.create(user, "Legacy mission: finish Delta")
    export = rt_legacy.portability.create_export(user)
    counts = {
        t: rt_legacy.db.query_one(f"SELECT COUNT(*) AS n FROM {t} WHERE user_id=?",
                                  (user,))["n"]
        for t in ("memories", "cognitive_events", "world_entities", "missions",
                  "portability_exports")}
    rt_legacy.close()
    return counts, export["export"]["id"]


@pytest.fixture(scope="module")
def migrated():
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-mig-"))
    legacy_counts, export_id = _build_v844_style_database(tmp)
    # Reopen the SAME database with auth required (the V8.5 upgrade moment).
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma",
                   auth_mode="required", auth_pbkdf2_iterations=1000,
                   rate_limit_enabled=False)
    runtime = Runtime(cfg)
    client = secure_client(runtime)
    yield runtime, client, legacy_counts, export_id, cfg
    teardown(runtime, tmp)


def test_reopening_under_v85_loses_nothing(migrated):
    runtime, client, legacy_counts, _export_id, cfg = migrated
    for table, expected in legacy_counts.items():
        actual = runtime.db.query_one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE user_id=?",
            (cfg.demo_user_id,))["n"]
        assert actual == expected, table


def test_owner_adopts_legacy_namespace_deterministically(migrated):
    runtime, client, legacy_counts, export_id, cfg = migrated
    r = client.post("/api/auth/register", json={
        "email": "owner@mig.test", "password": "migration-password-1",
        "display_name": "Owner"})
    assert r.status_code == 201
    token = client.post("/api/auth/login", json={
        "email": "owner@mig.test",
        "password": "migration-password-1"}).json()["token"]
    client.cookies.clear()

    # Before adoption the owner sees a fresh namespace (not the legacy data).
    before = client.get("/api/memories", headers=bearer(token)).json()
    assert all("metric units" not in m["content"] for m in before["memories"])

    r = client.post("/api/admin/migrate-legacy-namespace",
                    headers=bearer(token), json={})
    assert r.status_code == 200
    assert r.json()["migrated"] is True
    assert r.json()["namespace"] == cfg.demo_user_id

    # After adoption: full V8.4.4 cognitive state is theirs.
    after = client.get("/api/memories", headers=bearer(token)).json()
    assert any("metric units" in m["content"] for m in after["memories"])
    world = client.get("/api/world", headers=bearer(token)).json()
    import json as j
    assert "Legacy project Delta" in j.dumps(world)
    missions = client.get("/api/missions", headers=bearer(token)).json()
    assert "Legacy mission" in j.dumps(missions)
    # Portability history came across too — including the pre-upgrade export.
    exports = client.get("/api/portability/v1/exports",
                         headers=bearer(token)).json()["exports"]
    assert any(e["id"] == export_id for e in exports)


def test_migration_is_idempotent(migrated):
    runtime, client, *_ = migrated
    token = client.post("/api/auth/login", json={
        "email": "owner@mig.test",
        "password": "migration-password-1"}).json()["token"]
    client.cookies.clear()
    r = client.post("/api/admin/migrate-legacy-namespace",
                    headers=bearer(token), json={})
    assert r.status_code == 200
    assert r.json()["migrated"] is False
    assert r.json()["reason"] == "already_adopted"


def test_second_user_cannot_steal_the_legacy_namespace(migrated):
    runtime, client, _counts, _eid, cfg = migrated
    client.post("/api/auth/register", json={
        "email": "thief@mig.test", "password": "thief-password-123",
        "display_name": "Thief"})
    token = client.post("/api/auth/login", json={
        "email": "thief@mig.test", "password": "thief-password-123"}).json()["token"]
    client.cookies.clear()
    r = client.post("/api/admin/migrate-legacy-namespace",
                    headers=bearer(token),
                    json={"legacy_namespace": cfg.demo_user_id})
    assert r.status_code == 409
    # And the thief still cannot read the legacy data.
    mems = client.get("/api/memories", headers=bearer(token)).json()
    assert all("metric units" not in m["content"] for m in mems["memories"])


def test_schema_upgrade_is_additive_only(migrated):
    """No V8.4.4 table was dropped or altered destructively."""
    runtime, *_ = migrated
    tables = {r["name"] for r in runtime.db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for legacy_table in ("memories", "memory_versions", "cognitive_events",
                         "world_entities", "missions", "decisions",
                         "knowledge_items", "portability_exports",
                         "portability_imports", "conversations", "messages"):
        assert legacy_table in tables, legacy_table
    for new_table in ("tenants", "auth_users", "auth_sessions"):
        assert new_table in tables, new_table
