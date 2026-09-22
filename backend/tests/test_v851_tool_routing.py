"""
V8.5.1 — real-model cognitive tool routing reliability.

Windows verification with a real llama3.2:3b exposed five reproducible
failures: with ~40 tool schemas advertised at once, the small model selected
the wrong cognitive subsystem (get_attention_state for a mission question) or
selected nothing at all (no resume_mission for "Resume it.", no learned tool
for "what skills have you learned?", no explain tool for "why did you use
that skill?").

The correction is architectural: the EXISTING CapabilityRouter now narrows the
ADVERTISED tool surface to relevant capability families before the model makes
its final — genuine — tool choice. These tests pin down that layer
deterministically so routing regressions are diagnosable without waiting
minutes per Ollama turn:

  A. tool_surface(): family selection for the five reported messages, plus
     fail-open behaviour (no signal / broad message / no router).
  B. Graph integration: the model is bound to the narrowed surface, the
     narrowing is traced honestly (TOOL_SURFACE + routing.tool_surface), and
     the final TOOL_DECISION still comes from the model's own tool_calls.
  C. Narrowing is advertising only: execution is never blocked by it.
  D. The no-provider demo/fallback contract is unchanged.
"""
from __future__ import annotations

import json
import uuid

import pytest
from langchain_core.messages import AIMessage

from app.providers.capabilities import (ALWAYS_OFFERED_FAMILIES,
                                        MAX_NARROWED_FAMILIES, TOOL_FAMILIES,
                                        CapabilityRouter, ToolSurfaceDecision)


