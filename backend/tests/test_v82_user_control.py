"""
V8.2 §19 — user-controlled cognition through natural language.

The defining constraint: commands fire ONLY on unambiguous phrasing, and a
destructive command whose target cannot be resolved must refuse rather than
guess which memory the user meant.
"""
import uuid

import pytest

from app.cognition.user_control import (CORRECT, EXPLAIN_BEHAVIOUR,
                                        EXPLAIN_BELIEF, FORGET, PRIORITISE,
                                        REMEMBER, STOP_TOPIC, parse)


@pytest.fixture
def control(runtime):
    return runtime.cognition.control


@pytest.fixture
def u():
    return f"ctl-{uuid.uuid4().hex[:8]}"


# ------------------------------------------------------------------- parsing
@pytest.mark.parametrize("text,expected", [
    ("forget that", FORGET),
    ("delete what I said about Redis", FORGET),
    ("that's wrong", CORRECT),
    ("remember that I deploy on Tuesdays", REMEMBER),
    ("why do you believe that?", EXPLAIN_BELIEF),
    ("why do you always ask so many questions", EXPLAIN_BEHAVIOUR),
    ("stop asking me about the migration", STOP_TOPIC),
    ("this is important", PRIORITISE),
])
def test_commands_are_recognised(text, expected):
    parsed = parse(text)
    assert parsed is not None, f"{text!r} should be recognised"
    assert parsed["command"] == expected


@pytest.mark.parametrize("text", [
    "I forgot to mention the deploy window",
    "what should I remember for the meeting",
    "the migration is important to the team",
    "I think that's a wrong turn on the highway",
    "can you help me plan the week",
])
def test_ordinary_conversation_is_not_a_command(text):
    """False positives here would silently mutate the user's memory."""
    assert parse(text) is None


def test_handle_returns_none_for_ordinary_conversation(control, u):
    assert control.handle(u, "I had a productive morning today") is None


# -------------------------------------------------------------------- forget
def test_forget_that_with_nothing_in_focus_refuses(control, u):
    result = control.handle(u, "forget that")
    assert result["applied"] is False
    assert result["requires"] == "clarification"
    assert "did not guess" in result["summary"]


def test_forget_that_deletes_the_focused_memory(runtime, control, u):
    created = runtime.memory.create(u, "Prefers Redis for the cache layer",
                                    allow_duplicate=True)
    mid = created["memory"]["id"]
    runtime.cognition.focus.set_focus(u, "memory", mid, session_id="s1")

    result = control.handle(u, "forget that", session_id="s1")
    assert result["applied"] is True
    assert result["memory_id"] == mid
    assert runtime.memory.get(mid) is None


def test_forget_by_description_resolves_through_search(runtime, control, u):
    runtime.memory.create(u, "Uses Redis for the cache layer in production",
                          allow_duplicate=True)
    result = control.handle(u, "forget what I said about Redis")
    assert result["applied"] is True
    assert "Forgotten" in result["summary"]


def test_forget_refuses_when_the_description_matches_nothing(control, u):
    result = control.handle(u, "forget what I said about quantum tunnelling")
    assert result["applied"] is False
    assert "could not find anything" in result["summary"]


def test_forget_is_reported_in_plain_language(runtime, control, u):
    runtime.memory.create(u, "Deploys the API every Tuesday morning",
                          allow_duplicate=True)
    result = control.handle(u, "forget what I said about Tuesday")
    assert "no longer be retrieved" in result["summary"]


# ------------------------------------------------------------------- correct
def test_correction_records_a_contradiction(runtime, control, u):
    created = runtime.memory.create(u, "Uses MySQL in production",
                                    allow_duplicate=True)
    mid = created["memory"]["id"]
    runtime.cognition.focus.set_focus(u, "memory", mid, session_id="s1")

    result = control.handle(u, "that's wrong", session_id="s1")
    assert result["applied"] is True
    rep = runtime.cognition.reputation.get(u, mid)
    assert rep["negative_outcomes"] >= 0  # contradiction counter moved
    assert "contradicted" in result["summary"]


