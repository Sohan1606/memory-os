"""Composition root: wires persistence, vectors, provider, agent and voice."""
from __future__ import annotations

import logging
import threading

from .agent.graph import MemoryAgent
from .cognition.orchestrator import Cognition
from .config import settings
from .cognition.events import EVENT_TYPES
from .cognition.surface import CognitiveSurface
from .memory.seeds import SEED_MEMORIES
from .memory.service import MemoryService
from .memory.vector_store import VectorStore
from .persistence.db import Database
from .memory.langmem_adapter import LangMemExtractor
from .providers.base import build_provider
from .voice.transcription import Transcriber
from .portability import PortabilityService
from .security.identity import IdentityService
from .security.observability import Metrics, configure_logging
from .security.ratelimit import RateLimiter

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, cfg=settings) -> None:
        self.settings = cfg
        # V8.5: a deployment that asserts PRODUCTION must actually be
        # production-safe. Failing here is deliberate — a silently insecure
        # start would be fake trust.
        if getattr(cfg, "production", False):
            problems = cfg.validate_production()
            if problems:
                raise RuntimeError(
                    "PRODUCTION=1 but the configuration is not production-safe: "
                    + " ".join(problems))
        configure_logging(getattr(cfg, "log_json", False))
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
        self.surface = CognitiveSurface(
            self.cognition.bus, self, self.cognition.surface_lifecycle)
        # V8.4.4 extends the same persistence and EventBus with a user-owned
        # package lifecycle. The service is attached to cognition so the agent
        # tools and ExplanationEngine share one composition root.
        self.portability = PortabilityService(
            self.db, self.cognition.bus, self.settings,
            explanation_engine=self.cognition.explanation_engine)
        self.cognition.portability = self.portability
        # V8.5 production trust. Identity/session/tenant state lives in the
        # SAME database and audits through the SAME EventBus — no parallel
        # identity store, no second audit system. Metrics and the rate
        # limiter are per-runtime so tests are isolated.
        self.identity = IdentityService(self.db, self.cognition.bus, self.settings)
        self.metrics = Metrics()
        self.rate_limiter = RateLimiter(self.settings)
        # v8.2: give the agent its cognitive collaborators now that they exist.
        # Done after construction because Cognition introspects the runtime,
        # which already holds the agent - this breaks the circular dependency
        # without duplicating any state.
        self.agent.recorder = self.cognition.traces
        self.agent.context_builder = self.cognition.context
        self.agent.router = self.cognition.router
        self.agent.policy_engine = self.cognition.policy
        # v8.3.1: the cognitive subsystems become tools the model can call, so
        # missions, world state, history and the rest participate in ordinary
        # conversation instead of living only behind HTTP routes.
        self.agent.cognition = self.cognition
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
        """Restore demo memory and remove user-owned V8.4.1 learned state."""
        user_id = self.settings.demo_user_id
        # Reset predates the cognitive tables. New persisted product state must
        # still honour its user-visible clean-slate contract. Delete dependants
        # first; observation/world state retain their established V8.3 behavior.
        delete_statements = (
            "DELETE FROM explanation_snapshots WHERE user_id = ?",
            "DELETE FROM knowledge_usages WHERE user_id = ?",
            "DELETE FROM abstraction_reputation WHERE user_id = ?",
            "DELETE FROM knowledge_validations WHERE user_id = ?",
            "DELETE FROM knowledge_transitions WHERE user_id = ?",
            "DELETE FROM knowledge_evidence WHERE user_id = ?",
            "DELETE FROM knowledge_items WHERE user_id = ?",
            "DELETE FROM experience_transitions WHERE user_id = ?",
            "DELETE FROM experience_evidence WHERE user_id = ?",
            "DELETE FROM experiences WHERE user_id = ?",
        )
        for statement in delete_statements:
            self.db.execute(statement, (user_id,))
        self.db.execute(
            "DELETE FROM arbitration_records WHERE user_id=? AND ("
            "candidates LIKE ? OR candidates LIKE ?)",
            (user_id, '%\"subject_kind\": \"skill\"%',
             '%\"subject_kind\": \"principle\"%'))
        self.db.execute(
            "DELETE FROM causal_links WHERE user_id=? AND (cause_kind IN "
            "('experience','skill','principle','usage') OR effect_kind IN "
            "('experience','skill','principle','usage'))", (user_id,))
        self.db.execute(
            "DELETE FROM cognitive_events WHERE user_id=? AND subject_kind IN "
            "('experience','skill','principle','usage')", (user_id,))
        self.db.execute(
            "DELETE FROM focus_state WHERE user_id=? AND subject_kind IN "
            "('experience','skill','principle')", (user_id,))
        return self.memory.reset(user_id, SEED_MEMORIES)

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
                "semantic_extraction": self.cognition.meaning.model_compiler.status(),
            },
            # v8.2: capability truth and how each task would actually execute.
            "capabilities": self.cognition.router.report().as_dict(),
            "routing": self.cognition.router.routing_table(),
            # `version` is the established V8.2 API-contract marker retained for
            # backwards compatibility; `release` identifies the running slice.
            "version": "8.2",
            "release": "9.0.1",
            "semantic": {
                "schema": "9.0",
                "object_types": 26,
                "personal_state_versioned": True,
            },
            "portability": {
                "format": "memory-os-export",
                "schema": "9.0",
                "limits": self.portability.limits,
            },
            # V8.5 production-trust surfaces. `security` never contains
            # secrets or cognitive content.
            "security": {
                "auth_mode": self.settings.auth_mode,
                "rate_limiting": self.rate_limiter.state()["enabled"],
                "production_asserted": bool(getattr(self.settings, "production", False)),
            },
        }

    # ------------------------------------------------------------ V8.5 health
    # Liveness, readiness and per-dependency status are DISTINCT questions.
    # A dependency is never reported healthy merely because the process runs:
    # each check below actively exercises the dependency or reports the
    # honest degraded/not-configured state the subsystem itself measured.
    @staticmethod
    def _dep(state: str, detail: str, required: bool) -> dict:
        # state ∈ ACTIVE | DEGRADED | NOT_CONFIGURED | BLOCKED | FAILED
        return {"state": state, "detail": detail, "required": required}

    def dependency_status(self) -> dict[str, dict]:
        deps: dict[str, dict] = {}
        # SQLite: actively probed with a real query.
        try:
            self.db.query_one("SELECT 1 AS ok")
            deps["database"] = self._dep("ACTIVE", "SQLite responding to queries.", True)
        except Exception as exc:
            deps["database"] = self._dep("FAILED", f"SQLite probe failed: {exc}", True)
        # Vector store: Chroma either works or the honest keyword fallback is on.
        if self.vectors.available:
            deps["vector_store"] = self._dep("ACTIVE", "ChromaDB semantic retrieval.", False)
        else:
            deps["vector_store"] = self._dep(
                "DEGRADED", self.vectors.error or "Keyword fallback in use.", False)
        # Model provider: demo mode is NOT_CONFIGURED (deterministic planner),
        # a configured-but-unreachable provider is FAILED.
        ps = self.provider.status()
        if ps.name == "demo":
            deps["model_provider"] = self._dep(
                "NOT_CONFIGURED", "Deterministic demo planner; no LLM configured.", False)
        elif ps.available:
            deps["model_provider"] = self._dep("ACTIVE", f"{ps.name}:{ps.model}", False)
        else:
            deps["model_provider"] = self._dep("FAILED", ps.detail, False)
        # Voice: optional, honest about the browser fallback.
        if self.transcriber.available:
            deps["voice"] = self._dep("ACTIVE", self.transcriber.detail, False)
        else:
            deps["voice"] = self._dep("NOT_CONFIGURED", self.transcriber.detail, False)
        # LangMem: optional extraction layer.
        lm = self.langmem.status().as_dict()
        deps["langmem"] = self._dep(
            "ACTIVE" if lm.get("available") else "NOT_CONFIGURED",
            str(lm.get("detail", ""))[:200], False)
        # Checkpointer: sqlite is durable; memory means degraded persistence.
        deps["checkpointer"] = (
            self._dep("ACTIVE", "SQLite-backed LangGraph checkpoints.", False)
            if self.checkpoint_backend == "sqlite"
            else self._dep("DEGRADED", "In-memory checkpoints; no cross-restart persistence.", False))
        return deps

    def readiness(self) -> dict:
        deps = self.dependency_status()
        required_failed = [n for n, d in deps.items() if d["required"] and d["state"] == "FAILED"]
        degraded = [n for n, d in deps.items() if d["state"] in ("DEGRADED", "FAILED")]
        status = "ready" if not required_failed else "not_ready"
        return {
            "status": status,
            "degraded_capabilities": degraded,
            "dependencies": deps,
            "auth_mode": self.settings.auth_mode,
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
