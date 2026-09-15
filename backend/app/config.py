"""Runtime configuration for MEMORY//OS backend.

Every value has a safe default so the project runs with zero environment
variables in LOCAL DEMO mode.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("MEMORY_OS_DATA_DIR", BACKEND_ROOT / "data"))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    data_dir: Path = DATA_DIR
    sqlite_path: Path = DATA_DIR / "memory_os.sqlite3"
    checkpoint_path: Path = DATA_DIR / "checkpoints.sqlite3"
    chroma_path: Path = DATA_DIR / "chroma"

    demo_user_id: str = os.getenv("DEMO_USER_ID", "demo-user")

    # provider: demo | ollama | openai
    model_provider: str = os.getenv("MODEL_PROVIDER", "demo").strip().lower()
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:1.5b-instruct")
    # v8.1: when MODEL_PROVIDER is unset we probe for a local Ollama server and
    # use it automatically. Real intelligence should not require configuration.
    provider_autodetect: bool = os.getenv("MODEL_PROVIDER") is None
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "120"))
    llm_num_ctx: int = int(os.getenv("LLM_NUM_CTX", "4096"))

    # v8.2 agent-loop bounds. A local model can stall, so every turn is
    # explicitly capped in both tool rounds and wall-clock time.
    max_tool_depth: int = int(os.getenv("MAX_TOOL_DEPTH", "4"))
    turn_timeout_s: float = float(os.getenv("TURN_TIMEOUT_S", "180"))

    whisper_model: str | None = os.getenv("WHISPER_MODEL") or None

    # retrieval tuning (single source of truth)
    retrieval_top_k: int = int(os.getenv("RETRIEVAL_TOP_K", "5"))
    strong_match_threshold: float = float(os.getenv("STRONG_MATCH_THRESHOLD", "0.40"))
    weak_match_threshold: float = float(os.getenv("WEAK_MATCH_THRESHOLD", "0.20"))
    duplicate_threshold: float = float(os.getenv("DUPLICATE_THRESHOLD", "0.80"))
    conflict_threshold: float = float(os.getenv("CONFLICT_THRESHOLD", "0.45"))
    # lower bar when an explicit contradiction signal is present
    contradiction_threshold: float = float(os.getenv("CONTRADICTION_THRESHOLD", "0.30"))

    # Ranking weights (single source of truth, documented in docs/retrieval.md)
    w_semantic: float = 0.45
    w_keyword: float = 0.15
    w_category: float = 0.20
    w_importance: float = 0.08
    w_confidence: float = 0.04
    w_recency: float = 0.08
    min_semantic: float = float(os.getenv("MIN_SEMANTIC", "0.18"))

    cors_origins: str = os.getenv("CORS_ORIGINS", "*")
    disable_embeddings: bool = _bool("MEMORY_OS_DISABLE_EMBEDDINGS", False)


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
