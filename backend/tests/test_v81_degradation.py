"""A failing local model must degrade honestly, never return an opaque error."""


class _FailingModel:
    """Stands in for a model server that cannot load the model (e.g. OOM)."""

    def __init__(self, message: str) -> None:
        self.message = message

    def bind_tools(self, tools):
        message = self.message

        class _Bound:
            def invoke(self, msgs):
                raise RuntimeError(message)

        return _Bound()


def _force_model_failure(runtime, message: str) -> dict:
    runtime.agent._graph = None
    original = runtime.agent.provider.chat_model
    runtime.agent.provider.chat_model = lambda: _FailingModel(message)
    try:
        return runtime.agent.run("degraded-user", "t-degraded",
                                 "remember that I prefer FastAPI for backend work")
    finally:
        runtime.agent.provider.chat_model = original
        runtime.agent._graph = None


def test_model_failure_does_not_raise(runtime):
    result = _force_model_failure(
        runtime, "model requires more system memory (885.7 MiB) than is available")
    assert result["answer"].strip()


def test_model_failure_is_disclosed_to_the_user(runtime):
    result = _force_model_failure(
        runtime, "model requires more system memory (885.7 MiB) than is available")
    answer = result["answer"].lower()
    assert "deterministic fallback" in answer
    assert "not enough memory" in answer
    # The user is told it was NOT the language model that answered.
    assert "not the language model" in answer


def test_model_failure_emits_a_degraded_activity_entry(runtime):
    result = _force_model_failure(runtime, "connection refused")
    kinds = [a.get("type") for a in result["activity"]]
    assert "PROVIDER_DEGRADED" in kinds


def test_degraded_turn_still_uses_real_tools(runtime):
    """Degrading must not disable memory - the deterministic planner still runs."""
    result = _force_model_failure(runtime, "connection refused")
    kinds = [a.get("type") for a in result["activity"]]
    assert "DEMO_PLANNER" in kinds
    assert "MEMORY_MANAGER" in kinds


def test_failure_reasons_are_plain_language(runtime):
    from app.agent.graph import MemoryAgent

    assert MemoryAgent._short_reason(
        "model requires more system memory (885 MiB)") == \
        "not enough memory to load the model"
    assert MemoryAgent._short_reason("connection refused") == \
        "the model server is unreachable"
    assert MemoryAgent._short_reason("read timed out") == "the model timed out"
    # Unknown errors must not leak raw internals.
    assert MemoryAgent._short_reason("KeyError: 'x' at line 42") == "model error"
