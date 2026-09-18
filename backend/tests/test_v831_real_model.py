"""
§17 — REAL model test. No scripting, no stubs.

A genuine `llama3.2:3b` running under Ollama must decide, by itself, to call the
mission tools for a natural question, and the answer must be grounded in what
the tool actually returned.

These tests probe for Ollama once and SKIP with an explicit reason when it is
not present. They never pass silently: skipping is visible in the report, and a
skip is not evidence of anything.

They are slow. On CPU with the model paged through swap a single tool-calling
turn takes minutes, so they are marked `slow` and excluded from the default run
via `-m "not slow"` if you need a quick suite.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.slow

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("V831_TEST_MODEL", "llama3.2:3b")


def _probe() -> str | None:
    """Return a skip reason, or None when a real tool-calling model is ready."""
    try:
        import httpx
    except ImportError:  # pragma: no cover
        return "httpx is not installed"
    try:
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
    except Exception as exc:
        return (f"REAL MODEL NOT AVAILABLE: no Ollama server at {OLLAMA_URL} "
                f"({type(exc).__name__}). This test verifies nothing today.")
    if response.status_code != 200:
        return f"REAL MODEL NOT AVAILABLE: Ollama returned {response.status_code}"
    names = [m.get("name", "") for m in response.json().get("models", [])]
    if not any(n == MODEL or n.startswith(MODEL.split(":")[0]) for n in names):
        return (f"REAL MODEL NOT AVAILABLE: {MODEL} is not pulled. "
                f"Run: ollama pull {MODEL}. Present: {names or 'none'}")
    return None


SKIP_REASON = _probe()
requires_model = pytest.mark.skipif(SKIP_REASON is not None,
                                    reason=SKIP_REASON or "")


@pytest.fixture(scope="module")
def real_runtime(tmp_path_factory):
    """A runtime whose provider is the real local model."""
    from app.config import Settings
    from app.runtime import Runtime

    tmp = tmp_path_factory.mktemp("v831-real")
    # A 3B model on CPU, paged through swap, needs minutes per tool-calling
    # turn. The product defaults (120 s / 180 s) are right for a normal
    # machine; this fixture raises them so the test measures cognitive routing
    # rather than the speed of this particular box.
    cfg = Settings(data_dir=tmp, sqlite_path=tmp / "m.db",
                   checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma",
                   ollama_base_url=OLLAMA_URL, ollama_model=MODEL,
                   llm_timeout_s=float(os.getenv("V831_LLM_TIMEOUT_S", "900")),
                   turn_timeout_s=float(os.getenv("V831_TURN_TIMEOUT_S", "1800")))
    rt = Runtime(cfg)
    yield rt
    rt.close()


@requires_model
class TestRealModelCognitiveRouting:
    """The model must reach the mission subsystem on its own."""

    def test_model_uses_mission_tools_for_a_natural_question(self, real_runtime):
        cog = real_runtime.cognition
        user = f"real_{uuid.uuid4().hex[:6]}"

        # Real state, created through the real registry.
        cog.missions.create(user, "Finish the quarterly report", state="active",
                            source="test")

        health = real_runtime.health()
        assert health["provider"]["available"], (
            f"provider must really be up: {health['provider']}")

        thread = f"real_{uuid.uuid4().hex[:8]}"
        result = real_runtime.agent.run(
            user, thread, "What missions am I currently working on?")

        # 1. It must be the REAL agent, not the deterministic fallback.
        assert result["provider"] != "demo", (
            "this test is meaningless in fallback mode")

        # 2. The model must have chosen a mission tool itself.
        kinds = [a.get("type") for a in result["activity"]]
        assert any(k in ("LIST_MISSIONS", "GET_CURRENT_FOCUS", "GET_MISSION")
                   for k in kinds), (
            f"the model did not consult the mission subsystem; activity={kinds}")

        # 3. The answer must reflect the real mission.
        assert "quarterly report" in result["answer"].lower(), (
            f"answer is not grounded in the real mission: {result['answer']!r}")

    def test_model_does_not_invent_a_next_step(self, real_runtime):
        """§5 against a real model: no recorded next step must not become one."""
        cog = real_runtime.cognition
        user = f"real_{uuid.uuid4().hex[:6]}"
        cog.missions.create(user, "Plan the product launch", state="active",
                            source="test")

        thread = f"real_{uuid.uuid4().hex[:8]}"
        real_runtime.agent.run(user, thread,
                               "What missions am I currently working on?")
        result = real_runtime.agent.run(
            user, thread, "What is the next step for that mission?")

        assert result["provider"] != "demo"
        answer = result["answer"].lower()

        # The mission genuinely has no recorded next step. The model may propose
        # one, but it must not present a recorded plan that does not exist.
        honest_markers = ("no next step", "not recorded", "no recorded",
                          "nothing is recorded", "no specific next step",
                          "isn't recorded", "is not recorded", "no step",
                          "suggest", "propose", "recommend", "could")
        assert any(m in answer for m in honest_markers), (
            "the model appears to have invented a recorded next step: "
            f"{result['answer']!r}")

        # Whatever it said, the registry must still hold no next step.
        missions = cog.missions.list(user, open_only=True)
        assert missions[0]["next_step"] is None, (
            "answering a question must not write state")


@requires_model
class TestRealModelMissionActions:
    """
    V8.3.1.1 regression. The reported defect was that a real llama3.2:3b could
    not act on "Resume it." after pausing — it returned a misleading "current
    state is unknown". This asserts the real model now selects resume_mission
    and that the mission genuinely returns to active.
    """

    def test_model_selects_resume_mission_for_resume_it(self, real_runtime):
        cog = real_runtime.cognition
        user = f"real_{uuid.uuid4().hex[:6]}"
        thread = f"real_{uuid.uuid4().hex[:8]}"

        # A paused mission, put into conversational focus the way a real
        # conversation would: through a tool call the model itself makes.
        mission = cog.missions.create(
            user, "Finish the next MEMORY//OS milestone", state="active",
            source="test")
        cog.missions.set_state(user, mission["id"], "paused",
                               reason="User asked to pause it")
        cog.focus.set_focus(user, "mission", mission["id"],
                            label=mission["title"], session_id=thread)
        assert cog.missions.get(user, mission["id"])["state"] == "paused"

        result = real_runtime.agent.run(user, thread, "Resume it.")

        assert result["provider"] != "demo", (
            "this regression is meaningless in fallback mode")

        # 1. The model chose the action tool by itself — both the decision to
        #    call it and the result of that call must be in the trace.
        decided = [a.get("tool") for a in result["activity"]
                   if a.get("type") == "TOOL_DECISION"]
        returned = [a.get("tool") for a in result["activity"]
                    if a.get("type") == "TOOL_RESULT"]
        assert "resume_mission" in decided, (
            f"the model did not select resume_mission; it decided on {decided}")
        assert "resume_mission" in returned, (
            f"resume_mission produced no result; results were {returned}")

        # 1b. No tool may have failed — a ValidationError surfaces as
        #     TOOL_FAILED and would mean the model's call was rejected.
        failures = [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]
        assert not failures, f"a tool call failed: {failures}"

        # 2. The mission really came back to active, through the registry.
        assert cog.missions.get(user, mission["id"])["state"] == "active"

        # 3. mission.resumed was emitted by the canonical primitive.
        events = [e.type for e in cog.bus.recent(user, limit=100)]
        assert "mission.resumed" in events

        # 4. The answer does not claim the state is unknown — the exact
        #    symptom that was reported.
        answer = result["answer"].lower()
        assert "unknown" not in answer, (
            f"the model still reported an unknown state: {result['answer']!r}")

    def test_model_lists_missions_without_a_tool_failure(self, real_runtime):
        """
        V8.3.1.2 regression. A real llama3.2:3b emitted
        {"open_only": null} for list_missions, which the schema rejected, and
        the user saw TOOL_FAILED with a Pydantic ValidationError. The model's
        call was reasonable; the schema was too strict.
        """
        cog = real_runtime.cognition
        user = f"real_{uuid.uuid4().hex[:6]}"
        thread = f"real_{uuid.uuid4().hex[:8]}"
        cog.missions.create(user, "Migrate the billing database to Postgres",
                            state="active", source="test")

        result = real_runtime.agent.run(
            user, thread, "What missions am I currently working on?")

        assert result["provider"] != "demo"

        failures = [a for a in result["activity"]
                    if a.get("type") == "TOOL_FAILED"]
        assert not failures, (
            f"a tool call failed on a plain mission question: {failures}")

        # A legitimate mission-reading tool was used...
        tools_used = [a.get("tool") for a in result["activity"]
                      if a.get("type") == "TOOL_DECISION"]
        assert any(t in tools_used for t in
                   ("list_missions", "get_mission", "get_current_focus")), (
            f"no mission-reading tool was selected; used {tools_used}")

        # ...and the real mission is surfaced in the answer.
        assert "billing" in result["answer"].lower(), (
            f"the recorded mission was not surfaced: {result['answer']!r}")

    def test_model_does_not_resurrect_a_completed_mission(self, real_runtime):
        cog = real_runtime.cognition
        user = f"real_{uuid.uuid4().hex[:6]}"
        thread = f"real_{uuid.uuid4().hex[:8]}"

        mission = cog.missions.create(user, "Already delivered work",
                                      state="active", source="test")
        cog.missions.set_state(user, mission["id"], "completed",
                               reason="Finished before this test")
        cog.focus.set_focus(user, "mission", mission["id"],
                            label=mission["title"], session_id=thread)

        real_runtime.agent.run(user, thread, "Resume it.")

        # Whatever the model said, a terminal mission must stay terminal.
        assert cog.missions.get(user, mission["id"])["state"] == "completed"


@requires_model
def test_real_model_reports_as_real_agent(real_runtime):
    """§16: the real path must be distinguishable from the fallback."""
    user = f"real_{uuid.uuid4().hex[:6]}"
    result = real_runtime.agent.run(user, f"real_{uuid.uuid4().hex[:8]}",
                                    "Say hello in five words.")
    assert result["provider"] != "demo"


def test_real_model_availability_is_reported_honestly():
    """
    Always runs. Records whether §17 was actually exercised, so a skip can
    never be mistaken for a pass.
    """
    if SKIP_REASON is not None:
        pytest.skip(SKIP_REASON)
    assert SKIP_REASON is None