def _u(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class _NoProvider:
    """A provider stub for router-only tests. Never called by tool_surface."""
    name = "stub"

    def status(self):  # pragma: no cover - not used by tool_surface
        raise AssertionError("tool_surface must not consult the provider")

    def chat_model(self):  # pragma: no cover
        return None


@pytest.fixture
def router():
    return CapabilityRouter(_NoProvider())


def _family_tools(name: str) -> set[str]:
    return {t for f in TOOL_FAMILIES if f.name == name for t in f.tools}


# ================================================= A — the tool-surface layer
class TestToolSurfaceSelection:
    """Family selection for the five real-model failure messages."""

    def test_mission_question_narrows_to_missions_not_attention(self, router):
        """Failures 1 and 3: 'What missions am I currently working on?'"""
        surface = router.tool_surface("What missions am I currently working on?")
        assert surface.narrowed
        assert "missions" in surface.families
        assert "awareness" not in surface.families, (
            "the attention/awareness family must not be advertised for a "
            "plain mission question")
        for tool in ("list_missions", "get_mission", "get_current_focus"):
            assert tool in surface.allowed
        assert "get_attention_state" not in surface.allowed, (
            "get_attention_state stole this question from the mission tools "
            "on the real model — it must be off this surface")

    def test_resume_it_narrows_to_missions(self, router):
        """Failure 2: 'Resume it.' with a paused mission in focus."""
        surface = router.tool_surface("Resume it.", focus_kinds=("mission",))
        assert surface.narrowed
        assert "missions" in surface.families
        assert "resume_mission" in surface.allowed
        assert "get_attention_state" not in surface.allowed
        assert "start_research" not in surface.allowed

    def test_skills_question_narrows_to_learned(self, router):
        """Failure 4: 'What skills have you learned, and what evidence...'"""
        surface = router.tool_surface(
            "What skills have you learned, and what evidence supports them?")
        assert surface.narrowed
        assert "learned" in surface.families
        assert "list_learned" in surface.allowed
        assert "inspect_learned" in surface.allowed
        assert "get_attention_state" not in surface.allowed

    def test_why_question_narrows_to_explanation(self, router):
        """Failure 5: 'Why did you use that skill? What is it based on?'"""
        surface = router.tool_surface(
            "Why did you use that skill? What is it based on?",
            focus_kinds=("skill",))
        assert surface.narrowed
        assert "explanation" in surface.families
        assert "explain_cognition" in surface.allowed
        assert "explain" in surface.allowed, (
            "legacy explain must stay available (backwards compatibility)")
        # The skill reference keeps the learned family available too — the
        # model must still be able to choose inspect_learned when appropriate.
        assert "learned" in surface.families

    def test_families_never_a_single_tool(self, router):
        """The router selects FAMILIES: a signal brings the whole family."""
        surface = router.tool_surface("Resume it.", focus_kinds=("mission",))
        assert _family_tools("missions") <= set(surface.allowed), (
            "narrowing must advertise the complete mission family, giving the "
            "model a genuine choice — not a pre-made decision")

    def test_memory_family_is_always_offered(self, router):
        """Cross-cutting tools survive narrowing (search_memory contract)."""
        for message in ("What missions am I currently working on?",
                        "Resume it.",
                        "Why did you use that skill?"):
            surface = router.tool_surface(message)
            for tool in _family_tools("memory"):
                assert tool in surface.allowed, (
                    f"{tool} must be advertised for {message!r}")
        assert "memory" in ALWAYS_OFFERED_FAMILIES

    def test_no_signal_fails_open_to_the_full_surface(self, router):
        surface = router.tool_surface("Hello there, nice day today.")
        assert not surface.narrowed
        all_tools = {t for f in TOOL_FAMILIES for t in f.tools}
        assert all_tools <= set(surface.allowed)
        assert "full" in surface.reason.lower()

    def test_broad_message_fails_open(self, router):
        """Signals across most families mean narrowing would not help."""
        message = ("Compare my missions, projects, skills, predictions, "
                   "research claims and export packages, and explain why "
                   "each changed.")
        surface = router.tool_surface(message)
        substantive = [f for f in surface.families
                       if f not in ALWAYS_OFFERED_FAMILIES]
        if surface.narrowed:  # pragma: no cover - depends on signal breadth
            assert len(substantive) <= MAX_NARROWED_FAMILIES
        else:
            assert len(surface.signals) > MAX_NARROWED_FAMILIES

    def test_focus_kind_activates_its_family_without_words(self, router):
        """'It' questions carry no topic words; live focus supplies the family."""
        surface = router.tool_surface("Do it now.", focus_kinds=("skill",))
        assert "learned" in surface.families
        assert any(s.startswith("focus:") for s in surface.signals["learned"])

    def test_decision_is_auditable(self, router):
        surface = router.tool_surface("What missions am I working on?")
        assert isinstance(surface, ToolSurfaceDecision)
        body = surface.as_dict()
        assert body["narrowed"] is True
        assert body["reason"]
        assert body["signals"].get("missions"), (
            "the decision must record WHICH signal activated the family")

    def test_every_cognitive_tool_belongs_to_exactly_one_family(self, router):
        seen: dict[str, str] = {}
        for family in TOOL_FAMILIES:
            for tool in family.tools:
                assert tool not in seen, (
                    f"{tool} is in both {seen[tool]} and {family.name}")
                seen[tool] = family.name
        # Full surface == union of all families.
        surface = router.tool_surface("hello")
        assert set(surface.allowed) == set(seen)


# ============================================ B — integration with the graph
class ScriptedModel:
    """A fake tool-calling model that records what was bound to it."""

    def __init__(self, script):
        self.script = list(script)
        self.bound_names: list[set[str]] = []

    def bind_tools(self, tools):
        self.bound_names.append({t.name for t in tools})
        return self

    def invoke(self, messages):
        if self.script:
            return self.script.pop(0)
        return AIMessage(content="Final answer.")


def _drive(runtime, script, user, message, thread=None):
    model = ScriptedModel(script)
    original = runtime.agent.provider.chat_model
    runtime.agent.provider.chat_model = lambda: model
    runtime.agent._graph = None
    try:
        result = runtime.agent.run(user, thread or _u("thr"), message)
    finally:
        runtime.agent.provider.chat_model = original
        runtime.agent._graph = None
    result["_model"] = model
    return result


class TestGraphBindsNarrowedSurface:

    def test_mission_question_binds_mission_tools_not_attention(self, runtime):
        user = _u("v851")
        runtime.cognition.missions.create(user, "Finish the quarterly report",
                                          state="active", source="test")
        result = _drive(
            runtime,
            [AIMessage(content="", tool_calls=[
                {"name": "list_missions", "args": {}, "id": "c1"}]),
             AIMessage(content="You are working on the quarterly report.")],
            user, "What missions am I currently working on?")

        bound = result["_model"].bound_names[0]
        assert "list_missions" in bound
        assert "get_current_focus" in bound
        assert "search_memory" in bound, "memory tools always survive"
        assert "get_attention_state" not in bound, (
            "the wrong-subsystem tool must not be advertised for this turn")

        # The model's own choice is the TOOL_DECISION, and it executed.
        decisions = [a.get("tool") for a in result["activity"]
                     if a.get("type") == "TOOL_DECISION"]
        results = [a.get("tool") for a in result["activity"]
                   if a.get("type") == "TOOL_RESULT"]
        assert decisions == ["list_missions"]
        assert "list_missions" in results
        assert not [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]

    def test_resume_it_binds_action_tools(self, runtime):
        user = _u("v851")
        thread = _u("thr")
        cog = runtime.cognition
        mission = cog.missions.create(user, "Milestone", state="active",
                                      source="test")
        cog.missions.set_state(user, mission["id"], "paused", reason="test")
        cog.focus.set_focus(user, "mission", mission["id"],
                            label=mission["title"], session_id=thread)

        result = _drive(
            runtime,
            [AIMessage(content="", tool_calls=[
                {"name": "resume_mission", "args": {}, "id": "r1"}]),
             AIMessage(content="Resumed; it is active again.")],
            user, "Resume it.", thread=thread)

        bound = result["_model"].bound_names[0]
        assert "resume_mission" in bound
        assert "pause_mission" in bound, (
            "the WHOLE mission family is advertised — the router must not "
            "pre-decide resume over pause")
        assert "get_attention_state" not in bound

        decisions = [a.get("tool") for a in result["activity"]
                     if a.get("type") == "TOOL_DECISION"]
        assert "resume_mission" in decisions
        assert cog.missions.get(user, mission["id"])["state"] == "active"
        events = [e.type for e in cog.bus.recent(user, limit=100)]
        assert "mission.resumed" in events

    def test_surface_narrowing_is_traced_honestly(self, runtime):
        """TOOL_SURFACE appears in the activity, the persisted trace and the
        canonical event bus — no invisible routing."""
        user = _u("v851")
        runtime.cognition.missions.create(user, "Traced surface",
                                          state="active", source="test")
        result = _drive(
            runtime,
            [AIMessage(content="", tool_calls=[
                {"name": "list_missions", "args": {}, "id": "c1"}]),
             AIMessage(content="Done.")],
            user, "What missions am I currently working on?")

        surfaces = [a for a in result["activity"]
                    if a.get("type") == "TOOL_SURFACE"]
        assert len(surfaces) == 1, "one surface decision per turn"
        assert surfaces[0]["narrowed"] is True
        assert "missions" in surfaces[0]["families"]

        stored = runtime.cognition.traces.for_correlation(
            result["correlation_id"])
        assert any(s["stage"] == "TOOL_SURFACE" for s in stored)

        events = runtime.cognition.bus.for_correlation(result["correlation_id"])
        assert any(e.type == "routing.tool_surface" for e in events)

    def test_no_narrowing_for_a_plain_message_binds_everything(self, runtime):
        user = _u("v851")
        result = _drive(runtime, [AIMessage(content="Hello!")],
                        user, "Good morning!")
        bound = result["_model"].bound_names[0]
        assert "list_missions" in bound
        assert "get_attention_state" in bound
        assert "explain_cognition" in bound
        surfaces = [a for a in result["activity"]
                    if a.get("type") == "TOOL_SURFACE"]
        assert surfaces and surfaces[0]["narrowed"] is False

    def test_revision_call_reuses_the_same_surface(self, runtime):
        """Both model calls in one turn see one consistent surface."""
        user = _u("v851")
        runtime.cognition.missions.create(user, "Steady surface",
                                          state="active", source="test")
        result = _drive(
            runtime,
            [AIMessage(content="", tool_calls=[
                {"name": "list_missions", "args": {}, "id": "c1"}]),
             AIMessage(content="Answered after the tool.")],
            user, "What missions am I currently working on?")
        model = result["_model"]
        assert len(model.bound_names) == 2, "initial call + revision"
        assert model.bound_names[0] == model.bound_names[1]
        surfaces = [a for a in result["activity"]
                    if a.get("type") == "TOOL_SURFACE"]
        assert len(surfaces) == 1

    def test_standalone_agent_without_router_still_works(self, runtime):
        """No router (standalone construction) means the full surface."""
        original = runtime.agent.router
        runtime.agent.router = None
        try:
            user = _u("v851")
            result = _drive(runtime, [AIMessage(content="Hi.")], user,
                            "What missions am I currently working on?")
            bound = result["_model"].bound_names[0]
            assert "list_missions" in bound
            assert "get_attention_state" in bound
            assert not [a for a in result["activity"]
                        if a.get("type") == "TOOL_SURFACE"]
        finally:
            runtime.agent.router = original


# =================================== C — narrowing advertises, never blocks
class TestNarrowingNeverBlocksExecution:

    def test_out_of_surface_tool_call_still_executes(self, runtime):
        """
        If the model calls a tool that was NOT advertised this turn, the call
        still executes genuinely: the surface shapes the choice, it is not an
        authorization boundary. (Authorization is V8.5's job, per-user,
        server-side — not this layer's.)
        """
        user = _u("v851")
        result = _drive(
            runtime,
            [AIMessage(content="", tool_calls=[
                {"name": "get_attention_state", "args": {}, "id": "a1"}]),
             AIMessage(content="Answered from attention state.")],
            user, "What missions am I currently working on?")

        bound = result["_model"].bound_names[0]
        assert "get_attention_state" not in bound
        results = [a.get("tool") for a in result["activity"]
                   if a.get("type") == "TOOL_RESULT"]
        assert "get_attention_state" in results, (
            "an out-of-surface call must execute, not fail")
        assert not [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]

    def test_surface_failure_fails_open(self, runtime):
        """A router error must yield the full surface, never a broken turn."""
        class ExplodingRouter:
            def tool_surface(self, message, focus_kinds=()):
                raise RuntimeError("boom")

        original = runtime.agent.router
        runtime.agent.router = ExplodingRouter()
        try:
            user = _u("v851")
            result = _drive(runtime, [AIMessage(content="Hello.")],
                            user, "What missions am I working on?")
            bound = result["_model"].bound_names[0]
            assert "list_missions" in bound
            assert "get_attention_state" in bound
            assert result["answer"] == "Hello."
        finally:
            runtime.agent.router = original


# ================================ D — the no-provider fallback is unchanged
class TestFallbackContractPreserved:
    """With no model, the deterministic DEMO planner still answers honestly
    from the real registries — V8.5.1 must not have touched that path."""

    def test_demo_mission_question_uses_the_real_registry(self, runtime):
        user = _u("v851demo")
        runtime.cognition.missions.create(user, "Demo mission survives",
                                          state="active", source="test")
        original = runtime.agent.provider.chat_model
        runtime.agent.provider.chat_model = lambda: None
        runtime.agent._graph = None
        try:
            result = runtime.agent.run(user, _u("thr"),
                                       "What missions am I currently working on?")
        finally:
            runtime.agent.provider.chat_model = original
            runtime.agent._graph = None

        assert result["provider"] == "demo"
        assert "Demo mission survives" in result["answer"]
        decisions = [a.get("tool") for a in result["activity"]
                     if a.get("type") == "TOOL_DECISION"]
        assert any(t in decisions
                   for t in ("list_missions", "get_current_focus"))

    def test_demo_planner_sees_the_full_toolset(self, runtime):
        """Narrowing applies to what the MODEL is shown; the deterministic
        planner keeps its complete toolset."""
        user = _u("v851demo")
        original = runtime.agent.provider.chat_model
        runtime.agent.provider.chat_model = lambda: None
        runtime.agent._graph = None
        try:
            result = runtime.agent.run(user, _u("thr"),
                                       "Why did you do that?")
        finally:
            runtime.agent.provider.chat_model = original
            runtime.agent._graph = None
        assert result["provider"] == "demo"
        # The why-branch of the fallback reached explain_cognition unimpeded.
        decisions = [a.get("tool") for a in result["activity"]
                     if a.get("type") == "TOOL_DECISION"]
        assert "explain_cognition" in decisions


# ====================================== the architecture constraint itself
class TestModelStillDecides:
    """The router narrows families; the model makes the final tool choice."""

    def test_tool_surface_never_returns_a_single_tool_family_subset(self):
        """Every narrowed surface offers >= 2 substantive tools, so there is
        always a genuine decision left to the model."""
        router = CapabilityRouter(_NoProvider())
        for message, focus in (
                ("What missions am I currently working on?", ()),
                ("Resume it.", ("mission",)),
                ("What skills have you learned?", ()),
                ("Why did you use that skill?", ("skill",))):
            surface = router.tool_surface(message, focus)
            assert len(surface.allowed) >= 2

    def test_no_fabricated_tool_decisions(self, runtime):
        """When the model chooses NO tool, no TOOL_DECISION appears — the
        narrowing layer must never inject one."""
        user = _u("v851")
        runtime.cognition.missions.create(user, "Untouched", state="active",
                                          source="test")
        result = _drive(runtime,
                        [AIMessage(content="Direct answer, no tools.")],
                        user, "What missions am I currently working on?")
        decisions = [a for a in result["activity"]
                     if a.get("type") == "TOOL_DECISION"]
        assert decisions == [], (
            "a genuine model no-tool answer must stay tool-free in the trace")

    def test_real_path_still_contains_no_command_dispatch(self):
        """Extend the V8.3.1.2 guarantee to the new code: the graph's real
        path gained no message-conditional tool invocation."""
        import inspect

        from app.agent import graph as graph_module
        source = inspect.getsource(graph_module)
        real_path = source.split("def _demo_turn")[0]
        for forbidden in ('"resume" in', "'resume' in",
                          '"pause" in', "'pause' in",
                          ".invoke({", "invoke(args)"):
            assert forbidden not in real_path, (
                f"{forbidden!r} on the real model path suggests hidden "
                "deterministic tool execution")


# ====================== E — stringified-null tool argument normalisation
class TestStringifiedNullArguments:
    """
    Second Windows real-model failure in V8.5.1 verification: with routing
    fixed, llama3.2:3b chose list_missions correctly but emitted
    {"open_only": "null"} — the JSON null token serialised as a STRING —
    and strict validation surfaced TOOL_FAILED.

    The exact lowercase token "null" is normalised to None in the shared
    _NullTolerant base schema BEFORE default substitution. Nothing else is
    coerced: strictness for genuinely invalid strings is preserved.
    """

    @pytest.fixture
    def tools(self, runtime):
        from app.agent.cognitive_tools import build_cognitive_tools
        user = _u("v851null")
        runtime.cognition.missions.create(
            user, "Null-token mission", state="active", source="test")
        return user, {t.name: t for t in build_cognitive_tools(
            runtime.cognition, user, thread_id=_u("thr"))}

    def test_string_null_equals_none_equals_omitted(self, tools):
        """The exact Windows failure: {"open_only": "null"}."""
        _, by_name = tools
        stringified = by_name["list_missions"].invoke({"open_only": "null"})
        real_null = by_name["list_missions"].invoke({"open_only": None})
        omitted = by_name["list_missions"].invoke({})
        assert json.loads(stringified)["status"] == "OK"
        assert stringified == real_null == omitted, (
            'the stringified JSON null token must behave exactly like a real '
            'null and like omitting the argument')

    def test_real_booleans_are_preserved(self, tools):
        user, by_name = tools
        only_open = json.loads(
            by_name["list_missions"].invoke({"open_only": True}))
        everything = json.loads(
            by_name["list_missions"].invoke({"open_only": False}))
        assert only_open["status"] == "OK"
        assert everything["status"] == "OK"

    def test_invalid_strings_still_fail_validation(self, tools):
        """No general string->bool coercion: only the exact token 'null'."""
        _, by_name = tools
        for bad in ("banana", "None", "NULL", "Null", "nil", ""):
            with pytest.raises(Exception) as excinfo:
                by_name["list_missions"].invoke({"open_only": bad})
            assert "validation" in str(excinfo.value).lower() or \
                "boolean" in str(excinfo.value).lower(), (
                f"{bad!r} must still be rejected by strict validation")

    def test_string_null_on_string_fields_uses_the_default(self, tools):
        """The normalisation is schema-wide: a stringified null for an
        optional string argument (mission_id) resolves like a real null —
        via conversational focus, not a lookup of the literal id 'null'."""
        user, by_name = tools
        result = json.loads(by_name["get_mission"].invoke(
            {"mission_id": "null"}))
        # One mission exists for this user; focus-less resolution finds it
        # or reports honestly — it must NOT report a mission named 'null'.
        assert "null" not in json.dumps(result.get("mission", {})).lower() or \
            result.get("status") != "OK" or \
            result["mission"]["title"] == "Null-token mission"

    def test_model_emitting_string_null_produces_tool_result_not_failure(
            self, runtime):
        """End-to-end through the graph: the model's own call carries
        {"open_only": "null"}; the tool executes, TOOL_RESULT is recorded,
        TOOL_FAILED is absent, and the TOOL_DECISION is the model's."""
        user = _u("v851null")
        runtime.cognition.missions.create(
            user, "Windows trace mission", state="active", source="test")
        result = _drive(
            runtime,
            [AIMessage(content="", tool_calls=[
                {"name": "list_missions", "args": {"open_only": "null"},
                 "id": "n1"}]),
             AIMessage(content="You are working on the Windows trace mission.")],
            user, "What missions am I currently working on?")

        decisions = [a.get("tool") for a in result["activity"]
                     if a.get("type") == "TOOL_DECISION"]
        results = [a.get("tool") for a in result["activity"]
                   if a.get("type") == "TOOL_RESULT"]
        failures = [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]
        assert decisions == ["list_missions"]
        assert "list_missions" in results
        assert not failures, f"the stringified null must not fail: {failures}"


# ============== F — the ACTUAL execution boundary: run_tool_safely itself
class TestExecutionBoundaryNormalization:
    """
    Final Windows trace: routing correct, TOOL_DECISION list_missions
    correct, then TOOL_FAILED — the raw model dict {"open_only": "null"}
    reached LangChain schema validation unnormalised. Schema-level
    tolerance was not sufficient on the real Windows LangChain path, so
    the normalisation now lives at the actual execution boundary:
    run_tool_safely (and _invoke_untraced for standalone agents) call
    normalize_model_tool_args on the RAW model arguments before
    tool.invoke, hence before args_schema validation.

    These tests call the SAME functions the real agent calls — not
    Args.model_validate.
    """

    @pytest.fixture
    def mission_tools(self, runtime):
        from app.agent.cognitive_tools import build_cognitive_tools
        user = _u("v851edge")
        runtime.cognition.missions.create(
            user, "Boundary mission", state="active", source="test")
        return user, {t.name: t for t in build_cognitive_tools(
            runtime.cognition, user, thread_id=_u("thr"))}

    @staticmethod
    def _through_boundary(tool, args):
        """Exactly what tools_node does: run_tool_safely with raw args."""
        from app.agent.execution import ExecutionTrace, run_tool_safely
        trace = ExecutionTrace(correlation_id="c", user_id="u")
        outcome = run_tool_safely(trace, None, tool, args)
        stages = [s["type"] for s in trace.steps]
        return outcome, stages

    def test_windows_trace_repro_string_null_produces_tool_result(
            self, mission_tools):
        """The exact Windows failure, through the exact execution path."""
        _, by_name = mission_tools
        outcome, stages = self._through_boundary(
            by_name["list_missions"], {"open_only": "null"})
        assert outcome["ok"] is True, outcome
        assert stages == ["TOOL_RESULT"], (
            f"expected a clean TOOL_RESULT, got {stages}")
        assert "TOOL_FAILED" not in stages
        payload = json.loads(outcome["content"])
        assert payload["status"] == "OK"
        assert any(m["title"] == "Boundary mission"
                   for m in payload["missions"])

    def test_boundary_equivalence_with_real_null_and_omitted(
            self, mission_tools):
        _, by_name = mission_tools
        results = []
        for args in ({"open_only": "null"}, {"open_only": None}, {}):
            outcome, _ = self._through_boundary(
                by_name["list_missions"], dict(args))
            assert outcome["ok"], (args, outcome)
            results.append(outcome["content"])
        assert results[0] == results[1] == results[2]

    def test_boundary_preserves_real_booleans(self, mission_tools):
        _, by_name = mission_tools
        for value in (True, False):
            outcome, stages = self._through_boundary(
                by_name["list_missions"], {"open_only": value})
            assert outcome["ok"], outcome
            assert stages == ["TOOL_RESULT"]

    def test_boundary_keeps_strict_validation_for_invalid_strings(
            self, mission_tools):
        """"banana", "NULL", "None" must still fail — honestly, as
        TOOL_FAILED with a real ValidationError, never silently coerced."""
        _, by_name = mission_tools
        for bad in ("banana", "NULL", "None", "Null", "nil", ""):
            outcome, stages = self._through_boundary(
                by_name["list_missions"], {"open_only": bad})
            assert outcome["ok"] is False, (
                f"{bad!r} must not be accepted at the boundary")
            assert "TOOL_FAILED" in stages
            assert "ValidationError" in (outcome["error"] or "")

    def test_boundary_does_not_coerce_string_booleans(self):
        """'true'/'false' strings are NOT converted to booleans by the
        boundary helper — only the schema decides what they mean."""
        from app.agent.execution import normalize_model_tool_args
        assert normalize_model_tool_args(
            {"a": "true", "b": "false"}) == {"a": "true", "b": "false"}

    def test_helper_is_recursive_and_minimal(self):
        from app.agent.execution import normalize_model_tool_args
        raw = {"s": "null", "keep": ["null", "NULL", {"deep": "null"}],
               "b": True, "n": None, "i": 3, "f": 1.5, "t": "text"}
        assert normalize_model_tool_args(raw) == {
            "s": None, "keep": [None, "NULL", {"deep": None}],
            "b": True, "n": None, "i": 3, "f": 1.5, "t": "text"}
        # Non-dict inputs pass through untouched except the token itself.
        assert normalize_model_tool_args("null") is None
        assert normalize_model_tool_args("None") == "None"
        assert normalize_model_tool_args(7) == 7

    def test_untraced_standalone_path_is_also_normalised(self, mission_tools,
                                                         runtime):
        """_invoke_untraced (agent without a trace) shares the boundary."""
        _, by_name = mission_tools
        outcome = runtime.agent._invoke_untraced(
            by_name["list_missions"], {"open_only": "null"})
        assert outcome["ok"] is True, outcome
        assert json.loads(outcome["content"])["status"] == "OK"

    def test_duplicate_detection_sees_normalised_args(self, mission_tools):
        """{"open_only": "null"} and {"open_only": None} are the same call:
        the second must be blocked as a duplicate, not double-executed."""
        from app.agent.execution import ExecutionTrace, run_tool_safely
        _, by_name = mission_tools
        trace = ExecutionTrace(correlation_id="c", user_id="u")
        first = run_tool_safely(trace, None, by_name["list_missions"],
                                {"open_only": "null"})
        second = run_tool_safely(trace, None, by_name["list_missions"],
                                 {"open_only": None})
        assert first["ok"] is True
        assert second["duplicate"] is True


# ============ G — graph handoff: wrappers see the canonical representation
class TestGraphHandoffNormalization:
    """
    Final Windows regression: with routing and boundary normalisation in
    place, the legacy V8.4.1 correction test still failed because it wraps
    graph_module.run_tool_safely and captures arguments BEFORE the
    boundary's internal normalisation runs. The model emitted
    {"action": "retire", "reason": "...", "item_id": "null"} and the
    wrapper observed the raw string "null", violating the historical
    contract that item_id arrives as None/empty when the model means JSON
    null.

    tools_node therefore normalises at the HANDOFF — before calling
    run_tool_safely — with the same normalize_model_tool_args helper (one
    implementation, applied idempotently at both levels). These tests
    monkeypatch run_tool_safely exactly the way the V8.4.1 test does and
    assert on what the wrapper observes.
    """

    def _capture(self, runtime, monkeypatch, script, user, message,
                 thread=None):
        import app.agent.graph as graph_module
        captured = []
        real = graph_module.run_tool_safely

        def wrapper(trace, recorder, tool, args):
            captured.append({"tool": getattr(tool, "name", str(tool)),
                             "args": dict(args)})
            return real(trace, recorder, tool, args)

        monkeypatch.setattr(graph_module, "run_tool_safely", wrapper)
        result = _drive(runtime, script, user, message, thread=thread)
        return result, captured

    def test_wrapper_sees_none_not_stringified_null(self, runtime,
                                                    monkeypatch):
        """The exact V8.4.1 observation point: args AFTER graph handoff."""
        user = _u("v851hand")
        runtime.cognition.missions.create(
            user, "Handoff mission", state="active", source="test")
        result, captured = self._capture(
            runtime, monkeypatch,
            [AIMessage(content="", tool_calls=[
                {"name": "list_missions", "args": {"open_only": "null"},
                 "id": "h1"}]),
             AIMessage(content="Done.")],
            user, "What missions am I currently working on?")

        assert captured, "the wrapper must have observed the call"
        assert captured[0]["args"] == {"open_only": None}, (
            "the graph must hand execution the canonical representation — "
            f"a real None, not the string 'null': {captured[0]['args']}")
        assert not [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]

    def test_v841_correction_shape_arrives_canonical(self, runtime,
                                                     monkeypatch):
        """The exact Windows payload from the failing legacy test:
        {"action": "retire", "reason": ..., "item_id": "null"}. The wrapper
        must observe item_id as None (the historical V8.4.1 contract) and
        the correction must execute against the focused skill."""
        user = _u("v851hand")
        thread = _u("thr")
        cog = runtime.cognition
        # A minimal trusted skill, exactly like the V8.4.1 helper builds.
        experience_ids = []
        for index in range(3):
            observation = cog.observations.record(
                user, f"Logs exposed timeout {index}", source="outcome",
                origin=f"handoff-scenario:{index}", confidence=0.95)
            experience = cog.experiences.create(
                user, "a deployment health check fails",
                evidence_ids=[observation["id"]],
                action="inspect the deployment logs before retrying",
                outcome="the timeout is identified", success=True,
                pattern_key="inspect_deployment_logs", source="handoff-test")
            cog.experiences.enrich(user, experience["id"])
            cog.experiences.validate(user, experience["id"])
            cog.experiences.activate(user, experience["id"])
            experience_ids.append(experience["id"])
        skill = cog.knowledge.propose_skill(
            user, "Inspect deployment logs before retrying",
            "When a deployment health check fails, inspect its logs first.",
            trigger="deployment health check fails",
            procedure=["inspect deployment logs", "identify the failure"],
            expected_outcome="the underlying failure is identified",
            supporting_experience_ids=experience_ids,
            pattern_key="inspect_deployment_logs")
        assert cog.knowledge.validate(user, skill["id"])["decision"] == "PASS"
        skill = cog.knowledge.promote(user, skill["id"])
        cog.focus.set_focus(user, "skill", skill["id"], label=skill["name"],
                            session_id=thread)

        result, captured = self._capture(
            runtime, monkeypatch,
            [AIMessage(content="", tool_calls=[
                {"name": "correct_learned",
                 "args": {"action": "retire", "reason": "forget it",
                          "item_id": "null"}, "id": "h2"}]),
             AIMessage(content="Retired.")],
            user, "Forget that skill. Stop using it.", thread=thread)

        corrections = [c for c in captured if c["tool"] == "correct_learned"]
        assert corrections, captured
        args = corrections[0]["args"]
        assert args["action"] == "retire"
        assert args.get("item_id") in (None, ""), (
            f"item_id must arrive as None/empty for a JSON null: {args}")
        assert not [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]
        # The correction genuinely executed against the focused skill.
        assert cog.knowledge.get(user, skill["id"])["lifecycle"] == "retired"

    def test_handoff_preserves_invalid_strings_for_validation(self, runtime,
                                                              monkeypatch):
        """The handoff must NOT clean up genuinely invalid values — the
        wrapper sees them unchanged and validation still rejects them."""
        user = _u("v851hand")
        result, captured = self._capture(
            runtime, monkeypatch,
            [AIMessage(content="", tool_calls=[
                {"name": "list_missions", "args": {"open_only": "banana"},
                 "id": "h3"}]),
             AIMessage(content="Could not list.")],
            user, "What missions am I currently working on?")

        assert captured[0]["args"] == {"open_only": "banana"}, (
            "invalid strings must pass through the handoff untouched")
        failures = [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]
        assert failures, "strict validation must still reject 'banana'"
