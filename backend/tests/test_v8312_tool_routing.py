"""
V8.3.1.2 — null-tolerant tool arguments and focus-driven action selection.

Two defects showed up on a real Ollama `llama3.2:3b` after V8.3.1.1 shipped.

1. `list_missions` failed with a Pydantic ValidationError because the model
   emitted `{"open_only": null}`. A model saying "I have no value for this
   optional argument" is reasonable; rejecting it surfaced as TOOL_FAILED.

2. For "Resume it." against a focused PAUSED mission, the model selected
   `get_mission` and `get_world_state` and never reached `resume_mission`, so
   the mission stayed paused. The transition itself was fine — the model simply
   could not see that a specific mission was already identified, so it spent
   its tool budget rediscovering an identity the system already held.

The fix for (2) is context, not routing: the focused object is now serialised
into the prompt with its id, its real state and the lifecycle actions valid
from that state. No substring matching, no command dispatch — the model still
chooses the tool.
"""
from __future__ import annotations

import json
import uuid

import pytest

from app.agent.cognitive_tools import build_cognitive_tools


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def cog(runtime):
    return runtime.cognition


@pytest.fixture
def user():
    return _u("v8312")


@pytest.fixture
def session():
    return _u("sess")


@pytest.fixture
def tools(cog, user, session):
    return {t.name: t for t in build_cognitive_tools(
        cog, user, thread_id=session, correlation_id="corr-8312")}


def _call(tools, name, **kwargs):
    """Invoke through the StructuredTool so the Pydantic schema really runs."""
    return json.loads(tools[name].invoke(kwargs))


# ============================================ A — null-tolerant tool arguments
class TestNullOpenOnly:
    """The model may legitimately send null for an optional argument."""

    def test_open_only_true_lists_only_open_missions(self, tools, cog, user):
        opened = _call(tools, "create_mission", title="Still open")
        done = _call(tools, "create_mission", title="Already finished")
        _call(tools, "complete_mission",
              mission_id=done["mission"]["mission_id"])

        result = _call(tools, "list_missions", open_only=True)

        titles = [m["title"] for m in result["missions"]]
        assert "Still open" in titles
        assert "Already finished" not in titles, (
            "open_only=True must exclude the completed mission")
        assert opened["mission"]["mission_id"]

    def test_open_only_false_includes_closed_missions(self, tools):
        _call(tools, "create_mission", title="An open one")
        done = _call(tools, "create_mission", title="A closed one")
        _call(tools, "complete_mission",
              mission_id=done["mission"]["mission_id"])

        result = _call(tools, "list_missions", open_only=False)

        titles = [m["title"] for m in result["missions"]]
        assert "A closed one" in titles, (
            "open_only=False must include terminal missions")

    def test_open_only_null_does_not_raise_and_uses_the_default(self, tools):
        """The exact real-model failure: {"open_only": null}."""
        _call(tools, "create_mission", title="Null argument mission")

        # Must not raise ValidationError.
        result = _call(tools, "list_missions", open_only=None)

        assert result["status"] == "OK"
        titles = [m["title"] for m in result["missions"]]
        assert "Null argument mission" in titles, (
            "null must behave as the documented default, not fail the tool")

    def test_null_matches_omitted_exactly(self, tools):
        _call(tools, "create_mission", title="Comparison mission")

        explicit_null = _call(tools, "list_missions", open_only=None)
        omitted = _call(tools, "list_missions")

        assert explicit_null == omitted, (
            "an explicit null must be identical to omitting the argument")

    def test_null_does_not_create_or_mutate_a_mission(self, tools, cog, user):
        _call(tools, "create_mission", title="Untouched by listing")
        before = [(m["id"], m["state"]) for m in cog.missions.list(user)]

        _call(tools, "list_missions", open_only=None)

        after = [(m["id"], m["state"]) for m in cog.missions.list(user)]
        assert before == after, "a read must never invent or change a mission"

    def test_null_mission_id_on_action_tools_is_tolerated(self, tools, cog,
                                                          user, session):
        """Action tools must also survive {"mission_id": null, "reason": null}."""
        created = _call(tools, "create_mission", title="Null id resume")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        cog.focus.set_focus(user, "mission", mid, label="Null id resume",
                            session_id=session)

        result = _call(tools, "resume_mission", mission_id=None, reason=None)

        assert result["status"] == "UPDATED"
        assert result["state"] == "active"


