"""
V8.3.1.1 — conversational mission action reliability.

Real-model verification of V8.3.1 exposed a defect: after "Pause that mission",
the instruction "Resume it." failed conversationally and the assistant reported
that the current state was unknown. The mission subsystem was never at fault —
`MissionRegistry.set_state()` performed paused → active correctly when called
directly. The failure was in the conversational tool interface: a 3B model
recognises the verb "resume" but struggles to assemble the three arguments the
generic `update_mission_state` requires, and then narrates an unknown state.

The fix is tool design, not routing: one tool per intention, each resolving its
own object through conversational focus. These tests pin that behaviour down.
"""
from __future__ import annotations

import json
import uuid

import pytest
from langchain_core.messages import AIMessage

from app.agent.cognitive_tools import build_cognitive_tools


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cog(runtime):
    return runtime.cognition


@pytest.fixture
def user():
    return _u("v8311")


@pytest.fixture
def tools(cog, user):
    return {t.name: t for t in build_cognitive_tools(
        cog, user, thread_id="thread-act", correlation_id="corr-act")}


def _call(tools, name, **kwargs):
    return json.loads(tools[name].invoke(kwargs))


# ==================================================== Test 1 — direct resume
class TestDirectResume:

    def test_resume_paused_mission_by_id(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Ship the milestone")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        assert cog.missions.get(user, mid)["state"] == "paused"

        result = _call(tools, "resume_mission", mission_id=mid)

        assert result["status"] == "UPDATED"
        assert result["previous_state"] == "paused"
        assert result["state"] == "active"
        assert result["mission_id"] == mid, "the stable id must not change"
        assert cog.missions.get(user, mid)["state"] == "active"

    def test_resume_records_the_transition_in_history(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Recorded transition")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        _call(tools, "resume_mission", mission_id=mid)

        history = cog.missions.history(user, mid)
        transitions = [(h["previous_state"], h["new_state"]) for h in history]
        assert ("paused", "active") in transitions

    def test_resume_emits_mission_resumed_event(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Event emitting mission")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        _call(tools, "resume_mission", mission_id=mid)

        events = [e.type for e in cog.bus.recent(user, limit=80)]
        assert "mission.resumed" in events, (
            "the canonical registry must emit mission.resumed, not a generic "
            f"update; saw {events[:12]}")

    def test_resume_reason_is_recorded_as_user_requested(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Reasoned resume")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        _call(tools, "resume_mission", mission_id=mid)

        history = cog.missions.history(user, mid)
        resumed = next(h for h in history if h["new_state"] == "active"
                       and h["previous_state"] == "paused")
        assert "resume" in (resumed["reason"] or "").lower()
        assert resumed["evidence"], "the transition must carry evidence"


# ======================================== Test 2 — conversational focus resume
class TestFocusResume:

    def test_resume_with_no_mission_id_uses_focus(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Focused mission")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission")  # bare call, focus supplies the object

        assert cog.missions.get(user, mid)["state"] == "paused"

        result = _call(tools, "resume_mission")  # "Resume it."

        assert result["status"] == "UPDATED"
        assert result["mission_id"] == mid
        assert result["state"] == "active"

    def test_focus_is_refreshed_to_the_resumed_mission(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Refocus me")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission")
        _call(tools, "resume_mission")

        focused = [f for f in cog.focus.current(user, session_id="thread-act")
                   if f["subject_kind"] == "mission"]
        assert focused and focused[0]["subject_id"] == mid

    def test_resume_never_invents_a_next_step(self, tools, cog, user):
        created = _call(tools, "create_mission", title="No steps here")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission")
        result = _call(tools, "resume_mission")

        assert result["next_step_status"] == "NO_NEXT_STEP_RECORDED"
        assert result["next_step"] is None
        assert cog.missions.get(user, mid)["next_step"] is None

    def test_resume_preserves_a_real_next_step(self, tools):
        _call(tools, "create_mission", title="Has a step")
        _call(tools, "add_mission_step", summary="Draft the release notes")
        _call(tools, "pause_mission")
        result = _call(tools, "resume_mission")

        assert result["next_step_status"] == "RECORDED"
        assert "release notes" in result["next_step"]


# ============================== Test 3 — the exact natural-language scenario
class TestReportedScenario:
    """The five-turn conversation from the bug report, end to end."""

    def test_create_list_next_pause_resume(self, tools, cog, user):
        # 1. "Create a mission to finish the next MEMORY//OS milestone..."
        created = _call(tools, "create_mission",
                        title="Finish the next MEMORY//OS milestone by month end")
        mid = created["mission"]["mission_id"]

        # 2. "What missions am I currently working on?"
        listed = _call(tools, "list_missions")
        assert listed["count"] == 1
        assert listed["missions"][0]["mission_id"] == mid

        # 3. "What is the next step?" — none recorded, and none invented.
        detail = _call(tools, "get_mission")
        assert detail["mission"]["next_step_status"] == "NO_NEXT_STEP_RECORDED"

        # 4. "Pause that mission."
        paused = _call(tools, "pause_mission")
        assert paused["status"] == "UPDATED"
        assert paused["state"] == "paused"

        # 5. "Resume it."  <- this is what used to fail
        resumed = _call(tools, "resume_mission")
        assert resumed["status"] == "UPDATED"
        assert resumed["previous_state"] == "paused"
        assert resumed["state"] == "active"

        # Final truth: one mission, same id, active, still no invented step.
        missions = cog.missions.list(user)
        assert len(missions) == 1
        assert missions[0]["id"] == mid
        assert missions[0]["state"] == "active"
        assert missions[0]["next_step"] is None
        assert missions[0]["steps"] == []


# =================================================== Test 4 — terminal states
class TestTerminalMissions:

    @pytest.mark.parametrize("terminal", ["completed", "failed", "abandoned"])
    def test_resume_does_not_resurrect_a_terminal_mission(
            self, tools, cog, user, terminal):
        created = _call(tools, "create_mission", title=f"Ends as {terminal}")
        mid = created["mission"]["mission_id"]
        cog.missions.set_state(user, mid, terminal,
                               reason="Test fixture reached a final state")

        result = _call(tools, "resume_mission", mission_id=mid)

        assert result["status"] == "TERMINAL_STATE"
        assert result["state"] == terminal
        assert cog.missions.get(user, mid)["state"] == terminal, (
            "a terminal mission must not be silently reopened")

    def test_terminal_response_tells_the_truth(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Already finished")
        mid = created["mission"]["mission_id"]
        _call(tools, "complete_mission", mission_id=mid)
        result = _call(tools, "resume_mission", mission_id=mid)
        assert "final state" in result["detail"]
        assert "cannot be resumed" in result["detail"]

    def test_already_active_is_a_no_op_not_a_transition(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Already running")
        mid = created["mission"]["mission_id"]
        before = len(cog.missions.history(user, mid))

        result = _call(tools, "resume_mission", mission_id=mid)

        assert result["status"] == "NO_CHANGE"
        assert result["reason_code"] == "ALREADY_ACTIVE"
        assert result["state"] == "active"
        assert len(cog.missions.history(user, mid)) == before, (
            "a no-op must not manufacture a transition event")

    def test_already_paused_is_a_no_op(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Resting")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        before = len(cog.missions.history(user, mid))

        result = _call(tools, "pause_mission", mission_id=mid)

        assert result["status"] == "NO_CHANGE"
        assert result["reason_code"] == "ALREADY_PAUSED"
        assert len(cog.missions.history(user, mid)) == before

    def test_pause_rejects_an_invalid_source_state(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Completed work")
        mid = created["mission"]["mission_id"]
        _call(tools, "complete_mission", mission_id=mid)
        result = _call(tools, "pause_mission", mission_id=mid)
        assert result["status"] == "TERMINAL_STATE"
        assert cog.missions.get(user, mid)["state"] == "completed"


# ================================================== Test 5 — ambiguous focus
class TestAmbiguity:

    def test_resume_with_several_candidates_refuses_to_guess(
            self, tools, cog, user):
        first = _call(tools, "create_mission", title="Mission One")
        second = _call(tools, "create_mission", title="Mission Two")
        cog.missions.set_state(user, first["mission"]["mission_id"], "paused",
                               reason="test")
        cog.missions.set_state(user, second["mission"]["mission_id"], "paused",
                               reason="test")
        cog.focus.clear(user, session_id="thread-act")

        result = _call(tools, "resume_mission")

        assert result["status"] == "AMBIGUOUS_REFERENCE"
        assert "Mission One" in result["detail"]
        assert "Mission Two" in result["detail"]
        # Crucially: nothing was mutated.
        for mission in cog.missions.list(user):
            assert mission["state"] == "paused", (
                "an ambiguous reference must not change any mission")

    def test_resume_with_nothing_recorded_says_so(self, tools):
        result = _call(tools, "resume_mission")
        assert result["status"] == "NO_MISSIONS_RECORDED"

    def test_unknown_id_is_not_found_and_mutates_nothing(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Untouched")
        mid = created["mission"]["mission_id"]
        result = _call(tools, "resume_mission", mission_id="ms_nope")
        assert result["status"] == "NOT_FOUND"
        assert cog.missions.get(user, mid)["state"] == "active"


# ==================================================== Test 6 — tool tracing
class TestTracing:

    def test_resume_tool_is_traced_as_decision_and_result(self, runtime, user):
        """§15: a model-chosen action tool emits TOOL_DECISION and TOOL_RESULT."""
        cog = runtime.cognition
        mission = cog.missions.create(user, "Traced resume", state="active",
                                      source="test")
        cog.missions.set_state(user, mission["id"], "paused",
                               reason="Set up for the test")

        class Scripted:
            def __init__(self):
                self.calls = 0
                self.bound: set[str] = set()

            def bind_tools(self, tools):
                self.bound = {t.name for t in tools}
                return self

            def invoke(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return AIMessage(content="", tool_calls=[
                        {"name": "resume_mission", "args": {}, "id": "r1"}])
                return AIMessage(content="Resumed: it is active again.")

        model = Scripted()
        original = runtime.agent.provider.chat_model
        runtime.agent.provider.chat_model = lambda: model
        runtime.agent._graph = None
        try:
            result = runtime.agent.run(user, _u("thr"), "Resume it.")
        finally:
            runtime.agent.provider.chat_model = original
            runtime.agent._graph = None

        assert "resume_mission" in model.bound, (
            "the action tools must be bound on the real model path")

        decisions = [a for a in result["activity"]
                     if a.get("type") == "TOOL_DECISION"
                     and a.get("tool") == "resume_mission"]
        results = [a for a in result["activity"]
                   if a.get("type") == "TOOL_RESULT"
                   and a.get("tool") == "resume_mission"]
        assert decisions, f"missing TOOL_DECISION; saw {result['activity']}"
        assert results, f"missing TOOL_RESULT; saw {result['activity']}"

        # And the real state actually changed.
        assert cog.missions.get(user, mission["id"])["state"] == "active"

    def test_action_tools_reach_the_agent_toolset(self, runtime, user):
        names = {t.name for t in runtime.agent._tools_for(user, "t", [])}
        for expected in ("pause_mission", "resume_mission",
                         "complete_mission", "abandon_mission"):
            assert expected in names
        # The generic tool is preserved for blocked/waiting.
        assert "update_mission_state" in names


# ======================================== complete / abandon action semantics
class TestCompleteAndAbandon:

    def test_complete_moves_to_completed(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Finish me")
        mid = created["mission"]["mission_id"]
        result = _call(tools, "complete_mission")
        assert result["status"] == "UPDATED"
        assert result["state"] == "completed"
        assert cog.missions.get(user, mid)["completed_at"] is not None

    def test_abandon_moves_to_abandoned(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Drop me")
        mid = created["mission"]["mission_id"]
        result = _call(tools, "abandon_mission")
        assert result["status"] == "UPDATED"
        assert result["state"] == "abandoned"
        assert cog.missions.get(user, mid)["state"] == "abandoned"

    def test_generic_tool_still_handles_blocked(self, tools, cog, user):
        """The action tools must not have displaced update_mission_state."""
        created = _call(tools, "create_mission", title="Blocked work")
        mid = created["mission"]["mission_id"]
        result = _call(tools, "update_mission_state", state="blocked",
                       reason="Waiting on a vendor",
                       blocked_reason="Vendor has not replied")
        assert result["status"] == "UPDATED"
        assert cog.missions.get(user, mid)["state"] == "blocked"

    def test_resume_from_blocked_is_rejected_as_invalid(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Blocked not paused")
        mid = created["mission"]["mission_id"]
        _call(tools, "update_mission_state", state="blocked",
              reason="Blocked for the test")
        result = _call(tools, "resume_mission", mission_id=mid)
        assert result["status"] == "INVALID_TRANSITION"
        assert result["state"] == "blocked"
        assert cog.missions.get(user, mid)["state"] == "blocked"


# ==================================================== deterministic fallback
class TestFallbackActions:

    def test_fallback_resumes_through_the_real_registry(self, runtime, user):
        if runtime.agent.provider.name != "demo":
            pytest.skip("provider is not the deterministic fallback here")
        cog = runtime.cognition
        mission = cog.missions.create(user, "Fallback resume", state="active",
                                      source="test")
        cog.missions.set_state(user, mission["id"], "paused", reason="setup")

        runtime.agent._graph = None
        try:
            result = runtime.agent.run(user, _u("demo"), "Resume it.")
        finally:
            runtime.agent._graph = None

        assert result["provider"] == "demo"
        assert "DETERMINISTIC FALLBACK" in result["answer"]
        assert cog.missions.get(user, mission["id"])["state"] == "active"

    def test_fallback_runs_the_whole_reported_scenario(self, runtime):
        """The five-turn conversation, through the real agent, in DEMO mode."""
        if runtime.agent.provider.name != "demo":
            pytest.skip("provider is not the deterministic fallback here")
        cog = runtime.cognition
        user = _u("fbscenario")
        thread = _u("fbthread")

        def say(message):
            runtime.agent._graph = None
            try:
                return runtime.agent.run(user, thread, message)["answer"]
            finally:
                runtime.agent._graph = None

        created = say("Create a mission to finish the next MEMORY//OS "
                      "milestone by the end of this month.")
        assert "milestone" in created.lower()

        listed = say("What missions am I currently working on?")
        assert "milestone" in listed.lower()

        nxt = say("What is the next step?")
        assert "NO NEXT STEP RECORDED" in nxt

        paused = say("Pause that mission.")
        assert "paused" in paused.lower()

        resumed = say("Resume it.")
        assert "active" in resumed.lower()

        missions = cog.missions.list(user)
        assert len(missions) == 1
        assert missions[0]["state"] == "active"
        assert missions[0]["next_step"] is None

    def test_fallback_question_does_not_create_a_mission(self, runtime):
        """§6 holds in the fallback: a question is not a creation request."""
        if runtime.agent.provider.name != "demo":
            pytest.skip("provider is not the deterministic fallback here")
        cog = runtime.cognition
        user = _u("fbnocreate")
        runtime.agent._graph = None
        try:
            runtime.agent.run(user, _u("t"),
                              "What should I do about the mission deadline?")
        finally:
            runtime.agent._graph = None
        assert cog.missions.list(user) == []
