"""V8.3 §10-§12 — long-running missions, bounded planning, continuity."""
from __future__ import annotations

import uuid

import pytest

from app.cognition.missions import MAX_OPEN_STEPS, OPEN_STATES, STATES


@pytest.fixture
def missions(runtime):
    return runtime.cognition.missions


def _u(prefix: str = "ms") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def test_mission_created_in_draft_with_history(missions):
    user = _u()
    mission = missions.create(user, "Ship the migration", priority=0.8)
    assert mission["state"] == "draft"
    assert mission["progress"] == 0.0

    history = missions.history(user, mission["id"])
    assert len(history) == 1
    assert history[0]["change"] == "created"
    assert history[0]["new_state"] == "draft"


def test_title_must_be_meaningful(missions):
    with pytest.raises(ValueError):
        missions.create(_u(), "ab")


def test_every_state_transition_records_a_reason(missions):
    user = _u()
    mission = missions.create(user, "Publish the paper")
    missions.set_state(user, mission["id"], "active", reason="User started work")
    missions.set_state(user, mission["id"], "blocked",
                       reason="Waiting on co-author review",
                       blocked_reason="Co-author has not replied")

    updated = missions.get(user, mission["id"])
    assert updated["state"] == "blocked"
    assert updated["blocked_reason"] == "Co-author has not replied"

    history = missions.history(user, mission["id"])
    reasons = [h["reason"] for h in history]
    assert "Waiting on co-author review" in reasons
    # Every transition is reconstructable, not just the latest state.
    transitions = [(h["previous_state"], h["new_state"]) for h in history
                   if h["change"] == "state_changed"]
    assert ("active", "blocked") in transitions
    assert ("draft", "active") in transitions


def test_unknown_state_rejected(missions):
    user = _u()
    mission = missions.create(user, "Some objective")
    with pytest.raises(ValueError):
        missions.set_state(user, mission["id"], "finished", reason="typo")


def test_progress_derives_only_from_completed_steps(missions):
    user = _u()
    mission = missions.create(user, "Three part plan")
    ids = []
    for label in ("First part", "Second part", "Third part"):
        result = missions.add_step(user, mission["id"], label)
        assert result["added"] is True
        ids.append(result["step"]["id"])

    assert missions.get(user, mission["id"])["progress"] == 0.0
    missions.complete_step(user, ids[0])
    assert missions.get(user, mission["id"])["progress"] == pytest.approx(1 / 3, abs=0.01)
    missions.complete_step(user, ids[1])
    missions.complete_step(user, ids[2])
    assert missions.get(user, mission["id"])["progress"] == 1.0


def test_progress_is_not_invented_without_steps(missions):
    """A mission with no steps must not grow a fabricated progress number."""
    user = _u()
    mission = missions.create(user, "No steps yet")
    missions.set_state(user, mission["id"], "active", reason="started")
    assert missions.get(user, mission["id"])["progress"] == 0.0


def test_planning_is_bounded(missions):
    user = _u()
    mission = missions.create(user, "Very large project")
    for i in range(MAX_OPEN_STEPS):
        assert missions.add_step(user, mission["id"], f"Step number {i}")["added"]
    overflow = missions.add_step(user, mission["id"], "One step too many")
    assert overflow["added"] is False
    assert "bounded" in overflow["reason"].lower()


def test_replanning_requires_a_recognised_trigger(missions):
    user = _u()
    mission = missions.create(user, "Replannable mission")
    bogus = missions.replan(user, mission["id"], reason="felt like it")
    assert bogus["replanned"] is False

    real = missions.replan(user, mission["id"], reason="blocked",
                           steps=["Escalate to the vendor"])
    assert real["replanned"] is True
    assert real["steps_added"] == 1


def test_resume_brief_separates_blocked_waiting_and_active(missions):
    user = _u()
    a = missions.create(user, "Active thing")
    b = missions.create(user, "Blocked thing")
    c = missions.create(user, "Waiting thing")
    missions.set_state(user, a["id"], "active", reason="go")
    missions.set_state(user, b["id"], "blocked", reason="stuck",
                       blocked_reason="Needs a decision from finance")
    missions.set_state(user, c["id"], "waiting", reason="pending",
                       waiting_on="Vendor quote")

    brief = missions.resume_brief(user)
    assert brief["open"] == 3
    assert [m["title"] for m in brief["active"]] == ["Active thing"]
    assert brief["blocked"][0]["blocked_reason"] == "Needs a decision from finance"
    assert brief["waiting"][0]["waiting_on"] == "Vendor quote"
    assert "blocked" in brief["summary"]


def test_empty_brief_is_honest(missions):
    assert missions.resume_brief(_u())["summary"] == "No open missions."


def test_completed_missions_leave_open_set(missions):
    user = _u()
    mission = missions.create(user, "Finishable mission")
    missions.set_state(user, mission["id"], "active", reason="go")
    missions.set_state(user, mission["id"], "completed", reason="done")
    assert missions.resume_brief(user)["open"] == 0
    assert missions.get(user, mission["id"])["completed_at"] is not None


def test_missions_persist_across_registry_instances(runtime, missions):
    """Mission state is in SQLite, not memory — it survives a fresh object."""
    from app.cognition.missions import MissionRegistry

    user = _u()
    mission = missions.create(user, "Durable mission")
    missions.set_state(user, mission["id"], "active", reason="go")

    fresh = MissionRegistry(runtime.db, runtime.cognition.bus)
    reloaded = fresh.get(user, mission["id"])
    assert reloaded is not None
    assert reloaded["state"] == "active"
    assert len(fresh.history(user, mission["id"])) == 2


def test_mission_state_change_emits_observation(runtime, missions):
    user = _u()
    mission = missions.create(user, "Observed mission")
    missions.set_state(user, mission["id"], "active", reason="user began work")
    evidence = runtime.cognition.observations.evidence_for(
        user, "mission", mission["id"])
    assert evidence["total"] >= 1
    assert evidence["by_status"]["OBSERVED"] >= 1


def test_links_connect_missions_to_other_cognition(missions):
    user = _u()
    mission = missions.create(user, "Linked mission")
    missions.link(mission["id"], "memory", "mem_123", "informed_by")
    links = missions.get(user, mission["id"])["links"]
    assert links[0]["subject_id"] == "mem_123"
    assert links[0]["relation"] == "informed_by"


def test_open_states_cover_all_non_terminal(missions):
    from app.cognition.missions import TERMINAL_STATES

    assert set(OPEN_STATES) | set(TERMINAL_STATES) == set(STATES)
