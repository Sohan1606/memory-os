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

-- ===================== V8.2 COGNITIVE CORE SCHEMA =====================
-- Additive only: every V8/V8.1 table above is unchanged, so an existing
-- database opens and keeps working after upgrading.

-- Structured arbitration records (§6). One row per resolved competition
-- between memories, retained so "why did you use that one?" is answerable
-- long after the turn.
CREATE TABLE IF NOT EXISTS arbitration_records (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    query TEXT,
    winner_id TEXT,
    candidates TEXT,
    conflict INTEGER NOT NULL DEFAULT 0,
    uncertainty REAL,
    reason TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_arbitration_user ON arbitration_records(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_arbitration_winner ON arbitration_records(winner_id);

-- Memory influence ledger (§8): memory -> influence -> outcome -> impact.
-- outcome_verdict stays NULL until an outcome is genuinely observed.
CREATE TABLE IF NOT EXISTS memory_influences (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    memory_id TEXT NOT NULL,
    influenced_kind TEXT NOT NULL,
    influenced_id TEXT NOT NULL,
    how TEXT,
    weight REAL NOT NULL DEFAULT 0.5,
    outcome_verdict TEXT,
    outcome_detail TEXT,
    outcome_evidence TEXT,
    reputation_effect TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_influences_memory ON memory_influences(user_id, memory_id);
CREATE INDEX IF NOT EXISTS idx_influences_open ON memory_influences(user_id, outcome_verdict);

-- Intent transitions (§10). Intent stays probabilistic; every change records
-- what it changed from, why, and on what evidence.
CREATE TABLE IF NOT EXISTS intent_transitions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    intent_id TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    previous_intent_id TEXT,
    confidence REAL,
    uncertainty REAL,
    changed_because TEXT,
    evidence TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intent_transitions ON intent_transitions(user_id, id DESC);

-- Need-detection hypotheses (§11), with optional later correctness feedback.
CREATE TABLE IF NOT EXISTS need_hypotheses (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    need TEXT NOT NULL,
    confidence REAL NOT NULL,
    signals TEXT,
    source TEXT,
    utterance TEXT,
    was_correct INTEGER,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_need_user ON need_hypotheses(user_id, id DESC);

-- World-state change history (§12): source, evidence, previous state.
CREATE TABLE IF NOT EXISTS world_changes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    change TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT,
    source TEXT,
    confidence REAL,
    evidence TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_world_changes ON world_changes(user_id, entity_id, id DESC);

-- Per-capability trust (§17). Separate from the V8 trust_records table so a
-- failure in one capability cannot damage an unrelated one.
CREATE TABLE IF NOT EXISTS capability_trust (
    user_id TEXT NOT NULL,
    capability TEXT NOT NULL,
    task_class TEXT NOT NULL DEFAULT 'general',
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_success_at TEXT,
    last_failure_at TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, capability, task_class)
);

-- Execution traces (§4): MODEL_CALL / TOOL_DECISION / TOOL_RESULT /
-- MODEL_REVISION / FINAL_RESPONSE for one turn.
CREATE TABLE IF NOT EXISTS execution_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    correlation_id TEXT,
    thread_id TEXT,
    step INTEGER NOT NULL,
    stage TEXT NOT NULL,
    detail TEXT,
    payload TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_traces_corr ON execution_traces(correlation_id, step);
CREATE INDEX IF NOT EXISTS idx_traces_user ON execution_traces(user_id, id DESC);

-- Continuity threads (§9): what is worth carrying across conversations.
CREATE TABLE IF NOT EXISTS continuity_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    subject_kind TEXT,
    subject_id TEXT,
    summary TEXT NOT NULL,
    reason TEXT,
    relevance REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'open',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_continuity_user ON continuity_items(user_id, status);

-- Object permanence (§21): what the user is currently inspecting, so "that
-- memory" resolves to a stable id rather than display text.
CREATE TABLE IF NOT EXISTS focus_state (
    user_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    label TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, session_id, subject_kind)
);
"""

# Additive column migrations for databases created by V8/V8.1. Each entry is
# (table, column, DDL type). Applied only when the column is absent, so
# upgrading an existing deployment never loses data.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("policies", "confidence", "REAL NOT NULL DEFAULT 0.0"),
    ("policies", "evidence", "TEXT"),
    ("predictions", "expected_evaluation_at", "TEXT"),
    ("predictions", "error", "REAL"),
    ("predictions", "surprise", "REAL"),
    ("predictions", "learning_signal", "TEXT"),
    ("intents", "uncertainty", "REAL"),
    ("intents", "previous_intent_id", "TEXT"),
    ("decisions", "second_order", "TEXT"),
    ("decisions", "delayed_consequences", "TEXT"),
    ("decisions", "opportunity_cost", "TEXT"),
    ("decisions", "regret_evidence", "TEXT"),
)


class Database:
    """Thin thread-safe SQLite wrapper (one connection per thread)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = f"conn_{id(self)}"
        with self.connect() as conn:
            conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """
        Apply additive column migrations for V8/V8.1 databases.

        Only ever ADDs columns — no data is rewritten or dropped, so an older
        database keeps every row it had and V8.1 code paths still read it.
        """
        conn = self.connect()
        for table, column, ddl in MIGRATIONS:
            try:
                existing = {r["name"] for r in
                            conn.execute(f"PRAGMA table_info({table})").fetchall()}
            except sqlite3.Error:
                continue
            if not existing or column in existing:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            except sqlite3.Error:  # pragma: no cover - defensive
                continue
        conn.commit()

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