# ================================== B–E — focus-driven lifecycle tool behaviour
class TestFocusDrivenLifecycle:
    """Focus supplies the object so the model can act without an id."""

    def _focused(self, tools, cog, user, session, title, state):
        created = _call(tools, "create_mission", title=title)
        mid = created["mission"]["mission_id"]
        if state == "paused":
            _call(tools, "pause_mission", mission_id=mid)
        cog.focus.set_focus(user, "mission", mid, label=title,
                            session_id=session)
        assert cog.missions.get(user, mid)["state"] == state
        return mid

    def test_b_focused_paused_mission_resumes(self, tools, cog, user, session):
        mid = self._focused(tools, cog, user, session, "Resume me", "paused")

        result = _call(tools, "resume_mission")  # no arguments at all

        assert result["status"] == "UPDATED"
        assert result["previous_state"] == "paused"
        assert result["state"] == "active"
        assert result["mission_id"] == mid
        assert cog.missions.get(user, mid)["state"] == "active"

    def test_c_focused_active_mission_pauses(self, tools, cog, user, session):
        mid = self._focused(tools, cog, user, session, "Pause me", "active")

        result = _call(tools, "pause_mission")

        assert result["status"] == "UPDATED"
        assert result["previous_state"] == "active"
        assert result["state"] == "paused"
        assert cog.missions.get(user, mid)["state"] == "paused"

    def test_d_focused_active_mission_completes(self, tools, cog, user,
                                                session):
        mid = self._focused(tools, cog, user, session, "Complete me", "active")

        result = _call(tools, "complete_mission")

        assert result["status"] == "UPDATED"
        assert result["state"] == "completed"
        assert cog.missions.get(user, mid)["state"] == "completed"

    def test_e_focused_mission_abandons(self, tools, cog, user, session):
        mid = self._focused(tools, cog, user, session, "Abandon me", "active")

        result = _call(tools, "abandon_mission")

        assert result["status"] == "UPDATED"
        assert result["state"] == "abandoned"
        assert cog.missions.get(user, mid)["state"] == "abandoned"

    def test_g_focus_supplies_the_id_the_model_omitted(self, tools, cog, user,
                                                       session):
        """Two open missions: only focus can disambiguate, and it must."""
        other = _call(tools, "create_mission", title="Not the focused one")
        mid = self._focused(tools, cog, user, session, "The focused one",
                            "paused")

        result = _call(tools, "resume_mission")

        assert result["mission_id"] == mid
        assert result["title"] == "The focused one"
        # The unfocused mission must be exactly as it was created.
        untouched = cog.missions.get(user, other["mission"]["mission_id"])
        assert untouched["state"] == "active", (
            "resuming the focused mission must not touch any other mission")
        assert untouched["id"] != mid


# ============================== F — step completion is not mission completion
class TestStepIsNotMission:

    def test_f_complete_step_does_not_complete_the_mission(self, tools, cog,
                                                           user, session):
        created = _call(tools, "create_mission", title="Multi-step mission")
        mid = created["mission"]["mission_id"]
        cog.focus.set_focus(user, "mission", mid, label="Multi-step mission",
                            session_id=session)
        _call(tools, "add_mission_step", summary="First step", mission_id=mid)

        _call(tools, "complete_mission_step", step_summary="First step",
              mission_id=mid)

        state = cog.missions.get(user, mid)["state"]
        assert state != "completed", (
            "finishing one step must never complete the whole mission")

    def test_f_complete_mission_is_a_distinct_tool(self, tools):
        assert "complete_mission" in tools
        assert "complete_mission_step" in tools
        assert tools["complete_mission"] is not tools["complete_mission_step"]
        step_desc = tools["complete_mission_step"].description.lower()
        assert "not" in step_desc and "lifecycle" in step_desc, (
            "the step tool must tell the model it is not a lifecycle tool")


