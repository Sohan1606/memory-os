"""Focused V10.2 CORE tests: evidence model, gating, signals, lifecycle,
confirmation boundary, security scoping and portability.

Every test asserts the binding rules of docs/V10.2-MASTER-SPEC.md —
especially §6 (policy state is not evidence), §10.6 (no policy→policy
self-evidence), §8 (lifecycle), §9 (confirmation) and §12 (measurement
roles). These tests deliberately do NOT exercise the full runtime; that is
test_v102_governance_runtime.py.
"""
from __future__ import annotations

import gc
import json
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.v102_deterministic

from app.config import Settings
from app.cognition.events import EventBus, EVENT_TYPES
from app.cognition.governance import (
    DOMAINS, Evidence, LearningSignalDeriver, MIN_DISTINCT_CORRELATIONS,
    MIN_EPISODES, PolicyEvidenceAssembler, PolicyGovernanceService,
    evaluate_evidence_gate)
from app.cognition.policy_engine import (CognitivePolicyEngine, DIMENSIONS,
                                         ORIGIN_GOVERNED, ORIGIN_USER, STRONG)
from app.cognition.autonomy import AutonomyGovernor, TrustModel
from app.persistence.db import Database
from app.runtime import Runtime

# --------------------------------------------------------------- helpers


def core(tmp_path):
    """Fresh core services (no Runtime): db, bus, policy engine, governor."""
    db = Database(Path(tmp_path) / "memory.db")
    bus = EventBus(db)
    policy = CognitivePolicyEngine(db, bus)
    trust = TrustModel(db, bus)
    governor = AutonomyGovernor(db, bus, trust)
    assembler = PolicyEvidenceAssembler(db, bus)
    deriver = LearningSignalDeriver()
    governance = PolicyGovernanceService(
        db, bus, policy, governor, evidence_assembler=assembler,
        signal_deriver=deriver)
    return db, bus, policy, governor, assembler, deriver, governance


def now() -> datetime:
    return datetime.now(timezone.utc)


def rejected_intervention_evidence(n=3, *, user="u1", days=(6, 3, 1)):
    """Craft canonical-shaped Evidence for N rejected interventions."""
    out = []
    for i in range(n):
        when = (now() - timedelta(days=days[i % len(days)])).isoformat(
            timespec="seconds")
        out.append(Evidence(
            id=f"intervention_outcome:iv_{i}", kind="intervention_outcome",
            record_id=f"iv_{i}", provenance="AttentionEngine",
            correlation_id=f"turn_{i}", timestamp=when,
            epistemic_state="OBSERVED", scope=user,
            payload={"topic": "t", "decision": "mention", "verdict": "rejected"}))
    return out


def record_rejected_interventions(db, bus, user="u1", n=3,
                                  days=(6, 3, 1)):
    """Create REAL canonical rejected interventions (backdated)."""
    import uuid
    from app.cognition.attention import AttentionEngineV2
    from app.cognition.autonomy import AttentionEngine
    engine = AttentionEngine(db, bus)
    v2 = AttentionEngineV2(db, bus, engine, None, None)
    batch = uuid.uuid4().hex[:6]
    out = []
    cids = []
    for i in range(n):
        cid = f"turn_{user}_{batch}_{i}"
        att = engine.consider(user, "status update", importance=.9, urgency=.8,
                              confidence=.9, correlation_id=cid)
        v2.record_reaction(user, att["id"], False, detail="not now",
                           correlation_id=cid)
        stamp = (now() - timedelta(days=days[i % len(days)])).isoformat(
            timespec="seconds")
        db.execute("UPDATE interventions SET created_at=? WHERE id=?",
                   (stamp, att["id"]))
        db.execute(
            "UPDATE cognitive_events SET created_at=? WHERE correlation_id=? "
            "AND type LIKE 'intervention%'", (stamp, cid))
        out.append(att)
        cids.append(cid)
    out.append({"_batch": batch})
    out.append({"_cids": cids})
    return out


def seed_signal(assembler, user="u1", n=3, *, correlation_ids=None,
                name="REPEATED_FALSE_POSITIVE_INTERVENTION",
                domain="ATTENTION", target="interruption_tolerance",
                value="low"):
    """Assemble REAL evidence and build a signal over it (the freshest
    batch when correlation_ids is given, else the n newest records)."""
    evidence = [e for e in assembler.assemble(user)
                if e.kind == "intervention_outcome"
                and e.payload.get("verdict") == "rejected"]
    if correlation_ids is not None:
        evidence = [e for e in evidence if e.correlation_id in correlation_ids]
    else:
        # newest records first from the assembler; re-derive the gate-proof
        # set by preferring distinct correlation ids and distinct days.
        by_recency = sorted(evidence, key=lambda e: e.timestamp, reverse=True)
        picked: list = []
        days: set = set()
        for e in by_recency:
            day = e.timestamp[:10]
            if day not in days or len(picked) >= n:
                if e not in picked:
                    picked.append(e)
                    days.add(day)
            if len(picked) >= n:
                break
        evidence = picked[:n]
    assert len(evidence) == n, f"expected {n} rejected interventions"
    return {"signal": name, "domain": domain, "target": target,
            "proposed_value": value, "verdict": "SUFFICIENT",
            "reason": "three dismissed interventions across three turns",
            "epistemic_state": "INFERRED",
            "derivation_rule": "intervention_outcome.verdict == 'rejected' "
                               "× N≥3 fresh episodes (AttentionEngine "
                               "reactions)",
            "expected_effect": {"success": ("intervention_accepted",),
                                "harm": ("intervention_rejected",)},
            "supporting_evidence_ids": [e.id for e in evidence],
            "conflicting_evidence_ids": [],
            "evidence": [e.as_ref() for e in evidence],
            "correlation_ids": sorted({e.correlation_id for e in evidence})}


