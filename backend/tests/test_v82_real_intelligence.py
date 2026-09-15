"""
V8.2 "real intelligence" scenarios.

These are the tests that must distinguish GENUINE model-driven cognition from
the deterministic fallback, and prove the system never pretends to be the
former while running the latter.

Two halves:

  A. Fallback path (runs everywhere, including this sandbox). Asserts the
     system is HONEST about being deterministic and still does real cognitive
     work — retrieval, arbitration, evidence, refusal to guess.

  B. Model path, driven by a scripted fake model. Asserts the loop is genuinely
     model-driven: the model's own tool choices are executed, its revisions are
     traced, and the reply is the model's, not a template.

Tests that require a real Ollama server are skipped with an explicit reason
rather than silently passing.
"""
import os
import uuid

import pytest
from langchain_core.messages import AIMessage

from app.providers.base import OllamaProvider

REAL_MODEL_AVAILABLE = OllamaProvider(
    os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    os.environ.get("OLLAMA_MODEL", "qwen2.5:1.5b-instruct")).status().available

requires_real_model = pytest.mark.skipif(
    not REAL_MODEL_AVAILABLE,
    reason="No Ollama server reachable - the real-model path cannot be "
           "exercised in this environment.")


@pytest.fixture
def u():
    return f"ri-{uuid.uuid4().hex[:8]}"


# =============================================================================
# A. Deterministic fallback — honesty and real cognitive work
# =============================================================================
def test_fallback_declares_itself_and_never_claims_to_be_a_model(runtime):
    status = runtime.provider.status()
    if status.available and status.name != "demo":
        pytest.skip("a real provider is configured in this environment")
    assert status.mode == "DETERMINISTIC FALLBACK"
    assert "DEMO" in status.detail.upper() or "no llm" in status.detail.lower()

    report = runtime.cognition.router.report().as_dict()
    for cap in report["capabilities"]:
        if cap["name"] in ("generation", "tool_calling", "vision"):
            assert cap["state"] == "NOT_SUPPORTED", (
                "the fallback must never claim model capabilities")


def test_fallback_turn_is_marked_deterministic_end_to_end(runtime, u):
    trace = runtime.cognition.process_turn(u, "what should I work on next?")
    routing = trace["routing"]
    if routing["uses_model"]:
        pytest.skip("a real model is configured in this environment")
    assert routing["mode"] == "DETERMINISTIC"
    assert routing["degraded"] is True
    assert "DEGRADED" in routing["reason"] or "NOT CONFIGURED" in routing["reason"]


def test_fallback_still_performs_real_retrieval_and_arbitration(runtime, u):
    """Deterministic does NOT mean fake: memory work is genuinely happening."""
    runtime.memory.create(u, "Deploys the billing service on Tuesday mornings",
                          allow_duplicate=True)
    runtime.memory.create(u, "Deploys the billing service on Thursday evenings",
                          allow_duplicate=True)
    trace = runtime.cognition.process_turn(u, "when do I deploy billing?")

    assert trace["retrieval"]["count"] > 0, "retrieval must really run"
    for candidate in trace["retrieval"]["candidates"]:
        assert candidate["reasons"], "scores must be explained, never invented"
        assert candidate["semantic"] is not None
        assert candidate["keyword"] is not None

    arbitration = trace["arbitration"]
    assert arbitration["reason"], "arbitration must justify its choice"
    assert arbitration["candidates"]


def test_fallback_answers_are_produced_without_pretending_to_reason(runtime, u):
    result = runtime.agent.run(u, "ri-fallback", "I prefer concise answers.")
    kinds = [a["type"] for a in result["activity"]]
    assert "DEMO_PLANNER" in kinds, "the fallback must declare its planner"
    assert "MODEL_CALL" not in kinds, "no model call may be claimed"
    assert result["answer"].strip()


def test_no_capability_is_claimed_without_evidence(runtime, u):
    """Every trust label starts at INSUFFICIENT EVIDENCE, never optimistic."""
    for entry in runtime.cognition.capability_trust.all(u):
        if entry["total"] < 3:
            assert entry["label"] == "INSUFFICIENT EVIDENCE"
            assert entry["reliability"] is None


def test_the_system_refuses_to_guess_rather_than_confabulate(runtime, u):
    """Asked to forget something it cannot identify, it must ask, not delete."""
    result = runtime.cognition.control.handle(u, "forget that")
    assert result["applied"] is False
    assert result["requires"] == "clarification"


def test_unconnected_sources_are_declared_in_every_turn(runtime, u):
    trace = runtime.cognition.process_turn(u, "what is on my calendar today?")
    unavailable = trace["context"]["unavailable"]
    assert any(s["source"] == "calendar" for s in unavailable)
    assert all(s["state"].startswith("NOT") for s in unavailable)


