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


# V8.3 continuous-cognition tables. Kept in a separate constant so the V8/V8.1
# schema above is provably untouched; both are executed at startup.
SCHEMA_V83 = """
-- ============================================================ V8.3 CONTINUOUS
-- Long-running objectives that span many conversations (§10).
CREATE TABLE IF NOT EXISTS missions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    state TEXT NOT NULL DEFAULT 'draft',
    priority REAL NOT NULL DEFAULT 0.5,
    scope TEXT,
    constraints TEXT,
    success_criteria TEXT,
    progress REAL NOT NULL DEFAULT 0.0,
    next_step TEXT,
    blocked_reason TEXT,
    waiting_on TEXT,
    source TEXT,
    confidence REAL NOT NULL DEFAULT 0.6,
    evidence TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_activity_at TEXT,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_missions_user ON missions(user_id, state);

-- Bounded next actions for a mission. Never an auto-generated mega-plan.
CREATE TABLE IF NOT EXISTS mission_steps (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    kind TEXT NOT NULL DEFAULT 'task',
    depends_on TEXT,
    evidence TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mission_steps ON mission_steps(mission_id, state);

-- Every mission state change, with the reason and the evidence for it.
CREATE TABLE IF NOT EXISTS mission_events (
    id TEXT PRIMARY KEY,
    mission_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    change TEXT NOT NULL,
    previous_state TEXT,
    new_state TEXT,
    reason TEXT,
    evidence TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mission_events ON mission_events(mission_id, id DESC);

-- Links between missions and the rest of cognition (memories, world, goals...).
CREATE TABLE IF NOT EXISTS mission_links (
    mission_id TEXT NOT NULL,
    subject_kind TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    relation TEXT NOT NULL DEFAULT 'relates_to',
    created_at TEXT NOT NULL,
    PRIMARY KEY (mission_id, subject_kind, subject_id, relation)
);

-- Canonical observations (§17). Evidence, NOT automatically memory.
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source TEXT NOT NULL,
    origin TEXT NOT NULL,
    content TEXT NOT NULL,
    epistemic_status TEXT NOT NULL DEFAULT 'OBSERVED',
    confidence REAL NOT NULL DEFAULT 0.6,
    scope TEXT,
    provenance TEXT,
    subject_kind TEXT,
    subject_id TEXT,
    correlation_id TEXT,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observations_user ON observations(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_observations_subject ON observations(subject_kind, subject_id);

-- Background cognition cycles (§13/§14). A cycle that found nothing is
-- recorded as having found nothing - never dressed up as activity.
CREATE TABLE IF NOT EXISTS background_cycles (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    trigger TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'running',
    tasks_run TEXT,
    findings TEXT,
    changes_made INTEGER NOT NULL DEFAULT 0,
    skipped_reason TEXT,
    error TEXT,
    duration_ms INTEGER,
    started_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_bg_cycles ON background_cycles(user_id, id DESC);

-- Ingested documents and their lifecycle (§6).
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    filename TEXT NOT NULL,
    media_type TEXT,
    checksum TEXT,
    bytes_len INTEGER NOT NULL DEFAULT 0,
    state TEXT NOT NULL DEFAULT 'INGESTED',
    parser TEXT,
    pages INTEGER,
    extracted_chars INTEGER NOT NULL DEFAULT 0,
    understanding TEXT,
    detail TEXT,
    replaces_id TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id, state);

-- Connector registry (§26). Rows describe INTERFACES, never fake data.
CREATE TABLE IF NOT EXISTS connectors (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'NOT CONNECTED',
    capabilities TEXT,
    scopes TEXT,
    detail TEXT,
    last_checked_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_connectors_user ON connectors(user_id, name);

-- Research sessions (§27). State model only until a provider exists.
CREATE TABLE IF NOT EXISTS research_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    question TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'DRAFT',
    provider_state TEXT NOT NULL DEFAULT 'RESEARCH PROVIDER NOT CONFIGURED',
    claims TEXT,
    contradictions TEXT,
    open_questions TEXT,
    conclusion TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_user ON research_sessions(user_id, id DESC);
"""

