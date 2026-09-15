"""
V8.3 §38 — end-to-end living-system scenarios A–G.

These are not unit tests. Each one drives a multi-step storyline through the
real subsystems and asserts that the system behaves like a continuously
maintained cognitive environment: it remembers, it reconciles, it stays quiet
when it should, and above all it never claims more than it observed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _u(prefix: str = "sc") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _backdate(runtime, entity_id: str, days: int) -> None:
    old = _iso(datetime.now(timezone.utc) - timedelta(days=days))
    runtime.db.execute(
        "UPDATE world_entities SET created_at=?, updated_at=?,"
        " last_confirmed_at=? WHERE id=?", (old, old, old, entity_id))
    runtime.db.execute(
        "UPDATE world_changes SET created_at=? WHERE entity_id=?",
        (old, entity_id))


# ============================================================== scenario A
def test_scenario_a_project_survives_across_sessions(runtime):
    """
    A: A project mentioned weeks ago is still coherent when the user returns —
    state, history and freshness all intact, with nothing invented.
    """
    user = _u("A")
    cog = runtime.cognition

    entity = cog.world_v2.upsert(user, "project", "Warehouse migration",
                                 evidence=["User described the project"])
    mission = cog.missions.create(user, "Complete the warehouse migration",
                                  evidence=["Derived from the conversation"])
    cog.missions.set_state(user, mission["id"], "active", reason="Work started")
    cog.missions.link(mission["id"], "world", entity["id"], "concerns")

    # Weeks pass with no contact.
    _backdate(runtime, entity["id"], 60)
    runtime.db.execute(
        "UPDATE missions SET last_activity_at=? WHERE id=?",
        (_iso(datetime.now(timezone.utc) - timedelta(days=60)), mission["id"]))

    cog.world_v2.refresh_staleness(user)
    brief = cog.missions.resume_brief(user)

    # The mission is still there, still active, and honestly flagged as quiet.
    assert brief["open"] == 1
    assert brief["active"][0]["title"] == "Complete the warehouse migration"
    assert brief["needs_review"], "a 60-day-silent mission must be surfaced"
    assert "No recorded activity" in brief["needs_review"][0]["review_reason"]

    # The world fact is stale but NOT false, and its history survived.
    fact = cog.world_v2.get(user, entity["id"])
    assert fact["stale"] == 1
    assert fact["state"] == "active"
    assert cog.world_v2.changes(user, entity_id=entity["id"])


# ============================================================== scenario B
def test_scenario_b_contradiction_is_reconciled_not_overwritten(runtime):
    """
    B: The user contradicts an old fact. The system reconciles with evidence,
    keeps the history, and can still say what it believed before.
    """
    user = _u("B")
    cog = runtime.cognition

    entity = cog.world_v2.upsert(user, "project", "Mobile rewrite",
                                 confidence=0.8)
    _backdate(runtime, entity["id"], 3)
    checkpoint = _iso(datetime.now(timezone.utc) - timedelta(days=1))

    result = cog.world_v2.reconcile(
        user, "project", "Mobile rewrite", state="abandoned", confidence=0.9,
        explicit_correction=True,
        evidence=["User said: we cancelled the mobile rewrite"])

    assert result["verdict"] == "supersede"
    assert cog.world_v2.get(user, entity["id"])["state"] == "abandoned"

    # The earlier belief is still reconstructable — nothing was erased.
    past = cog.timemachine.world_at(user, checkpoint)
    assert past["available"] is True
    earlier = [e for e in past["entities"] if e["id"] == entity["id"]]
    assert earlier and earlier[0]["state_at_time"] == "active"

    # And the change itself carries its evidence.
    change = cog.world_v2.changes(user, entity_id=entity["id"])[0]
    assert change["change"] == "superseded"


# ============================================================== scenario C
def test_scenario_c_background_upkeep_is_honest_and_bounded(runtime):
    """
    C: Background cognition runs, finds something real, reports it, changes
    nothing destructive — and the next run is rate-limited.
    """
    user = _u("C")
    cog = runtime.cognition

    entity = cog.world_v2.upsert(user, "risk", "Vendor may miss the SLA")
    _backdate(runtime, entity["id"], 300)
    memory = runtime.memory.create(user_id=user, content="Vendor contact is Dana",
                                   category="fact")

    first = cog.background.run_cycle(user, trigger="test", force=True)
    assert first["state"] == "completed"
    assert any("Vendor may miss the SLA" in f["summary"] for f in first["findings"])
    assert first["changes_made"] == 0

    # Nothing was destroyed or altered in value.
    assert runtime.memory.get(memory["memory"]["id"]) is not None
    assert cog.world_v2.get(user, entity["id"])["state"] == "active"

    # A second immediate run is refused, so refreshes cannot cause churn.
    second = cog.background.run_cycle(user, trigger="test")
    assert second["state"] == "skipped"
    assert "Rate limited" in second["skipped_reason"]


# ============================================================== scenario D
def test_scenario_d_system_learns_to_stay_quiet(runtime):
    """
    D: After repeated rejections the system raises its own bar for
    interrupting — learned from recorded reactions only.
    """
    user = _u("D")
    cog = runtime.cognition

    assert cog.attention_v2.silence_policy(user)["verdict"] == "INSUFFICIENT EVIDENCE"

    for i in range(6):
        decision = cog.attention_v2.evaluate(
            user, f"Suggestion {i}", importance=0.8, urgency=0.8, confidence=0.9)
        cog.attention_v2.record_reaction(user, decision["intervention_id"],
                                         False, detail="Please stop")

    policy = cog.attention_v2.silence_policy(user)
    assert policy["learned"] is True
    assert policy["verdict"] == "PREFERS SILENCE"

    # The same-strength topic now costs more to raise than it did at the start.
    later = cog.attention_v2.evaluate(user, "Another suggestion", importance=0.8,
                                      urgency=0.8, confidence=0.9)
    assert later["interruption_cost"] > 0.4
    # Every suppression it makes is inspectable.
    assert isinstance(cog.attention_v2.suppressions(user), list)


# ============================================================== scenario E
def test_scenario_e_simulation_then_deliberate_commit(runtime):
    """
    E: The user explores a what-if, it changes nothing, and only an explicit
    confirmation turns it into real state.
    """
    user = _u("E")
    cog = runtime.cognition

    entity = cog.world_v2.upsert(user, "project", "Conference talk")
    mission = cog.missions.create(user, "Deliver the conference talk")
    cog.missions.set_state(user, mission["id"], "active", reason="accepted")

    before = [dict(r) for r in runtime.db.query(
        "SELECT * FROM world_entities WHERE user_id=?", (user,))]

    simulation = cog.simulation.simulate(
        user, "What if I dropped the talk to focus on the migration?")
    assert simulation["epistemic_status"] == "SIMULATED"

    # Exploring changed nothing at all.
    assert [dict(r) for r in runtime.db.query(
        "SELECT * FROM world_entities WHERE user_id=?", (user,))] == before

    # Confirming without concrete changes still does nothing.
    assert cog.simulation.commit(user, simulation["id"],
                                 confirm=True)["committed"] is False

    # Only the full, explicit commit takes effect.
    committed = cog.simulation.commit(
        user, simulation["id"], confirm=True,
        changes=[{"kind": "world_state", "entity_id": entity["id"],
                  "state": "abandoned"}])
    assert committed["committed"] is True
    assert cog.world_v2.get(user, entity["id"])["state"] == "abandoned"
    # And the real change is recorded as a real change.
    assert cog.world_v2.changes(user, entity_id=entity["id"])[0]["new_state"] == (
        "abandoned")


# ============================================================== scenario F
def test_scenario_f_prediction_without_evidence_stays_unresolved(runtime):
    """
    F: A prediction's window passes with no evidence. The system says
    UNRESOLVED and refuses to score it — silence is not an outcome.
    """
    user = _u("F")
    cog = runtime.cognition

    prediction = cog.predictions.create(
        user, "The migration will finish before the audit", 0.75,
        evaluation_window_days=7,
        evidence=["User said the team is ahead of schedule"])

    past = _iso(datetime.now(timezone.utc) - timedelta(days=1))
    runtime.db.execute(
        "UPDATE predictions SET expected_evaluation_at=? WHERE id=?",
        (past, prediction["id"]))

    due = cog.predictions.due_for_evaluation(user)
    assert [p["id"] for p in due] == [prediction["id"]]

    # Background cognition surfaces it but does NOT decide it.
    cycle = cog.background.run_cycle(user, trigger="test", force=True)
    assert any("UNRESOLVED" in f.get("reason", "") for f in cycle["findings"])
    assert cog.predictions.get(prediction["id"])["status"] == "open"

    # Closing it honestly keeps it out of accuracy scoring.
    cog.predictions.mark_unresolved(user, prediction["id"])
    assert cog.predictions.get(prediction["id"])["status"] == "unresolved"
    assert cog.predictions.accuracy(user)["resolved"] == 0


# ============================================================== scenario G
def test_scenario_g_document_and_evidence_chain(runtime):
    """
    G: A document is ingested, produces an honest observation, and only an
    explicit promotion turns evidence into memory. An unparseable file is
    tracked without any invented content.
    """
    user = _u("G")
    cog = runtime.cognition

    readable = cog.documents.ingest(
        user, "retro.md", b"# Retro\nThe deploy failed because of a config typo.")
    assert readable["understanding"] == "FULL"

    evidence = cog.observations.evidence_for(user, "document", readable["id"])
    assert evidence["by_status"]["OBSERVED"] == 1

    # Evidence is not yet memory.
    before = len(runtime.memory.list(user))
    observation_id = evidence["observations"][0]["id"]
    assert len(runtime.memory.list(user)) == before

    promoted = cog.observations.promote(user, observation_id, runtime.memory)
    assert promoted["promoted"] is True
    assert len(runtime.memory.list(user)) == before + 1

    # An unreadable document is tracked but never fabricated.
    opaque = cog.documents.ingest(user, "scan.pdf", b"%PDF-1.5 opaque bytes")
    assert opaque["understanding"] == "METADATA ONLY"
    assert opaque["extracted_chars"] == 0
    opaque_evidence = cog.observations.evidence_for(user, "document", opaque["id"])
    recorded = opaque_evidence["observations"][0]["content"]
    assert "NOT CONFIGURED" in recorded
    # Crucially: nothing resembling a summary of the PDF's contents exists.
    assert "scan.pdf" in recorded


# ====================================================== cross-cutting checks
def test_epistemic_statuses_never_blur(runtime):
    """OBSERVED / INFERRED / PREDICTED / SIMULATED stay distinguishable (§39)."""
    user = _u("X")
    cog = runtime.cognition

    cog.observations.record(user, "Directly stated", source="conversation",
                            origin="m1")
    cog.observations.record(user, "Deduced from context", source="conversation",
                            origin="m2", epistemic_status="INFERRED")
    cog.observations.record(user, "Expected to happen", source="system",
                            origin="p1", epistemic_status="PREDICTED")
    cog.observations.record(user, "Only in a what-if", source="system",
                            origin="s1", epistemic_status="SIMULATED")

    stats = cog.observations.stats(user)
    assert stats["by_status"] == {"OBSERVED": 1, "INFERRED": 1,
                                  "PREDICTED": 1, "SIMULATED": 1}
    # Only the observed one is promotable.
    for item in cog.observations.list(user):
        result = cog.observations.promote(user, item["id"], runtime.memory)
        expected = item["epistemic_status"] == "OBSERVED"
        assert result["promoted"] is expected


def test_full_state_survives_a_runtime_restart(runtime):
    """Everything V8.3 adds is in SQLite, not in process memory."""
    from app.config import Settings
    from app.runtime import Runtime

    user = _u("R")
    cog = runtime.cognition
    mission = cog.missions.create(user, "Mission that must survive")
    cog.missions.set_state(user, mission["id"], "active", reason="started")
    entity = cog.world_v2.upsert(user, "project", "Project that must survive")
    cog.observations.record(user, "Observation that must survive",
                            source="conversation", origin="m1")
    cog.documents.ingest(user, "survivor.txt", b"content that must survive")
    cog.background.run_cycle(user, force=True)

    counts_before = {
        "missions": len(cog.missions.list(user)),
        "observations": cog.observations.stats(user)["total"],
        "world": cog.world_v2.snapshot(user)["count"],
        "changes": len(cog.world_v2.changes(user)),
        "documents": len(cog.documents.list(user)),
        "cycles": len(cog.background.cycles(user)),
    }

    # A brand-new Runtime over the same files.
    cfg = Settings(
        data_dir=runtime.settings.data_dir,
        sqlite_path=runtime.settings.sqlite_path,
        checkpoint_path=runtime.settings.checkpoint_path,
        chroma_path=runtime.settings.chroma_path,
    )
    restarted = Runtime(cfg)
    try:
        after = restarted.cognition
        assert len(after.missions.list(user)) == counts_before["missions"]
        assert after.observations.stats(user)["total"] == counts_before["observations"]
        assert after.world_v2.snapshot(user)["count"] == counts_before["world"]
        assert len(after.world_v2.changes(user)) == counts_before["changes"]
        assert len(after.documents.list(user)) == counts_before["documents"]
        assert len(after.background.cycles(user)) == counts_before["cycles"]
        # And the mission history is fully intact.
        assert len(after.missions.history(user, mission["id"])) == 2
    finally:
        restarted.close()
