"""
V8.2 §3 — provider capability detection and task-aware routing.

These tests use stub providers so every branch (available / unavailable / model
unavailable / unsupported capability) is exercised deterministically, without
needing a real model server.
"""
import pytest

from app.providers.base import DemoProvider, OllamaProvider, ProviderStatus
from app.providers.capabilities import (EMBEDDINGS, GENERATION,
                                        MODE_DETERMINISTIC, MODE_MODEL_STRUCTURED,
                                        MODE_MODEL_TOOLS, MODE_NOT_CONFIGURED,
                                        NOT_SUPPORTED, STRUCTURED_OUTPUT,
                                        SUPPORTED, TOOL_CALLING, UNKNOWN, VISION,
                                        CapabilityRouter, describe,
                                        known_model_profile)


class StubProvider:
    """A provider whose status we control exactly."""

    def __init__(self, status: ProviderStatus) -> None:
        self._status = status
        self.name = status.name

    def status(self) -> ProviderStatus:
        return self._status

    def chat_model(self):
        return object() if self._status.available else None


def provider(name="ollama", available=True, model="qwen2.5:1.5b-instruct",
             tools=True, detail="stub"):
    return StubProvider(ProviderStatus(name, available, model, tools, detail))


# ------------------------------------------------------------ capability report
def test_demo_provider_claims_no_model_capabilities():
    report = describe(DemoProvider())
    assert report.states[GENERATION] == NOT_SUPPORTED
    assert report.states[TOOL_CALLING] == NOT_SUPPORTED
    assert report.states[VISION] == NOT_SUPPORTED
    assert not report.supports(GENERATION)


def test_unavailable_provider_supports_nothing():
    report = describe(provider(available=False, detail="server unreachable"))
    assert report.states[GENERATION] == NOT_SUPPORTED
    assert "unreachable" in report.reason(GENERATION)


def test_known_model_capabilities_are_reported():
    report = describe(provider(model="qwen2.5:1.5b-instruct"))
    assert report.supports(GENERATION)
    assert report.supports(TOOL_CALLING)
    assert report.states[VISION] == NOT_SUPPORTED


def test_small_model_tool_calling_is_not_claimed():
    """The 0.5b tag emits tool syntax unreliably, so we must not claim it."""
    report = describe(provider(model="qwen2.5:0.5b-instruct"))
    assert report.states[TOOL_CALLING] == NOT_SUPPORTED
    assert not report.supports(TOOL_CALLING)


def test_unknown_model_capability_is_unknown_not_assumed():
    report = describe(provider(model="some-unreleased-model:7b"))
    assert report.states[TOOL_CALLING] == UNKNOWN
    # UNKNOWN must never satisfy a requirement.
    assert report.supports(TOOL_CALLING) is False
    assert "not in the known-capability table" in report.reason(TOOL_CALLING)


def test_provider_can_veto_a_capable_model():
    """A provider that cannot bind tools overrides the model's own ability."""
    report = describe(provider(model="qwen2.5:7b", tools=False,
                               detail="no tool binding"))
    assert report.states[TOOL_CALLING] == NOT_SUPPORTED
    assert "does not expose tool binding" in report.reason(TOOL_CALLING)


def test_vision_model_is_recognised():
    report = describe(provider(model="llava:7b"))
    assert report.supports(VISION)


def test_embeddings_come_from_the_vector_store_not_the_chat_model():
    off = describe(provider(), embeddings_available=False)
    on = describe(provider(), embeddings_available=True)
    assert off.states[EMBEDDINGS] == NOT_SUPPORTED
    assert on.states[EMBEDDINGS] == SUPPORTED


def test_known_model_profile_unknown_returns_empty():
    assert known_model_profile("totally-made-up") == {}
    assert known_model_profile(None) == {}


# ------------------------------------------------------------------- routing
def test_tool_task_routes_to_model_tools_when_capable():
    router = CapabilityRouter(provider(model="qwen2.5:1.5b-instruct"))
    decision = router.route("memory_retrieval")
    assert decision.mode == MODE_MODEL_TOOLS
    assert decision.degraded is False
    assert decision.missing == []


def test_tool_task_degrades_when_tool_calling_missing():
    router = CapabilityRouter(provider(model="qwen2.5:0.5b-instruct"))
    decision = router.route("memory_retrieval")
    assert decision.mode == MODE_DETERMINISTIC
    assert decision.degraded is True
    assert TOOL_CALLING in decision.missing
    assert "DEGRADED" in decision.reason


def test_vision_without_a_vision_model_is_not_configured():
    """There is no honest deterministic fallback for vision."""
    router = CapabilityRouter(provider(model="qwen2.5:1.5b-instruct"))
    decision = router.route("vision")
    assert decision.mode == MODE_NOT_CONFIGURED
    assert decision.degraded is True
    assert "NOT CONFIGURED" in decision.reason
    assert decision.uses_model is False


def test_vision_routes_to_model_with_a_vision_model():
    router = CapabilityRouter(provider(model="llava:7b"))
    assert router.route("vision").mode != MODE_NOT_CONFIGURED


def test_structured_extraction_routes_to_schema_path():
    router = CapabilityRouter(provider(model="qwen2.5:1.5b-instruct"))
    assert router.route("structured_extraction").mode == MODE_MODEL_STRUCTURED


def test_demo_provider_routes_everything_deterministic_or_unconfigured():
    router = CapabilityRouter(DemoProvider())
    assert router.route("conversation").mode == MODE_DETERMINISTIC
    assert router.route("memory_retrieval").mode == MODE_DETERMINISTIC
    assert router.route("vision").mode == MODE_NOT_CONFIGURED


def test_unknown_task_is_rejected():
    router = CapabilityRouter(DemoProvider())
    with pytest.raises(ValueError):
        router.route("teleportation")


def test_routing_table_covers_every_task():
    table = CapabilityRouter(DemoProvider()).routing_table()
    names = {row["task"] for row in table}
    assert {"conversation", "memory_retrieval", "structured_extraction",
            "tool_execution", "vision", "embedding"} <= names
    assert all("reason" in row for row in table)


# --------------------------------------------------- real provider degradation
def test_ollama_provider_reports_unreachable_server_honestly():
    """Point at a port nothing is listening on: it must not claim availability."""
    status = OllamaProvider("http://127.0.0.1:9", "qwen2.5:1.5b").status()
    assert status.available is False
    assert status.mode == "DETERMINISTIC FALLBACK"


def test_runtime_exposes_a_capability_report(runtime):
    report = runtime.cognition.router.report().as_dict()
    assert "capabilities" in report
    assert {c["name"] for c in report["capabilities"]} >= {
        GENERATION, TOOL_CALLING, VISION, STRUCTURED_OUTPUT, EMBEDDINGS}
    for entry in report["capabilities"]:
        assert entry["state"] in (SUPPORTED, NOT_SUPPORTED, UNKNOWN)
        assert entry["reason"]