# V8.4.1 experience -> skill -> principle persistence. These tables are
# additive and deliberately reference stable ids rather than embedding evidence
# blobs in the learned object. That keeps provenance queryable and auditable.
SCHEMA_V841 = """
CREATE TABLE IF NOT EXISTS experiences (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    thread_id TEXT,
    situation TEXT NOT NULL,
    context TEXT,
    intent TEXT,
    need TEXT,
    action TEXT,
    observation TEXT,
    outcome TEXT,
    consequences TEXT,
    success INTEGER,
    surprise REAL,
    regret REAL,
    confidence REAL NOT NULL DEFAULT 0.5,
    scope_kind TEXT NOT NULL DEFAULT 'user',
    scope_value TEXT,
    pattern_key TEXT,
    source TEXT NOT NULL,
    provenance TEXT NOT NULL,
    lifecycle TEXT NOT NULL DEFAULT 'observed',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experiences_user
    ON experiences(user_id, lifecycle, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_experiences_pattern
    ON experiences(user_id, pattern_key, success);

CREATE TABLE IF NOT EXISTS experience_evidence (
    experience_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'supporting',
    created_at TEXT NOT NULL,
    PRIMARY KEY (experience_id, observation_id, role)
);
CREATE INDEX IF NOT EXISTS idx_experience_evidence
    ON experience_evidence(user_id, experience_id);

CREATE TABLE IF NOT EXISTS experience_transitions (
    id TEXT PRIMARY KEY,
    experience_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    previous_lifecycle TEXT,
    lifecycle TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_experience_transitions
    ON experience_transitions(user_id, experience_id, created_at);

-- Skills and principles share one structural store because both are learned
-- abstractions with the same evidence, lifecycle, validation and usage rules.
-- `kind` preserves their distinct semantics and validation thresholds.
CREATE TABLE IF NOT EXISTS knowledge_items (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    statement TEXT NOT NULL,
    trigger_text TEXT,
    context TEXT,
    preconditions TEXT,
    procedure TEXT,
    expected_outcome TEXT,
    scope_kind TEXT NOT NULL DEFAULT 'user',
    scope_value TEXT,
    pattern_key TEXT,
    generalization_hint TEXT,
    generality REAL NOT NULL DEFAULT 0.0,
    confidence REAL NOT NULL DEFAULT 0.0,
    lifecycle TEXT NOT NULL DEFAULT 'candidate',
    validation_status TEXT NOT NULL DEFAULT 'pending',
    source TEXT NOT NULL,
    provenance TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_user
    ON knowledge_items(user_id, kind, lifecycle, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_pattern
    ON knowledge_items(user_id, kind, pattern_key);

CREATE TABLE IF NOT EXISTS knowledge_evidence (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    evidence_kind TEXT NOT NULL,
    evidence_id TEXT NOT NULL,
    stance TEXT NOT NULL DEFAULT 'supporting',
    relation TEXT NOT NULL DEFAULT 'derived_from',
    quality REAL NOT NULL DEFAULT 0.5,
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (item_id, evidence_kind, evidence_id, stance)
);
CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_item
    ON knowledge_evidence(user_id, item_id, stance);
CREATE INDEX IF NOT EXISTS idx_knowledge_evidence_source
    ON knowledge_evidence(evidence_kind, evidence_id);

CREATE TABLE IF NOT EXISTS knowledge_validations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    decision TEXT NOT NULL,
    confidence REAL NOT NULL,
    metrics TEXT NOT NULL,
    reason TEXT NOT NULL,
    validator TEXT NOT NULL,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_validations
    ON knowledge_validations(user_id, item_id, created_at DESC);

CREATE TABLE IF NOT EXISTS abstraction_reputation (
    item_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    retrievals INTEGER NOT NULL DEFAULT 0,
    usages INTEGER NOT NULL DEFAULT 0,
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    neutral_outcomes INTEGER NOT NULL DEFAULT 0,
    evidence_contradictions INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_abstraction_reputation_user
    ON abstraction_reputation(user_id, item_kind);

CREATE TABLE IF NOT EXISTS knowledge_usages (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    influenced_kind TEXT NOT NULL,
    influenced_id TEXT NOT NULL,
    context TEXT,
    how TEXT NOT NULL,
    weight REAL NOT NULL DEFAULT 0.5,
    status TEXT NOT NULL DEFAULT 'awaiting_outcome',
    outcome_verdict TEXT,
    outcome_detail TEXT,
    outcome_evidence TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_usages_item
    ON knowledge_usages(user_id, item_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_knowledge_usages_open
    ON knowledge_usages(user_id, status);

CREATE TABLE IF NOT EXISTS knowledge_transitions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_kind TEXT NOT NULL,
    previous_lifecycle TEXT,
    lifecycle TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_transitions
    ON knowledge_transitions(user_id, item_id, created_at);
"""

