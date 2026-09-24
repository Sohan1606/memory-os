"""
REAL local-LLM tests.

These are skipped unless a genuine Ollama server is reachable AND the configured
model is actually pulled. A skipped run must never be reported as a passing
real-agent test.

Run them with:
    OLLAMA_BASE_URL=http://127.0.0.1:11434 \
    OLLAMA_MODEL=qwen2.5:0.5b-instruct \
    python -m pytest tests/test_v81_real_agent.py -v
"""

import os
from pathlib import Path

import pytest

from app.config import Settings
from app.providers.base import OllamaProvider
from app.runtime import Runtime

BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:0.5b-instruct")

# Honour an explicit provider choice: `MODEL_PROVIDER=demo` means the developer
# asked for the deterministic suite, so the real-LLM tests must stand down even
# if an Ollama server happens to be running on this machine.
_requested = (os.getenv("MODEL_PROVIDER") or "").strip().lower()
_opted_out = _requested not in ("", "ollama")

_status = OllamaProvider(BASE_URL, MODEL).status()
_enabled = _status.available and not _opted_out

real_llm = pytest.mark.skipif(
    not _enabled,
    reason=(f"MODEL_PROVIDER={_requested!r} excludes the real-LLM suite"
            if _opted_out else
            f"No real Ollama model available: {_status.detail}"))


@pytest.fixture(scope="module")
def real_runtime(tmp_path_factory):
    """A runtime wired to the real local model."""
    data = tmp_path_factory.mktemp("real-llm")
    settings = Settings(
        data_dir=Path(data), sqlite_path=Path(data) / "m.db",
        checkpoint_path=Path(data) / "c.db", chroma_path=Path(data) / "chroma",
        model_provider="ollama", ollama_model=MODEL, ollama_base_url=BASE_URL)
    runtime = Runtime(settings)
    yield runtime
    runtime.close()


@real_llm
def test_provider_reports_real_agent_mode(real_runtime):
    status = real_runtime.provider.status()
    assert status.name == "ollama"
    assert status.available is True
    assert status.supports_tool_calling is True
    assert status.mode == "REAL AGENT"
    assert status.model                      # a concrete, resolved model tag


@real_llm
def test_model_resolves_a_pulled_tag(real_runtime):
    """A short name must resolve to a tag that is genuinely installed."""
    provider = real_runtime.provider
    assert provider.resolve_model() in (provider.tags() or [])


@real_llm
def test_real_end_to_end_tool_call_loop(real_runtime):
    """
    The headline guarantee:
    USER -> REAL LLM -> REAL TOOL CALL -> REAL RESULT -> SECOND MODEL DECISION.

    The model - not a keyword rule - must decide to call a tool.
    """
    user = real_runtime.settings.demo_user_id
    real_runtime.memory.create(
        user, "Prefers FastAPI for building backend services",
        category="PREFERENCE", allow_duplicate=True)

    result = real_runtime.agent.run(
        user, "real-thread-1",
        "Search my memory: what do I prefer for backend work?")

    answer = result["answer"]
    kinds = [a.get("type") for a in result["activity"]]

    # A real tool decision was made by the model.
    assert "TOOL_DECISION" in kinds, kinds
    # The first real model invocation is MODEL_CALL. Every real model
    # invocation after tool execution is recorded as MODEL_REVISION so the
    # trace can distinguish initial generation from post-tool reconsideration.
    model_invocations = (
        kinds.count("MODEL_CALL") +
        kinds.count("MODEL_REVISION")
    )
    assert "MODEL_CALL" in kinds, kinds
    assert "MODEL_REVISION" in kinds, kinds
    assert model_invocations >= 2, kinds
    # Raw tool-call syntax must never leak to the user.
    assert "<tool_call>" not in answer
    assert answer.strip()


@real_llm
def test_extraction_returns_validated_structures(real_runtime):
    """Whatever the model returns must be schema-valid or dropped."""
    from app.cognition.llm_extract import ENTITY_KINDS, NEEDS

    extractor = real_runtime.cognition.extractor
    assert extractor.available is True

    result = extractor.extract(
        "I need to migrate our billing system to Stripe by Friday")
    assert result.available is True
    assert result.source == "llm"
    for entity in result.entities:
        assert entity["kind"] in ENTITY_KINDS
        assert 0.0 <= entity["confidence"] <= 1.0
    if result.need:
        assert result.need["need"] in NEEDS
    if result.intent:
        assert 0.0 <= result.intent["confidence"] <= 1.0


@real_llm
def test_concurrent_turns_all_succeed(real_runtime):
    """
    A local model server holds one model in RAM. Parallel requests must be
    serialised rather than forcing a second load and failing - this reproduced
    a real 500 in browser QA.
    """
    import threading

    results: dict[int, str] = {}

    def turn(index: int) -> None:
        try:
            answer = real_runtime.agent.run(
                real_runtime.settings.demo_user_id, f"concurrent-{index}",
                "what do I prefer for backend work?")["answer"]
            results[index] = "ok" if answer.strip() else "empty"
        except Exception as exc:                        # pragma: no cover
            results[index] = f"error: {type(exc).__name__}"

    threads = [threading.Thread(target=turn, args=(i,)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=300)

    assert len(results) == 3
    assert all(state == "ok" for state in results.values()), results


@real_llm
def test_extraction_yields_to_a_busy_model(real_runtime):
    """Extraction must not queue behind a reply; it degrades instead."""
    extractor = real_runtime.cognition.extractor
    assert extractor.lock is not None
    with extractor.lock:                 # simulate a reply in flight
        result = extractor.extract("I am migrating billing to Stripe")
    assert result.available is False
    assert "busy" in (result.error or "").lower()


@real_llm
def test_turn_is_labelled_model_assisted(real_runtime):
    turn = real_runtime.cognition.process_turn(
        "real-user", "I am migrating our billing system to Stripe")
    assert turn["understanding"] == "model-assisted"
    assert turn["extraction"]["available"] is True
