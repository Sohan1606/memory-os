"""V10.2 real-model acceptance gate.

This module is deliberately separate from the deterministic V10.2 CORE and
RUNTIME suites.  A skip means NOT CONNECTED / NOT VERIFIED; no deterministic
or demo fallback can satisfy this gate.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.slow

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
MODEL = os.getenv("V102_TEST_MODEL", "llama3.2:3b")
LLM_TIMEOUT_S = float(os.getenv("V102_LLM_TIMEOUT_S", "180"))


def _probe_ollama() -> str | None:
    """Return an explicit NOT VERIFIED reason unless the real connector is usable."""
    try:
        import httpx
        import langchain_ollama  # noqa: F401

        response = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5.0)
    except Exception as exc:
        return (
            f"NOT CONNECTED / NOT VERIFIED: Ollama real-model connector at "
            f"{OLLAMA_BASE_URL} is unavailable ({type(exc).__name__}: {exc})."
        )
    if response.status_code != 200:
        return (
            f"NOT CONNECTED / NOT VERIFIED: Ollama at {OLLAMA_BASE_URL} returned "
            f"HTTP {response.status_code}."
        )
    try:
        names = [str(item.get("name", ""))
                 for item in response.json().get("models", [])]
    except Exception as exc:
        return (
            f"NOT CONNECTED / NOT VERIFIED: Ollama returned an invalid model list "
            f"({type(exc).__name__})."
        )
    installed = (MODEL in names if ":" in MODEL else
                 any(name.split(":", 1)[0] == MODEL for name in names))
    if not installed:
        return (
            f"NOT CONNECTED / NOT VERIFIED: requested real model {MODEL!r} is not "
            f"installed at {OLLAMA_BASE_URL}; present models: {names or 'none'}."
        )
    return None


NOT_CONNECTED_REASON = _probe_ollama()
requires_real_model = pytest.mark.skipif(
    NOT_CONNECTED_REASON is not None, reason=NOT_CONNECTED_REASON or "")


@requires_real_model
@pytest.mark.v102_real_model
def test_v102_real_ollama_turn_preserves_governance_boundaries(tmp_path):
    """Run one genuine model-backed cognitive+agent turn through the V10.2 seam.

    The test intentionally does not manufacture the three episodes/two days
    needed for an ACTIVE ATTENTION adaptation.  It verifies the highest safe
    real-model boundary and proves that governance remains deterministic and
    retains its existing mutation authorities.
    """
    from app.config import Settings
    from app.runtime import Runtime

    runtime = Runtime(Settings(
        data_dir=tmp_path,
        sqlite_path=tmp_path / "memory.db",
        checkpoint_path=tmp_path / "checkpoints.db",
        chroma_path=tmp_path / "chroma",
        disable_embeddings=True,
        model_provider="ollama",
        provider_autodetect=False,
        ollama_base_url=OLLAMA_BASE_URL,
        ollama_model=MODEL,
        llm_timeout_s=LLM_TIMEOUT_S,
        turn_timeout_s=max(LLM_TIMEOUT_S * 3, 180.0),
    ))
    try:
        user = f"v102_real_{uuid.uuid4().hex[:10]}"
        thread = f"thread_{uuid.uuid4().hex[:10]}"
        correlation_id = f"turn_{uuid.uuid4().hex[:16]}"

        # Provider selection must be real and truthfully ACTIVE before work.
        provider = runtime.provider.status()
        assert provider.name == "ollama", provider.as_dict()
        assert provider.available is True, provider.as_dict()
        assert provider.model is not None, provider.as_dict()
        assert provider.mode == "REAL AGENT", provider.as_dict()
        dependency = runtime.dependency_status()["model_provider"]
        assert dependency["state"] == "ACTIVE", dependency
        assert dependency["detail"].startswith("ollama:"), dependency

        cognition = runtime.cognition
        # Identity assertions prove this is the released composition root, not
        # a test-local policy/governance implementation or a second authority.
        assert cognition.policy_runtime.governance is cognition.policy_governance
        assert cognition.policy_runtime.assembler is cognition.policy_evidence
        assert cognition.policy_runtime.deriver is cognition.learning_signals
        assert cognition.policy_runtime.policy is cognition.policy
        assert cognition.policy_governance.policy is cognition.policy
        assert cognition.policy_governance.autonomy is cognition.autonomy

        policies_before = [dict(row) for row in runtime.db.query(
            "SELECT * FROM policies WHERE user_id=? ORDER BY id", (user,))]
        trace = cognition.process_turn(
            user, "What is the capital of France?",
            conversation_id=thread, correlation_id=correlation_id)

        # A real model compiled meaning in the actual cognitive pipeline.  A
        # deterministic compiler fallback is a hard failure, not success.
        meaning = trace["meaning"]
        assert meaning["semantic_mode"] == "MODEL", meaning["model_fallback_reason"]
        assert meaning["model_fallback_reason"] is None
        assert meaning["semantic"]["compiler"] == "model-assisted"
        persisted = cognition.meaning.compilation(user, meaning["compilation_id"])
        assert persisted is not None
        assert persisted["semantic"]["compiler"] == "model-assisted"

        # The same correlation traversed the real, bounded V10.2 coordinator.
        governance = trace["policy_governance"]
        assert governance["correlation_id"] == correlation_id
        assert governance["status"] != "GOVERNANCE_FAILED", governance
        assert governance["proposals"] == []  # no fabricated threshold evidence
        runs = runtime.db.query(
            "SELECT * FROM policy_governance_runs WHERE user_id=? "
            "AND correlation_id=?", (user, correlation_id))
        assert len(runs) == 1

        # Run the response half of the production turn with the same trace id.
        result = runtime.agent.run(
            user, thread, "What is the capital of France?",
            correlation_id=correlation_id)
        activity_types = {item.get("type") for item in result["activity"]}
        assert result["provider"] == "ollama", result
        assert result.get("degraded") is not True, result
        assert "PROVIDER_DEGRADED" not in activity_types, result["activity"]
        assert "DEMO_PLANNER" not in activity_types, result["activity"]
        assert "MODEL_CALL" in activity_types, result["activity"]
        assert result["execution"]["model_calls"] >= 1, result["execution"]
        assert result["answer"].strip()
        assert result["correlation_id"] == correlation_id

        # Model-generated semantic material is canonical cognition state, but
        # it is not silently promoted to class-B policy evidence.  Only the
        # assembler's explicit canonical allowlist can feed deterministic
        # governance, and this harmless turn cannot mutate effective policy.
        evidence = cognition.policy_evidence.assemble(
            user, correlation_id=correlation_id)
        assert all(item.provenance != "CognitivePolicyEngine" for item in evidence)
        assert all(item.kind not in {"meaning", "model_output", "policy"}
                   for item in evidence)
        policies_after = [dict(row) for row in runtime.db.query(
            "SELECT * FROM policies WHERE user_id=? ORDER BY id", (user,))]
        assert policies_after == policies_before
        assert cognition.policy_governance.list(user) == []

        print(
            "V10.2 REAL-MODEL GATE: CONNECTED / VERIFIED; "
            f"provider=ollama model={provider.model} "
            f"model_calls={result['execution']['model_calls']} "
            f"governance={governance['status']}"
        )
    finally:
        runtime.close()
