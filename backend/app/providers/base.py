"""Model provider abstraction.

Three modes, all honestly reported to the UI:
  demo   - deterministic local composer, no LLM, zero cost
  ollama - local Ollama server via langchain-ollama (if installed & reachable)
  openai - OpenAI via langchain-openai (if installed & key present)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class ProviderStatus:
    name: str            # demo | ollama | openai
    available: bool
    model: str | None
    supports_tool_calling: bool
    detail: str

    @property
    def mode(self) -> str:
        """Coarse, unambiguous runtime mode for the UI.

        REAL AGENT        - a real LLM is answering and may call tools
        DETERMINISTIC     - no LLM; the demo planner composes replies
        """
        if self.available and self.supports_tool_calling and self.name != "demo":
            return "REAL AGENT"
        return "DETERMINISTIC FALLBACK"

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "available": self.available, "model": self.model,
                "tool_calling": self.supports_tool_calling, "detail": self.detail,
                "mode": self.mode}


class BaseProvider:
    name = "base"

    def status(self) -> ProviderStatus:  # pragma: no cover - interface
        raise NotImplementedError

    def chat_model(self) -> Any | None:
        """Return a LangChain BaseChatModel, or None for the demo provider."""
        return None


class DemoProvider(BaseProvider):
    """Deterministic, zero-cost composer. Never claims to be an LLM."""

    name = "demo"

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            name="demo", available=True, model="deterministic-demo",
            supports_tool_calling=False,
            detail="LOCAL DEMO - deterministic responses, no LLM. Memory, embeddings "
                   "and retrieval are still real.")


class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, base_url: str, model: str, *, timeout: float = 120.0,
                 num_ctx: int = 4096) -> None:
        self.base_url = base_url
        self.model = model
        self.timeout = timeout
        self.num_ctx = num_ctx
        self._cached: Any | None = None
        # Status is read by many panels on every refresh; probing the server for
        # each one is wasteful. Cache the tag list briefly.
        self._tags_cache: tuple[float, list[str] | None] = (0.0, None)
        self._tags_ttl = 10.0

    def tags(self) -> list[str] | None:
        """Models actually present on the server, or None if unreachable."""
        import time
        fetched_at, cached = self._tags_cache
        if cached is not None and (time.monotonic() - fetched_at) < self._tags_ttl:
            return cached
        try:
            import httpx
            r = httpx.get(f"{self.base_url}/api/tags", timeout=2.0)
            if r.status_code != 200:
                return None
            names = [m.get("name", "") for m in r.json().get("models", [])]
            self._tags_cache = (time.monotonic(), names)
            return names
        except Exception:
            return None

    def _reachable(self) -> bool:
        return self.tags() is not None

    def resolve_model(self) -> str | None:
        """Match the configured model against what is installed.

        Accepts an exact match or a `name:tag` prefix match, so `qwen2.5:1.5b`
        resolves to `qwen2.5:1.5b-instruct-q4_K_M` if that is what is pulled.
        """
        tags = self.tags()
        if not tags:
            return None
        if self.model in tags:
            return self.model
        for t in tags:
            if t.startswith(self.model) or t.split(":")[0] == self.model:
                return t
        return None

    def status(self) -> ProviderStatus:
        try:
            import langchain_ollama  # noqa: F401
        except ImportError:
            return ProviderStatus("ollama", False, self.model, True,
                                  "langchain-ollama not installed.")
        if not self._reachable():
            return ProviderStatus("ollama", False, self.model, True,
                                  f"No Ollama server reachable at {self.base_url}.")
        resolved = self.resolve_model()
        if resolved is None:
            return ProviderStatus(
                "ollama", False, self.model, True,
                f"Ollama is running at {self.base_url} but model '{self.model}' is "
                f"not pulled. Run: ollama pull {self.model}")
        return ProviderStatus("ollama", True, resolved, True,
                              f"Local Ollama model {resolved} at {self.base_url}.")

    def chat_model(self):
        if self._cached is None:
            from langchain_ollama import ChatOllama
            self._cached = ChatOllama(
                model=self.resolve_model() or self.model, base_url=self.base_url,
                temperature=0.2, client_kwargs={"timeout": self.timeout},
                num_ctx=self.num_ctx)
        return self._cached


class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, api_key: str | None, model: str) -> None:
        self.api_key = api_key
        self.model = model
        self._cached: Any | None = None

    def status(self) -> ProviderStatus:
        try:
            import langchain_openai  # noqa: F401
        except ImportError:
            return ProviderStatus("openai", False, self.model, True,
                                  "langchain-openai not installed.")
        if not self.api_key:
            return ProviderStatus("openai", False, self.model, True,
                                  "OPENAI_API_KEY not set.")
        return ProviderStatus("openai", True, self.model, True,
                              f"OpenAI model {self.model}.")

    def chat_model(self):
        if self._cached is None:
            from langchain_openai import ChatOpenAI
            self._cached = ChatOpenAI(model=self.model, api_key=self.api_key, temperature=0.2)
        return self._cached


def build_provider(settings) -> BaseProvider:
    """Select a provider, preferring a real LLM when one is genuinely available.

    v8.1: when MODEL_PROVIDER is not set we probe for a local Ollama server and
    adopt it automatically, so the real-intelligence path needs no config. The
    deterministic demo remains the honest fallback and is never mislabelled.
    """
    requested = (settings.model_provider or "demo").lower()

    if getattr(settings, "provider_autodetect", False) and requested == "demo":
        candidate = OllamaProvider(settings.ollama_base_url, settings.ollama_model,
                                   timeout=getattr(settings, "llm_timeout_s", 120.0),
                                   num_ctx=getattr(settings, "llm_num_ctx", 4096))
        if candidate.status().available:
            log.info("Auto-detected local Ollama; enabling real agent mode.")
            return candidate
    if requested == "openai":
        p = OpenAIProvider(settings.openai_api_key, settings.openai_model)
        if p.status().available:
            return p
        log.warning("OpenAI requested but unavailable: %s", p.status().detail)
    if requested == "ollama":
        p = OllamaProvider(settings.ollama_base_url, settings.ollama_model,
                           timeout=getattr(settings, "llm_timeout_s", 120.0),
                           num_ctx=getattr(settings, "llm_num_ctx", 4096))
        if p.status().available:
            return p
        log.warning("Ollama requested but unavailable: %s", p.status().detail)
    return DemoProvider()
