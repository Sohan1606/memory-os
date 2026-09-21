"""
V8.4.3 — schema migration test (Phase 15).

Proves: a genuine V8.4.2 database (schema without the V8.4.3 research_* /
research_sessions_v2 tables) opens under V8.4.3 startup with ALL pre-existing
data intact, and immediately gains the new V8.4.3 tables. No destructive
migration: every column added by the DB layer's MIGRATIONS tuple is
additive, and this test operates a database that predates it.
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from app.persistence.db import SCHEMA, SCHEMA_V83, SCHEMA_V841, SCHEMA_V842, Database


@pytest.fixture
def legacy_v842_db_path():
    """Build a real SQLite file using ONLY the pre-V8.4.3 schema definitions,
    then populate it with representative V8/V8.1/.../V8.4.2 data — exactly
    the shape a genuine upgrade would encounter."""
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-migration-"))
    db_path = tmp / "legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    conn.executescript(SCHEMA_V83)
    conn.executescript(SCHEMA_V841)
    conn.executescript(SCHEMA_V842)

    user = f"legacy_{uuid.uuid4().hex[:8]}"
    now = "2026-01-01T00:00:00+00:00"

    conn.execute(
        "INSERT INTO memories (id,user_id,content,category,importance,"
        "confidence,status,version,source,created_at,updated_at) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?)",
        ("mem_legacy1", user, "Legacy memory from before V8.4.3", "FACT",
         0.7, 0.8, "active", 1, "conversation", now, now))

    conn.execute(
        "INSERT INTO missions (id,user_id,title,state,priority,progress,"
        "confidence,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("mission_legacy1", user, "Legacy mission", "active", 0.5, 0.2, 0.6,
         now, now))

    conn.execute(
        "INSERT INTO world_entities (id,user_id,kind,label,state,confidence,"
        "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("world_legacy1", user, "project", "Legacy project", "active", 0.6,
         now, now))
    conn.execute(
        "INSERT INTO world_changes (id,user_id,entity_id,change,"
        "previous_state,new_state,source,confidence,created_at) VALUES "
        "(?,?,?,?,?,?,?,?,?)",
        ("wc_legacy1", user, "world_legacy1", "created", None, "active",
         "conversation", 0.6, now))

    conn.execute(
        "INSERT INTO knowledge_items (id,user_id,kind,name,statement,"
        "scope_kind,generality,confidence,lifecycle,validation_status,"
        "source,provenance,created_at,updated_at) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("skill_legacy1", user, "skill", "Legacy Skill", "Do the legacy thing",
         "user", 0.5, 0.7, "trusted", "passed", "conversation", "{}", now, now))

    conn.execute(
        "INSERT INTO explanation_snapshots (id,user_id,subject_kind,"
        "subject_id,explanation_type,query_intent,summary,graph_data,"
        "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("exp_legacy1", user, "memory", "mem_legacy1", "MEMORY_RECALL", "why",
         "Legacy explanation summary", "{}", now))

    conn.execute(
        "INSERT INTO cognitive_events (user_id,thread_id,type,subject_kind,"
        "subject_id,summary,payload,created_at) VALUES (?,?,?,?,?,?,?,?)",
        (user, None, "memory.created", "memory", "mem_legacy1",
         "Remembered something new", "{}", now))

    conn.commit()
    conn.close()

    # Sanity: the new V8.4.3 tables genuinely do not exist yet.
    check = sqlite3.connect(db_path)
    tables = {r[0] for r in check.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert "research_sessions_v2" not in tables
    assert "research_claims" not in tables
    check.close()

    yield user, db_path
    shutil.rmtree(tmp, ignore_errors=True)


def test_v842_database_migrates_cleanly_into_v843(legacy_v842_db_path):
    user, db_path = legacy_v842_db_path

    # This is the real V8.4.3 startup path: constructing Database() runs the
    # full additive schema (including SCHEMA_V843) and the column migrations,
    # against the pre-existing file.
    db = Database(db_path)

    tables = {r["name"] for r in db.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for new_table in ("research_sessions_v2", "research_sources",
                      "research_fetches", "research_evidence",
                      "research_claims", "research_conflicts",
                      "research_world_updates"):
        assert new_table in tables, f"{new_table} must exist after V8.4.3 startup"

    # ---- every pre-existing row must still be there, unmodified -----------
    mem = db.query_one("SELECT * FROM memories WHERE id='mem_legacy1'")
    assert mem is not None
    assert mem["content"] == "Legacy memory from before V8.4.3"
    assert mem["user_id"] == user

    mission = db.query_one("SELECT * FROM missions WHERE id='mission_legacy1'")
    assert mission is not None
    assert mission["title"] == "Legacy mission"

    world = db.query_one("SELECT * FROM world_entities WHERE id='world_legacy1'")
    assert world is not None
    assert world["label"] == "Legacy project"
    # Additive columns introduced by MIGRATIONS must now be present, with the
    # row's identity otherwise untouched.
    assert "freshness_class" in world.keys()

    change = db.query_one("SELECT * FROM world_changes WHERE id='wc_legacy1'")
    assert change is not None

    skill = db.query_one("SELECT * FROM knowledge_items WHERE id='skill_legacy1'")
    assert skill is not None
    assert skill["name"] == "Legacy Skill"

    explanation = db.query_one(
        "SELECT * FROM explanation_snapshots WHERE id='exp_legacy1'")
    assert explanation is not None
    assert explanation["summary"] == "Legacy explanation summary"

    event = db.query_one(
        "SELECT * FROM cognitive_events WHERE subject_id='mem_legacy1'")
    assert event is not None
    assert event["type"] == "memory.created"

    # ---- the new engine can now operate against this migrated database ----
    from app.cognition.events import EventBus
    from app.cognition.research import ResearchEngine

    bus = EventBus(db)
    engine = ResearchEngine(db, bus, world_v2=None)
    session = engine.start(user, "Does research work after migration?")
    assert session["state"] == "DRAFT"
    assert len(engine.list(user)) == 1

    db.close()


def test_migration_is_idempotent(legacy_v842_db_path):
    """Opening the same (already-migrated) database twice must not duplicate
    or corrupt anything — startup migration is safe to run repeatedly."""
    user, db_path = legacy_v842_db_path
    db1 = Database(db_path)
    db1.close()
    db2 = Database(db_path)
    mem = db2.query_one("SELECT * FROM memories WHERE id='mem_legacy1'")
    assert mem is not None
    count = db2.query_one(
        "SELECT COUNT(*) AS n FROM memories WHERE id='mem_legacy1'")
    assert count["n"] == 1
    db2.close()
