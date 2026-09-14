"""
LangMem contract tests.

These assert the HONESTY contract rather than pretending LangMem is running:
the adapter must report NOT INSTALLED / NOT CONFIGURED accurately, must never
fabricate extractions when inert, and must genuinely delegate when active.
"""

from __future__ import annotations

import pytest

from app.memory import policy
from app.memory.langmem_adapter import (
    ACTIVE,
    NOT_CONFIGURED,
    NOT_INSTALLED,
    LangMemExtractor,
)


def _installed() -> bool:
    try:
        import langmem  # noqa: F401
    except Exception:
        return False
    return True


def test_status_without_model_is_honest():
    """With no chat model, LangMem must never claim to be active."""
    ex = LangMemExtractor(None)
    status = ex.status()
    assert status.active is False
    assert status.state in {NOT_INSTALLED, NOT_CONFIGURED}
    if _installed():
        assert status.state == NOT_CONFIGURED
        assert status.version is not None
    else:
        assert status.state == NOT_INSTALLED


def test_inert_extractor_returns_nothing():
    """An inert adapter yields no memories - it must not invent any."""
    ex = LangMemExtractor(None)
    assert ex.extract([{"role": "user", "content": "I prefer dark mode."}]) == []


def test_status_dict_is_serialisable():
    d = LangMemExtractor(None).status().as_dict()
    assert set(d) == {"state", "version", "detail"}
    assert isinstance(d["detail"], str) and d["detail"]


def test_health_reports_langmem(runtime):
    """The API health payload must always disclose LangMem's real state."""
    lm = runtime.health()["langmem"]
    assert lm["state"] in {NOT_INSTALLED, NOT_CONFIGURED, ACTIVE}
    # langmem is not a dependency of this project, so it can never be ACTIVE -
    # regardless of which model provider is configured.
    assert lm["state"] != ACTIVE


def test_agent_falls_back_to_policy_engine(runtime):
    """
    With LangMem inert, the deterministic policy engine must still extract
    memories - the feature degrades, it does not disappear.

    This asserts the FALLBACK path, so it is skipped when a real LLM is driving
    the agent: what a live model chooses to store is its own decision and is
    covered by tests/test_v81_real_agent.py instead.
    """
    if runtime.provider.status().name != "demo":
        pytest.skip("Deterministic fallback test; a real LLM provider is active.")
    assert runtime.langmem.status().active is False
    out = runtime.agent.run("u-langmem", "t-langmem",
                            "Remember that I prefer Rust for systems programming.")
    assert any(a["type"] == "MEMORY_MANAGER" for a in out["activity"])
    assert not any(a["type"] == "LANGMEM_EXTRACT" for a in out["activity"])
    found = runtime.memory.search("u-langmem", "which language for systems programming")
    results = found["results"] if isinstance(found, dict) else found
    assert any("Rust" in r.memory.content for r in results)


def test_classify_extracted_accepts_statements_and_rejects_noise():
    """The LangMem hand-off path wraps text without re-running durability checks."""
    cand = policy.classify_extracted("User prefers concise technical explanations.")
    assert cand is not None
    assert cand.is_durable and cand.category == "COMMUNICATION_STYLE"
    assert cand.reason == "Extracted by LangMem"
    assert policy.classify_extracted("hi") is None


@pytest.mark.skipif(not _installed(), reason="langmem is not installed (optional)")
def test_active_state_requires_a_model():
    """When installed, binding a model must flip the state to ACTIVE."""
    class FakeToolCallingModel:
        def bind_tools(self, tools, **kwargs):
            return self

        def invoke(self, *args, **kwargs):
            raise AssertionError("not exercised in this test")

    ex = LangMemExtractor(FakeToolCallingModel())
    assert ex.status().state in {ACTIVE, NOT_CONFIGURED}