def seed_candidate(governance, db, bus, user="u1", *, domain="ATTENTION",
                   target="interruption_tolerance", value="low",
                   n=3, correlation_id="turn_seed"):
    recorded = record_rejected_interventions(db, bus, user=user, n=n)
    cids = set(recorded[-1]["_cids"])
    signal = seed_signal(governance.assembler, user=user, n=n,
                         correlation_ids=cids, target=target, value=value)
    if domain == "MODEL_HANDLING":
        signal = seed_model_error_signal(governance.assembler, user=user)
    return governance.create_candidate(
        user, domain=domain, target=target, proposed_value=value,
        reason="three dismissed interventions", signal=signal,
        correlation_id=correlation_id)


def seed_model_error_signal(assembler, user="u1", n=3, cls="USER_MODEL_ERROR"):
    from app.cognition.personal_state import PersonalStateService
    from app.cognition.v10 import ModelErrorService
    service = ModelErrorService(assembler.db, assembler.bus,
                                PersonalStateService(assembler.db, assembler.bus))
    for i in range(n):
        service.record(user, expected_state="calibrated",
                       actual_observation="wrong again",
                       classification=cls, user_model_evidence=True,
                       correlation_id=f"turn_me_{i}")
    evidence = [e for e in assembler.assemble(user) if e.kind == "model_error"]
    assert len(evidence) >= n
    return {"signal": "REPEATED_MODEL_ERROR_CLASS", "domain": "MODEL_HANDLING",
            "target": "model_handling_strategy",
            "proposed_value": "prefer_deterministic", "verdict": "SUFFICIENT",
            "reason": "repeated USER_MODEL_ERROR episodes",
            "expected_effect": {"success": (), "harm": ("model_error_class",)},
            "error_class": cls,
            "supporting_evidence_ids": [e.id for e in evidence],
            "conflicting_evidence_ids": [],
            "evidence": [e.as_ref() for e in evidence],
            "correlation_ids": sorted({e.correlation_id for e in evidence})}


def make_signal(evidence, *, name="REPEATED_FALSE_POSITIVE_INTERVENTION",
                domain="ATTENTION", target="interruption_tolerance",
                value="low"):
    return {"signal": name, "domain": domain, "target": target,
            "proposed_value": value, "verdict": "SUFFICIENT",
            "reason": "test signal", "expected_effect": {"success": (), "harm": ()},
            "supporting_evidence_ids": [e.id for e in evidence],
            "conflicting_evidence_ids": [],
            "evidence": [e.as_ref() for e in evidence]}


# ===================================================== persistence regression
def test_database_connections_are_instance_scoped(tmp_path):
    """Regression: connections were cached in a process-global thread-local
    keyed by id(self), which is not unique across instance lifetimes — a new
    Database could silently inherit a dead instance's connection to a
    DIFFERENT file (the mechanism behind the full-suite provenance failure).
    Connections must be instance-scoped."""
    for i in range(25):
        with tempfile.TemporaryDirectory() as previous:
            db = Database(Path(previous) / "one.db")
            db.execute(
                "INSERT INTO policies (id,user_id,key,value,rationale,"
                "evidence_count,created_at,updated_at) VALUES ("
                "'p1','u1','planning_preference','structured','r',1,"
                "'2026-01-01','2026-01-01')")
            del db  # no close(): mirrors suite lifecycles that rely on GC
            gc.collect()
        with tempfile.TemporaryDirectory() as fresh:
            new_db = Database(Path(fresh) / "two.db")
            row = new_db.query_one(
                "SELECT * FROM policies WHERE user_id='u1' AND "
                "key='planning_preference'")
            assert row is None, "a fresh Database saw another instance's rows"
            new_db.close()


# ============================================================ evidence model
def test_policy_rows_are_not_evidence(tmp_path):
    """§6.B/§10.6: POLICY STATE IS NOT EVIDENCE. A learned policy row (set
    through the engine itself) never appears in the assembled evidence set
    and never increases the evidence count."""
    db, bus, policy, _, assembler, _, _ = core(tmp_path)
    policy.observe("u1", "planning_preference", "structured",
                   strength=STRONG, evidence="user said plan it out")
    evidence = assembler.assemble("u1")
    assert all(e.provenance != "CognitivePolicyEngine" for e in evidence)
    assert all("policy" not in e.kind for e in evidence)
    # Same-shaped user with no policy rows assembles the same (empty) set.
    assert assembler.assemble("u1") == assembler.assemble("u_other")


def test_policy_and_governance_events_are_not_evidence(tmp_path):
    """§10.6: no policy→policy self-evidence. policy.* events (the engine's
    own state-change events) and governance.* events are structurally
    inadmissible — an adaptation's own events can never support promotion."""
    db, bus, _, _, assembler, _, _ = core(tmp_path)
    for event_type in ("policy.updated", "policy.proposed", "policy.reverted",
                       "governance.transition", "governance.consultation",
                       "governance.observation"):
        bus.emit("u1", event_type, "x", correlation_id="turn-2")
    evidence = assembler.assemble("u1", correlation_id="turn-2")
    assert evidence == []


def test_eventbus_outcome_evidence_retains_provenance(tmp_path):
    """Admissible EventBus records keep provenance 'EventBus' and a
    resolvable canonical record id."""
    db, bus, _, _, assembler, _, _ = core(tmp_path)
    event = bus.emit("u1", "outcome.observed", "shipping landed",
                     subject_kind="mission", subject_id="m1",
                     correlation_id="turn-2")
    evidence = assembler.assemble("u1", correlation_id="turn-2")
    assert len(evidence) == 1
    assert evidence[0].provenance == "EventBus"
    assert evidence[0].kind == "outcome_record"
    assert evidence[0].record_id == str(event.id)
    row = db.query_one("SELECT 1 FROM cognitive_events WHERE id=? AND user_id=?",
                       (evidence[0].record_id, "u1"))
    assert row is not None  # the reference is canonical and resolvable


