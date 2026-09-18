"""V8.3 §19-§22 — simulation isolation, the time machine, prediction windows,
documents, connectors and research honesty."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _u(prefix: str = "s") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# ============================================================ simulation §20
def test_simulation_is_tagged_simulated(runtime):
    user = _u()
    runtime.cognition.world_v2.upsert(user, "project", "Something in flight")
    result = runtime.cognition.simulation.simulate(
        user, "What if I delayed the launch by two weeks?")
    assert result["epistemic_status"] == "SIMULATED"
    assert "SIMULATED" in result["disclaimer"]


def test_simulation_does_not_mutate_real_state(runtime):
    """The core isolation guarantee: real state is byte-identical afterwards."""
    user = _u()
    entity = runtime.cognition.world_v2.upsert(user, "project", "Untouched project")
    mission = runtime.cognition.missions.create(user, "Untouched mission")
    memory = runtime.memory.create(user_id=user, content="Untouched memory",
                                   category="fact")

    before_world = [dict(r) for r in runtime.db.query(
        "SELECT * FROM world_entities WHERE user_id=?", (user,))]
    before_missions = [dict(r) for r in runtime.db.query(
        "SELECT * FROM missions WHERE user_id=?", (user,))]
    before_changes = len(runtime.cognition.world_v2.changes(user))

    runtime.cognition.simulation.simulate(
        user, "What if I dropped this project entirely?")

    after_world = [dict(r) for r in runtime.db.query(
        "SELECT * FROM world_entities WHERE user_id=?", (user,))]
    after_missions = [dict(r) for r in runtime.db.query(
        "SELECT * FROM missions WHERE user_id=?", (user,))]

    assert before_world == after_world
    assert before_missions == after_missions
    assert len(runtime.cognition.world_v2.changes(user)) == before_changes
    assert runtime.memory.get(memory["memory"]["id"]) is not None


def test_simulation_creates_no_observations(runtime):
    """A projection is not evidence and must not enter the observation log."""
    user = _u()
    runtime.cognition.world_v2.upsert(user, "goal", "A goal to project against")
    before = runtime.cognition.observations.stats(user)["total"]
    runtime.cognition.simulation.simulate(user, "What if I focused only on this?")
    assert runtime.cognition.observations.stats(user)["total"] == before


def test_simulation_declares_its_assumptions(runtime):
    user = _u()
    result = runtime.cognition.simulation.simulate(
        user, "What if the vendor delivers late?",
        assumptions=["The vendor contract does not change"])
    assert "The vendor contract does not change" in result["assumptions"]


def test_thin_state_produces_low_confidence(runtime):
    user = _u()
    result = runtime.cognition.simulation.simulate(user, "What if everything changed?")
    assert result["confidence"] <= 0.3
    assert "assumptions" in result["evidence_note"]


def test_commit_refused_without_confirmation(runtime):
    user = _u()
    entity = runtime.cognition.world_v2.upsert(user, "project", "Commit candidate")
    result = runtime.cognition.simulation.simulate(user, "What if I paused it?")
    outcome = runtime.cognition.simulation.commit(
        user, result["id"],
        changes=[{"kind": "world_state", "entity_id": entity["id"],
                  "state": "blocked"}])
    assert outcome["committed"] is False
    assert runtime.cognition.world_v2.get(user, entity["id"])["state"] == "active"


def test_commit_refused_without_explicit_changes(runtime):
    user = _u()
    result = runtime.cognition.simulation.simulate(user, "What if I stopped?")
    outcome = runtime.cognition.simulation.commit(user, result["id"], confirm=True)
    assert outcome["committed"] is False
    assert "no concrete changes" in outcome["reason"]


def test_commit_applies_only_with_both_confirmation_and_changes(runtime):
    user = _u()
    entity = runtime.cognition.world_v2.upsert(user, "project", "Deliberate commit")
    result = runtime.cognition.simulation.simulate(user, "What if I blocked it?")
    outcome = runtime.cognition.simulation.commit(
        user, result["id"], confirm=True,
        changes=[{"kind": "world_state", "entity_id": entity["id"],
                  "state": "blocked"}])
    assert outcome["committed"] is True
    assert runtime.cognition.world_v2.get(user, entity["id"])["state"] == "blocked"


# =========================================================== time machine §21
def test_no_history_reports_unavailable(runtime):
    user = _u()
    result = runtime.cognition.timemachine.world_at(user, _iso(datetime.now(timezone.utc)))
    assert result["available"] is False
    assert "HISTORY NOT AVAILABLE" in result["reason"]


def test_request_before_recorded_history_is_refused(runtime):
    """We must never back-project current state onto an unrecorded past."""
    user = _u()
    runtime.cognition.world_v2.upsert(user, "project", "Recently created")
    long_ago = _iso(datetime.now(timezone.utc) - timedelta(days=3650))
    result = runtime.cognition.timemachine.world_at(user, long_ago)
    assert result["available"] is False
    assert "predates the earliest recorded history" in result["reason"]


def test_invalid_timestamp_is_refused(runtime):
    result = runtime.cognition.timemachine.world_at(_u(), "last tuesday")
    assert result["available"] is False
    assert "HISTORY NOT AVAILABLE" in result["reason"]


def test_world_reconstructed_from_real_changes(runtime):
    user = _u()
    entity = runtime.cognition.world_v2.upsert(user, "project", "Tracked over time")
    runtime.cognition.world_v2.set_state(user, entity["id"], "blocked",
                                         reason="hit a blocker")
    now = _iso(datetime.now(timezone.utc) + timedelta(seconds=1))
    result = runtime.cognition.timemachine.world_at(user, now)
    assert result["available"] is True
    match = [e for e in result["entities"] if e["id"] == entity["id"]]
    assert match and match[0]["state_at_time"] == "blocked"


def test_entity_absent_before_it_existed(runtime):
    """A fact created today did not exist yesterday — it must not appear."""
    user = _u()
    runtime.cognition.world_v2.upsert(user, "goal", "An anchor for history")
    anchor = _iso(datetime.now(timezone.utc) + timedelta(seconds=1))
    later = runtime.cognition.world_v2.upsert(user, "goal", "Created afterwards")
    runtime.db.execute(
        "UPDATE world_entities SET created_at=? WHERE id=?",
        (_iso(datetime.now(timezone.utc) + timedelta(days=1)), later["id"]))

    result = runtime.cognition.timemachine.world_at(user, anchor)
    ids = [e["id"] for e in result["entities"]] + [
        e["id"] for e in result["unknown"]]
    assert later["id"] not in ids


def test_coverage_reports_the_real_window(runtime):
    user = _u()
    assert runtime.cognition.timemachine.coverage(user)["available"] is False
    runtime.cognition.world_v2.upsert(user, "project", "Starts the history")
    coverage = runtime.cognition.timemachine.coverage(user)
    assert coverage["available"] is True
    assert coverage["earliest"]


def test_diff_between_two_moments(runtime):
    user = _u()
    first = runtime.cognition.world_v2.upsert(user, "project", "Existed early")
    # Backdate creation and its change record so the two sampling points
    # genuinely straddle the state transition.
    old = _iso(datetime.now(timezone.utc) - timedelta(days=10))
    runtime.db.execute("UPDATE world_entities SET created_at=? WHERE id=?",
                       (old, first["id"]))
    runtime.db.execute(
        "UPDATE world_changes SET created_at=? WHERE entity_id=?",
        (old, first["id"]))

    start = _iso(datetime.now(timezone.utc) - timedelta(days=5))
    runtime.cognition.world_v2.set_state(user, first["id"], "completed",
                                         reason="finished it")
    end = _iso(datetime.now(timezone.utc) + timedelta(seconds=1))

    diff = runtime.cognition.timemachine.diff(user, start, end)
    assert diff["available"] is True
    changed = [c for c in diff["changed"] if c["id"] == first["id"]]
    assert changed and changed[0]["from"] == "active"
    assert changed[0]["to"] == "completed"


def test_mission_history_reconstruction(runtime):
    user = _u()
    mission = runtime.cognition.missions.create(user, "Historic mission")
    runtime.cognition.missions.set_state(user, mission["id"], "active",
                                         reason="began")
    now = _iso(datetime.now(timezone.utc) + timedelta(seconds=1))
    result = runtime.cognition.timemachine.missions_at(user, now)
    match = [m for m in result["missions"] if m["id"] == mission["id"]]
    assert match and match[0]["state_at_time"] == "active"


# ======================================================== predictions §19
def test_prediction_window_creates_evaluation_date(runtime):
    user = _u()
    prediction = runtime.cognition.predictions.create(
        user, "The migration will finish this month", 0.7,
        evaluation_window_days=30)
    assert prediction["expected_evaluation_at"] is not None
    assert prediction["evaluation_window_days"] == 30


def test_only_elapsed_windows_are_due(runtime):
    user = _u()
    runtime.cognition.predictions.create(
        user, "Something far in the future", 0.6, evaluation_window_days=365)
    assert runtime.cognition.predictions.due_for_evaluation(user) == []


def test_elapsed_window_becomes_due(runtime):
    user = _u()
    prediction = runtime.cognition.predictions.create(
        user, "Should have happened by now", 0.6, evaluation_window_days=1)
    past = _iso(datetime.now(timezone.utc) - timedelta(days=2))
    runtime.db.execute(
        "UPDATE predictions SET expected_evaluation_at=? WHERE id=?",
        (past, prediction["id"]))
    due = runtime.cognition.predictions.due_for_evaluation(user)
    assert [p["id"] for p in due] == [prediction["id"]]


def test_unresolved_is_not_scored_as_right_or_wrong(runtime):
    """Silence must never be converted into a correct/incorrect verdict."""
    user = _u()
    prediction = runtime.cognition.predictions.create(
        user, "No evidence will arrive", 0.8, evaluation_window_days=1)
    result = runtime.cognition.predictions.mark_unresolved(user, prediction["id"])
    assert result["status"] == "unresolved"
    assert "UNRESOLVED" in result["outcome"]

    accuracy = runtime.cognition.predictions.accuracy(user)
    assert accuracy["resolved"] == 0


# ========================================================== documents §6
def test_text_document_is_genuinely_read(runtime):
    user = _u()
    doc = runtime.cognition.documents.ingest(
        user, "notes.txt", b"The quarterly review is on the 14th.")
    assert doc["understanding"] == "FULL"
    assert doc["state"] == "AVAILABLE"
    assert doc["extracted_chars"] > 0


def test_pdf_is_metadata_only_never_faked(runtime):
    """No fake OCR, no invented summary — only honest metadata."""
    user = _u()
    doc = runtime.cognition.documents.ingest(user, "contract.pdf", b"%PDF-1.7 fake")
    assert doc["understanding"] == "METADATA ONLY"
    assert "NOT CONFIGURED" in doc["detail"]
    assert doc["extracted_chars"] == 0


def test_csv_is_structure_only(runtime):
    user = _u()
    doc = runtime.cognition.documents.ingest(
        user, "data.csv", b"name,role\nAda,engineer\nGrace,admiral\n")
    assert doc["understanding"] == "STRUCTURE ONLY"
    assert "3 row(s)" in doc["detail"]
    assert "not inferred" in doc["detail"]


def test_document_lifecycle_and_replacement(runtime):
    user = _u()
    first = runtime.cognition.documents.ingest(user, "spec.md", b"# Version one")
    second = runtime.cognition.documents.ingest(user, "spec.md", b"# Version two")
    assert runtime.cognition.documents.get(user, first["id"])["state"] == "REPLACED"
    assert second["state"] == "AVAILABLE"


def test_removal_is_soft(runtime):
    user = _u()
    doc = runtime.cognition.documents.ingest(user, "temp.txt", b"disposable")
    assert runtime.cognition.documents.remove(user, doc["id"])
    assert runtime.cognition.documents.get(user, doc["id"])["state"] == "REMOVED"


def test_document_capabilities_are_explicit(runtime):
    caps = runtime.cognition.documents.capabilities()
    assert ".pdf" in caps["not_configured"]
    assert caps["not_configured"][".pdf"]["state"] == "NOT CONFIGURED"
    assert "inferred from a filename" in caps["detail"]


def test_oversized_document_rejected(runtime):
    from app.cognition.documents import MAX_BYTES

    with pytest.raises(ValueError):
        runtime.cognition.documents.ingest(_u(), "huge.txt", b"x" * (MAX_BYTES + 1))


# ================================================= connectors & research §26-27
def test_connectors_are_declared_but_not_connected(runtime):
    user = _u()
    runtime.cognition.connectors.declare_defaults(user)
    status = runtime.cognition.connectors.status(user)
    assert status["connected"] == 0
    assert all(c["state"] == "NOT CONNECTED" for c in status["connectors"])


def test_fetch_distinguishes_absent_from_empty(runtime):
    """'No connector' must never be reported as 'the source had no data'."""
    user = _u()
    runtime.cognition.connectors.declare_defaults(user)
    result = runtime.cognition.connectors.fetch(user, "calendar")
    assert result["connected"] is False
    assert result["items"] is None          # not [] — there is no source
    assert "not the same as the source being empty" in result["detail"]


def test_unknown_connector_is_honest(runtime):
    result = runtime.cognition.connectors.fetch(_u(), "telepathy")
    assert result["connected"] is False
    assert "no source to read from" in result["detail"]


def test_research_produces_no_findings_without_a_provider(runtime):
    user = _u()
    session = runtime.cognition.research.start(user, "What is the best database?")
    assert session["state"] == "BLOCKED"
    assert session["provider_state"] == "RESEARCH PROVIDER NOT CONFIGURED"
    assert session["claims"] == []
    assert "no findings have been generated" in session["detail"].lower()


def test_research_status_is_explicit(runtime):
    status = runtime.cognition.research.status(_u())
    assert status["available"] is False
    assert "no web access" in status["detail"]


# ============================================== maintenance §23 (non-destructive)
def test_review_changes_nothing(runtime):
    user = _u()
    created = runtime.memory.create(user_id=user, content="A memory to review",
                                    category="fact")
    report = runtime.cognition.maintenance.review(user)
    assert report["changes"] == 0
    assert runtime.memory.get(created["memory"]["id"]) is not None


def test_reinforcement_requires_evidence_not_retrieval(runtime):
    """Retrieval alone must never strengthen a memory."""
    user = _u()
    created = runtime.memory.create(user_id=user, content="Retrieved often",
                                    category="fact")
    result = runtime.cognition.maintenance.reinforce_from_outcome(
        user, created["memory"]["id"], helped=True, evidence=[])
    assert result["applied"] is False
    assert "INSUFFICIENT EVIDENCE" in result["reason"]
    assert "Retrieval on its own is not evidence" in result["reason"]


def test_reinforcement_applies_with_recorded_outcome(runtime):
    user = _u()
    created = runtime.memory.create(user_id=user, content="Genuinely useful",
                                    category="fact", importance=0.5)
    result = runtime.cognition.maintenance.reinforce_from_outcome(
        user, created["memory"]["id"], helped=True,
        evidence=["User confirmed this advice worked"])
    assert result["applied"] is True
    assert result["importance"] > 0.5


def test_remedies_are_all_non_destructive(runtime):
    remedies = runtime.cognition.maintenance.remedies()
    assert remedies["non_destructive"] is True
    assert "nothing is permanently destroyed" in remedies["detail"].lower()
