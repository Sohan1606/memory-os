"""Focused V10.2 RUNTIME tests: the real PolicyRuntimeCoordinator inside the
orchestrator's turn — bounds, idempotency, terminal correlations, proposal
surfacing, future-turn influence, measurement, weakening and the additive API.

These tests run the REAL stack (Runtime + CognitiveOrchestrator.process_turn);
no fake runtime, no scheduler, no second engine.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings
from app.cognition.events import EVENT_TYPES
from app.runtime import Runtime

GOVERNANCE_STAGES = ("CHECKING_ADAPTIVE_POLICY", "COMPARING_POLICY_EVIDENCE",
                     "EVALUATING_POLICY_CHANGE", "WAITING_FOR_POLICY_CONFIRMATION",
                     "APPLYING_ADAPTIVE_POLICY", "MEASURING_POLICY_OUTCOME")


def make_runtime(prefix="v102-runtime-"):
    root = Path(tempfile.mkdtemp(prefix=prefix))
    return Runtime(Settings(data_dir=root, sqlite_path=root / "m.db",
                            checkpoint_path=root / "c.db",
                            chroma_path=root / "chroma",
                            disable_embeddings=True)), root


def now() -> datetime:
    return datetime.now(timezone.utc)


def turn_with_rejected_intervention(rt, user, message, *, when=None):
    """A real turn whose attention intervention is dismissed afterwards."""
    trace = rt.cognition.process_turn(user, message)
    cid = trace["correlation_id"]
    att = rt.cognition.attention.consider(
        user, "status check", importance=.9, urgency=.8, confidence=.9,
        correlation_id=cid)
    rt.cognition.attention_v2.record_reaction(
        user, att["id"], False, detail="not now", correlation_id=cid)
    if when is not None:
        stamp = when.isoformat(timespec="seconds")
        rt.db.execute("UPDATE interventions SET created_at=? WHERE id=?",
                      (stamp, att["id"]))
        rt.db.execute(
            "UPDATE cognitive_events SET created_at=? WHERE correlation_id=? "
            "AND type LIKE 'intervention%'", (stamp, cid))
    return trace


def turn_with_accepted_intervention(rt, user, message):
    trace = rt.cognition.process_turn(user, message)
    cid = trace["correlation_id"]
    att = rt.cognition.attention.consider(
        user, "status check", importance=.9, urgency=.8, confidence=.9,
        correlation_id=cid)
    rt.cognition.attention_v2.record_reaction(
        user, att["id"], True, detail="good", correlation_id=cid)
    return trace


def proposal_for(rt, user, target="interruption_tolerance"):
    return next((p for p in rt.cognition.policy_governance.open_proposals(user)
                 if p["target"] == target), None)


def confirm(rt, user, proposal):
    return rt.cognition.policy_governance.confirm(
        user, proposal["id"], confirmation=True, reason="test confirmation",
        correlation_id=f"confirm-{proposal['id'][:8]}")


# ==================================================== bounded-turn behaviour
def test_ordinary_turn_runs_no_governance():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        trace = rt.cognition.process_turn(user, "What is the weather?")
        gov = trace["policy_governance"]
        assert gov["status"] == "NO_GOVERNANCE_NEEDED"
        assert gov["proposals"] == [] and gov["measurements"] == []
        assert gov["relevance"]["relevant"] is False
        # No policy stages were performed for a turn with no policy work.
        events = rt.cognition.bus.for_correlation(trace["correlation_id"])
        stages = [e.payload.get("stage") for e in events
                  if e.type == "surface.activity"]
        assert not [s for s in stages if s in GOVERNANCE_STAGES]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_exactly_one_evaluation_per_correlation_and_idempotent():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        first = rt.cognition.policy_runtime.run_for_turn(
            user, "hello", correlation_id="turn-idem")
        second = rt.cognition.policy_runtime.run_for_turn(
            user, "hello", correlation_id="turn-idem")
        # Persisted result replayed verbatim (plus the replay marker).
        assert second == {**first, "persisted": True}
        runs = rt.db.query(
            "SELECT COUNT(*) AS n FROM policy_governance_runs WHERE user_id=?",
            (user,))
        assert runs[0]["n"] == 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_terminal_correlations_never_evaluate():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        rt.db.execute("DELETE FROM policy_governance_runs WHERE user_id=?",
                      (user,))
        for marker in ("cid::reaudit1", "cid::pgov1"):
            result = rt.cognition.policy_runtime.run_for_turn(
                user, "anything", correlation_id=marker)
            assert result["status"] == "GOVERNANCE_TERMINAL"
        # The maintenance coordinator treats ::pgov1 as terminal too: an
        # explicitly audit-relevant message must still produce no audit.
        maintenance = rt.cognition.maintenance_runtime.run_for_turn(
            user, "please audit my personal model now", correlation_id="cid::pgov1",
            thread_id="t1", meaning=None)
        assert maintenance["status"] == "NO_MAINTENANCE_NEEDED"
        assert any("terminal" in reason.lower() for reason in
                   maintenance["relevance"]["reasons"])
        assert not [e for e in rt.cognition.bus.recent(
            user, types=["maintenance.audit_completed"])]
        runs = rt.db.query(
            "SELECT COUNT(*) AS n FROM policy_governance_runs WHERE user_id=?",
            (user,))
        assert runs[0]["n"] == 0  # no run rows, no work, no events
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_hand_emitted_events_do_not_trigger_evaluation():
    """No subscribers: emitting every V10.2 event type by hand creates zero
    governance runs."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        for event_type in ("governance.transition", "governance.consultation",
                           "governance.observation"):
            rt.cognition.bus.emit(user, event_type, "hand made",
                                  correlation_id="hand-1")
        runs = rt.db.query(
            "SELECT COUNT(*) AS n FROM policy_governance_runs WHERE user_id=?",
            (user,))
        assert runs[0]["n"] == 0
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ================================================= proposal generation (§7)
def test_proposal_surfaced_in_same_turn_after_threshold():
    """PR #14 visibility: the proposal surfaces on the first turn AFTER the
    threshold-completing record lands (incremental watermark semantics)."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        t0 = now()
        turn_with_rejected_intervention(rt, user, "one", when=t0 - timedelta(days=6))
        turn_with_rejected_intervention(rt, user, "two", when=t0 - timedelta(days=3))
        trace3 = turn_with_rejected_intervention(rt, user, "three")
        # The third episode landed mid-turn-3; its evidence is evaluated on
        # the NEXT top-level evaluation.
        assert trace3["policy_governance"]["proposals"] == []
        trace4 = rt.cognition.process_turn(user, "four")
        gov = trace4["policy_governance"]
        assert gov["status"] == "PROPOSAL_CREATED"
        assert len(gov["proposals"]) == 1
        proposal = gov["proposals"][0]
        assert proposal["target"] == "interruption_tolerance"
        assert proposal["proposed_value"] == "low"
        assert proposal["state"] == "PROPOSED"
        events = rt.cognition.bus.for_correlation(trace4["correlation_id"])
        stages = [e.payload.get("stage") for e in events
                  if e.type == "surface.activity"]
        assert "WAITING_FOR_POLICY_CONFIRMATION" in stages
        assert "EVALUATING_POLICY_CHANGE" in stages
        # Nothing was applied yet: the engine is untouched until confirmation.
        assert rt.cognition.policy.get(user, "interruption_tolerance")["is_default"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_two_episodes_do_not_propose_but_show_insufficient_signal():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        t0 = now()
        turn_with_rejected_intervention(rt, user, "one", when=t0 - timedelta(days=6))
        turn_with_rejected_intervention(rt, user, "two", when=t0 - timedelta(days=3))
        trace = rt.cognition.process_turn(user, "three")
        gov = trace["policy_governance"]
        assert gov["proposals"] == []
        false_positives = [s for s in gov["signals"]
                           if s["signal"] == "REPEATED_FALSE_POSITIVE_INTERVENTION"]
        assert false_positives
        assert false_positives[0]["verdict"] == "INSUFFICIENT_EVIDENCE"
        assert "INSUFFICIENT EVIDENCE" in false_positives[0]["reason"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ============================================== future-turn influence (§11)
def test_active_adaptation_influences_a_later_turn():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        t0 = now()
        turn_with_rejected_intervention(rt, user, "one", when=t0 - timedelta(days=6))
        turn_with_rejected_intervention(rt, user, "two", when=t0 - timedelta(days=3))
        turn_with_rejected_intervention(rt, user, "three")
        rt.cognition.process_turn(user, "four")
        proposal = proposal_for(rt, user)
        assert proposal, "expected a surfaced proposal"
        active = confirm(rt, user, proposal)
        assert active["state"] == "ACTIVE"
        # The engine's effective value actually changed.
        assert rt.cognition.policy.get(user, "interruption_tolerance")["value"] == "low"

        # A LATER turn must consult the adaptation through a real consumer.
        trace5 = rt.cognition.process_turn(user, "another message")
        events = rt.cognition.bus.for_correlation(trace5["correlation_id"])
        consultations = [e for e in events if e.type == "governance.consultation"]
        assert consultations, "no consultation was recorded for a later turn"
        payload = consultations[0].payload
        assert payload["adaptation_id"] == active["id"]
        assert payload["consumer"]  # a real consumer, named
        assert payload["matched_scope"] == {"user_id": user, "tenant_id": "local"}
        assert payload["effect"]     # a concrete, checkable effect
        # Inspection is read-only and observable in the trace.
        inspection = trace5["policy_governance"]["inspection"]
        assert inspection["count"] >= 1
        assert len(inspection["consulted"]) >= 1
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_consultation_requires_real_consumer_proof():
    """§11.2: no consultation event without a real downstream use. Drop the
    engine confidence for the dimension below the context-bundle threshold
    and the preference item must not be consulted."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        t0 = now()
        turn_with_rejected_intervention(rt, user, "one", when=t0 - timedelta(days=6))
        turn_with_rejected_intervention(rt, user, "two", when=t0 - timedelta(days=3))
        turn_with_rejected_intervention(rt, user, "three")
        rt.cognition.process_turn(user, "four")
        proposal = proposal_for(rt, user)
        active = confirm(rt, user, proposal)
        # Force the engine confidence under the context-bundle threshold.
        rt.db.execute(
            "UPDATE policies SET confidence=0.05 WHERE user_id=? AND "
            "key='interruption_tolerance'", (user,))
        trace = rt.cognition.process_turn(user, "quiet turn")
        events = rt.cognition.bus.for_correlation(trace["correlation_id"])
        assert not [e for e in events if e.type == "governance.consultation"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ================================================= measurement (§12) and harm
def _confirmed_adaptation(rt, user):
    t0 = now()
    turn_with_rejected_intervention(rt, user, "one", when=t0 - timedelta(days=6))
    turn_with_rejected_intervention(rt, user, "two", when=t0 - timedelta(days=3))
    turn_with_rejected_intervention(rt, user, "three")
    rt.cognition.process_turn(user, "four")
    proposal = proposal_for(rt, user)
    assert proposal
    return confirm(rt, user, proposal)


def test_measurement_harmful_influence_and_weakening():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        active = _confirmed_adaptation(rt, user)
        # Turn 5 consults the adaptation; its intervention is REJECTED under
        # the adapted value: the adaptation made things worse.
        trace5 = turn_with_rejected_intervention(rt, user, "five")
        rt.cognition.policy_runtime.run_for_turn(user, "measure", correlation_id="m1")
        measurements = rt.cognition.policy_governance.measurements(
            user, active["id"])
        assert measurements["outcome_evidence"]["observation_count"] == 1
        frozen = measurements["adaptation_evidence"]
        assert frozen["role"] == "ADAPTATION_EVIDENCE"
        assert len(frozen["evidence_refs"]) == 3  # frozen at acceptance
        assert "1 HARMFUL_INFLUENCE" in measurements["summary"]

        # A second harmful observation must deterministically weaken (HARM_WEAKEN_MIN=2).
        trace7 = turn_with_rejected_intervention(rt, user, "six")
        rt.cognition.policy_runtime.run_for_turn(user, "measure2", correlation_id="m2")
        weakened = rt.cognition.policy_governance.get(user, active["id"])
        assert weakened["state"] == "WEAKENED"
        assert rt.cognition.policy.get(user, "interruption_tolerance")["is_default"]
        # The weakened adaptation is no longer consulted.
        trace8 = rt.cognition.process_turn(user, "after weaken")
        events = rt.cognition.bus.for_correlation(trace8["correlation_id"])
        assert not [e for e in events if e.type == "governance.consultation"]
        # Its harmful evidence must not be recycled into a new proposal (§12.1).
        assert trace8["policy_governance"]["proposals"] == []
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_measurement_successful_influence():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        active = _confirmed_adaptation(rt, user)
        turn_with_accepted_intervention(rt, user, "good turn")
        rt.cognition.policy_runtime.run_for_turn(user, "measure", correlation_id="m1")
        measurements = rt.cognition.policy_governance.measurements(user, active["id"])
        assert measurements["outcome_evidence"]["observation_count"] == 1
        observations = measurements["outcome_evidence"]["observations"]
        assert observations[0]["category"] == "SUCCESSFUL_INFLUENCE"
        assert "1 SUCCESSFUL_INFLUENCE" in measurements["summary"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_measurement_conflicting_and_insufficient():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        active = _confirmed_adaptation(rt, user)

        # CONFLICTING_OUTCOMES: both an accepted and a rejected intervention
        # in one influenced turn.
        trace = rt.cognition.process_turn(user, "mixed turn")
        cid = trace["correlation_id"]
        a1 = rt.cognition.attention.consider(user, "one", importance=.9,
                                             urgency=.8, confidence=.9,
                                             correlation_id=cid)
        rt.cognition.attention_v2.record_reaction(user, a1["id"], True,
                                                  correlation_id=cid)
        a2 = rt.cognition.attention.consider(user, "two", importance=.9,
                                             urgency=.8, confidence=.9,
                                             correlation_id=cid)
        rt.cognition.attention_v2.record_reaction(user, a2["id"], False,
                                                  correlation_id=cid)
        rt.cognition.policy_runtime.run_for_turn(user, "measure", correlation_id="m1")
        measurements = rt.cognition.policy_governance.measurements(user, active["id"])
        categories = {o["category"] for o in
                      measurements["outcome_evidence"]["observations"]}
        assert categories == {"CONFLICTING_OUTCOMES"}

        # INSUFFICIENT_EVIDENCE: an influenced turn with no measurable outcome.
        rt.cognition.process_turn(user, "silent turn", )
        rt.cognition.policy_runtime.run_for_turn(user, "measure2", correlation_id="m2")
        measurements = rt.cognition.policy_governance.measurements(user, active["id"])
        categories = {o["category"] for o in
                      measurements["outcome_evidence"]["observations"]}
        assert categories == {"CONFLICTING_OUTCOMES", "INSUFFICIENT_EVIDENCE"}
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_zero_observations_reported_truthfully():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        active = _confirmed_adaptation(rt, user)
        measurements = rt.cognition.policy_governance.measurements(user, active["id"])
        assert measurements["outcome_evidence"]["observation_count"] == 0
        assert measurements["summary"] == "no measured outcomes yet"
        assert measurements["outcome_evidence"]["observations"] == []
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ============================================ autonomy boundary invariants
def test_autonomy_governor_dispositions_unchanged():
    """A2: the AutonomyGovernor's authorize() output is byte-identical with an
    active adaptation in the picture — V10.2 cannot weaken it."""
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        before = rt.cognition.autonomy.authorize(
            user, "send report", risk_class="act", reversible=True,
            confidence=.8, correlation_id="before-1")
        active = _confirmed_adaptation(rt, user)
        after = rt.cognition.autonomy.authorize(
            user, "send report", risk_class="act", reversible=True,
            confidence=.8, correlation_id="after-1")
        assert after["disposition"] == before["disposition"]
        assert after["level"] == before["level"]
        # Irreversible actions stay gated regardless of any adaptation.
        blocked = rt.cognition.autonomy.authorize(
            user, "delete production", risk_class="act", reversible=False,
            confidence=.8, correlation_id="after-2")
        assert blocked["disposition"] in ("BLOCKED", "ASK_USER")
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ===================================================== additive HTTP surface
def test_governance_api_endpoints():
    from fastapi.testclient import TestClient
    import app.main as main_module
    rt, root = make_runtime("v102-api-")
    try:
        main_module.app.dependency_overrides[main_module.rt] = lambda: rt
        client = TestClient(main_module.app)
        user = rt.settings.demo_user_id
        active = _confirmed_adaptation(rt, user)

        res = client.get("/api/v10/governance/adaptations")
        assert res.status_code == 200
        items = res.json()["adaptations"]
        assert any(a["id"] == active["id"] for a in items)

        res = client.get(f"/api/v10/governance/adaptations/{active['id']}")
        assert res.status_code == 200
        body = res.json()
        assert body["state"] == "ACTIVE"
        assert len(body["history"]) >= 5

        res = client.get(
            f"/api/v10/governance/adaptations/{active['id']}/measurements")
        assert res.status_code == 200
        assert res.json()["summary"] == "no measured outcomes yet"

        # Confirmation is refused without an explicit confirmation flag.
        res = client.post("/api/v10/governance/adaptations/x/confirm",
                          json={"reason": "hi"})
        assert res.status_code == 400

        # Direct-ID access to another user's adaptation is not-found.
        res = client.get("/api/v10/governance/adaptations/does-not-exist")
        assert res.status_code == 404
    finally:
        main_module.app.dependency_overrides.pop(main_module.rt, None)
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_api_confirm_applies_and_revalidation_is_bounded():
    from fastapi.testclient import TestClient
    import app.main as main_module
    rt, root = make_runtime("v102-api2-")
    try:
        main_module.app.dependency_overrides[main_module.rt] = lambda: rt
        client = TestClient(main_module.app)
        user = rt.settings.demo_user_id
        t0 = now()
        turn_with_rejected_intervention(rt, user, "one", when=t0 - timedelta(days=6))
        turn_with_rejected_intervention(rt, user, "two", when=t0 - timedelta(days=3))
        turn_with_rejected_intervention(rt, user, "three")
        rt.cognition.process_turn(user, "four")
        proposal = proposal_for(rt, user)
        assert proposal

        res = client.post(
            f"/api/v10/governance/adaptations/{proposal['id']}/confirm",
            json={"confirmation": True, "reason": "approved"})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["state"] == "ACTIVE"
        assert body["revalidation"] is not None
        assert rt.cognition.policy.get(user, "interruption_tolerance")["value"] == "low"

        # The confirm-triggered re-validation is terminal and creates no loop.
        runs = rt.db.query(
            "SELECT correlation_id FROM policy_governance_runs WHERE user_id=?",
            (user,))
        assert all("::pgov1" not in r["correlation_id"] or
                   r["correlation_id"].count("::pgov1") == 1 for r in runs)

        # Later turns still consult the applied adaptation.
        trace = rt.cognition.process_turn(user, "later")
        events = rt.cognition.bus.for_correlation(trace["correlation_id"])
        assert [e for e in events if e.type == "governance.consultation"]
    finally:
        main_module.app.dependency_overrides.pop(main_module.rt, None)
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_chat_response_carries_governance_summary():
    from fastapi.testclient import TestClient
    import app.main as main_module
    rt, root = make_runtime("v102-chat-")
    try:
        main_module.app.dependency_overrides[main_module.rt] = lambda: rt
        client = TestClient(main_module.app)
        user = rt.settings.demo_user_id
        t0 = now()
        turn_with_rejected_intervention(rt, user, "one", when=t0 - timedelta(days=6))
        turn_with_rejected_intervention(rt, user, "two", when=t0 - timedelta(days=3))
        turn_with_rejected_intervention(rt, user, "three")
        res = client.post("/api/chat", json={
            "message": "so what is new?", "thread_id": "v102-chat"})
        assert res.status_code == 200
        cognition = res.json().get("cognition") or {}
        governance = cognition.get("policy_governance")
        assert governance is not None
        assert governance["status"] == "PROPOSAL_CREATED"
        assert len(governance["proposals"]) == 1
    finally:
        main_module.app.dependency_overrides.pop(main_module.rt, None)
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_health_reports_v10_2_marker():
    rt, root = make_runtime("v102-health-")
    try:
        health = rt.health()
        assert health["release"] == "10.1.0"  # never bumped by V10.2
        assert health["version"] == "8.2"      # preserved compatibility marker
        marker = health["cognition"]["v10"]["v10_2_governance"]
        assert marker["enabled"] is True
        assert marker["model_handling_activatable"] is False
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


# ================================================= event registry invariants
def test_governance_event_types_and_labels_registered():
    for event_type in ("governance.transition", "governance.consultation",
                       "governance.observation"):
        assert event_type in EVENT_TYPES
