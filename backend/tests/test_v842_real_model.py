"""
V8.4.2 real-model gate for conversational explanation tools.

These tests only pass when a real model provider executes, selects the explain_cognition
tool itself, and produces auditable evidence-backed explanations.
A skip is reported honestly as NOT VERIFIED.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.slow

OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL = os.getenv(
    "V842_TEST_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2:3b"))


def _probe() -> str | None:
    try:
        import httpx
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5.0)
    except Exception as exc:
        return (f"REAL MODEL NOT AVAILABLE: no Ollama server at {OLLAMA_URL} "
                f"({type(exc).__name__}). V8.4.2 real-model behavior is NOT VERIFIED.")
    if response.status_code != 200:
        return f"REAL MODEL NOT AVAILABLE: Ollama returned {response.status_code}"
    names = [m.get("name", "") for m in response.json().get("models", [])]
    available = (MODEL in names if ":" in MODEL
                 else any(n.split(":", 1)[0] == MODEL for n in names))
    if not available:
        return (f"REAL MODEL NOT AVAILABLE: {MODEL} is not pulled. Present: "
                f"{names or 'none'}. V8.4.2 real-model behavior is NOT VERIFIED.")
    return None


SKIP_REASON = _probe()
requires_model = pytest.mark.skipif(SKIP_REASON is not None,
                                    reason=SKIP_REASON or "")


@pytest.fixture(scope="module")
def real_runtime(tmp_path_factory):
    from app.config import Settings
    from app.runtime import Runtime

    tmp = tmp_path_factory.mktemp("v842-real")
    cfg = Settings(
        data_dir=tmp, sqlite_path=tmp / "m.db", checkpoint_path=tmp / "c.db",
        chroma_path=tmp / "chroma", model_provider="ollama",
        provider_autodetect=False, ollama_base_url=OLLAMA_URL,
        ollama_model=MODEL,
        llm_timeout_s=float(os.getenv("V842_LLM_TIMEOUT_S", "900")),
        turn_timeout_s=float(os.getenv("V842_TURN_TIMEOUT_S", "1800")))
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
def test_real_model_selects_explanation_tool_for_why(real_runtime, monkeypatch):
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

    user = f"v842_real_{uuid.uuid4().hex[:8]}"
    thread = f"thread_{uuid.uuid4().hex[:8]}"
    skill = _trusted_skill(real_runtime, user)
    real_runtime.cognition.focus.set_focus(
        user, "skill", skill["id"], label=skill["name"], session_id=thread)

    result = real_runtime.agent.run(
        user, thread, "Why did you use that skill? What is it based on?")

    assert result["provider"] != "demo"
    assert "degraded" not in result["provider"].lower()
    activity_types = {a.get("type") for a in result["activity"]}
    assert "PROVIDER_DEGRADED" not in activity_types
    assert "DEMO_PLANNER" not in activity_types
    decisions = [a.get("tool") for a in result["activity"]
                 if a.get("type") == "TOOL_DECISION"]
    results = [a.get("tool") for a in result["activity"]
               if a.get("type") == "TOOL_RESULT"]
    assert any(t in decisions for t in ("explain_cognition", "explain"))
    assert any(t in results for t in ("explain_cognition", "explain"))
    assert not [a for a in result["activity"] if a.get("type") == "TOOL_FAILED"]

    explain_calls = [
        call for call in executed_calls if call["tool"] in ("explain_cognition", "explain")]
    assert explain_calls, executed_calls
    actual = explain_calls[0]
    print("V8.4.2 ACTUAL OLLAMA EXPLAIN CALL:", actual["args"])
    assert actual["outcome"]["ok"] is True
    payload = json.loads(actual["outcome"]["content"])
    print("V8.4.2 ACTUAL OLLAMA EXPLAIN PAYLOAD:", payload)
    assert payload["status"] == "OK"
    assert payload["subject"]["id"] == skill["id"]
    assert payload["explanation_type"] in ("LEARNED_KNOWLEDGE_RETRIEVAL", "MEMORY_RECALL", "KNOWLEDGE_VALIDATION")
    assert isinstance(payload["state_now"], dict)
    assert payload["state_now"]["lifecycle"] == "trusted"
    assert payload["state_now"]["eligible_for_retrieval"] is True
    assert "deployment" in result["answer"].lower() or "logs" in result["answer"].lower()


@requires_model
def test_real_model_asks_why_not_after_retirement(real_runtime, monkeypatch):
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

    user = f"v842_real_{uuid.uuid4().hex[:8]}"
    thread = f"thread_{uuid.uuid4().hex[:8]}"
    skill = _trusted_skill(real_runtime, user)

    # Retire the skill
    retired = real_runtime.cognition.knowledge.correct(
        user, skill["id"], action="retire",
        reason="Security policy forbids modifying lockfile in CI. Use frozen lockfile.")
    assert retired["lifecycle"] == "retired"

    real_runtime.cognition.focus.set_focus(
        user, "skill", skill["id"], label=skill["name"], session_id=thread)

    result = real_runtime.agent.run(
        user, thread, "Why did you not use that skill?")

    assert result["provider"] != "demo"
    assert "degraded" not in result["provider"].lower()
    activity_types = {a.get("type") for a in result["activity"]}
    assert "PROVIDER_DEGRADED" not in activity_types
    assert "DEMO_PLANNER" not in activity_types
    decisions = [a.get("tool") for a in result["activity"]
                 if a.get("type") == "TOOL_DECISION"]
    results = [a.get("tool") for a in result["activity"]
               if a.get("type") == "TOOL_RESULT"]
    assert any(t in decisions for t in ("explain_cognition", "explain"))
    assert any(t in results for t in ("explain_cognition", "explain"))
    assert not [a for a in result["activity"] if a.get("type") == "TOOL_FAILED"]

    explain_calls = [
        call for call in executed_calls if call["tool"] in ("explain_cognition", "explain")]
    assert explain_calls, executed_calls
    actual = explain_calls[0]
    print("V8.4.2 ACTUAL OLLAMA WHY_NOT CALL:", actual["args"])
    assert actual["outcome"]["ok"] is True
    payload = json.loads(actual["outcome"]["content"])
    print("V8.4.2 ACTUAL OLLAMA WHY_NOT PAYLOAD:", payload)

    assert payload["status"] == "OK"
    assert payload["explanation_type"] in ("KNOWLEDGE_RETIREMENT", "KNOWLEDGE_CORRECTION")
    assert isinstance(payload["state_now"], dict)
    assert payload["state_now"]["lifecycle"] == "retired"
    assert payload["state_now"]["eligible_for_retrieval"] is False
    assert isinstance(payload["state_then"], dict)
    assert payload["state_then"]["lifecycle"] == "trusted"
    assert payload["state_then"]["eligible_for_retrieval"] is True
    assert payload.get("change_reason") or (payload.get("correction") and payload["correction"].get("reason"))
    assert "security policy forbids" in (payload.get("change_reason") or payload.get("correction", {}).get("reason", "")).lower()
    assert "retired" in result["answer"].lower() or "forbids" in result["answer"].lower() or "no longer" in result["answer"].lower() or "stopped" in result["answer"].lower()


@requires_model
def test_real_model_asks_what_changed(real_runtime, monkeypatch):
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

    user = f"v842_real_{uuid.uuid4().hex[:8]}"
    thread = f"thread_{uuid.uuid4().hex[:8]}"
    skill = _trusted_skill(real_runtime, user)
    real_runtime.cognition.knowledge.correct(
        user, skill["id"], action="retire",
        reason="Replaced by automated CI workflow")
    real_runtime.cognition.focus.set_focus(
        user, "skill", skill["id"], label=skill["name"], session_id=thread)

    result = real_runtime.agent.run(
        user, thread, "What changed about that skill?")

    assert result["provider"] != "demo"
    decisions = [a.get("tool") for a in result["activity"]
                 if a.get("type") == "TOOL_DECISION"]
    assert any(t in decisions for t in ("explain_cognition", "explain"))
    assert not [a for a in result["activity"] if a.get("type") == "TOOL_FAILED"]
