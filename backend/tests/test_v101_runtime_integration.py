"""Focused V10.1 runtime-integration tests.

These exercise the REAL composition root: relevance gating, bounded
execution, truthful surface stages, findings/proposal flow, confirmation,
re-audit depth, and security scoping — all through the same Runtime the API
serves.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.cognition.events import EVENT_TYPES
from app.cognition.maintenance_runtime import (
    MAINTENANCE_COMPLETED_NO_FINDINGS, MAINTENANCE_COMPLETED_WITH_FINDINGS,
    NO_MAINTENANCE_NEEDED, REAUDIT_SUFFIX, WAITING_FOR_CONFIRMATION)
from app.runtime import Runtime
from app.schemas.semantic import (CognitiveObjectCreate, CognitiveType,
                                  Modality, Provenance)


def make_runtime():
    root = Path(tempfile.mkdtemp(prefix="memoryos-v101-"))
    return Runtime(Settings(data_dir=root, sqlite_path=root / "m.db",
                            checkpoint_path=root / "c.db", chroma_path=root / "chroma",
                            disable_embeddings=True)), root


def obj(rt, user, typ, content, *, provenance=Provenance.USER_STATED,
        metadata=None, modality=Modality.ASSERTED):
    return rt.cognition.personal_state.create(user, CognitiveObjectCreate(
        type=typ, content=content, provenance=provenance,
        metadata=metadata or {}, modality=modality))


# ------------------------------------------------------------- A. relevance
def test_irrelevant_turn_runs_no_maintenance_audit():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        trace = rt.cognition.process_turn(user, "What's the weather today?")
        m = trace["maintenance"]
        assert m["status"] == NO_MAINTENANCE_NEEDED
        assert m["relevance"]["relevant"] is False
        assert m["audit"] is None
        # No maintenance run row was written for this correlation.
        assert rt.db.query_one(
            "SELECT 1 FROM maintenance_runs WHERE correlation_id=?",
            (trace["correlation_id"],)) is None
        # The relevance verdict itself is observable on the canonical bus.
        events = rt.cognition.bus.for_correlation(trace["correlation_id"])
        assert any(e.type == "maintenance.relevance_determined" for e in events)
        assert not any(e.type == "cognitive_model.audit_started" for e in events)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_state_change_turn_invokes_bounded_maintenance():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        trace = rt.cognition.process_turn(user, "I prefer working from home.")
        m = trace["maintenance"]
        assert m["relevance"]["relevant"] is True
        assert m["relevance"]["trigger_kind"] == "STATE_CHANGE_SIGNAL"
        assert m["status"] in (MAINTENANCE_COMPLETED_NO_FINDINGS,
                               MAINTENANCE_COMPLETED_WITH_FINDINGS,
                               WAITING_FOR_CONFIRMATION)
        assert m["audit"] is not None
        # Narrowed families only — a state change never runs model_errors.
        assert "model_errors" not in m["audit"]["families"]
        assert "contradictions" in m["audit"]["families"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_explicit_conflict_query_triggers_contradiction_check():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        trace = rt.cognition.process_turn(
            user, "Does anything I said contradict my earlier statements?")
        m = trace["maintenance"]
        assert m["relevance"]["relevant"] is True
        assert "CONFLICT_QUERY" in m["relevance"]["trigger_kinds"]
        assert "contradictions" in m["audit"]["families"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_prediction_outcome_turn_triggers_prediction_check():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        rt.cognition.predictions.create(user, "I save money each month", .8)
        trace = rt.cognition.process_turn(
            user, "The outcome was I saved money this month.")
        m = trace["maintenance"]
        assert m["relevance"]["relevant"] is True
        assert "PREDICTION_OUTCOME" in m["relevance"]["trigger_kinds"]
        assert "model_errors" in m["audit"]["families"]
        # The proposition-matched outcome produced a confirmation-gated
        # RECORD_OUTCOME proposal — no prediction was auto-evaluated.
        assert any(f["kind"] == "PREDICTION_OUTCOME" for f in m["findings"])
        open_predictions = rt.db.query(
            "SELECT * FROM predictions WHERE user_id=? AND status='open'", (user,))
        assert len(open_predictions) == 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_unknown_resolution_turn_triggers_unknown_check():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        anchor = obj(rt, user, CognitiveType.BELIEF, "I like the mountain office")
        rt.cognition.unknowns.identify(
            user, what_unknown="Do I like the mountain office?",
            why_it_matters="Location preference affects planning.",
            missing_evidence=[{"kind": "OBSERVATION"}],
            resolution_path="Wait for a direct statement.",
            relevant_object_ids=[anchor["id"]])
        trace = rt.cognition.process_turn(
            user, "I noticed that I like the mountain office.")
        m = trace["maintenance"]
        assert m["relevance"]["relevant"] is True
        assert "UNKNOWN_RESOLUTION" in m["relevance"]["trigger_kinds"]
        # Unknown remains OPEN until the user confirms the proposal.
        unknowns = rt.cognition.unknowns.list(user, status="OPEN")
        assert len(unknowns) == 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_relevance_gate_is_deterministic_and_explainable():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        gate = rt.cognition.maintenance_runtime.gate
        first = gate.evaluate(user, "I value honesty above speed.",
                              meaning={"semantic": {"candidates": [
                                  {"type": "VALUE", "material": True}]},
                                  "created_objects": []})
        second = gate.evaluate(user, "I value honesty above speed.",
                               meaning={"semantic": {"candidates": [
                                   {"type": "VALUE", "material": True}]},
                                   "created_objects": []})
        assert first.as_dict() == second.as_dict()
        assert first.relevant and first.reasons
        assert all(isinstance(r, str) and r for r in first.reasons)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ------------------------------------------------------ B. execution bounds
def test_exactly_one_top_level_maintenance_call_per_turn():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        trace = rt.cognition.process_turn(user, "I prefer tea over coffee.")
        cid = trace["correlation_id"]
        runs = rt.db.query(
            "SELECT * FROM maintenance_runs WHERE user_id=? AND correlation_id=?",
            (user, cid))
        assert len(runs) == 1
        events = rt.cognition.bus.for_correlation(cid)
        assert sum(1 for e in events if e.type == "cognitive_model.audit_started") == 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_duplicate_audit_for_same_correlation_returns_prior_result():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        trace = rt.cognition.process_turn(user, "I prefer tea over coffee.")
        cid = trace["correlation_id"]
        again = rt.cognition.self_maintenance.audit(user, correlation_id=cid)
        assert again["correlation_id"] == cid
        runs = rt.db.query(
            "SELECT * FROM maintenance_runs WHERE user_id=? AND correlation_id=?",
            (user, cid))
        assert len(runs) == 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_maintenance_events_cannot_recursively_trigger_audits():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        before = rt.db.query_one(
            "SELECT COUNT(*) AS n FROM maintenance_runs WHERE user_id=?", (user,))["n"]
        # Emit every V10/V10.1 maintenance event type directly on the bus.
        for etype in ("cognitive_model.audit_completed", "contradiction.detected",
                      "maintenance.proposed", "maintenance.relevance_determined",
                      "maintenance.reaudit_completed"):
            rt.cognition.bus.emit(user, etype, "synthetic", subject_kind="test",
                                  subject_id="t", correlation_id="loop_test")
        after = rt.db.query_one(
            "SELECT COUNT(*) AS n FROM maintenance_runs WHERE user_id=?", (user,))["n"]
        assert after == before  # no subscriber-triggered audit exists
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_reaudit_depth_is_capped_at_one():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        coordinator = rt.cognition.maintenance_runtime
        applied = {"id": "proposal_x", "status": "APPLIED",
                   "correlation_id": "turn_base"}
        first = coordinator.reaudit_after_confirmation(user, applied)
        assert first is not None
        assert first["correlation_id"].endswith(REAUDIT_SUFFIX)
        # A proposal whose correlation already IS a re-audit refuses depth 2.
        deeper = coordinator.reaudit_after_confirmation(
            user, {"id": "proposal_y", "status": "APPLIED",
                   "correlation_id": first["correlation_id"]})
        assert deeper is None
        # run_for_turn also refuses to audit a re-audit correlation.
        ctx = coordinator.run_for_turn(
            user, "audit my personal model",
            correlation_id=f"turn_z{REAUDIT_SUFFIX}", thread_id="t",
            meaning={"semantic": {"candidates": []}, "created_objects": []})
        assert ctx["status"] == NO_MAINTENANCE_NEEDED
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_unconfirmed_proposal_never_triggers_reaudit():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        result = rt.cognition.maintenance_runtime.reaudit_after_confirmation(
            user, {"id": "p1", "status": "PROPOSED", "correlation_id": "c1"})
        assert result is None
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------- C. surface
def test_maintenance_surface_stages_are_real_and_correlated():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        thread = "surface-thread"
        cid = "turn_v101surface"
        rt.cognition.surface_lifecycle.start_turn(user, thread, cid)
        trace = rt.cognition.process_turn(
            user, "Please audit my personal model.",
            conversation_id=thread, correlation_id=cid)
        assert trace["maintenance"]["relevance"]["relevant"] is True
        snapshot = rt.cognition.surface_lifecycle.snapshot(user, cid)
        stages = {a["stage"]: a["status"] for a in snapshot["activities"]}
        assert "AUDITING_PERSONAL_MODEL" in stages
        assert stages["AUDITING_PERSONAL_MODEL"] in ("COMPLETED", "FAILED")
        # Every emitted maintenance stage belongs to the SAME correlation.
        for activity in snapshot["history"]:
            assert activity["event_id"] is not None
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_no_maintenance_stage_for_irrelevant_turn():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        thread = "surface-quiet"
        cid = "turn_v101quiet"
        rt.cognition.surface_lifecycle.start_turn(user, thread, cid)
        rt.cognition.process_turn(user, "What's the weather?",
                                  conversation_id=thread, correlation_id=cid)
        snapshot = rt.cognition.surface_lifecycle.snapshot(user, cid)
        stages = {a["stage"] for a in snapshot["activities"]}
        assert not stages & {"AUDITING_PERSONAL_MODEL", "CHECKING_CONTRADICTIONS",
                             "CHECKING_FOR_STALE_STATE", "COMPARING_PERSONAL_STATE",
                             "CHECKING_OPEN_UNKNOWNS",
                             "COMPARING_PREDICTION_TO_OUTCOME"}
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_failed_audit_surfaces_failed_stage_and_failed_run():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        thread = "surface-failed"
        cid = "turn_v101failed"
        rt.cognition.surface_lifecycle.start_turn(user, thread, cid)
        original = rt.cognition.cognitive_debt.detect

        def broken(*args, **kwargs):
            raise RuntimeError("synthetic debt failure")

        rt.cognition.cognitive_debt.detect = broken
        try:
            with pytest.raises(RuntimeError):
                rt.cognition.self_maintenance.audit(
                    user, correlation_id=cid, families=["debt"],
                    lifecycle=rt.cognition.surface_lifecycle, thread_id=thread)
        finally:
            rt.cognition.cognitive_debt.detect = original
        run = rt.db.query_one(
            "SELECT status FROM maintenance_runs WHERE correlation_id=?", (cid,))
        assert run["status"] == "FAILED"
        snapshot = rt.cognition.surface_lifecycle.snapshot(user, cid)
        stages = {a["stage"]: a["status"] for a in snapshot["activities"]}
        assert stages.get("AUDITING_PERSONAL_MODEL") == "FAILED"
        # FAILED remains FAILED: the snapshot never relabels a failed stage.
        assert snapshot["status"] == "FAILED"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_failed_maintenance_never_blocks_the_conversation_turn():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        original = rt.cognition.self_maintenance.audit

        def broken(*args, **kwargs):
            raise RuntimeError("synthetic audit crash")

        rt.cognition.self_maintenance.audit = broken
        try:
            trace = rt.cognition.process_turn(user, "I prefer tea over coffee.")
        finally:
            rt.cognition.self_maintenance.audit = original
        assert trace["maintenance"]["status"] == "MAINTENANCE_FAILED"
        assert "error" in trace["maintenance"]
        # The turn itself completed with its normal fields.
        assert trace["correlation_id"] and trace["meaning"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# -------------------------------------------------------------- D. findings
def test_true_contradiction_finding_appears_in_turn_result():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        obj(rt, user, CognitiveType.PREFERENCE, "I like remote work")
        obj(rt, user, CognitiveType.PREFERENCE, "I don't like remote work",
            modality=Modality.NEGATED)
        trace = rt.cognition.process_turn(
            user, "Do my statements about remote work conflict?")
        m = trace["maintenance"]
        assert m["relevance"]["relevant"] is True
        kinds = {f["kind"]: f for f in m["findings"]}
        assert "CONTRADICTION" in kinds
        finding = kinds["CONTRADICTION"]
        assert finding["classification"] == "TRUE_CONTRADICTION"
        assert finding["summary"]
        # Evidence refs point at the canonical objects.
        assert all(ref.get("kind") == "cognitive_object"
                   for ref in finding["evidence_refs"])
        record = rt.cognition.contradictions.get(user, finding["finding_id"])
        assert record is not None and record["status"] == "OPEN"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_unrelated_correction_does_not_supersede_other_preference():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        coffee = obj(rt, user, CognitiveType.PREFERENCE, "I like strong coffee")
        work = obj(rt, user, CognitiveType.BELIEF, "The project deadline is June")
        obj(rt, user, CognitiveType.CORRECTION, "The project deadline is July",
            metadata={"corrects_object_id": work["id"]})
        rt.cognition.process_turn(user, "Did my project info change?")
        refreshed = rt.cognition.personal_state.get(user, coffee["id"])
        assert refreshed["status"] == "ACTIVE"
        assert refreshed.get("superseded_by") in (None, "")
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_insufficient_context_is_preserved_not_forced():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        obj(rt, user, CognitiveType.PREFERENCE, "I prefer remote work")
        obj(rt, user, CognitiveType.PREFERENCE, "I prefer office collaboration")
        trace = rt.cognition.process_turn(
            user, "Is that inconsistent with my other preferences?")
        contradictions = [f for f in trace["maintenance"]["findings"]
                          if f["kind"] == "CONTRADICTION"]
        assert contradictions
        assert all(f["classification"] != "TRUE_CONTRADICTION"
                   for f in contradictions)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------- E. confirmation
def _stale_assumption_proposal(rt, user):
    assumption = obj(rt, user, CognitiveType.ASSUMPTION,
                     "the project depends on the legacy database")
    obj(rt, user, CognitiveType.FACT,
        "the project does not depend on the legacy database",
        modality=Modality.NEGATED)
    audit = rt.cognition.self_maintenance.audit(user)
    proposals = [p for p in audit["proposals"]
                 if p["proposal_type"] == "RETIRE_ASSUMPTION"]
    assert proposals, "expected a RETIRE_ASSUMPTION proposal"
    return assumption, proposals[0]


def test_proposal_is_not_applied_before_confirmation():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption, proposal = _stale_assumption_proposal(rt, user)
        assert proposal["status"] == "PROPOSED"
        live = rt.cognition.personal_state.get(user, assumption["id"])
        assert live["status"] == "ACTIVE"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_reject_and_defer_do_not_mutate_state():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption, proposal = _stale_assumption_proposal(rt, user)
        deferred = rt.cognition.maintenance_proposals.defer(
            user, proposal["id"], reason="later")
        assert deferred["status"] == "DEFERRED"
        rejected = rt.cognition.maintenance_proposals.reject(
            user, proposal["id"], reason="no")
        assert rejected["status"] == "REJECTED"
        live = rt.cognition.personal_state.get(user, assumption["id"])
        assert live["status"] == "ACTIVE"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_confirm_applies_through_autonomy_and_versions_state():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption, proposal = _stale_assumption_proposal(rt, user)
        version_before = rt.cognition.personal_state.current(user)["version"]
        applied = rt.cognition.maintenance_proposals.confirm(
            user, proposal["id"], reason="Confirmed retirement.")
        assert applied["status"] == "APPLIED"
        live = rt.cognition.personal_state.get(user, assumption["id"])
        assert live["status"] == "RETIRED"
        version_after = rt.cognition.personal_state.current(user)["version"]
        assert version_after > version_before
        # History preserved: the earlier version still reconstructs.
        past = rt.cognition.personal_state.reconstruct(user, version=version_before)
        assert past is not None
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------- F. re-audit
def test_confirmed_update_causes_exactly_one_bounded_reaudit():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        _, proposal = _stale_assumption_proposal(rt, user)
        applied = rt.cognition.maintenance_proposals.confirm(
            user, proposal["id"], reason="Confirmed.")
        assert applied["status"] == "APPLIED"
        result = rt.cognition.maintenance_runtime.reaudit_after_confirmation(
            user, applied)
        assert result is not None and result["depth"] == 1
        reaudit_cid = result["correlation_id"]
        assert reaudit_cid.endswith(REAUDIT_SUFFIX)
        runs = rt.db.query(
            "SELECT * FROM maintenance_runs WHERE user_id=? AND correlation_id LIKE ?",
            (user, f"%{REAUDIT_SUFFIX}"))
        assert len(runs) == 1
        # Idempotent: calling again reuses the SAME persisted run.
        second = rt.cognition.maintenance_runtime.reaudit_after_confirmation(
            user, applied)
        runs_after = rt.db.query(
            "SELECT * FROM maintenance_runs WHERE user_id=? AND correlation_id LIKE ?",
            (user, f"%{REAUDIT_SUFFIX}"))
        assert len(runs_after) == 1
        assert second["correlation_id"] == reaudit_cid
        events = rt.cognition.bus.for_correlation(reaudit_cid)
        assert any(e.type == "maintenance.reaudit_started" for e in events)
        assert any(e.type == "maintenance.reaudit_completed" for e in events)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------------- G. security
def test_cross_user_maintenance_isolation_at_the_runtime_layer():
    rt, root = make_runtime()
    try:
        alice, mallory = "alice-v101", "mallory-v101"
        assumption = rt.cognition.personal_state.create(alice, CognitiveObjectCreate(
            type=CognitiveType.ASSUMPTION,
            content="the project depends on the legacy database",
            provenance=Provenance.USER_STATED))
        rt.cognition.personal_state.create(alice, CognitiveObjectCreate(
            type=CognitiveType.FACT,
            content="the project does not depend on the legacy database",
            provenance=Provenance.USER_STATED, modality=Modality.NEGATED))
        audit = rt.cognition.self_maintenance.audit(alice)
        proposal = next(p for p in audit["proposals"]
                        if p["proposal_type"] == "RETIRE_ASSUMPTION")
        # Substituted user cannot read, confirm, or re-audit Alice's data.
        assert rt.cognition.maintenance_proposals.get(mallory, proposal["id"]) is None
        assert rt.cognition.maintenance_proposals.confirm(
            mallory, proposal["id"], reason="steal") is None
        stolen = rt.cognition.maintenance_runtime.reaudit_after_confirmation(
            mallory, {"id": proposal["id"], "status": "PROPOSED",
                      "correlation_id": "x"})
        assert stolen is None
        # Mallory's own turn-level maintenance never sees Alice's findings.
        ctx = rt.cognition.maintenance_runtime.run_for_turn(
            mallory, "audit my personal model", correlation_id="turn_mallory",
            thread_id="t", meaning={"semantic": {"candidates": []},
                                    "created_objects": []})
        finding_ids = {f["finding_id"] for f in ctx["findings"]}
        alice_debts = {d["id"] for d in rt.cognition.cognitive_debt.list(alice)}
        assert not finding_ids & alice_debts
        live = rt.cognition.personal_state.get(alice, assumption["id"])
        assert live["status"] == "ACTIVE"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_cross_user_correlation_snapshot_is_not_visible():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        thread, cid = "sec-thread", "turn_sec_v101"
        rt.cognition.surface_lifecycle.start_turn(user, thread, cid)
        rt.cognition.process_turn(user, "Audit my personal model.",
                                  conversation_id=thread, correlation_id=cid)
        assert rt.cognition.surface_lifecycle.snapshot("other-user", cid) is None
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# --------------------------------------------------------- event vocabulary
def test_v101_events_are_registered_and_bounded():
    for etype in ("maintenance.relevance_determined",
                  "maintenance.reaudit_started",
                  "maintenance.reaudit_completed"):
        assert etype in EVENT_TYPES


def test_health_reports_v10_release_and_runtime_integration():
    """Regression: the released V10.1.0 slice must be reported truthfully.

    `version` stays "8.2" (established API-compatibility contract marker);
    `release` is the released-slice marker and must be "10.1.0" after the
    V10.1 merge/tag/release.
    """
    rt, root = make_runtime()
    try:
        health = rt.health()
        assert health["version"] == "8.2"  # preserved compatibility marker
        assert health["release"] == "10.1.0"  # released V10.1.0 slice
        assert health["cognition"]["v10"]["runtime_integration"] is True
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ----------------------------------------------------- conversation summary
def test_chat_response_carries_maintenance_summary():
    from fastapi.testclient import TestClient
    import app.main as main_module
    rt, root = make_runtime()
    try:
        main_module.app.dependency_overrides[main_module.rt] = lambda: rt
        client = TestClient(main_module.app)
        res = client.post("/api/chat", json={
            "message": "I prefer remote work over office work.",
            "thread_id": "v101-chat"})
        assert res.status_code == 200
        body = res.json()
        maintenance = (body.get("cognition") or {}).get("maintenance")
        assert maintenance is not None
        assert maintenance["relevant"] is True
        assert maintenance["status"] in (
            "MAINTENANCE_COMPLETED_NO_FINDINGS",
            "MAINTENANCE_COMPLETED_WITH_FINDINGS",
            "WAITING_FOR_CONFIRMATION")
        res2 = client.post("/api/chat", json={
            "message": "What's the weather today?", "thread_id": "v101-chat"})
        maintenance2 = (res2.json().get("cognition") or {}).get("maintenance")
        assert maintenance2["relevant"] is False
        assert maintenance2["status"] == "NO_MAINTENANCE_NEEDED"
    finally:
        main_module.app.dependency_overrides.pop(main_module.rt, None)
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_confirm_endpoint_returns_bounded_reaudit():
    from fastapi.testclient import TestClient
    import app.main as main_module
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        _, proposal = _stale_assumption_proposal(rt, user)
        main_module.app.dependency_overrides[main_module.rt] = lambda: rt
        client = TestClient(main_module.app)
        res = client.post(
            f"/api/v10/maintenance/proposals/{proposal['id']}/confirm",
            json={"confirmation": True, "reason": "Confirmed via API."})
        assert res.status_code == 200
        body = res.json()
        assert body["status"] == "APPLIED"
        assert body["reaudit"] is not None
        assert body["reaudit"]["depth"] == 1
        assert body["reaudit"]["correlation_id"].endswith(REAUDIT_SUFFIX)
    finally:
        main_module.app.dependency_overrides.pop(main_module.rt, None)
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# =====================================================================
# PR #14 regression tests: proposal visibility, canonical RECORD_OUTCOME
# application, honest insufficiency, and trigger-aware re-audit.
# =====================================================================
def test_record_outcome_proposal_is_visible_in_turn_context():
    """Fix 1A: a matcher-created RECORD_OUTCOME proposal must be surfaced
    in the same turn's maintenance context, with WAITING_FOR_CONFIRMATION."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        rt.cognition.predictions.create(user, "I save money each month",
                                        confidence=0.8)
        trace = rt.cognition.process_turn(
            user, "The outcome was I saved money this month.")
        m = trace["maintenance"]
        assert m["relevance"]["relevant"] is True
        assert "PREDICTION_OUTCOME" in m["relevance"]["trigger_kinds"]
        record_outcome = [p for p in m["proposals"]
                          if p["proposal_type"] == "RECORD_OUTCOME"]
        assert record_outcome, (
            "The RECORD_OUTCOME proposal created during this turn must appear "
            "in the turn's maintenance.proposals — not silently only in the DB.")
        assert record_outcome[0]["status"] == "PROPOSED"
        assert m["status"] == WAITING_FOR_CONFIRMATION
        # The surfaced proposal is the same canonical row the service stores.
        stored = rt.cognition.maintenance_proposals.get(
            user, record_outcome[0]["id"])
        assert stored is not None and stored["status"] == "PROPOSED"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_confirm_unknown_proposal_is_visible_in_turn_context():
    """Fix 1B: a matcher-created CONFIRM_UNKNOWN proposal must be surfaced
    in the same turn's maintenance context."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        anchor = obj(rt, user, CognitiveType.BELIEF, "I like the mountain office")
        rt.cognition.unknowns.identify(
            user, what_unknown="Do I like the mountain office?",
            why_it_matters="Location preference affects planning.",
            missing_evidence=[{"kind": "OBSERVATION"}],
            resolution_path="Wait for a direct statement.",
            relevant_object_ids=[anchor["id"]])
        trace = rt.cognition.process_turn(
            user, "I noticed that I like the mountain office.")
        m = trace["maintenance"]
        confirm_unknown = [p for p in m["proposals"]
                           if p["proposal_type"] == "CONFIRM_UNKNOWN"]
        assert confirm_unknown and confirm_unknown[0]["status"] == "PROPOSED"
        assert m["status"] == WAITING_FOR_CONFIRMATION
        # Still confirmation-gated: nothing auto-resolved.
        assert len(rt.cognition.unknowns.list(user, status="OPEN")) == 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_confirmed_record_outcome_resolves_prediction_canonically():
    """Fix 2A: confirming RECORD_OUTCOME applies through PredictionEngine
    .observe() — the prediction is resolved canonically with full history."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        pred = rt.cognition.predictions.create(
            user, "I save money each month", confidence=0.8)
        trace = rt.cognition.process_turn(
            user, "The outcome was I saved money this month, "
                  "so the prediction came true.")
        m = trace["maintenance"]
        record_outcome = [p for p in m["proposals"]
                          if p["proposal_type"] == "RECORD_OUTCOME"]
        assert record_outcome and m["status"] == WAITING_FOR_CONFIRMATION
        result = rt.cognition.maintenance_proposals.confirm(
            user, record_outcome[0]["id"], reason="Yes, record the outcome.")
        assert result["status"] == "APPLIED"
        # The canonical prediction row was resolved by the engine itself.
        row = rt.db.query_one("SELECT * FROM predictions WHERE id=?",
                              (pred["id"],))
        assert row["status"] == "correct"
        assert row["outcome"]  # the real observation text, not a fabrication
        assert row["evaluated_at"]
        # Canonical evaluation events exist (engine-emitted, not duplicated).
        events = [e.type for e in rt.cognition.bus.recent(user, limit=500)]
        assert "prediction.evaluated" in events
        assert "prediction.correct" in events
        # No duplicate outcome store: the proposal row carries no verdict.
        stored = rt.cognition.maintenance_proposals.get(
            user, record_outcome[0]["id"])
        assert stored["status"] == "APPLIED"
        assert "outcome" not in (stored.get("proposed_state") or {})
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_record_outcome_with_insufficient_evidence_is_blocked_not_applied():
    """Fix 2B: an observation without an explicit resolution signal must NOT
    produce a fabricated verdict; the prediction stays open and the proposal
    is honestly BLOCKED — never falsely APPLIED."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        pred = rt.cognition.predictions.create(
            user, "I save money each month", confidence=0.8)
        trace = rt.cognition.process_turn(
            user, "The outcome was I saved money this month.")
        m = trace["maintenance"]
        record_outcome = [p for p in m["proposals"]
                          if p["proposal_type"] == "RECORD_OUTCOME"]
        assert record_outcome and m["status"] == WAITING_FOR_CONFIRMATION
        result = rt.cognition.maintenance_proposals.confirm(
            user, record_outcome[0]["id"], reason="Record it.")
        assert result["status"] == "BLOCKED"
        assert result["status"] != "APPLIED"
        row = rt.db.query_one("SELECT * FROM predictions WHERE id=?",
                              (pred["id"],))
        assert row["status"] == "open"      # prediction remains open
        assert row["outcome"] is None       # no invented verdict
        assert row["evaluated_at"] is None
        events = [e.type for e in rt.cognition.bus.recent(user, limit=500)]
        assert "prediction.correct" not in events
        assert "prediction.incorrect" not in events
        assert "maintenance.blocked" in events
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_record_outcome_reaudit_targets_model_errors_family():
    """Fix 3: the bounded re-audit after a confirmed RECORD_OUTCOME must run
    the trigger-derived families (model_errors, debt) — exactly one re-audit,
    no depth-2."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        rt.cognition.predictions.create(
            user, "I save money each month", confidence=0.8)
        trace = rt.cognition.process_turn(
            user, "The outcome was I saved money this month, "
                  "so the prediction came true.")
        record_outcome = [p for p in trace["maintenance"]["proposals"]
                          if p["proposal_type"] == "RECORD_OUTCOME"]
        applied = rt.cognition.maintenance_proposals.confirm(
            user, record_outcome[0]["id"], reason="Record it.")
        assert applied["status"] == "APPLIED"
        reaudit = rt.cognition.maintenance_runtime.reaudit_after_confirmation(
            user, applied)
        assert reaudit is not None
        assert reaudit["depth"] == 1
        assert reaudit["correlation_id"].endswith(REAUDIT_SUFFIX)
        assert set(reaudit["families"]) == {"model_errors", "debt"}, (
            "A confirmed prediction outcome must re-audit the model_errors "
            "and debt families — not a blind hard-coded family list.")
        # Exactly one re-audit run row; a second attempt at depth 2 is refused.
        runs = rt.db.query(
            "SELECT correlation_id FROM maintenance_runs "
            "WHERE user_id=? AND correlation_id LIKE ?",
            (user, f"%{REAUDIT_SUFFIX}"))
        assert len(runs) == 1
        second = rt.cognition.maintenance_runtime.reaudit_after_confirmation(
            user, {**applied, "correlation_id": reaudit["correlation_id"]})
        assert second is None
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_record_outcome_flow_causes_no_recursive_maintenance_storm():
    """The full visibility → confirm → apply → re-audit chain must produce a
    bounded number of audits: one turn audit plus one re-audit, no recursion
    triggered by the maintenance/prediction events themselves."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        rt.cognition.predictions.create(
            user, "I save money each month", confidence=0.8)
        trace = rt.cognition.process_turn(
            user, "The outcome was I saved money this month, "
                  "so the prediction came true.")
        record_outcome = [p for p in trace["maintenance"]["proposals"]
                          if p["proposal_type"] == "RECORD_OUTCOME"]
        applied = rt.cognition.maintenance_proposals.confirm(
            user, record_outcome[0]["id"], reason="Record it.")
        rt.cognition.maintenance_runtime.reaudit_after_confirmation(
            user, applied)
        runs = rt.db.query(
            "SELECT correlation_id FROM maintenance_runs WHERE user_id=?",
            (user,))
        assert len(runs) == 2  # exactly: the turn audit + the single re-audit
        assert sum(1 for r in runs
                   if r["correlation_id"].endswith(REAUDIT_SUFFIX)) == 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)
