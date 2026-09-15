"""
V8.2 §4 — the model-driven agent loop: tracing, depth limits, timeouts,
duplicate detection, tool-failure recovery and cancellation.

A fake tool-calling model drives the loop deterministically so these behaviours
are tested without needing a real LLM.
"""
import time

import pytest
from langchain_core.messages import AIMessage

from app.agent.execution import (DUPLICATE_TOOL_CALL, FINAL_RESPONSE,
                                 LIMIT_REACHED, MODEL_CALL, MODEL_REVISION,
                                 TOOL_DECISION, TOOL_FAILED, TOOL_RESULT,
                                 Cancellation, ExecutionTrace, TraceRecorder,
                                 run_tool_safely)


class FakeTool:
    """A tool whose behaviour the test controls."""

    def __init__(self, name="search_memory", result="OK", error=None):
        self.name = name
        self.result = result
        self.error = error
        self.calls = 0

    def invoke(self, args):
        self.calls += 1
        if self.error:
            raise RuntimeError(self.error)
        return self.result


class ScriptedModel:
    """A chat model that emits a scripted sequence of responses."""

    def __init__(self, script):
        self.script = list(script)
        self.invocations = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.invocations += 1
        if self.script:
            return self.script.pop(0)
        return AIMessage(content="Final answer.")


def tool_call_message(name="search_memory", args=None, call_id="c1"):
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": args or {"query": "preferences"}, "id": call_id}])


@pytest.fixture
def agent(runtime):
    """The real agent, with its graph reset between tests."""
    runtime.agent._graph = None
    yield runtime.agent
    runtime.agent._graph = None
    runtime.agent.provider.chat_model = type(runtime.agent.provider).chat_model.__get__(
        runtime.agent.provider)


def drive(agent, script, thread="v82-loop", message="what do I prefer?", **kw):
    original = agent.provider.chat_model
    model = ScriptedModel(script)
    agent.provider.chat_model = lambda: model
    agent._graph = None
    try:
        result = agent.run("v82-user", thread, message, **kw)
        result["_model"] = model
        return result
    finally:
        agent.provider.chat_model = original
        agent._graph = None


# ------------------------------------------------------------- trace mechanics
def test_trace_rejects_unknown_stages():
    trace = ExecutionTrace(correlation_id="c", user_id="u")
    with pytest.raises(ValueError):
        trace.add("TELEPORT")


def test_trace_counts_tool_rounds_and_depth():
    trace = ExecutionTrace(correlation_id="c", user_id="u", max_tool_depth=2)
    assert trace.depth_exceeded() is False
    trace.add(TOOL_DECISION, "one")
    assert trace.depth_exceeded() is False
    trace.add(TOOL_DECISION, "two")
    assert trace.depth_exceeded() is True
    stop, why = trace.should_stop()
    assert stop and "maximum of 2 tool rounds" in why


def test_trace_detects_timeout():
    trace = ExecutionTrace(correlation_id="c", user_id="u", deadline_s=0.01)
    time.sleep(0.02)
    stop, why = trace.should_stop()
    assert stop and "budget" in why


def test_trace_supports_cancellation():
    token = Cancellation()
    trace = ExecutionTrace(correlation_id="c", user_id="u", cancellation=token)
    assert trace.should_stop()[0] is False
    token.cancel()
    stop, why = trace.should_stop()
    assert stop and why == "cancelled by the caller"


def test_duplicate_signature_is_argument_sensitive():
    trace = ExecutionTrace(correlation_id="c", user_id="u")
    trace.remember_call("search", {"q": "a"})
    assert trace.is_duplicate("search", {"q": "a"}) is True
    assert trace.is_duplicate("search", {"q": "b"}) is False


# ------------------------------------------------------------ safe tool runner
def test_tool_success_is_traced():
    trace = ExecutionTrace(correlation_id="c", user_id="u")
    tool = FakeTool(result="found 2 memories")
    out = run_tool_safely(trace, None, tool, {"query": "x"})
    assert out["ok"] and out["content"] == "found 2 memories"
    assert any(s["type"] == TOOL_RESULT for s in trace.steps)


def test_duplicate_tool_call_is_blocked_not_repeated():
    trace = ExecutionTrace(correlation_id="c", user_id="u")
    tool = FakeTool()
    run_tool_safely(trace, None, tool, {"query": "x"})
    second = run_tool_safely(trace, None, tool, {"query": "x"})
    assert second["duplicate"] is True
    assert "DUPLICATE_CALL" in second["content"]
    assert tool.calls == 1, "the duplicate must not actually execute"
    assert any(s["type"] == DUPLICATE_TOOL_CALL for s in trace.steps)