# ==================================== H — canonical events on every transition
class TestCanonicalEvents:

    def _events(self, cog, user, mission_id):
        rows = cog.bus.recent(user, limit=200)
        return [r.type for r in rows
                if (r.subject_id == mission_id
                    or mission_id in str(r.payload or ""))]

    def test_h_resume_emits_mission_resumed(self, tools, cog, user, session):
        created = _call(tools, "create_mission", title="Event on resume")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        cog.focus.set_focus(user, "mission", mid, label="Event on resume",
                            session_id=session)

        _call(tools, "resume_mission")

        assert "mission.resumed" in self._events(cog, user, mid)

    def test_h_pause_emits_mission_paused(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Event on pause")
        mid = created["mission"]["mission_id"]

        _call(tools, "pause_mission", mission_id=mid)

        assert "mission.paused" in self._events(cog, user, mid)

    def test_h_no_op_emits_no_transition_event(self, tools, cog, user):
        """An already-active mission must not manufacture a resumed event."""
        created = _call(tools, "create_mission", title="Already active")
        mid = created["mission"]["mission_id"]  # created active
        before = self._events(cog, user, mid).count("mission.resumed")

        result = _call(tools, "resume_mission", mission_id=mid)

        assert result["status"] == "NO_CHANGE"
        after = self._events(cog, user, mid).count("mission.resumed")
        assert after == before, "a no-op must not emit a transition event"


# ===================== focus serialisation — what makes direct action possible
class TestFocusInModelContext:
    """
    The real-model fix: the prompt must name the focused mission, its state and
    the actions valid from it, so the model never needs a read-before-write.
    """

    def _prompt(self, cog, user, session, message="Resume it."):
        return cog.context.build(user, message, thread_id=session).to_prompt()

    def test_focused_paused_mission_appears_in_the_prompt(self, tools, cog,
                                                          user, session):
        created = _call(tools, "create_mission", title="Visible in prompt")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        cog.focus.set_focus(user, "mission", mid, label="Visible in prompt",
                            session_id=session)

        prompt = self._prompt(cog, user, session)

        assert "FOCUSED MISSION" in prompt
        assert mid in prompt, "the id must be present so no lookup is needed"
        assert "state=paused" in prompt
        assert "Visible in prompt" in prompt

    def test_prompt_offers_resume_for_a_paused_mission(self, tools, cog, user,
                                                       session):
        created = _call(tools, "create_mission", title="Paused options")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        cog.focus.set_focus(user, "mission", mid, label="Paused options",
                            session_id=session)

        prompt = self._prompt(cog, user, session)

        assert "resume_mission" in prompt
        assert "pause_mission" not in prompt.split("valid lifecycle actions")[1
            ].split("\n")[0], "pausing an already-paused mission is not valid"

    def test_prompt_offers_pause_for_an_active_mission(self, tools, cog, user,
                                                       session):
        created = _call(tools, "create_mission", title="Active options")
        mid = created["mission"]["mission_id"]  # created active
        cog.focus.set_focus(user, "mission", mid, label="Active options",
                            session_id=session)

        line = [ln for ln in self._prompt(cog, user, session).splitlines()
                if "FOCUSED MISSION" in ln][0]

        assert "pause_mission" in line
        assert "resume_mission" not in line

    def test_prompt_tells_the_model_not_to_read_first(self, tools, cog, user,
                                                      session):
        created = _call(tools, "create_mission", title="No read first")
        mid = created["mission"]["mission_id"]
        _call(tools, "pause_mission", mission_id=mid)
        cog.focus.set_focus(user, "mission", mid, label="No read first",
                            session_id=session)

        prompt = self._prompt(cog, user, session)

        assert "get_mission" in prompt and "get_world_state" in prompt, (
            "the prompt must name the tools it is steering the model away from")

    def test_terminal_focused_mission_offers_no_lifecycle_action(
            self, tools, cog, user, session):
        created = _call(tools, "create_mission", title="Finished for good")
        mid = created["mission"]["mission_id"]
        _call(tools, "complete_mission", mission_id=mid)
        cog.focus.set_focus(user, "mission", mid, label="Finished for good",
                            session_id=session)

        line = [ln for ln in self._prompt(cog, user, session).splitlines()
                if "FOCUSED MISSION" in ln][0]

        assert "terminal" in line
        assert "resume_mission" not in line, (
            "a completed mission must never be offered for resumption")

    def test_no_focus_produces_no_focus_claim(self, cog, user, session):
        prompt = cog.context.build(user, "Resume it.",
                                   thread_id=session).to_prompt()

        assert "FOCUSED MISSION" not in prompt, (
            "with nothing in focus the prompt must not imply there is")

    def test_focus_on_a_deleted_mission_is_not_claimed(self, tools, cog, user,
                                                       session):
        """Focus must never advertise an object that cannot be acted on."""
        cog.focus.set_focus(user, "mission", "ms_doesnotexist",
                            label="Ghost mission", session_id=session)

        prompt = self._prompt(cog, user, session)

        assert "Ghost mission" not in prompt
        assert "ms_doesnotexist" not in prompt


# ======================================= the architecture constraint itself
class TestNoKeywordRouting:
    """The model must remain the component that selects the tool."""

    def test_graph_contains_no_natural_language_command_dispatch(self):
        import inspect
        from app.agent import graph as graph_module

        source = inspect.getsource(graph_module)
        # The demo planner is allowed to pattern-match; the real path is not.
        real_path = source.split("def _demo_turn")[0]
        for forbidden in ('"resume" in', "'resume' in",
                          '"pause" in', "'pause' in"):
            assert forbidden not in real_path, (
                f"keyword routing {forbidden!r} must not exist on the real "
                f"model path")
