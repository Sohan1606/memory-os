"""SQLite persistence: memories, versions, relationships, events, conversations."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

_LOCAL = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    confidence REAL NOT NULL DEFAULT 0.7,
    status TEXT NOT NULL DEFAULT 'active',
    version INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'conversation',
    thread_id TEXT,
    reinforcement_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id, status);

CREATE TABLE IF NOT EXISTS memory_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    content TEXT NOT NULL,
    category TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_versions_memory ON memory_versions(memory_id);

CREATE TABLE IF NOT EXISTS memory_relationships (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'related',
    PRIMARY KEY (source_id, target_id)
);

CREATE TABLE IF NOT EXISTS memory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    memory_id TEXT,
    event_type TEXT NOT NULL,
    source TEXT,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_user ON memory_events(user_id, created_at);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id);

-- ===================== V8 COGNITIVE SCHEMA =====================
-- Canonical event log. Every meaningful cognitive change appends here and
-- every observability surface is derived from it.
CREATE TABLE IF NOT EXISTS cognitive_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    type TEXT NOT NULL,
    subject_kind TEXT,
    subject_id TEXT,
    summary TEXT NOT NULL,
    payload TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cog_events_user ON cognitive_events(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_cog_events_subject ON cognitive_events(subject_kind, subject_id);
CREATE INDEX IF NOT EXISTS idx_cog_events_corr ON cognitive_events(correlation_id);

-- Living world model: goals, projects, commitments, people, risks, resources.
CREATE TABLE IF NOT EXISTS world_entities (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'active',
    detail TEXT,
    confidence REAL NOT NULL DEFAULT 0.6,
    due_at TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_user ON world_entities(user_id, kind, state);

CREATE TABLE IF NOT EXISTS world_links (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'relates_to',
    PRIMARY KEY (source_id, target_id, kind)
);

-- Probabilistic, revisable intent.
CREATE TABLE IF NOT EXISTS intents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    label TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'current',
    confidence REAL NOT NULL DEFAULT 0.5,
    evidence TEXT,
    superseded_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intents_user ON intents(user_id, status);

-- Structured predictions with a real evaluation loop.
CREATE TABLE IF NOT EXISTS predictions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    subject_id TEXT,
    statement TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence TEXT,
    horizon TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    outcome TEXT,
    evaluated_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_user ON predictions(user_id, status);

-- Memory -> decision -> action -> outcome causal chain.
CREATE TABLE IF NOT EXISTS causal_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    cause_kind TEXT NOT NULL,
    cause_id TEXT NOT NULL,
    effect_kind TEXT NOT NULL,
    effect_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'influenced',
    weight REAL NOT NULL DEFAULT 0.5,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_causal_cause ON causal_links(cause_kind, cause_id);
CREATE INDEX IF NOT EXISTS idx_causal_effect ON causal_links(effect_kind, effect_id);

-- Decisions with alternatives, expectation and eventual outcome.
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    context TEXT,
    alternatives TEXT,
    chosen TEXT,
    expected_outcome TEXT,
    actual_outcome TEXT,
    tradeoffs TEXT,
    regret REAL,
    lesson TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_user ON decisions(user_id, status);

-- Evidence-based reliability per capability class. No invented scores.
CREATE TABLE IF NOT EXISTS trust_records (
    user_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, capability)
);

-- Memory reputation: did USING this memory lead to good outcomes?
CREATE TABLE IF NOT EXISTS memory_reputation (
    memory_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    supporting INTEGER NOT NULL DEFAULT 0,
    contradicting INTEGER NOT NULL DEFAULT 0,
    retrievals INTEGER NOT NULL DEFAULT 0,
    influences INTEGER NOT NULL DEFAULT 0,
    positive_outcomes INTEGER NOT NULL DEFAULT 0,
    negative_outcomes INTEGER NOT NULL DEFAULT 0,
    lifecycle TEXT NOT NULL DEFAULT 'candidate',
    updated_at TEXT NOT NULL
);

-- Learned behavioural policy (how to work with THIS user).
CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    rationale TEXT,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policies_user ON policies(user_id, key);

-- Sandbox branches. Never touch real state.
CREATE TABLE IF NOT EXISTS sandbox_runs (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    question TEXT NOT NULL,
    assumptions TEXT,
    projection TEXT,
    confidence REAL,
    created_at TEXT NOT NULL
);

-- Interventions considered, including the ones deliberately suppressed.
CREATE TABLE IF NOT EXISTS interventions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    topic TEXT NOT NULL,
    decision TEXT NOT NULL,
    expected_value REAL,
    urgency REAL,
    confidence REAL,
    interruption_cost REAL,
    rationale TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_interventions_user ON interventions(user_id, created_at);
"""


class Database:
    """Thin thread-safe SQLite wrapper (one connection per thread)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = f"conn_{id(self)}"
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        conn = getattr(_LOCAL, self._key, None)
        if conn is None:
            conn = sqlite3.connect(self.path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            setattr(_LOCAL, self._key, conn)
        return conn

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        conn = self.connect()
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        return list(self.connect().execute(sql, tuple(params)).fetchall())

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        conn = getattr(_LOCAL, self._key, None)
        if conn is not None:
            conn.close()
            setattr(_LOCAL, self._key, None)
