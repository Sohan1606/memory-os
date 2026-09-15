"""V8.3 §13-§16 — bounded background cognition and attention V2.

The tests here are mostly about what the system must NOT do: fabricate
activity, run unbounded, ignore cancellation, or treat silence as consent.
"""
from __future__ import annotations

import uuid

import pytest

from app.cognition.background import (Cancellation, MAX_TASKS_PER_CYCLE,
                                      MIN_INTERVAL_S)


def _u(prefix: str = "bg") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def background(runtime):
    return runtime.cognition.background


@pytest.fixture
def attention(runtime):
    return runtime.cognition.attention_v2


# ------------------------------------------------------- honesty §14
def test_empty_cycle_is_recorded_as_empty(background):
    """The central honesty rule: nothing found means nothing claimed."""
    user = _u()
    result = background.run_cycle(user, force=True)
    assert result["state"] == "completed"
    assert result["findings"] == []
    assert result["changes_made"] == 0
    assert "nothing needed attention" in result["detail"]


def test_empty_cycle_persists_honestly(background):
    user = _u()
    background.run_cycle(user, force=True)
    cycle = background.cycles(user)[0]
    assert cycle["state"] == "completed"
    assert cycle["findings"] == []
    assert cycle["changes_made"] == 0


def test_status_counts_empty_cycles_truthfully(background):
    user = _u()
    background.run_cycle(user, force=True)
    background.run_cycle(user, force=True)
    status = background.status(user)
    assert status["total_recent"] == 2
    assert status["empty_cycles"] == 2
    assert "found nothing" in status["detail"]


def test_never_run_reports_no_cycles(background):
    status = background.status(_u())
    assert status["recent_cycles"] == []
    assert "No background cycles have run yet" in status["detail"]


# --------------------------------------------------------- control §13
def test_disabled_background_does_not_run(background):
    user = _u()
    background.disable(user)
    result = background.run_cycle(user, force=True)
    assert result["state"] == "skipped"
    assert "disabled" in result["skipped_reason"]


def test_paused_background_does_not_run(background):
    user = _u()
    background.pause(user)
    assert background.run_cycle(user, force=True)["state"] == "skipped"


def test_resume_restores_running(background):
    user = _u()
    background.pause(user)
    background.resume(user)
    assert background.state(user) == "enabled"
    assert background.run_cycle(user, force=True)["state"] == "completed"


def test_state_changes_are_persisted_as_events(runtime, background):
    user = _u()
    background.disable(user)
    types = [e.type for e in runtime.cognition.bus.recent(user, limit=20)]
    assert "background.disabled" in types


def test_unknown_state_rejected(background):
    with pytest.raises(ValueError):
        background.set_state(_u(), "turbo")


# ----------------------------------------------------------- bounds §13
def test_rate_limiting_prevents_refresh_storms(background):
    """A browser refresh must not be able to trigger repeated work."""
    user = _u()
    assert background.run_cycle(user, force=True)["state"] == "completed"
    second = background.run_cycle(user)          # not forced
    assert second["state"] == "skipped"
    assert "Rate limited" in second["skipped_reason"]


def test_cancellation_stops_the_cycle(background):
    user = _u()
    token = Cancellation()
    token.cancel()
    result = background.run_cycle(user, force=True, cancellation=token)
    assert result["state"] == "cancelled"
    assert "Cancelled" in result["skipped_reason"]


def test_deadline_is_respected(background):
    """An impossibly short deadline truncates rather than overrunning."""
    user = _u()
    result = background.run_cycle(user, force=True, deadline_s=0.000001)
    assert result["state"] == "completed"
    assert result["tasks_run"] == []
    assert "Deadline reached" in (result["skipped_reason"] or "")


def test_task_budget_is_bounded(background):
    user = _u()
    result = background.run_cycle(user, force=True)
    assert len(result["tasks_run"]) <= MAX_TASKS_PER_CYCLE


def test_cycle_failure_is_contained_and_recorded(runtime, background,
                                                 monkeypatch):
    """An upkeep failure must never propagate into the user's session."""
    user = _u()

    def boom(*_a, **_kw):
        raise RuntimeError("simulated task failure")

    monkeypatch.setattr(background, "_task_world_staleness", boom)
    result = background.run_cycle(user, force=True)
    assert result["state"] == "failed"
    assert "simulated task failure" in result["error"]
    assert background.cycles(user)[0]["state"] == "failed"


def test_background_is_non_destructive(runtime, background):
    """A cycle must not delete memories or change world values."""
    user = _u()
    created = runtime.memory.create(user_id=user, content="A durable fact",
                                    category="fact")
    entity = runtime.cognition.world_v2.upsert(user, "project", "Live project")

    background.run_cycle(user, force=True)

    assert runtime.memory.get(created["memory"]["id"]) is not None
    after = runtime.cognition.world_v2.get(user, entity["id"])
    assert after["state"] == "active"
    assert after["label"] == "Live project"


def test_staleness_task_reports_real_findings(runtime, background):
    """When there IS something to find, the cycle reports it truthfully."""
    from datetime import datetime, timedelta, timezone

    user = _u()
    entity = runtime.cognition.world_v2.upsert(
        user, "risk", "An ageing untouched risk")
    old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat(
        timespec="seconds")
    runtime.db.execute(
        "UPDATE world_entities SET last_confirmed_at=?, updated_at=?,"
        " created_at=? WHERE id=?", (old, old, old, entity["id"]))

    result = background.run_cycle(user, force=True)
    summaries = [f["summary"] for f in result["findings"]]
    assert any("An ageing untouched risk" in s for s in summaries)
    assert result["changes_made"] == 0       # metadata only, no value changes


