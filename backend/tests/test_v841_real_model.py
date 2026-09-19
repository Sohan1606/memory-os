"""V8.4.1 real-model gate for conversational learned-knowledge tools.

These tests only pass when a real Ollama model executes, selects tools itself,
and changes/reads the canonical state. A skip is reported as NOT VERIFIED.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.slow

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv(
    "V841_TEST_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2:3b"))


def _probe() -> str | None:
    try:
        import httpx
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
    except Exception as exc:
        return (f"REAL MODEL NOT AVAILABLE: no Ollama server at {OLLAMA_URL} "
                f"({type(exc).__name__}). V8.4.1 real-model behavior is NOT VERIFIED.")
    if response.status_code != 200:
        return f"REAL MODEL NOT AVAILABLE: Ollama returned {response.status_code}"
    names = [m.get("name", "") for m in response.json().get("models", [])]
    available = (MODEL in names if ":" in MODEL
                 else any(n.split(":", 1)[0] == MODEL for n in names))
    if not available:
        return (f"REAL MODEL NOT AVAILABLE: {MODEL} is not pulled. Present: "
                f"{names or 'none'}. V8.4.1 real-model behavior is NOT VERIFIED.")
    return None


SKIP_REASON = _probe()
requires_model = pytest.mark.skipif(SKIP_REASON is not None,
                                    reason=SKIP_REASON or "")


@pytest.fixture(scope="module")
def real_runtime(tmp_path_factory):
    from app.config import Settings
    from app.runtime import Runtime

    tmp = tmp_path_factory.mktemp("v841-real")
    cfg = Settings(
        data_dir=tmp, sqlite_path=tmp / "m.db", checkpoint_path=tmp / "c.db",
        chroma_path=tmp / "chroma", model_provider="ollama",
        provider_autodetect=False, ollama_base_url=OLLAMA_URL,
        ollama_model=MODEL,
        llm_timeout_s=float(os.getenv("V841_LLM_TIMEOUT_S", "900")),
        turn_timeout_s=float(os.getenv("V841_TURN_TIMEOUT_S", "1800")))
    runtime = Runtime(cfg)
    yield runtime
    runtime.close()


def _trusted_skill(runtime, user: str):
    cog = runtime.cognition
    experience_ids = []
    for index in range(3):
        observation = cog.observations.record(
            user, f"Logs exposed timeout {index}", source="outcome",
            origin=f"real-model-scenario:{index}", confidence=0.95)
        experience = cog.experiences.create(
            user, "a deployment health check fails",
            evidence_ids=[observation["id"]],
            action="inspect the deployment logs before retrying",
            outcome="the timeout is identified", success=True,
            pattern_key="inspect_deployment_logs", source="real-model-test")
        cog.experiences.enrich(user, experience["id"])
        cog.experiences.validate(user, experience["id"])
        cog.experiences.activate(user, experience["id"])
        experience_ids.append(experience["id"])
    skill = cog.knowledge.propose_skill(
        user, "Inspect deployment logs before retrying",
        "When a deployment health check fails, inspect its logs before retrying.",
        trigger="deployment health check fails",
        procedure=["inspect deployment logs", "identify the failure signal"],
        expected_outcome="the underlying failure is identified",
        supporting_experience_ids=experience_ids,
        pattern_key="inspect_deployment_logs")
    assert cog.knowledge.validate(user, skill["id"])["decision"] == "PASS"
    return cog.knowledge.promote(user, skill["id"])


@requires_model
def test_real_model_selects_learned_inspection_tool(real_runtime):
    user = f"v841_real_{uuid.uuid4().hex[:8]}"
    skill = _trusted_skill(real_runtime, user)
    result = real_runtime.agent.run(
        user, f"thread_{uuid.uuid4().hex[:8]}",
        "What skills have you learned, and what evidence supports them?")

    assert result["provider"] != "demo"
    assert "degraded" not in result["provider"].lower()
    decisions = [a.get("tool") for a in result["activity"]
                 if a.get("type") == "TOOL_DECISION"]
    results = [a.get("tool") for a in result["activity"]
               if a.get("type") == "TOOL_RESULT"]
    failures = [a for a in result["activity"] if a.get("type") == "TOOL_FAILED"]
    assert any(tool in decisions for tool in ("list_learned", "inspect_learned"))
    assert any(tool in results for tool in ("list_learned", "inspect_learned"))
    assert not failures
    assert "deployment" in result["answer"].lower()
    assert real_runtime.cognition.knowledge.get(user, skill["id"])["lifecycle"] == "trusted"


@requires_model
def test_real_model_correction_retires_focused_skill(real_runtime, monkeypatch):
    # Observe, but do not replace, the production execution path. tools_node
    # passes these arguments directly from the model's AIMessage tool call.
    import app.agent.graph as graph_module

    executed_calls = []
    run_real_tool = graph_module.run_tool_safely

    def capture_ai_tool_call(trace, recorder, tool, args):
        outcome = run_real_tool(trace, recorder, tool, args)
        executed_calls.append({
            "tool": getattr(tool, "name", str(tool)),
            "args": dict(args), "outcome": dict(outcome)})
        return outcome

    monkeypatch.setattr(graph_module, "run_tool_safely", capture_ai_tool_call)

    user = f"v841_real_{uuid.uuid4().hex[:8]}"
    thread = f"thread_{uuid.uuid4().hex[:8]}"
    skill = _trusted_skill(real_runtime, user)
    real_runtime.cognition.focus.set_focus(
        user, "skill", skill["id"], label=skill["name"], session_id=thread)

    result = real_runtime.agent.run(user, thread, "Forget that skill. Stop using it.")

    assert result["provider"] != "demo"
    assert "degraded" not in result["provider"].lower()
    activity_types = {a.get("type") for a in result["activity"]}
    assert "PROVIDER_DEGRADED" not in activity_types
    assert "DEMO_PLANNER" not in activity_types
    decisions = [a.get("tool") for a in result["activity"]
                 if a.get("type") == "TOOL_DECISION"]
    results = [a.get("tool") for a in result["activity"]
               if a.get("type") == "TOOL_RESULT"]
    assert "correct_learned" in decisions
    assert "correct_learned" in results
    assert not [a for a in result["activity"] if a.get("type") == "TOOL_FAILED"]

    correction_calls = [
        call for call in executed_calls if call["tool"] == "correct_learned"]
    assert correction_calls, executed_calls
    actual = correction_calls[0]
    print("V8.4.1 ACTUAL OLLAMA CORRECTION CALL:", actual["args"])
    assert actual["args"]["action"] == "retire", actual["args"]
    assert str(actual["args"].get("reason") or "").strip(), actual["args"]
    assert actual["args"].get("item_id") in (None, ""), actual["args"]
    assert actual["outcome"]["ok"] is True
    payload = json.loads(actual["outcome"]["content"])
    print("V8.4.1 ACTUAL OLLAMA CORRECTION PAYLOAD:", payload)
    assert payload["status"] == "UPDATED"
    assert payload["action"] == "retire"
    assert payload["previous_lifecycle"] == "trusted"
    assert payload["resulting_lifecycle"] == "retired"
    assert payload["lifecycle"] == "retired"

    persisted = real_runtime.cognition.knowledge.get(user, skill["id"])
    assert persisted["lifecycle"] == "retired"
    retrieval = real_runtime.cognition.knowledge.retrieve(
        user, "deployment failed", kind="skill")
    assert retrieval["winner"] is None
    assert all(c["subject_id"] != skill["id"] for c in retrieval["candidates"])
    assert all(c["subject_id"] != skill["id"] for c in retrieval["blocked"])

    explanation = real_runtime.cognition.knowledge.explain(user, skill["id"])
    transition = explanation["lifecycle_history"][-1]
    assert transition["previous_lifecycle"] == "trusted"
    assert transition["lifecycle"] == "retired"
    correction_events = [
        event for event in real_runtime.cognition.bus.for_subject(
            "skill", skill["id"], user_id=user)
        if event.type == "skill.retired"]
    assert correction_events
    assert correction_events[-1].payload["correction"] is True
    assert correction_events[-1].correlation_id == result["correlation_id"]
