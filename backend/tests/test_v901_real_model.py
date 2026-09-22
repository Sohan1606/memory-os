"""V9.0.2 real llama3.2:3b semantic extraction gate.

A skip is explicitly NOT CONNECTED / NOT VERIFIED; deterministic fallback does
not satisfy this test. This test never mocks or monkey-patches the model path.
"""
from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.slow
OLLAMA_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL = os.getenv("V902_TEST_MODEL", os.getenv("OLLAMA_MODEL", "llama3.2:3b"))


def _reason():
    try:
        import httpx
        response = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        names = [m.get("name", "") for m in response.json().get("models", [])]
    except Exception as exc:
        return f"NOT CONNECTED: Ollama unavailable ({type(exc).__name__}); V9.0.2 model semantics NOT VERIFIED."
    if response.status_code != 200:
        return f"NOT CONNECTED: Ollama returned {response.status_code}; NOT VERIFIED."
    if MODEL not in names and not any(n.split(":")[0] == MODEL.split(":")[0] for n in names):
        return f"NOT CONFIGURED: model {MODEL} is not pulled; NOT VERIFIED."
    return None


SKIP = _reason()


@pytest.mark.skipif(SKIP is not None, reason=SKIP or "")
def test_real_ollama_semantic_representation_is_validated(tmp_path):
    from app.config import Settings
    from app.runtime import Runtime
    runtime = Runtime(Settings(
        data_dir=tmp_path, sqlite_path=tmp_path / "m.db",
        checkpoint_path=tmp_path / "c.db", chroma_path=tmp_path / "chroma",
        disable_embeddings=True, model_provider="ollama", provider_autodetect=False,
        ollama_base_url=OLLAMA_URL, ollama_model=MODEL,
        llm_timeout_s=float(os.getenv("V902_LLM_TIMEOUT_S", "180"))))
    try:
        result = runtime.cognition.meaning.process(
            f"real_{uuid.uuid4().hex[:8]}",
            "I might leave this job next year.", persist=False)
        assert result["semantic_mode"] == "MODEL", result["model_fallback_reason"]
        semantic = result["semantic"]
        assert semantic["compiler"] == "model-assisted"
        assert semantic["candidates"]
        assert all(c["provenance"] == "MODEL_HYPOTHESIS" for c in semantic["candidates"])
        assert all(0 <= c["confidence"] <= .85 for c in semantic["candidates"])
        assert all(c["type"] != "FACT" for c in semantic["candidates"])
        assert any(c["temporal_scope"]["kind"] == "FUTURE" for c in semantic["candidates"])
    finally:
        runtime.close()
