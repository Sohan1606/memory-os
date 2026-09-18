"""
V8.3.1 — conversational cognition.

These tests assert the thing the release is actually about: that the cognitive
subsystems participate in ordinary conversation, and that the assistant cannot
invent state it does not have.

They deliberately test the tools through the REAL runtime (real MissionRegistry,
real WorldStateV2, real FocusTracker, real database), not mocks. The model is
scripted so the assertions are about our behaviour, not the LLM's mood; the
separate `test_v831_real_model.py` covers a genuine local model.
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
    return _u("v831")


@pytest.fixture
def tools(cog, user):
    return {t.name: t for t in build_cognitive_tools(
        cog, user, thread_id="thread-1", correlation_id="corr-1")}


def _call(tools, name, **kwargs):
    return json.loads(tools[name].invoke(kwargs))


# =============================================================== §18 scenario
class TestEndToEndScenario:
    """The required seven-step conversation, executed for real."""

    def test_full_seven_step_scenario(self, tools, cog, user):
        # 1. Create a mission by asking for it.
        created = _call(tools, "create_mission",
                        title="Launch the beta programme",
                        description="Get 20 users onto the beta")
        assert created["status"] == "CREATED"
        mission_id = created["mission"]["mission_id"]
        assert created["mission"]["state"] == "active"

        # 2. List missions — the real registry, not a memory search.
        listed = _call(tools, "list_missions")
        assert listed["status"] == "OK"
        assert mission_id in [m["mission_id"] for m in listed["missions"]]

        # 3. Next step: nothing is recorded, and that must be said, not invented.
        detail = _call(tools, "get_mission", mission_id=mission_id)
        assert detail["mission"]["next_step_status"] == "NO_NEXT_STEP_RECORDED"
        assert detail["mission"]["next_step"] is None
        assert "proposal" in detail["mission"]["next_step_guidance"].lower()

        # 4. "Pause that" — resolved through conversational focus, no id given.
        paused = _call(tools, "update_mission_state", state="paused",
                       reason="User asked to pause it")
        assert paused["status"] == "UPDATED"
        assert paused["mission"]["state"] == "paused"
        assert paused["mission"]["mission_id"] == mission_id

        # 5. "Resume it" — same resolution path.
        resumed = _call(tools, "update_mission_state", state="active",
                        reason="User asked to resume")
        assert resumed["mission"]["state"] == "active"

        # 6. "Why?" — evidence from the real mission record.
        why = _call(tools, "explain", question_kind="why")
        assert why["status"] == "OK"
        subject = why["subjects"][0]
        assert subject["kind"] == "mission"
        assert subject["id"] == mission_id

        # 7. "What changed?" — real recorded transitions with real reasons.
        changed = _call(tools, "explain", question_kind="what_changed")
        assert changed["status"] == "OK"
        history = changed["mission_history"]
        transitions = [(h["from"], h["to"]) for h in history]
        assert ("active", "paused") in transitions
        assert ("paused", "active") in transitions
        assert any("resume" in (h["reason"] or "").lower() for h in history)


# ================================================== §5 no invented next steps
class TestNextStepTruthfulness:

    def test_absent_next_step_is_reported_not_filled_in(self, tools):
        _call(tools, "create_mission", title="Write the compiler book")
        result = _call(tools, "get_mission")
        assert result["mission"]["next_step_status"] == "NO_NEXT_STEP_RECORDED"
        assert result["mission"]["next_step"] is None

    def test_recorded_next_step_is_marked_recorded(self, tools):
        _call(tools, "create_mission", title="Refactor the billing module")
        _call(tools, "add_mission_step", summary="Remove the legacy adapter")
        result = _call(tools, "get_mission")
        assert result["mission"]["next_step_status"] == "RECORDED"
        assert "legacy adapter" in result["mission"]["next_step"]

    def test_empty_registry_says_so(self, tools):
        assert _call(tools, "list_missions")["status"] == "NO_MISSIONS_RECORDED"

    def test_blockers_absent_is_explicit(self, tools):
        _call(tools, "create_mission", title="Tidy the garage")
        assert _call(tools, "get_mission_blockers")["status"] == \
            "NO_BLOCKERS_RECORDED"

    def test_real_blockers_are_reported(self, tools):
        _call(tools, "create_mission", title="Deploy to production")
        _call(tools, "update_mission_state", state="blocked",
              reason="Waiting on security review",
              blocked_reason="Security review has not come back")
        blockers = _call(tools, "get_mission_blockers")
        assert blockers["status"] == "OK"
        assert "Security review" in blockers["blocked"][0]["blocked_reason"]


# ====================================================== §8 object permanence
class TestObjectPermanence:

    def test_focus_persists_across_tool_calls(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Plan the offsite")
        mid = created["mission"]["mission_id"]
        focused = cog.focus.current(user, session_id="thread-1")
        assert any(f["subject_id"] == mid and f["subject_kind"] == "mission"
                   for f in focused)

    def test_bare_reference_with_several_open_missions_is_refused(
            self, tools, cog, user):
        _call(tools, "create_mission", title="Mission Alpha")
        _call(tools, "create_mission", title="Mission Beta")
        # Clear focus so no object is under discussion.
        cog.focus.clear(user, session_id="thread-1")
        result = _call(tools, "update_mission_state", state="paused",
                       reason="pause that")
        assert result["status"] == "AMBIGUOUS_REFERENCE"
        assert "Alpha" in result["detail"] and "Beta" in result["detail"]

    def test_single_open_mission_resolves_without_ambiguity(
            self, tools, cog, user):
        _call(tools, "create_mission", title="The only mission")
        cog.focus.clear(user, session_id="thread-1")
        result = _call(tools, "update_mission_state", state="paused",
                       reason="pause it")
        assert result["status"] == "UPDATED"

    def test_mission_is_a_focusable_kind(self, cog, user):
        # Regression: 'mission' was not in FocusTracker.KINDS, so conversational
        # focus silently failed and 'pause that' could never resolve.
        record = cog.focus.set_focus(user, "mission", "ms_test",
                                     label="Test", session_id="s1")
        assert record["subject_kind"] == "mission"

    def test_that_mission_phrase_resolves(self, cog, user):
        cog.focus.set_focus(user, "mission", "ms_xyz", label="Ship it",
                            session_id="s2")
        resolved = cog.focus.resolve(user, "pause that mission", session_id="s2")
        assert resolved["resolved"] is True
        assert resolved["subject_id"] == "ms_xyz"

    def test_unknown_mission_id_is_not_found(self, tools):
        result = _call(tools, "get_mission", mission_id="ms_does_not_exist")
        assert result["status"] == "NOT_FOUND"


# ============================================== §6 no accidental mission creation
class TestMissionCreationDiscipline:

    def test_creation_is_explicit_and_recorded(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Learn Portuguese")
        assert created["status"] == "CREATED"
        assert cog.missions.get(user, created["mission"]["mission_id"]) is not None

    def test_rubbish_title_is_rejected_not_invented(self, tools):
        assert _call(tools, "create_mission", title="x")["status"] == "INVALID"


# ======================================================= §7 natural updates
class TestMissionUpdates:

    def test_completion_flows_through_real_registry(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Submit the tax return")
        mid = created["mission"]["mission_id"]
        done = _call(tools, "update_mission_state", state="completed",
                     reason="User said it is finished")
        assert done["mission"]["state"] == "completed"
        assert cog.missions.get(user, mid)["state"] == "completed"

    def test_partial_progress_updates_progress(self, tools):
        _call(tools, "create_mission", title="Renovate the kitchen")
        _call(tools, "add_mission_step", summary="Choose the worktop")
        _call(tools, "add_mission_step", summary="Book the fitter")
        _call(tools, "complete_mission_step", step_summary="worktop")
        result = _call(tools, "get_mission")
        assert result["mission"]["progress"] == pytest.approx(0.5)
        assert "Choose the worktop" in result["mission"]["steps_done"]

    def test_invalid_state_is_rejected(self, tools):
        _call(tools, "create_mission", title="Something trackable")
        assert _call(tools, "update_mission_state", state="banana",
                     reason="nonsense")["status"] == "INVALID_STATE"

    def test_state_change_records_a_reason(self, tools, cog, user):
        created = _call(tools, "create_mission", title="Fix the roof")
        mid = created["mission"]["mission_id"]
        _call(tools, "update_mission_state", state="paused",
              reason="Waiting for dry weather")
        history = cog.missions.history(user, mid)
        assert any("dry weather" in (h["reason"] or "") for h in history)


# ====================================== §19/§20 right subsystem for the question
class TestSubsystemRouting:

    def test_projects_come_from_world_state(self, tools, cog, user):
        cog.world.upsert(user, "project", "Apollo rewrite", source="test")
        result = _call(tools, "get_world_state", kind="project")
        assert result["status"] == "OK"
        assert "Apollo rewrite" in [e["label"] for e in result["entities"]]

    def test_goals_and_missions_are_distinct_objects(self, tools, cog, user):
        cog.world.upsert(user, "goal", "Run a marathon", source="test")
        _call(tools, "create_mission", title="Ship the mobile app")
        focus = _call(tools, "get_current_focus")
        assert "Run a marathon" in focus["goals"]
        assert "Ship the mobile app" in [m["title"] for m in focus["missions"]]
        # A goal must never be reported as a mission.
        assert "Run a marathon" not in [m["title"] for m in focus["missions"]]

    def test_current_focus_with_nothing_recorded_is_honest(self, tools):
        assert _call(tools, "get_current_focus")["status"] == "NOTHING_RECORDED"

    def test_empty_world_is_not_invented(self, tools):
        assert _call(tools, "get_world_state")["status"] == \
            "NO_WORLD_STATE_RECORDED"


# ============================================================ §9 explanation
class TestExplanation:

    def test_what_changed_uses_real_world_changes(self, tools, cog, user):
        entity = cog.world_v2.upsert(user, "project", "Payments platform",
                                     source="test")
        cog.world_v2.set_state(user, entity["id"], "at_risk",
                               reason="Vendor slipped the date")
        result = _call(tools, "explain", question_kind="what_changed")
        assert result["status"] == "OK"
        changes = result["world_changes"]
        assert any(c["to"] == "at_risk" for c in changes)

    def test_explanation_without_a_subject_asks_rather_than_guesses(
            self, tools, cog, user):
        cog.focus.clear(user, session_id="thread-1")
        result = _call(tools, "explain", question_kind="why")
        assert result["status"] == "NO_SUBJECT_IN_FOCUS"

    def test_nothing_changed_is_stated(self, tools, cog, user):
        cog.focus.clear(user, session_id="thread-1")
        result = _call(tools, "explain", question_kind="what_changed")
        assert result["status"] == "NO_CHANGES_RECORDED"


# ================================================= §10 epistemic labelling
class TestEpistemicLabelling:

    def test_missions_are_labelled_recorded(self, tools):
        _call(tools, "create_mission", title="Build the greenhouse")
        result = _call(tools, "list_missions")
        assert result["missions"][0]["epistemic_status"] == "RECORDED"

    def test_simulation_is_labelled_simulated_and_mutates_nothing(
            self, tools, cog, user):
        _call(tools, "create_mission", title="Migrate to Postgres")
        before = cog.missions.list(user)
        result = _call(tools, "simulate_scenario",
                       question="What if I delay the migration by a month?")
        assert result["epistemic_status"] == "SIMULATED"
        assert "SIMULATED" in result["note"]
        after = cog.missions.list(user)
        assert [m["state"] for m in before] == [m["state"] for m in after]

    def test_predictions_are_labelled_predicted(self, tools, cog, user):
        cog.predictions.create(user, "The vendor will slip again", 0.6,
                               evidence=["They slipped twice"])
        result = _call(tools, "get_predictions")
        assert result["predictions"][0]["epistemic_status"] == "PREDICTED"
        assert "not observed facts" in result["note"]

    def test_history_beyond_coverage_is_refused(self, tools):
        result = _call(tools, "get_historical_state", when="1999-01-01T00:00:00Z")
        assert result["status"] == "HISTORY_NOT_AVAILABLE"

    def test_invalid_timestamp_is_refused(self, tools):
        result = _call(tools, "get_historical_state", when="last tuesday-ish")
        assert result["status"] == "HISTORY_NOT_AVAILABLE"


# ===================================================== §13/§14 attention
class TestAttentionFeedback:

    def test_no_background_findings_is_stated_not_manufactured(self, tools):
        result = _call(tools, "get_attention_state")
        assert result["status"] == "OK"
        assert result["background_findings"] is None
        assert "No background finding" in result["background_note"]

    def test_direct_query_reveals_suppressed_topics(self, tools, cog, user):
        # §14: background policy may stay quiet, but a direct question must not
        # be starved of information the system actually holds.
        cog.attention_v2.evaluate(
            user, "Certificate expiry", importance=0.2, urgency=0.1,
            confidence=0.9)
        result = _call(tools, "get_attention_state")
        assert result["status"] == "OK"


# ======================================== §4 mission-first context assembly
class TestMissionFirstContext:

    def test_missions_appear_in_the_context_bundle(self, cog, user):
        cog.missions.create(user, "Publish the research paper", state="active",
                            source="test")
        bundle = cog.context.build(user, "what am I working on?")
        mission_items = bundle.sections.get("mission") or []
        assert mission_items, "missions must be first-class context"
        assert "Publish the research paper" in mission_items[0].content

    def test_context_prompt_labels_missing_next_step(self, cog, user):
        cog.missions.create(user, "Organise the conference", state="active",
                            source="test")
        prompt = cog.context.build(user, "what is next?").to_prompt()
        assert "NO NEXT STEP RECORDED" in prompt

    def test_context_prompt_shows_recorded_next_step(self, cog, user):
        mission = cog.missions.create(user, "Rewrite the parser",
                                      state="active", source="test")
        cog.missions.add_step(user, mission["id"], "Benchmark the tokenizer")
        prompt = cog.context.build(user, "what is next?").to_prompt()
        assert "next step (RECORDED): Benchmark the tokenizer" in prompt

    def test_mission_context_survives_an_unrelated_message(self, cog, user):
        cog.missions.create(user, "Finish the thesis", state="active",
                            source="test")
        bundle = cog.context.build(user, "what is the weather like?")
        assert bundle.sections.get("mission"), (
            "an active mission is context even on an unrelated turn")

    def test_no_missions_means_no_mission_section(self, cog, user):
        bundle = cog.context.build(user, "anything?")
        assert not (bundle.sections.get("mission") or [])


# ====================================== §2/§15 tools reach the agent + tracing
class TestAgentIntegration:

    def test_cognitive_tools_are_bound_to_the_agent(self, runtime, user):
        sink: list[dict] = []
        names = {t.name for t in
                 runtime.agent._tools_for(user, "thread-x", sink)}
        assert "list_missions" in names
        assert "get_world_state" in names
        assert "explain" in names
        # Memory tools must survive.
        assert "search_memory" in names

    def test_agent_without_cognition_keeps_memory_tools(self, runtime, user):
        original = runtime.agent.cognition
        runtime.agent.cognition = None
        try:
            names = {t.name for t in
                     runtime.agent._tools_for(user, "thread-y", [])}
            assert "search_memory" in names
            assert "list_missions" not in names
        finally:
            runtime.agent.cognition = original

    def test_model_driven_mission_call_is_traced(self, runtime, user):
        """A tool the MODEL chose produces real trace stages (§15)."""
        runtime.cognition.missions.create(user, "Traced mission",
                                          state="active", source="test")

        class Scripted:
            def __init__(self):
                self.calls = 0

            def bind_tools(self, tools):
                self.bound = {t.name for t in tools}
                return self

            def invoke(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return AIMessage(content="", tool_calls=[
                        {"name": "list_missions", "args": {}, "id": "c1"}])
                return AIMessage(content="You have one mission: Traced mission.")

        model = Scripted()
        original = runtime.agent.provider.chat_model
        runtime.agent.provider.chat_model = lambda: model
        runtime.agent._graph = None
        try:
            result = runtime.agent.run(user, _u("thr"), "what missions do I have?")
        finally:
            runtime.agent.provider.chat_model = original
            runtime.agent._graph = None

        assert "list_missions" in model.bound
        kinds = [a.get("type") for a in result["activity"]]
        assert "LIST_MISSIONS" in kinds, (
            f"the real mission tool must have executed; saw {kinds}")
        assert "Traced mission" in result["answer"]


# ============================================================ §24 persistence
class TestPersistence:

    def test_missions_survive_a_restart(self, tmp_path):
        from app.config import Settings
        from app.runtime import Runtime

        cfg = Settings(data_dir=tmp_path, sqlite_path=tmp_path / "m.db",
                       checkpoint_path=tmp_path / "c.db",
                       chroma_path=tmp_path / "chroma")
        first = Runtime(cfg)
        uid = "restart_user"
        created = first.cognition.missions.create(
            uid, "Survive the restart", state="active", source="test")
        first.cognition.missions.add_step(uid, created["id"], "Step one")
        first.close()

        second = Runtime(cfg)
        try:
            tools = {t.name: t for t in build_cognitive_tools(
                second.cognition, uid, thread_id="t")}
            result = json.loads(tools["get_mission"].invoke(
                {"mission_id": created["id"]}))
            assert result["status"] == "OK"
            assert result["mission"]["title"] == "Survive the restart"
            assert result["mission"]["next_step_status"] == "RECORDED"
        finally:
            second.close()


# ============================================ §16 deterministic fallback
class TestDeterministicFallback:
    """The fallback understands cognitive objects but never impersonates the
    real agent."""

    def _demo_run(self, runtime, user, message, thread=None):
        runtime.agent._graph = None
        try:
            return runtime.agent.run(user, thread or _u("demo"), message)
        finally:
            runtime.agent._graph = None

    def test_fallback_reads_real_missions(self, runtime, cog, user):
        if runtime.agent.provider.name != "demo":
            pytest.skip("provider is not the deterministic fallback here")
        cog.missions.create(user, "Fallback mission", state="active",
                            source="test")
        result = self._demo_run(runtime, user, "What missions am I working on?")
        assert result["provider"] == "demo"
        assert "Fallback mission" in result["answer"]
        assert "DETERMINISTIC FALLBACK" in result["answer"]

    def test_fallback_does_not_invent_a_next_step(self, runtime, cog, user):
        if runtime.agent.provider.name != "demo":
            pytest.skip("provider is not the deterministic fallback here")
        cog.missions.create(user, "Stepless mission", state="active",
                            source="test")
        result = self._demo_run(runtime, user, "What missions do I have?")
        assert "NO NEXT STEP RECORDED" in result["answer"]

    def test_fallback_keeps_goals_separate_from_missions(
            self, runtime, cog, user):
        if runtime.agent.provider.name != "demo":
            pytest.skip("provider is not the deterministic fallback here")
        cog.missions.create(user, "Real mission", state="active", source="test")
        cog.world.upsert(user, "goal", "A mere goal", source="test")
        result = self._demo_run(runtime, user, "What am I working on?")
        assert "not a mission" in result["answer"]

    def test_fallback_reports_emptiness_honestly(self, runtime, user):
        if runtime.agent.provider.name != "demo":
            pytest.skip("provider is not the deterministic fallback here")
        result = self._demo_run(runtime, _u("empty"),
                                "What missions am I working on?")
        assert "no missions are recorded" in result["answer"].lower()


# ================================================ §21 V8.2 behaviour preserved
class TestNoRegressionInPriorBehaviour:

    def test_preference_correction_still_works(self, runtime, cog, user):
        """V8.2 §21: a corrected preference supersedes, history is kept."""
        service = runtime.memory
        first = service.create(user, "I use VS Code as my editor",
                               category="PREFERENCE", source="test")
        service.update(first["memory"]["id"],
                       "I use Cursor as my editor",
                       reason="User switched editors")
        results = service.search(user, "which editor do I use", top_k=5)
        top = results[0].memory.content
        assert "Cursor" in top
        versions = service.versions(first["memory"]["id"])
        assert any("VS Code" in v["content"] for v in versions), (
            "history must be retained, not overwritten")

    def test_memory_tools_are_unchanged(self, runtime, user):
        names = {t.name for t in runtime.agent._tools_for(user, "t", [])}
        for expected in ("search_memory", "save_memory", "update_memory",
                         "delete_memory", "consolidate_memory"):
            assert expected in names
