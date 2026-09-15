"""V8.3 §7-§9, §17 — world change history, staleness, reconciliation,
and the canonical observation abstraction."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest


def _u(prefix: str = "w") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def world2(runtime):
    return runtime.cognition.world_v2


@pytest.fixture
def obs(runtime):
    return runtime.cognition.observations


# ----------------------------------------------------- change provenance §7
def test_creation_is_recorded_in_change_history(world2):
    user = _u()
    world2.upsert(user, "project", "Payment gateway rebuild")
    changes = world2.changes(user)
    assert changes[-1]["change"] == "created"
    assert changes[-1]["new_state"] == "active"


def test_state_change_records_previous_and_new(world2):
    user = _u()
    entity = world2.upsert(user, "project", "Data warehouse")
    world2.set_state(user, entity["id"], "blocked",
                     reason="Vendor contract unsigned")
    change = world2.changes(user, entity_id=entity["id"])[0]
    assert change["change"] == "state_changed"
    assert change["previous_state"] == "active"
    assert change["new_state"] == "blocked"
    assert "Vendor contract unsigned" in change["evidence"]


def test_repeat_mention_records_reconfirmation(world2):
    user = _u()
    world2.upsert(user, "goal", "Learn Portuguese")
    world2.upsert(user, "goal", "Learn Portuguese")
    kinds = [c["change"] for c in world2.changes(user)]
    assert "created" in kinds
    assert "reconfirmed" in kinds


# ------------------------------------------------------------- staleness §9
def test_fresh_fact_is_not_stale(world2):
    user = _u()
    entity = world2.upsert(user, "person", "Priya on the platform team")
    verdict = world2.assess_freshness(world2.get(user, entity["id"]))
    assert verdict["freshness_class"] == "FRESH"
    assert verdict["stale"] is False


def test_old_fact_becomes_stale_but_keeps_its_value(runtime, world2):
    """Stale must never mean false: the value and state are untouched."""
    user = _u()
    entity = world2.upsert(user, "risk", "Third-party API may rate limit us")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).isoformat(
        timespec="seconds")
    runtime.db.execute(
        "UPDATE world_entities SET last_confirmed_at=?, updated_at=?,"
        " created_at=? WHERE id=?", (old, old, old, entity["id"]))

    result = world2.refresh_staleness(user)
    assert any(item["id"] == entity["id"] for item in result["newly_stale"])

    after = world2.get(user, entity["id"])
    assert after["stale"] == 1
    assert after["state"] == "active"          # value preserved
    assert after["label"] == "Third-party API may rate limit us"
    assert "not as false" in after["freshness_reason"]


def test_staleness_horizons_differ_by_fact_class(world2):
    from app.cognition.world_v2 import FRESHNESS_HORIZON_DAYS

    # A risk goes stale far sooner than a person.
    assert FRESHNESS_HORIZON_DAYS["risk"] < FRESHNESS_HORIZON_DAYS["person"]


def test_confirming_a_fact_clears_staleness(runtime, world2):
    user = _u()
    entity = world2.upsert(user, "commitment", "Send the board deck")
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(
        timespec="seconds")
    runtime.db.execute(
        "UPDATE world_entities SET last_confirmed_at=? WHERE id=?",
        (old, entity["id"]))
    world2.refresh_staleness(user)
    assert world2.get(user, entity["id"])["stale"] == 1

    world2.confirm(user, entity["id"])
    assert world2.get(user, entity["id"])["stale"] == 0


# -------------------------------------------------------- reconciliation §8
def test_novel_claim_is_simply_recorded(world2):
    user = _u()
    result = world2.reconcile(user, "project", "Brand new initiative")
    assert result["verdict"] == "keep"
    assert result["entity"]["label"] == "Brand new initiative"


def test_same_subject_compatible_detail_merges(world2):
    user = _u()
    world2.upsert(user, "project", "Website redesign project")
    result = world2.reconcile(user, "project", "Website redesign project",
                              detail="Now includes the mobile app")
    assert result["verdict"] == "merge"


def test_explicit_correction_always_supersedes(world2):
    user = _u()
    world2.upsert(user, "project", "Mobile app launch", confidence=0.9)
    result = world2.reconcile(user, "project", "Mobile app launch",
                              state="abandoned", confidence=0.3,
                              explicit_correction=True)
    assert result["verdict"] == "supersede"
    assert result["entity"]["state"] == "abandoned"


def test_stronger_evidence_supersedes_weaker(world2):
    user = _u()
    world2.upsert(user, "project", "Analytics pipeline", confidence=0.5)
    result = world2.reconcile(user, "project", "Analytics pipeline",
                              state="completed", confidence=0.85,
                              evidence=["Deployed to production on Friday"])
    assert result["verdict"] == "supersede"
    assert result["entity"]["state"] == "completed"


def test_evenly_matched_conflict_is_flagged_not_guessed(world2):
    user = _u()
    world2.upsert(user, "project", "Ambiguous project state", confidence=0.6)
    result = world2.reconcile(user, "project", "Ambiguous project state",
                              state="blocked", confidence=0.6)
    assert result["verdict"] == "flag"
    # The system must not silently pick a side.
    assert result["entity"]["state"] == "active"
    assert "Not resolved automatically" in result["explanation"]


def test_weaker_conflicting_claim_downgrades_confidence(world2):
    user = _u()
    entity = world2.upsert(user, "project", "Well established project",
                           confidence=0.9)
    before = world2.get(user, entity["id"])["confidence"]
    result = world2.reconcile(user, "project", "Well established project",
                              state="abandoned", confidence=0.3)
    assert result["verdict"] == "downgrade"
    assert result["entity"]["state"] == "active"        # value kept
    assert result["entity"]["confidence"] < before      # certainty reduced


def test_reconciliation_never_deletes(runtime, world2):
    user = _u()
    world2.upsert(user, "goal", "A goal that gets contradicted")
    for _ in range(3):
        world2.reconcile(user, "goal", "A goal that gets contradicted",
                         state="abandoned", confidence=0.3)
    rows = runtime.db.query(
        "SELECT * FROM world_entities WHERE user_id=?", (user,))
    assert len(rows) == 1


# ------------------------------------------------------------ observations §17
def test_observation_requires_content_and_origin(obs):
    user = _u()
    with pytest.raises(ValueError):
        obs.record(user, "   ", source="conversation", origin="msg_1")
    with pytest.raises(ValueError):
        obs.record(user, "Something happened", source="conversation", origin="")


def test_observation_rejects_unknown_epistemic_status(obs):
    with pytest.raises(ValueError):
        obs.record(_u(), "A claim", source="conversation", origin="msg_1",
                   epistemic_status="PROBABLY")


def test_observation_is_not_a_memory(runtime, obs):
    """Recording evidence must not create a memory as a side effect."""
    user = _u()
    before = len(runtime.memory.list(user))
    obs.record(user, "User mentioned a deadline on Friday",
               source="conversation", origin="msg_42")
    assert len(runtime.memory.list(user)) == before


def test_promotion_is_explicit_and_creates_a_memory(runtime, obs):
    user = _u()
    record = obs.record(user, "User prefers async standups",
                        source="conversation", origin="msg_7")
    result = obs.promote(user, record["id"], runtime.memory)
    assert result["promoted"] is True
    assert result["memory"]["content"] == "User prefers async standups"


def test_only_observed_evidence_can_be_promoted(runtime, obs):
    """An inference must not be laundered into a fact."""
    user = _u()
    guess = obs.record(user, "User is probably behind schedule",
                       source="conversation", origin="msg_9",
                       epistemic_status="INFERRED")
    result = obs.promote(user, guess["id"], runtime.memory)
    assert result["promoted"] is False
    assert "INFERRED" in result["reason"]


def test_simulated_evidence_cannot_be_promoted(runtime, obs):
    user = _u()
    sim = obs.record(user, "If they delayed, load would drop",
                     source="system", origin="sim_1",
                     epistemic_status="SIMULATED")
    assert obs.promote(user, sim["id"], runtime.memory)["promoted"] is False


def test_evidence_for_separates_statuses(obs):
    user = _u()
    obs.record(user, "Directly observed thing", source="conversation",
               origin="m1", subject_kind="world", subject_id="w_1")
    obs.record(user, "Inferred thing", source="conversation", origin="m2",
               epistemic_status="INFERRED", subject_kind="world",
               subject_id="w_1")
    evidence = obs.evidence_for(user, "world", "w_1")
    assert evidence["by_status"]["OBSERVED"] == 1
    assert evidence["by_status"]["INFERRED"] == 1
    assert "1 direct observation" in evidence["verdict"]


def test_no_observations_reports_insufficient_evidence(obs):
    evidence = obs.evidence_for(_u(), "world", "nonexistent")
    assert evidence["verdict"] == "INSUFFICIENT EVIDENCE"


def test_discard_annotates_but_never_deletes(runtime, obs):
    user = _u()
    record = obs.record(user, "Possibly wrong claim", source="conversation",
                        origin="m3")
    assert obs.discard(user, record["id"], "User said this was a mistake")
    still_there = obs.get(record["id"])
    assert still_there is not None
    assert still_there["provenance"]["discarded"]["reason"] == (
        "User said this was a mistake")


def test_stats_are_honest_when_empty(obs):
    stats = obs.stats(_u())
    assert stats["total"] == 0
    assert "No observations" in stats["detail"]