def test_wrong_user_evidence_is_excluded(tmp_path):
    db, bus, _, _, assembler, _, _ = core(tmp_path)
    bus.emit("u1", "outcome.observed", "u1 fact", correlation_id="turn-u1")
    assert assembler.assemble("u2", correlation_id="turn-u1") == []
    assert assembler.assemble("u2") == []


def test_unevaluated_predictions_are_not_evidence(tmp_path):
    """L1: a prediction becomes evidence only after canonical evaluation."""
    db, bus, policy, _, assembler, _, _ = core(tmp_path)
    predictions = type("P", (), {})()
    from app.cognition.prediction import PredictionEngine
    engine = PredictionEngine(db, bus)
    open_prediction = engine.create("u1", "it will ship", 0.7)
    assert assembler.assemble("u1") == []
    engine.observe("u1", open_prediction["id"], "it happened", supports=True)
    evidence = assembler.assemble("u1")
    assert len(evidence) == 1
    assert evidence[0].kind == "evaluated_prediction"
    assert evidence[0].provenance == "PredictionEngine"
    assert evidence[0].payload["status"] == "correct"


def test_model_error_evidence_is_structured(tmp_path):
    db, bus, _, _, assembler, _, _ = core(tmp_path)
    from app.cognition.v10 import ModelErrorService
    from app.cognition.personal_state import PersonalStateService
    service = ModelErrorService(db, bus, PersonalStateService(db, bus))
    for i in range(2):
        service.record("u1", expected_state="calibrated",
                       actual_observation="wrong again",
                       classification="USER_MODEL_ERROR",
                       user_model_evidence=True,
                       correlation_id=f"t{i}")
    evidence = [e for e in assembler.assemble("u1") if e.kind == "model_error"]
    assert len(evidence) == 2
    assert {e.payload["error_class"] for e in evidence} == {"USER_MODEL_ERROR"}
    assert all(e.provenance == "ModelErrorService" for e in evidence)


def test_intervention_outcomes_use_structured_verdicts(tmp_path):
    """No keyword guessing: the verdict comes from the canonical reaction
    event or the suppression decision — never from topic text."""
    db, bus, policy, _, assembler, _, _ = core(tmp_path)
    from app.cognition.attention import AttentionEngineV2
    from app.cognition.autonomy import AttentionEngine
    engine = AttentionEngine(db, bus)
    v2 = AttentionEngineV2(db, bus, engine, None, None)
    rejected = engine.consider("u1", "beneficial success topic",
                               importance=.9, urgency=.8, confidence=.9,
                               correlation_id="t1")
    v2.record_reaction("u1", rejected["id"], False, correlation_id="t1")
    accepted = engine.consider("u1", "harmful failure topic",
                               importance=.9, urgency=.7, confidence=.8,
                               correlation_id="t2")
    v2.record_reaction("u1", accepted["id"], True, correlation_id="t2")
    presented_only = engine.consider("u1", "no reaction yet",
                                     importance=.9, urgency=.7, confidence=.8,
                                     correlation_id="t3")
    suppressed = engine.consider("u1", "left alone", importance=.1, urgency=.1,
                                 confidence=.2, correlation_id="t4")
    evidence = {e.record_id: e for e in assembler.assemble("u1")
                if e.kind == "intervention_outcome"}
    assert evidence[rejected["id"]].payload["verdict"] == "rejected"
    assert evidence[accepted["id"]].payload["verdict"] == "accepted"
    assert evidence[presented_only["id"]].payload["verdict"] is None
    assert evidence[suppressed["id"]].payload["verdict"] == "suppressed"


def test_autonomy_dispositions_need_observed_consequences(tmp_path):
    db, bus, policy, governor, assembler, _, _ = core(tmp_path)
    governor.authorize("u1", "deploy", risk_class="recommend",
                       reversible=True, confidence=.9, correlation_id="t1")
    bus.emit("u1", "action.executed", "deploy ran", subject_kind="action",
             subject_id="deploy", correlation_id="t1")
    governor.authorize("u1", "cleanup", risk_class="update_memory",
                       reversible=True, confidence=.9, correlation_id="t2")
    bus.emit("u1", "action.failed", "cleanup failed", subject_kind="action",
             subject_id="cleanup", correlation_id="t2")
    governor.authorize("u1", "pending", risk_class="read",
                       reversible=True, confidence=.9, correlation_id="t3")
    evidence = {e.payload.get("action"): e for e in assembler.assemble("u1")
                if e.kind == "autonomy_disposition"}
    assert evidence["deploy"].payload["consequence"] == "clean"
    assert evidence["cleanup"].payload["consequence"] == "failed"
    assert evidence["pending"].payload["consequence"] is None
    assert all(e.provenance == "AutonomyGovernor" for e in evidence.values())


def test_learning_references_are_included_by_reference(tmp_path):
    db, bus, _, _, assembler, _, _ = core(tmp_path)
    db.execute(
        "INSERT INTO knowledge_items (id,user_id,kind,name,statement,lifecycle,"
        "source,provenance,created_at,updated_at) VALUES ("
        "'k1','u1','skill','S','Statement works','trusted','test','test',"
        "'2026-01-01','2026-01-01')")
    evidence = [e for e in assembler.assemble("u1")
                if e.kind == "learning_reference"]
    assert len(evidence) == 1
    assert evidence[0].record_id == "k1"
    assert evidence[0].provenance == "KnowledgeService"
    # Referenced, never copied: the source row is untouched and unique.
    assert db.query_one("SELECT COUNT(*) AS n FROM knowledge_items")["n"] == 1