# ===================== V8.4.2 ADVANCED EXPLANATION SCHEMA =====================
SCHEMA_V842 = """
CREATE TABLE IF NOT EXISTS explanation_snapshots (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    subject_kind TEXT,
    subject_id TEXT,
    explanation_type TEXT NOT NULL,
    query_intent TEXT NOT NULL DEFAULT 'why',
    summary TEXT NOT NULL,
    graph_data TEXT NOT NULL,
    evidence_ids TEXT,
    event_ids TEXT,
    decision_ids TEXT,
    causality_ids TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_explanations_user
    ON explanation_snapshots(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_explanations_subject
    ON explanation_snapshots(user_id, subject_kind, subject_id);
"""

# ================= V8.4.3 CONNECTED RESEARCH SCHEMA =================
# Additive only. The V8.3 `research_sessions` / `connectors` tables above are
# untouched — ResearchMode keeps its honest BLOCKED-until-provider contract.
# These new tables back the real ResearchEngine: sessions that actually fetch
# real URLs, with a full source/fetch/evidence/claim provenance chain.
SCHEMA_V843 = """
CREATE TABLE IF NOT EXISTS research_sessions_v2 (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    question TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'DRAFT',
    provider_state TEXT NOT NULL DEFAULT 'CONFIGURED',
    correlation_id TEXT,
    source_count INTEGER NOT NULL DEFAULT 0,
    evidence_count INTEGER NOT NULL DEFAULT 0,
    claim_count INTEGER NOT NULL DEFAULT 0,
    conflict_count INTEGER NOT NULL DEFAULT 0,
    open_questions TEXT,
    detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_v2_user
    ON research_sessions_v2(user_id, id DESC);

-- A SOURCE is the identity of a place evidence came from (a URL/domain),
-- distinct from any one FETCH attempt against it.
CREATE TABLE IF NOT EXISTS research_sources (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT,
    publisher TEXT,
    source_type TEXT NOT NULL DEFAULT 'web',
    source_quality REAL,
    availability TEXT NOT NULL DEFAULT 'UNKNOWN',
    metadata TEXT,
    discovered_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_sources_session
    ON research_sources(session_id, id);
CREATE INDEX IF NOT EXISTS idx_research_sources_user
    ON research_sources(user_id, domain);

-- The FETCH LEDGER. Every outbound attempt is recorded, including failures —
-- a failed source must never disappear from research history.
CREATE TABLE IF NOT EXISTS research_fetches (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_id TEXT,
    requested_url TEXT NOT NULL,
    final_url TEXT,
    status TEXT NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    latency_ms INTEGER,
    redirect_count INTEGER NOT NULL DEFAULT 0,
    redirect_chain TEXT,
    content_hash TEXT,
    bytes_read INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    error_detail TEXT,
    correlation_id TEXT,
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_fetches_session
    ON research_fetches(session_id, id);
CREATE INDEX IF NOT EXISTS idx_research_fetches_user
    ON research_fetches(user_id, id DESC);

-- EVIDENCE: an actual bounded excerpt retrieved from a real fetch. Never
-- created without real fetched content behind it.
CREATE TABLE IF NOT EXISTS research_evidence (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    fetch_id TEXT NOT NULL,
    excerpt TEXT NOT NULL,
    locator TEXT,
    evidence_type TEXT NOT NULL DEFAULT 'excerpt',
    evidence_strength REAL NOT NULL DEFAULT 0.5,
    source_quality REAL,
    freshness TEXT,
    injection_flags TEXT,
    retrieved_at TEXT NOT NULL,
    content_hash TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_evidence_session
    ON research_evidence(session_id, id);
CREATE INDEX IF NOT EXISTS idx_research_evidence_user
    ON research_evidence(user_id, id DESC);

-- CLAIMS: interpretations derived from evidence. Deterministic sentence-level
-- extraction in this build — documented honestly, never described as
-- semantic comprehension.
CREATE TABLE IF NOT EXISTS research_claims (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    statement TEXT NOT NULL,
    evidence_ids TEXT NOT NULL,
    source_ids TEXT NOT NULL,
    evidence_strength REAL NOT NULL DEFAULT 0.5,
    source_quality REAL,
    corroboration_count INTEGER NOT NULL DEFAULT 1,
    independent_domain_count INTEGER NOT NULL DEFAULT 1,
    freshness TEXT,
    claim_confidence REAL NOT NULL DEFAULT 0.3,
    conflict_group TEXT,
    status TEXT NOT NULL DEFAULT 'unsupported',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_claims_session
    ON research_claims(session_id, id);
CREATE INDEX IF NOT EXISTS idx_research_claims_user
    ON research_claims(user_id, id DESC);
CREATE INDEX IF NOT EXISTS idx_research_claims_conflict
    ON research_claims(session_id, conflict_group);

-- CONFLICTS: groups of claims about the same subject with differing values.
-- Both sides are always preserved — never silently resolved.
CREATE TABLE IF NOT EXISTS research_conflicts (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    conflict_group TEXT NOT NULL,
    claim_ids TEXT NOT NULL,
    subject_key TEXT,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_conflicts_session
    ON research_conflicts(session_id, id);

-- WORLD MODEL UPDATES proposed/applied from research claims. A user-driven
-- promotion, always separately recorded, never automatic belief formation.
CREATE TABLE IF NOT EXISTS research_world_updates (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    claim_id TEXT NOT NULL,
    world_entity_id TEXT,
    kind TEXT NOT NULL,
    label TEXT NOT NULL,
    proposed_confidence REAL NOT NULL,
    applied_confidence REAL,
    state TEXT NOT NULL DEFAULT 'PROPOSED',
    reason TEXT,
    correlation_id TEXT,
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_research_world_updates_session
    ON research_world_updates(session_id, id);
CREATE INDEX IF NOT EXISTS idx_research_world_updates_user
    ON research_world_updates(user_id, id DESC);
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
    # ---------------------------------------------------------------- v8.3
    # World-state freshness (§9). Staleness is per-fact, never a universal TTL.
    ("world_entities", "last_confirmed_at", "TEXT"),
    ("world_entities", "freshness_class", "TEXT"),
    ("world_entities", "freshness_reason", "TEXT"),
    ("world_entities", "stale", "INTEGER NOT NULL DEFAULT 0"),
    ("world_entities", "epistemic_status", "TEXT"),
    # Predictions gain a real evaluation window (§19).
    ("predictions", "evaluation_window_days", "REAL"),
    ("predictions", "partial", "INTEGER NOT NULL DEFAULT 0"),
    # Interventions gain the richer attention vocabulary (§15).
    ("interventions", "mission_id", "TEXT"),
    ("interventions", "suppressed_because", "TEXT"),
    # Sandbox runs are explicitly tagged SIMULATED (§20/§39).
    ("sandbox_runs", "epistemic_status", "TEXT"),
    ("sandbox_runs", "assumptions", "TEXT"),
)


class Database:
    """Thin thread-safe SQLite wrapper (one connection per thread)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._key = f"conn_{id(self)}"
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(SCHEMA_V83)
            conn.executescript(SCHEMA_V841)
            conn.executescript(SCHEMA_V842)
            conn.executescript(SCHEMA_V843)
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