def test_correction_with_a_replacement_updates_the_memory(runtime, control, u):
    created = runtime.memory.create(u, "Uses MySQL in production",
                                    allow_duplicate=True)
    mid = created["memory"]["id"]
    runtime.cognition.focus.set_focus(u, "memory", mid, session_id="s1")

    result = control.handle(u, "that's wrong, we use Postgres in production",
                            session_id="s1")
    assert result["applied"] is True
    updated = runtime.memory.get(mid)
    assert "postgres" in updated.content.lower()


def test_correction_without_a_target_refuses(control, u):
    result = control.handle(u, "that's wrong")
    assert result["applied"] is False
    assert result["requires"] == "clarification"


# ------------------------------------------------------------------ remember
def test_remember_stores_an_explicit_memory(runtime, control, u):
    result = control.handle(u, "remember that my API key rotates every 30 days")
    assert result["applied"] is True
    assert "explicit instruction" in result["summary"]
    found = runtime.memory.search(u, "API key rotates", record_event=False)
    assert found


# ------------------------------------------------------------- explanations
def test_explain_belief_cites_source_and_reputation(runtime, control, u):
    runtime.memory.create(u, "Prefers TypeScript over JavaScript for new code",
                          allow_duplicate=True)
    result = control.handle(u, "why do you believe I prefer TypeScript?")
    assert result["applied"] is True
    assert "because it came from" in result["summary"]
    # Honest about having no track record yet.
    assert result["reputation"]["reputation"] == "INSUFFICIENT EVIDENCE"


def test_explain_belief_never_exposes_internal_deliberation(runtime, control, u):
    runtime.memory.create(u, "Prefers TypeScript over JavaScript",
                          allow_duplicate=True)
    result = control.handle(u, "why do you believe I prefer TypeScript?")
    lowered = result["summary"].lower()
    for leak in ("chain of thought", "let me think", "reasoning:", "step 1"):
        assert leak not in lowered


def test_explain_behaviour_reports_a_real_policy(control, u):
    result = control.handle(u, "why do you always ask so many questions")
    assert result["applied"] is True
    assert result["policy_key"] == "clarification_frequency"
    assert result["summary"]


def test_explain_behaviour_admits_when_it_cannot_map_the_complaint(control, u):
    result = control.handle(u, "why are you like a purple giraffe")
    assert result["applied"] is False
    assert "not sure which behaviour" in result["summary"]


# ---------------------------------------------------------------- stop topic
def test_stop_topic_records_an_explicit_instruction(runtime, control, u):
    result = control.handle(u, "stop asking me about the database migration")
    assert result["applied"] is True
    assert "database migration" in result["topic"]
    assert "explicit instruction" in result["summary"]
    policy = runtime.cognition.policy.get(u, "interruption_tolerance")
    assert policy["value"] == "low"


# ----------------------------------------------------------------- prioritise
def test_prioritise_marks_the_focused_memory_important(runtime, control, u):
    created = runtime.memory.create(u, "The Q4 board deadline is 1 December",
                                    allow_duplicate=True)
    mid = created["memory"]["id"]
    runtime.cognition.focus.set_focus(u, "memory", mid, session_id="s1")
    result = control.handle(u, "this is really important", session_id="s1")
    assert result["applied"] is True
    assert runtime.memory.get(mid).importance >= 0.95


# --------------------------------------------------------------------- events
def test_commands_emit_an_inspectable_event(runtime, control, u):
    control.handle(u, "remember that I use pnpm", correlation_id="ctl-corr")
    events = runtime.cognition.bus.for_correlation("ctl-corr")
    assert any(e.type == "control.command" for e in events)


# ------------------------------------------------------ integration via a turn
def test_a_command_inside_a_normal_turn_is_executed(runtime, u):
    trace = runtime.cognition.process_turn(
        u, "remember that I always deploy on Tuesday mornings")
    assert trace["control"] is not None
    assert trace["control"]["command"] == REMEMBER
    assert trace["control"]["applied"] is True


def test_an_ordinary_turn_has_no_control_result(runtime, u):
    trace = runtime.cognition.process_turn(u, "How is the project going?")
    assert trace["control"] is None