# ============================================================= evidence gate
def test_single_anecdote_never_creates_a_candidate_signal(tmp_path):
    """C1: one episode is INSUFFICIENT, explicitly."""
    one = rejected_intervention_evidence(1)
    gate = evaluate_evidence_gate("ATTENTION", one, [])
    assert gate["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert f"{1} of {MIN_EPISODES}" in gate["reason"]


def test_two_episodes_are_insufficient(tmp_path):
    gate = evaluate_evidence_gate("ATTENTION",
                                  rejected_intervention_evidence(2), [])
    assert gate["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_independence_requires_distinct_correlations(tmp_path):
    """Five episodes from ONE turn are one episode."""
    episodes = rejected_intervention_evidence(5, days=(1,))
    for e in episodes:
        object.__setattr__(e, "correlation_id", "turn_same")
    gate = evaluate_evidence_gate("ATTENTION", episodes, [])
    assert gate["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert gate["counts"]["distinct_correlations"] == 1


def test_temporal_spread_requires_two_days(tmp_path):
    episodes = rejected_intervention_evidence(3, days=(0,))
    gate = evaluate_evidence_gate("ATTENTION", episodes, [])
    assert gate["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "distinct day(s)" in gate["reason"]


def test_stale_evidence_stops_counting(tmp_path):
    episodes = rejected_intervention_evidence(3, days=(400, 395, 390))
    gate = evaluate_evidence_gate("ATTENTION", episodes, [])
    assert gate["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert gate["counts"]["stale_excluded"] == 3


def test_conflicting_evidence_blocks_validation(tmp_path):
    supporting = rejected_intervention_evidence(3)
    conflicting = rejected_intervention_evidence(2, days=(5, 2))
    for e in conflicting:
        object.__setattr__(e, "id", f"intervention_outcome:ok_{e.record_id}")
        object.__setattr__(e, "payload", {**e.payload, "verdict": "accepted"})
    gate = evaluate_evidence_gate("ATTENTION", supporting, conflicting)
    assert gate["verdict"] == "CONFLICTING_EVIDENCE"
    assert gate["supporting_ids"] and gate["conflicting_ids"]


def test_sufficient_gate(tmp_path):
    gate = evaluate_evidence_gate("ATTENTION",
                                  rejected_intervention_evidence(3), [])
    assert gate["verdict"] == "SUFFICIENT"
    assert gate["counts"]["episode_count"] == MIN_EPISODES
    assert gate["counts"]["distinct_correlations"] >= MIN_DISTINCT_CORRELATIONS


# ============================================================ learning signal
def test_deriver_produces_structured_signals_with_provenance(tmp_path):
    deriver = LearningSignalDeriver()
    evidence = rejected_intervention_evidence(3)
    signals = deriver.derive(evidence, user_id="u1")
    by_name = {s["signal"]: s for s in signals}
    signal = by_name["REPEATED_FALSE_POSITIVE_INTERVENTION"]
    assert signal["verdict"] == "SUFFICIENT"
    assert signal["domain"] == "ATTENTION"
    assert signal["target"] == "interruption_tolerance"
    assert signal["proposed_value"] == "low"
    assert signal["epistemic_state"] == "INFERRED"
    assert signal["derivation_rule"]
    assert signal["threshold"]["min_episodes"] == MIN_EPISODES
    assert signal["supporting_evidence_ids"] == [e.id for e in evidence]
    assert signal["correlation_ids"] == ["turn_0", "turn_1", "turn_2"]
    assert signal["scope"] == "u1"


def test_deriver_is_deterministic_not_keyword_based(tmp_path):
    """Anti-substring regression: payload text containing 'success'/
    'beneficial'/'harmful'/'failure' must never change a structured verdict."""
    deriver = LearningSignalDeriver()
    noisy = []
    for i, e in enumerate(rejected_intervention_evidence(3)):
        object.__setattr__(e, "payload", {**e.payload,
                                          "topic": "harmful failure success "
                                                   "beneficial positive"})
        noisy.append(e)
    signals = {s["signal"]: s for s in deriver.derive(noisy, user_id="u1")}
    assert signals["REPEATED_FALSE_POSITIVE_INTERVENTION"]["verdict"] == "SUFFICIENT"
    assert signals["REPEATED_SUCCESSFUL_INTERVENTION"]["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_deriver_reports_insufficient_evidence_explicitly(tmp_path):
    deriver = LearningSignalDeriver()
    signals = {s["signal"]: s for s in
               deriver.derive(rejected_intervention_evidence(2), user_id="u1")}
    signal = signals["REPEATED_FALSE_POSITIVE_INTERVENTION"]
    assert signal["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "INSUFFICIENT EVIDENCE" in signal["reason"]
    assert "window" in signal["reason"]


def test_prediction_and_decision_signals_have_no_dishonest_target(tmp_path):
    """REPEATED_PREDICTION_FAILURE / REPEATED_SUCCESSFUL_DECISION are real
    signals, but no existing policy dimension is honestly justified by them:
    candidacy stays NONE (documented CORE decision)."""
    deriver = LearningSignalDeriver()

    def prediction(status, i, day):
        return Evidence(
            id=f"evaluated_prediction:p{i}", kind="evaluated_prediction",
            record_id=f"p{i}", provenance="PredictionEngine",
            correlation_id=f"tp{i}",
            timestamp=(now() - timedelta(days=day)).isoformat(timespec="seconds"),
            epistemic_state="OBSERVED", scope="u1",
            payload={"statement": "s", "status": status, "confidence": .7})

    evidence = [prediction("incorrect", i, d) for i, d in enumerate((6, 3, 1))]
    signals = {s["signal"]: s for s in deriver.derive(evidence, user_id="u1")}
    failed = signals["REPEATED_PREDICTION_FAILURE_OF_CLASS"]
    assert failed["verdict"] == "SUFFICIENT"
    assert failed["candidacy"] == "NONE"
    assert failed["target"] is None
    assert deriver.sufficient(list(signals.values())) == []


def test_model_error_signal_is_per_class_and_governance_only(tmp_path):
    deriver = LearningSignalDeriver()

    def model_error(cls, i, day):
        return Evidence(
            id=f"model_error:me{i}", kind="model_error", record_id=f"me{i}",
            provenance="ModelErrorService", correlation_id=f"tm{i}",
            timestamp=(now() - timedelta(days=day)).isoformat(timespec="seconds"),
            epistemic_state="OBSERVED", scope="u1",
            payload={"error_class": cls, "expected_state": "x",
                     "actual_observation": "y"})

    evidence = ([model_error("USER_MODEL_ERROR", i, d) for i, d in enumerate((5, 3, 1))]
                + [model_error("TIMING_ERROR", 9, 2)])
    model_signals = [s for s in deriver.derive(evidence, user_id="u1")
                     if s["signal"] == "REPEATED_MODEL_ERROR_CLASS"]
    # One signal per error class, scoped to that class.
    assert len(model_signals) == 2
    by_class = {s["error_class"]: s for s in model_signals}
    assert by_class["USER_MODEL_ERROR"]["verdict"] == "SUFFICIENT"
    assert by_class["TIMING_ERROR"]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert by_class["USER_MODEL_ERROR"]["domain"] == "MODEL_HANDLING"


def test_deriver_creates_no_learning_objects(tmp_path):
    """L6: no duplicate learning engine — derivation writes nothing."""
    db, bus, policy, governor, assembler, deriver, _ = core(tmp_path)
    deriver.derive(rejected_intervention_evidence(3), user_id="u1")
    for table in ("knowledge_items", "knowledge_evidence", "experiences",
                  "experience_evidence"):
        assert db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0


# ========================================================== governance state
def test_full_lifecycle_with_append_only_history(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    candidate = seed_candidate(governance, db, bus)
    assert candidate["state"] == "PROPOSED"
    active = governance.confirm("u1", candidate["id"], confirmation=True,
                                reason="yes", correlation_id="confirm-1")
    assert active["state"] == "ACTIVE"
    history = governance.history("u1", candidate["id"])
    assert [(h["previous_state"], h["new_state"]) for h in history] == [
        (None, "CANDIDATE"), ("CANDIDATE", "VALIDATING"),
        ("VALIDATING", "PROPOSED"), ("PROPOSED", "ACCEPTED"),
        ("ACCEPTED", "ACTIVE")]
    transitions = [e for e in bus.for_correlation("confirm-1")
                   if e.type == "governance.transition"]
    assert [(e.payload["previous_state"], e.payload["new_state"])
            for e in transitions] == [("PROPOSED", "ACCEPTED"),
                                      ("ACCEPTED", "ACTIVE")]
    for event in transitions:
        payload = event.payload
        assert payload["candidate_id"] == candidate["id"]
        assert payload["reason"] and payload["evidence_refs"]
        assert payload["correlation_id"] == "confirm-1"


def test_illegal_transitions_raise_and_change_nothing(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    candidate = seed_candidate(governance, db, bus)
    with pytest.raises(ValueError):
        governance.transition("u1", candidate["id"], "ACTIVE",
                              reason="skip the boundary", evidence_refs=[],
                              correlation_id="x")
    with pytest.raises(ValueError):
        governance.transition("u1", candidate["id"], "ACCEPTED",
                              reason="r", evidence_refs=[], correlation_id="x")
    with pytest.raises(ValueError):
        governance.transition("u1", candidate["id"], "NOT_A_STATE",
                              reason="r", evidence_refs=[], correlation_id="x")
    assert governance.get("u1", candidate["id"])["state"] == "PROPOSED"


def test_validation_parks_insufficient_candidates_with_verdict(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    # Seed only ONE real rejected intervention: an honest single anecdote.
    record_rejected_interventions(db, bus, user="u1", n=1)
    weak = [e for e in assembler.assemble("u1")
            if e.kind == "intervention_outcome"
            and e.payload.get("verdict") == "rejected"]
    assert len(weak) == 1
    candidate = governance.create_candidate(
        "u1", domain="ATTENTION", target="interruption_tolerance",
        proposed_value="low", reason="only one anecdote",
        signal=seed_signal(assembler, user="u1", n=1), correlation_id="t1")
    assert candidate["state"] == "VALIDATING"  # parked, never PROPOSED
    assert candidate["validation"]["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert "INSUFFICIENT EVIDENCE" in candidate["validation"]["reason"]
    # A parked candidate has no behavioral effect at all.
    assert policy.get("u1", "interruption_tolerance")["is_default"] is True


def test_scope_and_vocabulary_validation(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    good = rejected_intervention_evidence(3)
    with pytest.raises(ValueError, match="domain"):
        governance.create_candidate("u1", domain="SPIRIT", target="mood",
                                    proposed_value="happy", reason="r",
                                    signal=make_signal(good), correlation_id="t")
    with pytest.raises(ValueError, match="irreversible"):
        governance.create_candidate("u1", domain="AUTONOMY",
                                    target="irreversible_action_handling",
                                    proposed_value="relax", reason="r",
                                    signal=make_signal(good), correlation_id="t")
    with pytest.raises(ValueError, match="not in the finite vocabulary"):
        governance.create_candidate("u1", domain="ATTENTION",
                                    target="interruption_tolerance",
                                    proposed_value="sometImes", reason="r",
                                    signal=make_signal(good), correlation_id="t")
    with pytest.raises(ValueError, match="unreferenced support"):
        governance.create_candidate("u1", domain="ATTENTION",
                                    target="interruption_tolerance",
                                    proposed_value="low", reason="r",
                                    signal={"signal": "x"}, correlation_id="t")
    # Proposing the current value is a no-op adaptation and invalid.
    policy.observe("u1", "interruption_tolerance", "low", strength=STRONG,
                   evidence="stop interrupting me")
    with pytest.raises(ValueError, match="nothing to adapt"):
        governance.create_candidate("u1", domain="ATTENTION",
                                    target="interruption_tolerance",
                                    proposed_value="low", reason="r",
                                    signal=make_signal(good), correlation_id="t")


# =================================================== confirmation / authority
def test_proposal_has_no_behavioral_effect_before_confirmation(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    candidate = seed_candidate(governance, db, bus)
    assert candidate["state"] == "PROPOSED"
    assert policy.get("u1", "interruption_tolerance")["is_default"] is True
    assert not [e for e in bus.recent("u1", types=["policy.updated"])]


def test_confirm_requires_explicit_confirmation(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    candidate = seed_candidate(governance, db, bus)
    with pytest.raises(ValueError, match="Explicit confirmation"):
        governance.confirm("u1", candidate["id"], confirmation=False,
                           correlation_id="c")
    with pytest.raises(ValueError, match="only PROPOSED"):
        governance.reject("u1", candidate["id"], reason="no",
                          correlation_id="c")
        governance.confirm("u1", candidate["id"], confirmation=True,
                           correlation_id="c")  # REJECTED is not confirmable
    assert governance.get("u1", candidate["id"])["state"] == "REJECTED"


def test_confirm_blocked_by_autonomy_governor_changes_nothing(tmp_path):
    """The AutonomyGovernor is not weakened by V10.2: a BLOCKED disposition
    refuses acceptance, changes nothing, and claims nothing."""
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    for _ in range(4):  # demonstrated unreliability withdraws authority
        governor.trust.record("u1", "action", False)
    candidate = seed_candidate(governance, db, bus)
    result = governance.confirm("u1", candidate["id"], confirmation=True,
                                correlation_id="c")
    assert result["blocked"] is True
    assert result["candidate"]["state"] == "PROPOSED"
    assert governance.get("u1", candidate["id"])["state"] == "PROPOSED"
    assert policy.get("u1", "interruption_tolerance")["is_default"] is True
    assert not [e for e in bus.recent("u1", types=["governance.transition"])
                if e.payload.get("new_state") in ("ACCEPTED", "ACTIVE")]


def test_confirm_applies_through_the_canonical_engine_path(tmp_path):
    """C8: applying an accepted adaptation goes through the engine and emits
    the ENGINE's own policy.updated — governance never writes policy itself."""
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    candidate = seed_candidate(governance, db, bus)
    governance.confirm("u1", candidate["id"], confirmation=True,
                       reason="yes", correlation_id="confirm-1")
    assert policy.get("u1", "interruption_tolerance")["value"] == "low"
    assert policy.get("u1", "interruption_tolerance")["confidence"] >= 0.45
    updates = [e for e in bus.for_correlation("confirm-1")
               if e.type == "policy.updated"]
    assert len(updates) == 1
    assert updates[0].payload["key"] == "interruption_tolerance"
    assert updates[0].payload["to"] == "low"
    assert updates[0].payload["origin"] == ORIGIN_GOVERNED
    # Current effective policy is readable only through the engine.
    assert policy.effective("u1")["interruption_tolerance"] == "low"


def test_governed_provenance_is_distinct_from_user_instruction(tmp_path):
    """A governed adaptation is never recorded as an explicit user
    utterance; a real utterance keeps its released STRONG/user semantics."""
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    governance.confirm("u1", seed_candidate(governance, db, bus)["id"],
                       confirmation=True, reason="yes", correlation_id="c1")
    policy.apply_utterance("u1", "just give me the answer", correlation_id="c2")
    governed = policy.get("u1", "interruption_tolerance")
    user = policy.get("u1", "response_depth")
    assert governed["value"] == "low"
    assert all(e["origin"] == ORIGIN_GOVERNED for e in governed["evidence"])
    assert "Adaptation you confirmed" in governed["rationale"]
    assert user["value"] == "brief"
    assert all(e["origin"] == ORIGIN_USER for e in user["evidence"])
    assert "You told me directly" in user["rationale"]
    with pytest.raises(ValueError):
        policy.observe("u1", "interruption_tolerance", "sometimes",
                       strength=STRONG, evidence="x")  # vocabulary still holds
    with pytest.raises(ValueError):
        policy.observe("u1", "interruption_tolerance", "low", strength=STRONG,
                       evidence="x", origin="made_up_origin")


def test_model_handling_is_governance_only(tmp_path):
    """MODEL_HANDLING has no safe consumer seam: candidates may be proposed,
    activation is refused, and no engine write ever happens."""
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    candidate = seed_candidate(governance, db, bus, domain="MODEL_HANDLING",
                               target="model_handling_strategy",
                               value="prefer_deterministic")
    assert candidate["state"] == "PROPOSED"
    with pytest.raises(ValueError, match="governance-only"):
        governance.confirm("u1", candidate["id"], confirmation=True,
                           correlation_id="c1")
    assert governance.get("u1", candidate["id"])["state"] == "PROPOSED"
    assert db.query_one("SELECT COUNT(*) AS n FROM policies")["n"] == 0


def test_reject_defer_and_supersede(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    first = seed_candidate(governance, db, bus)
    governance.confirm("u1", first["id"], confirmation=True, reason="yes",
                       correlation_id="c1")
    assert policy.get("u1", "interruption_tolerance")["value"] == "low"

    # Rejecting an applied adaptation restores through the engine revert path.
    governance.reject("u1", first["id"], reason="not working",
                      correlation_id="c2")
    assert governance.get("u1", first["id"])["state"] == "REJECTED"
    assert policy.get("u1", "interruption_tolerance")["is_default"] is True
    assert bus.recent("u1", types=["policy.reverted"])

    # Supersession retains the full prior version and history (C5).
    second = seed_candidate(governance, db, bus, value="high", correlation_id="t9")
    governance.confirm("u1", second["id"], confirmation=True, reason="switch",
                       correlation_id="c3")
    assert policy.get("u1", "interruption_tolerance")["value"] == "high"
    third = seed_candidate(governance, db, bus, value="low", correlation_id="t10")
    governance.confirm("u1", third["id"], confirmation=True, reason="back",
                       correlation_id="c4")
    superseded = governance.get("u1", second["id"])
    assert superseded["state"] == "SUPERSEDED"
    assert superseded["superseded_by"] == third["id"]
    assert len(governance.history("u1", second["id"])) >= 5  # retained

    # Deferral ends influence eligibility but retains everything.
    governance.defer("u1", third["id"], reason="not now", correlation_id="c5")
    assert governance.get("u1", third["id"])["state"] == "DEFERRED"
    assert not governance.list("u1", state="ACTIVE")


def test_conflicting_candidates_resolve_at_acceptance(tmp_path):
    """C7: two conflicting proposals — accepting one rejects the other with a
    cross-reference."""
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    low = seed_candidate(governance, db, bus, value="low", correlation_id="t1")
    high = seed_candidate(governance, db, bus, value="high", correlation_id="t2")
    governance.confirm("u1", low["id"], confirmation=True, reason="pick low",
                       correlation_id="c1")
    assert governance.get("u1", high["id"])["state"] == "REJECTED"
    history = governance.history("u1", high["id"])
    assert any(low["id"] in (h["reason"] or "") for h in history)
    assert governance.get("u1", low["id"])["state"] == "ACTIVE"


# ======================================================== security / tenancy
def test_user_and_tenant_isolation(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    # u1 and u9 live in different tenants (V8.5 identity namespace).
    db.execute("INSERT INTO tenants (id,name,slug,created_at) VALUES "
               "('t1','T1','t1','2026-01-01'),('t2','T2','t2','2026-01-01')")
    for uid, tenant in (("u1", "t1"), ("u9", "t2")):
        db.execute(
            "INSERT INTO auth_users (id,tenant_id,email,display_name,"
            "password_hash,namespace,created_at,updated_at) VALUES "
            f"('{uid}','{tenant}','{uid}@x','{uid}','h','{uid}',"
            "'2026-01-01','2026-01-01')")
    candidate = seed_candidate(governance, db, bus, user="u1")
    # Direct-ID access from another user behaves as not-found (no oracle).
    assert governance.get("u2", candidate["id"]) is None
    assert governance.get("u9", candidate["id"]) is None
    with pytest.raises(KeyError):
        governance.confirm("u9", candidate["id"], confirmation=True,
                           correlation_id="c")
    with pytest.raises(KeyError):
        governance.reject("u9", candidate["id"], reason="x", correlation_id="c")
    assert governance.list("u9") == []
    assert governance.history("u9", candidate["id"]) == []
    assert governance.get("u1", candidate["id"])["state"] == "PROPOSED"


def test_tenant_scoped_rows_stay_in_their_tenant(tmp_path):
    db, bus, policy, governor, assembler, deriver, governance = core(tmp_path)
    db.execute("INSERT INTO tenants (id,name,slug,created_at) VALUES "
               "('t1','T1','t1','2026-01-01'),('t2','T2','t2','2026-01-01')")
    for uid, tenant in (("u1", "t1"), ("u9", "t2")):
        db.execute(
            "INSERT INTO auth_users (id,tenant_id,email,display_name,"
            "password_hash,namespace,created_at,updated_at) VALUES "
            f"('{uid}','{tenant}','{uid}@x','{uid}','h','{uid}',"
            "'2026-01-01','2026-01-01')")
    seed_candidate(governance, db, bus, user="u1")
    seed_candidate(governance, db, bus, user="u9")
    assert governance.list("u1")[0]["tenant_id"] == "t1"
    assert governance.list("u9")[0]["tenant_id"] == "t2"
    assert len(governance.list("u1")) == 1
    assert len(governance.list("u9")) == 1


# ============================================================== portability
def _repack(package_bytes, patch_row):
    """Rebuild an export package with `patch_row` applied, re-signing the
    manifest and integrity metadata so the corruption reaches the semantic
    validation layer instead of tripping the integrity checks."""
    import hashlib
    import io
    import zipfile
    from app.portability import canonical_bytes

    def sha(value: bytes) -> str:
        return hashlib.sha256(value).hexdigest()

    with zipfile.ZipFile(io.BytesIO(package_bytes)) as zf:
        members = {n: zf.read(n) for n in zf.namelist()}
    manifest = json.loads(members["manifest.json"])
    integrity = json.loads(members["integrity.json"])
    for name, body in list(members.items()):
        if name in ("manifest.json", "integrity.json"):
            continue
        try:
            rows = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue  # non-JSON member (e.g. binary payload)
        if not isinstance(rows, list):
            continue
        changed = False
        for row in rows:
            if isinstance(row, dict) and patch_row(row):
                changed = True
        if changed:
            from app.portability import canonical_row
            new_body = canonical_bytes(rows)
            members[name] = new_body
            for entry in manifest["files"]:
                if entry["path"] == name:
                    entry["sha256"] = sha(new_body)
                    entry["bytes"] = len(new_body)
                    if "object_hashes" in entry:
                        entry["object_hashes"] = [
                            sha(canonical_row(record)) for record in rows]
            integrity["files"][name] = sha(new_body)
    manifest_body = canonical_bytes(manifest)
    members["manifest.json"] = manifest_body
    integrity["manifest_sha256"] = sha(manifest_body)
    members["integrity.json"] = canonical_bytes(integrity)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return buffer.getvalue()


def _runtime(tmp="v102-portability-"):
    root = Path(tempfile.mkdtemp(prefix=tmp))
    return Runtime(Settings(data_dir=root, sqlite_path=root / "m.db",
                            checkpoint_path=root / "c.db",
                            chroma_path=root / "chroma",
                            disable_embeddings=True)), root


def _active_adaptation(runtime, user):
    """Drive a real governed adaptation to ACTIVE through the services."""
    cog = runtime.cognition
    record_rejected_interventions(runtime.db, cog.bus, user=user, n=3)
    signal = seed_signal(cog.policy_evidence, user=user, n=3)
    candidate = cog.policy_governance.create_candidate(
        user, domain="ATTENTION", target="interruption_tolerance",
        proposed_value="low", reason="three dismissals", signal=signal,
        correlation_id="turn_portable")
    assert candidate["state"] == "PROPOSED", candidate
    active = cog.policy_governance.confirm(user, candidate["id"],
                                           confirmation=True, reason="yes",
                                           correlation_id="confirm_portable")
    assert active["state"] == "ACTIVE"
    return active


def test_governance_export_import_round_trip(tmp_path):
    """P1/P3/P4: governance records, history and provenance survive an
    export/restore round trip in the SAME memory-os-export format."""
    runtime, root = _runtime()
    try:
        user = runtime.settings.demo_user_id
        active = _active_adaptation(runtime, user)
        runtime.cognition.policy_governance.record_consultation(
            user, active["id"], consumer="context.preferences",
            effect="entered response context", turn_correlation_id="turn_x",
            correlation_id="turn_x")
        runtime.cognition.policy_governance.record_observation(
            user, active["id"], category="HARMFUL_INFLUENCE",
            turn_correlation_id="turn_x", evidence_refs=[],
            detail="rejected under the adapted value", correlation_id="turn_y")

        exported = runtime.portability.create_export(user, domains=["user_model"])
        package = runtime.portability.export_path(
            user, exported["export"]["id"]).read_bytes()
        staged = runtime.portability.stage_import(user, package, "gov.zip")
        validation = runtime.portability.validate_import(user, staged["import"]["id"])
        assert validation["validation"]["status"] == "VALIDATED", validation

        # Wipe the governance domain only, then restore it.
        for table in ("policy_governance", "policy_governance_history",
                      "policy_governance_runs",
                      "policy_governance_consultations",
                      "policy_governance_observations"):
            runtime.db.execute(f"DELETE FROM {table} WHERE user_id=?", (user,))
        restored = runtime.portability.apply_restore(
            user, staged["import"]["id"], confirm=True, domains=["user_model"])
        assert restored["operation"]["status"] == "APPLIED"

        back = runtime.cognition.policy_governance.get(user, active["id"])
        assert back is not None and back["state"] == "ACTIVE"
        assert back["proposed_value"] == "low"
        assert back["domain"] == "ATTENTION"
        # Lifecycle history reproduced, not just the latest state (P3).
        history = runtime.cognition.policy_governance.history(user, active["id"])
        assert [(h["previous_state"], h["new_state"]) for h in history] == [
            (None, "CANDIDATE"), ("CANDIDATE", "VALIDATING"),
            ("VALIDATING", "PROPOSED"), ("PROPOSED", "ACCEPTED"),
            ("ACCEPTED", "ACTIVE")]
        # Provenance and epistemic state survive the round trip (P4).
        assert back["evidence_refs"][0]["provenance"] == "AttentionEngine"
        assert back["signal_json"]["epistemic_state"] == "INFERRED"
        assert back["signal_json"]["derivation_rule"]
        assert runtime.cognition.policy_governance.consultations(
            user, active["id"])
        measurements = runtime.cognition.policy_governance.measurements(
            user, active["id"])
        assert measurements["outcome_evidence"]["observation_count"] == 1

        # Re-import is idempotent: same ids, no duplicates (P3).
        staged2 = runtime.portability.stage_import(user, package, "gov2.zip")
        validation2 = runtime.portability.validate_import(user, staged2["import"]["id"])
        assert validation2["validation"]["status"] == "VALIDATED"
        restored2 = runtime.portability.apply_restore(
            user, staged2["import"]["id"], confirm=True, domains=["user_model"])
        assert restored2["operation"]["applied"] == 0
        assert len(runtime.cognition.policy_governance.list(user)) == 1
    finally:
        runtime.close()
        shutil.rmtree(root, ignore_errors=True)


def test_portability_rejects_dangling_evidence_references(tmp_path):
    """P2: restore never creates dangling evidence references."""
    runtime, root = _runtime()
    try:
        user = runtime.settings.demo_user_id
        active = _active_adaptation(runtime, user)
        exported = runtime.portability.create_export(user, domains=["user_model"])
        package_path = runtime.portability.export_path(user, exported["export"]["id"])
        # Corrupt the package: point the frozen evidence at a missing record,
        # re-signing the integrity metadata so the semantic layer sees it.
        def dangle(row):
            if row.get("id") == active["id"]:
                row["evidence_refs"] = [
                    {"kind": "intervention_outcome", "id": "iv_missing",
                     "provenance": "AttentionEngine"}]
                return True
            return False
        package = _repack(package_path.read_bytes(), dangle)
        staged = runtime.portability.stage_import(user, package, "bad.zip")
        validation = runtime.portability.validate_import(user, staged["import"]["id"])
        assert validation["validation"]["status"] == "REJECTED"
        reasons = json.dumps(validation["validation"])
        assert "iv_missing" in reasons
    finally:
        runtime.close()
        shutil.rmtree(root, ignore_errors=True)


def test_portability_reports_same_id_conflicts(tmp_path):
    """P3: same-id different-content import is rejected with fingerprints."""
    runtime, root = _runtime()
    try:
        user = runtime.settings.demo_user_id
        active = _active_adaptation(runtime, user)
        exported = runtime.portability.create_export(user, domains=["user_model"])
        package_path = runtime.portability.export_path(user, exported["export"]["id"])
        # Same id, different content: flip the proposed value.
        def patch(row):
            if row.get("id") == active["id"]:
                row["proposed_value"] = "high"  # different content
                return True
            return False
        package = _repack(package_path.read_bytes(), patch)
        staged = runtime.portability.stage_import(user, package, "c.zip")
        validation = runtime.portability.validate_import(user, staged["import"]["id"])
        assert validation["validation"]["conflict_count"] >= 1
        conflicts = runtime.portability.list_conflicts(user, staged["import"]["id"])
        assert any(c["table_name"] == "policy_governance" for c in conflicts)
    finally:
        runtime.close()
        shutil.rmtree(root, ignore_errors=True)


def test_governance_events_are_registered(tmp_path):
    for event_type in ("governance.transition", "governance.consultation",
                       "governance.observation"):
        assert event_type in EVENT_TYPES
    from app.cognition.events import LABELS
    for event_type in ("governance.transition", "governance.consultation",
                       "governance.observation"):
        assert event_type in LABELS
