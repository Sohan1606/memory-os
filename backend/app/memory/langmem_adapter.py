"""
Optional LangMem integration.

HONESTY CONTRACT
----------------
LangMem (https://pypi.org/project/langmem/) provides LLM-driven memory
extraction. It is genuinely OPTIONAL here for two independent reasons:

  1. It is not installed by default (it is absent from requirements.txt and
     listed only in requirements-optional.txt).
  2. Even when installed, `langmem.create_memory_manager` requires a real
     tool-calling chat model. In the default zero-cost demo provider there is
     no LLM at all, so LangMem *cannot* run and we must not pretend it does.

This adapter therefore reports one of three honest states:

  NOT INSTALLED    - the package cannot be imported.
  NOT CONFIGURED   - installed, but no tool-calling model is available.
  ACTIVE           - installed AND a tool-calling model is bound; extraction
                     really is delegated to LangMem.

MEMORY//OS's own deterministic policy engine (`app.memory.policy`) is always
used as the baseline extractor and is never labelled "LangMem".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

NOT_INSTALLED = "NOT INSTALLED"
NOT_CONFIGURED = "NOT CONFIGURED"
ACTIVE = "ACTIVE"


@dataclass(frozen=True)
class LangMemStatus:
    """Truthful description of what LangMem is doing in this process."""

    state: str
    version: str | None
    detail: str

    @property
    def active(self) -> bool:
        return self.state == ACTIVE

    def as_dict(self) -> dict[str, Any]:
        return {"state": self.state, "version": self.version, "detail": self.detail}


def _import_langmem() -> tuple[Any, str | None]:
    """Import langmem if present. Returns (module_or_None, version_or_None)."""
    try:
        import langmem  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - exercised only when installed
        return None, None
    version: str | None = getattr(langmem, "__version__", None)
    if version is None:
        try:
            from importlib.metadata import version as _v

            version = _v("langmem")
        except Exception:
            version = "unknown"
    return langmem, version


class LangMemExtractor:
    """
    Thin, honest wrapper around `langmem.create_memory_manager`.

    Construct it with the chat model the provider layer already built. If either
    LangMem or the model is missing, the wrapper stays inert and `status()`
    explains exactly why - callers fall back to the deterministic policy engine.
    """

    def __init__(self, chat_model: Any | None) -> None:
        self._manager: Any | None = None
        module, version = _import_langmem()
        self._version = version

        if module is None:
            self._status = LangMemStatus(
                NOT_INSTALLED, None,
                "langmem is not installed. Install it from requirements-optional.txt "
                "to enable LLM-driven extraction. The deterministic policy engine is used instead.",
            )
            return

        if chat_model is None:
            self._status = LangMemStatus(
                NOT_CONFIGURED, version,
                f"langmem {version} is installed but no tool-calling model is configured "
                "(MODEL_PROVIDER=demo). LangMem requires an LLM, so it is not running.",
            )
            return

        try:
            create_memory_manager = module.create_memory_manager
            self._manager = create_memory_manager(
                chat_model,
                instructions=(
                    "Extract durable, user-specific facts, preferences, goals and "
                    "projects. Ignore transient questions and small talk."
                ),
                enable_inserts=True,
                enable_updates=True,
                enable_deletes=False,
            )
        except Exception as exc:  # pragma: no cover - depends on optional install
            log.warning("LangMem could not be initialised: %s", exc)
            self._status = LangMemStatus(
                NOT_CONFIGURED, version,
                f"langmem {version} is installed but failed to initialise ({exc}). "
                "Falling back to the deterministic policy engine.",
            )
            return

        self._status = LangMemStatus(
            ACTIVE, version,
            f"langmem {version} is installed and bound to a tool-calling model. "
            "Memory extraction is delegated to LangMem.",
        )

    def status(self) -> LangMemStatus:
        return self._status

    def extract(self, messages: list[dict[str, str]]) -> list[str]:
        """
        Run LangMem extraction. Returns candidate memory strings.

        Returns an empty list when LangMem is not ACTIVE - it never fabricates
        output, so the caller's policy-engine fallback stays authoritative.
        """
        if self._manager is None:
            return []
        try:
            extracted = self._manager.invoke({"messages": messages})
        except Exception as exc:  # pragma: no cover - depends on optional install
            log.warning("LangMem extraction failed: %s", exc)
            return []

        out: list[str] = []
        for item in extracted or []:
            content = getattr(item, "content", None)
            if content is None and isinstance(item, (tuple, list)) and len(item) == 2:
                content = getattr(item[1], "content", None)
            if content is None:
                continue
            text = getattr(content, "content", content)
            if isinstance(text, str) and text.strip():
                out.append(text.strip())
        return out
