"""Composition root: wires persistence, vectors, provider, agent and voice."""
from __future__ import annotations

import logging
import threading

from .agent.graph import MemoryAgent
from .cognition.orchestrator import Cognition
from .config import settings
from .cognition.events import EVENT_TYPES
from .memory.seeds import SEED_MEMORIES
from .memory.service import MemoryService
from .memory.vector_store import VectorStore
from .persistence.db import Database
from .memory.langmem_adapter import LangMemExtractor
from .providers.base import build_provider
from .voice.transcription import Transcriber

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, cfg=settings) -> None:
        self.settings = cfg
        self.db = Database(cfg.sqlite_path)
        self.vectors = VectorStore(cfg.chroma_path, disable_embeddings=cfg.disable_embeddings)
        self.memory = MemoryService(self.db, self.vectors)
        self.provider = build_provider(cfg)
        # A local Ollama server holds ONE model in RAM. Concurrent generate
        # requests make it load a second instance and fail on small machines,
        # so every LLM call in this process is serialised through one lock.
        self.llm_lock = threading.Lock()
        self.transcriber = Transcriber(cfg.whisper_model)
        self.checkpointer, self.checkpoint_backend = self._build_checkpointer(cfg)
        # LangMem is optional: inert unless installed AND a tool-calling model
        # exists. Its honest state is surfaced through /api/health.
        self.langmem = LangMemExtractor(
            self.provider.chat_model() if self.provider.status().supports_tool_calling else None)
        self.agent = MemoryAgent(self.memory, self.provider, self.checkpointer,
                                 langmem=self.langmem, llm_lock=self.llm_lock,
                                 max_tool_depth=getattr(cfg, "max_tool_depth", 4),
                                 turn_timeout_s=getattr(cfg, "turn_timeout_s", None))
        # v8 cognitive layer. Constructed last: it introspects the runtime it
        # belongs to (SelfModel reports on provider/vectors/voice/langmem).
        self.cognition = Cognition(self.db, self.memory, self)
        # v8.2: give the agent its cognitive collaborators now that they exist.
        # Done after construction because Cognition introspects the runtime,
        # which already holds the agent - this breaks the circular dependency
        # without duplicating any state.
        self.agent.recorder = self.cognition.traces
        self.agent.context_builder = self.cognition.context
        self.agent.router = self.cognition.router
        self.agent.policy_engine = self.cognition.policy
        self.seed_if_empty()

    @staticmethod
    def _build_checkpointer(cfg):
        """Persistent LangGraph checkpointer (SQLite), with in-memory fallback."""
        try:
            import sqlite3
            from langgraph.checkpoint.sqlite import SqliteSaver
            conn = sqlite3.connect(str(cfg.checkpoint_path), check_same_thread=False)
            return SqliteSaver(conn), "sqlite"
        except Exception as exc:  # pragma: no cover
            log.warning("SQLite checkpointer unavailable (%s); using in-memory.", exc)
            from langgraph.checkpoint.memory import InMemorySaver
            return InMemorySaver(), "memory"

    def seed_if_empty(self) -> None:
        if not self.memory.list(self.settings.demo_user_id):
            self.memory.seed(self.settings.demo_user_id, SEED_MEMORIES)

    def reset_demo(self) -> int:
        return self.memory.reset(self.settings.demo_user_id, SEED_MEMORIES)

    def health(self) -> dict:
        ps = self.provider.status()
        return {
            "status": "ok",
            "api": "fastapi",
            "agent": {"framework": "langgraph", "graph": "compiled",
                      "checkpointer": self.checkpoint_backend},
            "memory": {"backend": "sqlite", "path": str(self.settings.sqlite_path.name),
                       "count": len(self.memory.list(self.settings.demo_user_id))},
            "vector": {"backend": "chromadb" if self.vectors.available else "unavailable",
                       "mode": self.vectors.mode, "count": self.vectors.count(),
                       "error": self.vectors.error},
            "embeddings": {"model": self.vectors.embedding_model,
                           "dimension": self.vectors.embedding_dim,
                           "mode": "local" if self.vectors.available else "keyword-fallback"},
            "provider": {"name": ps.name, "model": ps.model, "available": ps.available,
                         "tool_calling": ps.supports_tool_calling, "detail": ps.detail,
                         "mode": ps.mode},
            "voice": {"mode": self.transcriber.mode, "detail": self.transcriber.detail},
            "langmem": self.langmem.status().as_dict(),
            "cognition": {
                "event_types": len(EVENT_TYPES),
                "events": sum(self.cognition.bus.counts(
                    self.settings.demo_user_id).values()),
                "autonomy": self.cognition.autonomy.level(self.settings.demo_user_id),
                "extraction": self.cognition.extractor.status(),
                "perception": self.cognition.perception.capabilities(),
            },
            # v8.2: capability truth and how each task would actually execute.
            "capabilities": self.cognition.router.report().as_dict(),
            "routing": self.cognition.router.routing_table(),
            "version": "8.2",
        }

    def close(self) -> None:
        """Release every resource this runtime owns (SQLite, Chroma, ONNX)."""
        self.db.close()
        try:
            self.vectors.close()
        except Exception:  # pragma: no cover - best-effort teardown
            pass
        checkpointer_conn = getattr(self.checkpointer, "conn", None)
        if checkpointer_conn is not None:
            try:
                checkpointer_conn.close()
            except Exception:  # pragma: no cover
                pass


# `lru_cache` alone is NOT enough here: it does not hold a lock while the
# factory runs, so two concurrent first requests would each build a Runtime.
# That races Chroma's shared client initialisation and one of them collapses to
# the keyword fallback. An explicit double-checked lock guarantees exactly one.
_runtime: Runtime | None = None
_runtime_lock = threading.Lock()


def get_runtime(cfg=settings) -> Runtime:
    """Return the process-wide Runtime, constructing it at most once."""
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = Runtime(cfg)
    return _runtime


def reset_runtime() -> None:
    """Drop the cached Runtime (used by tests to isolate temp data dirs)."""
    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close()
        _runtime = None