# ------------------------------------------------------- attention §15
def test_full_ladder_is_available(attention):
    from app.cognition.attention import DO_NOTHING, LEVELS

    assert LEVELS == ("IGNORE", "MONITOR", "PREPARE", "MENTION", "ASK", "ACT")
    assert DO_NOTHING == "DO_NOTHING"


def test_high_value_certain_topic_reaches_action(attention):
    user = _u()
    result = attention.evaluate(user, "Production database is down",
                                importance=0.98, urgency=0.98, confidence=0.95)
    assert result["level"] in ("ACT", "ASK", "MENTION")
    assert result["acted"] is True


def test_low_value_topic_is_suppressed(attention):
    user = _u()
    result = attention.evaluate(user, "Minor typo in an old note",
                                importance=0.05, urgency=0.05, confidence=0.3)
    assert result["level"] in ("IGNORE", "DO_NOTHING", "MONITOR")
    assert result["acted"] is False


def test_suppression_is_recorded_with_reasoning(attention):
    user = _u()
    attention.evaluate(user, "Something not worth raising",
                       importance=0.1, urgency=0.1, confidence=0.3)
    suppressions = attention.suppressions(user)
    assert len(suppressions) == 1
    assert suppressions[0]["suppressed_because"]


def test_low_relevance_raises_the_bar(attention):
    """Interrupting about something unrelated costs more."""
    user = _u()
    related = attention.evaluate(user, "A topic in focus", importance=0.5,
                                 urgency=0.5, confidence=0.6, relevance=1.0)
    unrelated = attention.evaluate(user, "A topic out of focus",
                                   importance=0.5, urgency=0.5,
                                   confidence=0.6, relevance=0.0)
    assert unrelated["interruption_cost"] > related["interruption_cost"]


def test_active_mission_lowers_the_bar(runtime, attention):
    user = _u()
    mission = runtime.cognition.missions.create(user, "An important mission")
    runtime.cognition.missions.set_state(user, mission["id"], "active",
                                         reason="started")
    plain = attention.evaluate(user, "Generic topic", importance=0.5,
                               urgency=0.5, confidence=0.6)
    linked = attention.evaluate(user, "Mission-related topic", importance=0.5,
                                urgency=0.5, confidence=0.6,
                                mission_id=mission["id"])
    assert linked["interruption_cost"] < plain["interruption_cost"]
    assert any("active mission" in r for r in linked["reasoning"])


def test_paused_mission_raises_the_bar(runtime, attention):
    user = _u()
    mission = runtime.cognition.missions.create(user, "A paused mission")
    runtime.cognition.missions.set_state(user, mission["id"], "paused",
                                         reason="user paused it")
    plain = attention.evaluate(user, "Generic topic", importance=0.5,
                               urgency=0.5, confidence=0.6)
    linked = attention.evaluate(user, "Paused mission topic", importance=0.5,
                                urgency=0.5, confidence=0.6,
                                mission_id=mission["id"])
    assert linked["interruption_cost"] > plain["interruption_cost"]


# ------------------------------------------------ learned silence §16
def test_silence_policy_needs_evidence(attention):
    policy = attention.silence_policy(_u())
    assert policy["learned"] is False
    assert policy["verdict"] == "INSUFFICIENT EVIDENCE"
    assert policy["interruption_cost"] == 0.4


def test_silence_is_never_treated_as_acceptance(attention):
    """Only recorded reactions count — never the absence of one."""
    user = _u()
    for i in range(6):
        attention.evaluate(user, f"Raised topic {i}", importance=0.9,
                           urgency=0.9, confidence=0.9)
    # Interventions were presented but the user never reacted.
    assert attention.silence_policy(user)["total"] == 0
    assert attention.silence_policy(user)["learned"] is False


def test_repeated_rejection_teaches_the_system_to_be_quieter(attention):
    user = _u()
    for i in range(6):
        result = attention.evaluate(user, f"Interrupting topic {i}",
                                    importance=0.9, urgency=0.9, confidence=0.9)
        attention.record_reaction(user, result["intervention_id"], False,
                                  detail="Not now")
    policy = attention.silence_policy(user)
    assert policy["learned"] is True
    assert policy["verdict"] == "PREFERS SILENCE"
    assert policy["interruption_cost"] > 0.4


def test_acceptance_keeps_the_system_receptive(attention):
    user = _u()
    for i in range(6):
        result = attention.evaluate(user, f"Welcome topic {i}", importance=0.9,
                                    urgency=0.9, confidence=0.9)
        attention.record_reaction(user, result["intervention_id"], True)
    policy = attention.silence_policy(user)
    assert policy["verdict"] == "RECEPTIVE"
    assert policy["interruption_cost"] == 0.4


def test_do_nothing_is_distinct_from_ignore(attention):
    """DO_NOTHING means 'real but not now'; IGNORE means 'not worth tracking'."""
    user = _u()
    # Confidence stays above the 0.35 floor so the difference is driven by
    # expected value, not by uncertainty (which correctly yields MONITOR).
    trivial = attention.evaluate(user, "Utterly trivial", importance=0.01,
                                 urgency=0.01, confidence=0.5)
    real = attention.evaluate(user, "Real but badly timed", importance=0.5,
                              urgency=0.5, confidence=0.45)
    assert trivial["level"] == "IGNORE"
    assert real["level"] == "DO_NOTHING"
    assert real["expected_value"] > trivial["expected_value"]


def test_uncertainty_yields_monitor_not_silence(attention):
    """Too unsure to act is MONITOR — it is not the same as 'not worth it'."""
    user = _u()
    result = attention.evaluate(user, "Something possibly important",
                                importance=0.8, urgency=0.8, confidence=0.2)
    assert result["level"] == "MONITOR"