def test_tool_failure_is_recoverable_not_fatal():
    trace = ExecutionTrace(correlation_id="c", user_id="u")
    tool = FakeTool(error="database is locked")
    out = run_tool_safely(trace, None, tool, {"query": "x"})
    assert out["ok"] is False
    assert "TOOL_ERROR" in out["content"]
    # The model is told what went wrong so it can recover.
    assert "database is locked" in out["error"]
    assert any(s["type"] == TOOL_FAILED for s in trace.steps)


# ------------------------------------------------------------- recorder + bus
def test_recorder_persists_and_emits(runtime, user):
    recorder = TraceRecorder(runtime.db, runtime.cognition.bus)
    trace = recorder.new_trace(user, thread_id="t")
    recorder.record(trace, MODEL_CALL, "asked the model", provider="stub")
    recorder.record(trace, FINAL_RESPONSE, "answered")

    stored = recorder.for_correlation(trace.correlation_id)
    assert [s["stage"] for s in stored] == [MODEL_CALL, FINAL_RESPONSE]

    events = runtime.cognition.bus.for_correlation(trace.correlation_id)
    assert {e.type for e in events} >= {"execution.model_call",
                                        "execution.final_response"}


# -------------------------------------------------------- end-to-end agent loop
def test_model_driven_tool_loop_produces_full_trace(agent):
    """MODEL_CALL -> TOOL_DECISION -> TOOL_RESULT -> MODEL_REVISION -> FINAL."""
    result = drive(agent, [tool_call_message(),
                           AIMessage(content="You prefer FastAPI.")])
    kinds = [a["type"] for a in result["activity"]]
    assert MODEL_CALL in kinds
    assert TOOL_DECISION in kinds
    assert TOOL_RESULT in kinds
    assert MODEL_REVISION in kinds
    assert result["answer"] == "You prefer FastAPI."
    assert result["execution"]["tool_rounds"] == 1
    assert result["execution"]["revisions"] >= 1


def test_tool_loop_depth_is_bounded(agent):
    """A model that loops forever must be stopped, not allowed to run away."""
    agent.max_tool_depth = 2
    try:
        result = drive(agent, [tool_call_message(call_id=f"c{i}",
                                                 args={"query": f"q{i}"})
                               for i in range(10)], thread="v82-depth")
    finally:
        agent.max_tool_depth = 4
    assert result["execution"]["tool_rounds"] <= 2
    assert result["execution"]["depth_limited"] is True
    assert result["answer"].strip()


def test_duplicate_tool_calls_are_blocked_in_the_real_loop(agent):
    """Identical repeated calls must not re-execute the tool."""
    same = [tool_call_message(call_id=f"c{i}", args={"query": "same"})
            for i in range(3)]
    result = drive(agent, same + [AIMessage(content="Done.")],
                   thread="v82-dupe")
    assert result["execution"]["duplicates_blocked"] >= 1


def test_timeout_stops_the_turn_honestly(agent):
    result = drive(agent, [tool_call_message(call_id=f"c{i}",
                                             args={"query": f"q{i}"})
                           for i in range(6)],
                   thread="v82-timeout", timeout_s=0.001)
    assert result["answer"].strip()
    assert result["execution"]["timed_out"] or result["execution"]["depth_limited"]


def test_cancellation_stops_the_turn(agent):
    token = Cancellation()
    token.cancel()
    result = drive(agent, [AIMessage(content="never reached")],
                   thread="v82-cancel", cancellation=token)
    kinds = [a["type"] for a in result["activity"]]
    assert LIMIT_REACHED in kinds
    assert "stopped" in result["answer"].lower()


def test_execution_trace_is_retrievable_by_correlation_id(agent, runtime):
    result = drive(agent, [tool_call_message(), AIMessage(content="Answer.")],
                   thread="v82-retrieve")
    steps = runtime.cognition.traces.for_correlation(result["correlation_id"])
    assert [s["stage"] for s in steps][0] in (MODEL_CALL, "CONTEXT_BUILD")
    assert any(s["stage"] == TOOL_DECISION for s in steps)


def test_unknown_tool_name_does_not_crash_the_loop(agent):
    result = drive(agent, [tool_call_message(name="nonexistent_tool"),
                           AIMessage(content="I could not do that.")],
                   thread="v82-unknown")
    kinds = [a["type"] for a in result["activity"]]
    assert TOOL_FAILED in kinds
    assert result["answer"].strip()


def test_deterministic_fallback_still_works_without_a_model(runtime, user):
    """The demo planner must remain fully functional (§3E)."""
    runtime.agent._graph = None
    result = runtime.agent.run(user, "v82-demo", "I prefer dark mode interfaces.")
    kinds = [a["type"] for a in result["activity"]]
    assert "DEMO_PLANNER" in kinds
    assert result["answer"].strip()