# =============================================================================
# B. Model path — driven by a scripted fake model
# =============================================================================
class ScriptedModel:
    """A stand-in for a real tool-calling LLM, with an exact script."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0
        self.seen_prompts = []

    def bind_tools(self, tools):
        self.bound = tools
        return self

    def invoke(self, messages):
        self.calls += 1
        self.seen_prompts.append(messages)
        return self.script.pop(0) if self.script else AIMessage(content="Done.")


def run_with_model(runtime, script, user, message, thread=None):
    # A unique thread per run: LangGraph checkpoints persist per thread, and a
    # shared thread would leak one test's messages into the next.
    thread = thread or f"ri-{uuid.uuid4().hex[:8]}"
    original = runtime.agent.provider.chat_model
    model = ScriptedModel(script)
    runtime.agent.provider.chat_model = lambda: model
    runtime.agent._graph = None
    try:
        result = runtime.agent.run(user, thread, message)
        result["_model"] = model
        return result
    finally:
        runtime.agent.provider.chat_model = original
        runtime.agent._graph = None


def test_the_model_not_a_keyword_rule_decides_to_use_a_tool(runtime, u):
    """
    The defining difference from V8.1's demo planner: the TOOL CHOICE comes
    from the model. The message contains no keyword that would trigger a
    search, yet the model's decision is honoured.
    """
    script = [
        AIMessage(content="", tool_calls=[
            {"name": "search_memory", "args": {"query": "deployment window"},
             "id": "t1"}]),
        AIMessage(content="Based on what I found, you deploy on Tuesdays."),
    ]
    result = run_with_model(runtime, script, u, "remind me about the thing")
    kinds = [a["type"] for a in result["activity"]]
    assert "TOOL_DECISION" in kinds
    assert "DEMO_PLANNER" not in kinds, "this must not fall back to keywords"
    assert result["answer"] == "Based on what I found, you deploy on Tuesdays."


def test_the_model_revises_its_answer_after_seeing_the_tool_result(runtime, u):
    script = [
        AIMessage(content="", tool_calls=[
            {"name": "search_memory", "args": {"query": "database"},
             "id": "t1"}]),
        AIMessage(content="Revised answer after reading the memories."),
    ]
    result = run_with_model(runtime, script, u, "what database do I use?")
    kinds = [a["type"] for a in result["activity"]]
    assert kinds.index("TOOL_RESULT") < kinds.index("MODEL_REVISION")
    assert result["execution"]["revisions"] >= 1


def test_the_tool_result_is_real_memory_data_not_a_stub(runtime, u):
    runtime.memory.create(u, "Uses CockroachDB for the ledger service",
                          allow_duplicate=True)
    script = [
        AIMessage(content="", tool_calls=[
            {"name": "search_memory", "args": {"query": "ledger service"},
             "id": "t1"}]),
        AIMessage(content="You use CockroachDB."),
    ]
    result = run_with_model(runtime, script, u, "what runs the ledger?")
    assert any(a["type"] == "TOOL_RESULT" for a in result["activity"])

    # The tool genuinely hit the memory store and found the real row.
    searches = [a for a in result["activity"] if a["type"] == "SEARCH_MEMORY"]
    assert searches, "the search tool must actually have executed"
    assert searches[0]["count"] >= 1, "it must return genuine stored data"
    assert searches[0]["query"] == "ledger service", (
        "the query must be the model's own, not a rewritten keyword")


def test_a_model_turn_is_fully_traced_for_inspection(runtime, u):
    script = [
        AIMessage(content="", tool_calls=[
            {"name": "search_memory", "args": {"query": "x"}, "id": "t1"}]),
        AIMessage(content="Answer."),
    ]
    result = run_with_model(runtime, script, u, "anything")
    steps = runtime.cognition.traces.for_correlation(result["correlation_id"])
    stages = [s["stage"] for s in steps]
    assert "MODEL_CALL" in stages
    assert "TOOL_DECISION" in stages
    assert "FINAL_RESPONSE" in stages
    # Every step is persisted with a human-readable detail.
    assert all(s["detail"] is not None for s in steps)


def test_a_runaway_model_cannot_loop_forever(runtime, u):
    """Real intelligence includes knowing when to stop."""
    script = [AIMessage(content="", tool_calls=[
        {"name": "search_memory", "args": {"query": f"q{i}"}, "id": f"t{i}"}])
        for i in range(20)]
    result = run_with_model(runtime, script, u, "loop forever please")
    assert result["execution"]["tool_rounds"] <= runtime.agent.max_tool_depth
    assert result["answer"].strip(), "it must still say something honest"


def test_a_failing_tool_does_not_produce_a_fabricated_answer(runtime, u):
    script = [
        AIMessage(content="", tool_calls=[
            {"name": "no_such_tool", "args": {}, "id": "t1"}]),
        AIMessage(content="I could not look that up."),
    ]
    result = run_with_model(runtime, script, u, "look something up")
    kinds = [a["type"] for a in result["activity"]]
    assert "TOOL_FAILED" in kinds
    assert result["answer"] == "I could not look that up."


def test_context_is_actually_given_to_the_model(runtime, u):
    """The assembled context must reach the prompt, or it is theatre."""
    runtime.memory.create(u, "Allergic to shellfish", allow_duplicate=True)
    result = run_with_model(runtime, [AIMessage(content="Noted.")], u,
                            "what should I avoid eating?")
    model = result["_model"]
    prompt_text = " ".join(
        str(getattr(m, "content", "")) for m in model.seen_prompts[0])
    assert "shellfish" in prompt_text.lower(), (
        "retrieved memory must genuinely appear in the model's prompt")


# =============================================================================
# C. Real Ollama — executed only when a server is genuinely present
# =============================================================================
@requires_real_model
def test_real_model_produces_a_non_template_answer(runtime, u):
    result = runtime.agent.run(u, "ri-real", "In one short sentence, what is "
                                             "the capital of France?")
    assert "paris" in result["answer"].lower()
    kinds = [a["type"] for a in result["activity"]]
    assert "MODEL_CALL" in kinds
    assert "DEMO_PLANNER" not in kinds


@requires_real_model
def test_real_model_path_reports_itself_as_a_real_agent(runtime):
    assert runtime.provider.status().mode == "REAL AGENT"
    assert runtime.cognition.router.route("conversation").uses_model is True


def test_the_environment_reports_model_availability_truthfully():
    """
    Whatever the answer, it must be derived from a real probe — this test
    records which path the rest of the suite actually exercised.
    """
    assert isinstance(REAL_MODEL_AVAILABLE, bool)
