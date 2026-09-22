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

    # V8.4.4 portability limits. Imported packages are untrusted input and are
    # bounded before any JSON is parsed or any live row is touched.
    portability_max_package_bytes: int = int(os.getenv("PORTABILITY_MAX_PACKAGE_BYTES", str(50 * 1024 * 1024)))
    portability_max_member_bytes: int = int(os.getenv("PORTABILITY_MAX_MEMBER_BYTES", str(20 * 1024 * 1024)))
    portability_max_decompressed_bytes: int = int(os.getenv("PORTABILITY_MAX_DECOMPRESSED_BYTES", str(200 * 1024 * 1024)))
    portability_max_records: int = int(os.getenv("PORTABILITY_MAX_RECORDS", "100000"))
    portability_max_processing_seconds: float = float(os.getenv("PORTABILITY_MAX_PROCESSING_SECONDS", "30"))

    # ------------------------------------------------------------- V8.5 trust
    # AUTH_MODE:
    #   disabled : V8.4.4-compatible local demo. No login exists, requests run
    #              in the single-user namespace. This is the DEFAULT so every
    #              established behavior is preserved bit-for-bit.
    #   required : real multi-user production mode. Every /api route outside
    #              the public allowlist needs an authenticated session, and the
    #              caller can never choose a namespace other than their own.
    auth_mode: str = os.getenv("AUTH_MODE", "disabled").strip().lower()
    # PRODUCTION=1 asserts a production deployment. Startup fails loudly if the
    # rest of the configuration is not production-safe (see validate()).
    production: bool = _bool("PRODUCTION", False)
    # Session lifecycle. Tokens are 256-bit random values stored only as
    # SHA-256 hashes; the TTL bounds both cookie and bearer use.
    session_ttl_hours: float = float(os.getenv("SESSION_TTL_HOURS", "168"))
    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "memoryos_session")
    cookie_secure: bool = _bool("COOKIE_SECURE", _bool("PRODUCTION", False))
    # Password hashing cost (PBKDF2-HMAC-SHA256). Tests may lower this; the
    # default follows current OWASP guidance for PBKDF2-SHA256.
    auth_pbkdf2_iterations: int = int(os.getenv("AUTH_PBKDF2_ITERATIONS", "600000"))
    # Open registration. When enabled the FIRST registered account becomes the
    # owner of the default workspace; every later account gets its own
    # workspace (tenant) unless created by an admin inside a workspace.
    auth_allow_registration: bool = _bool("AUTH_ALLOW_REGISTRATION", True)
    # Optional deterministic bootstrap admin (from environment only — never
    # from source). Both values must be provided for the account to exist.
    auth_bootstrap_email: str | None = os.getenv("AUTH_BOOTSTRAP_EMAIL") or None
    auth_bootstrap_password: str | None = os.getenv("AUTH_BOOTSTRAP_PASSWORD") or None
    # Rate limiting. Defaults ON whenever authentication is required and OFF in
    # the local demo so V8.4.4 behavior is untouched. RATE_LIMIT_ENABLED
    # overrides in either direction.
    rate_limit_enabled: bool = _bool(
        "RATE_LIMIT_ENABLED",
        os.getenv("AUTH_MODE", "disabled").strip().lower() == "required")
    rate_limit_auth_per_minute: int = int(os.getenv("RATE_LIMIT_AUTH_PER_MINUTE", "10"))
    rate_limit_api_per_minute: int = int(os.getenv("RATE_LIMIT_API_PER_MINUTE", "300"))
    rate_limit_research_per_minute: int = int(os.getenv("RATE_LIMIT_RESEARCH_PER_MINUTE", "30"))
    rate_limit_portability_per_minute: int = int(os.getenv("RATE_LIMIT_PORTABILITY_PER_MINUTE", "10"))
    rate_limit_expensive_per_minute: int = int(os.getenv("RATE_LIMIT_EXPENSIVE_PER_MINUTE", "30"))
    # Global request body cap (bytes). Portability keeps its own tighter caps.
    max_request_bytes: int = int(os.getenv("MAX_REQUEST_BYTES", str(64 * 1024 * 1024)))
    # Structured JSON logs for production log pipelines.
    log_json: bool = _bool("LOG_JSON", False)

    def validate_production(self) -> list[str]:
        """Return the list of reasons this configuration is NOT production-safe.

        Empty list == safe. Callers decide whether to raise; Runtime raises
        when `production` is asserted so a mis-configured deployment cannot
        start quietly.
        """
        problems: list[str] = []
        if self.auth_mode != "required":
            problems.append("AUTH_MODE must be 'required' in production.")
        if self.cors_origins.strip() == "*":
            problems.append("CORS_ORIGINS must list explicit origins in production.")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be enabled in production.")
        if self.auth_pbkdf2_iterations < 100_000:
            problems.append("AUTH_PBKDF2_ITERATIONS is below the safe minimum (100000).")
        return problems


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
