"""ChromaDB-backed vector store with honest keyword fallback.

Semantic mode uses a real local ONNX MiniLM embedding model (384-dim), bundled
with chromadb. No paid API is involved. If the model cannot be initialised the
store degrades to KEYWORD mode and reports that truthfully.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9']+")
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "am", "are", "i",
    "my", "me", "you", "your", "do", "does", "what", "about", "for", "on",
    "it", "that", "this", "with", "have", "has", "was", "were", "be", "been",
}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOP and len(t) > 1]


def keyword_score(query: str, content: str) -> float:
    """Deterministic token-overlap score in [0, 1] with a phrase bonus."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    c = set(tokenize(content))
    if not c:
        return 0.0
    overlap = len(q & c) / len(q)
    phrase = 0.25 if query.strip().lower() in content.lower() else 0.0
    return min(1.0, overlap + phrase)


@dataclass
class VectorHit:
    memory_id: str
    score: float          # normalised similarity in [0, 1]
    document: str
    metadata: dict[str, Any]


class VectorStore:
    """Persistent Chroma collection, one per process, lazily initialised."""

    def __init__(self, path, collection_name: str = "memories", disable_embeddings: bool = False):
        self._path = str(path)
        self._name = collection_name
        self._lock = threading.Lock()
        self._client = None
        self._collection = None
        self.mode = "keyword"
        self.embedding_model: str | None = None
        self.embedding_dim: int | None = None
        self._error: str | None = None
        if not disable_embeddings:
            self._init_chroma()

    # ------------------------------------------------------------------ setup
    def _init_chroma(self, attempts: int = 3) -> None:
        """
        Bring up Chroma + local embeddings.

        Chroma's client construction is not fully thread-safe on a cold cache:
        two near-simultaneous first clients can race and raise a KeyError on the
        persist path or an AttributeError from the Rust bindings. That is a
        transient startup race, not a genuine "embeddings unavailable", so we
        retry briefly before honestly downgrading to the keyword fallback.
        """
        for attempt in range(attempts):
            self._try_init_chroma()
            if self.available:
                self._error = None
                return
            if attempt < attempts - 1:
                time.sleep(0.35 * (attempt + 1))
        log.warning("Chroma/embeddings unavailable after %d attempts, keyword "
                    "fallback: %s", attempts, self._error)

    def _try_init_chroma(self) -> None:
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
            from chromadb.utils import embedding_functions

            ef = embedding_functions.ONNXMiniLM_L6_V2()
            probe = ef(["memory os embedding probe"])
            self.embedding_dim = len(probe[0])
            self._client = chromadb.PersistentClient(
                path=self._path,
                settings=ChromaSettings(anonymized_telemetry=False, allow_reset=True),
            )
            self._collection = self._client.get_or_create_collection(
                name=self._name,
                embedding_function=ef,
                metadata={"hnsw:space": "cosine"},
            )
            self.mode = "semantic"
            self.embedding_model = "all-MiniLM-L6-v2 (local ONNX)"
        except Exception as exc:  # pragma: no cover - environment dependent
            self._error = f"{type(exc).__name__}: {exc}"
            self.mode = "keyword"
            self._client = None
            self._collection = None

    @property
    def available(self) -> bool:
        return self._collection is not None

    @property
    def error(self) -> str | None:
        return self._error

    def embed(self, text: str) -> list[float] | None:
        """Expose a real embedding vector (used by tests and the UI status)."""
        if not self.available:
            return None
        ef = self._collection._embedding_function  # noqa: SLF001 - chroma API
        return list(ef([text])[0])

    # ------------------------------------------------------------- operations
    def upsert(self, memory_id: str, content: str, metadata: dict[str, Any]) -> None:
        if not self.available:
            return
        clean = {k: v for k, v in metadata.items() if isinstance(v, (str, int, float, bool))}
        with self._lock:
            self._collection.upsert(ids=[memory_id], documents=[content], metadatas=[clean])

    def delete(self, memory_id: str) -> None:
        if not self.available:
            return
        with self._lock:
            self._collection.delete(ids=[memory_id])

    def get(self, memory_id: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        res = self._collection.get(ids=[memory_id])
        if not res["ids"]:
            return None
        return {"id": res["ids"][0], "document": res["documents"][0], "metadata": res["metadatas"][0]}

    def count(self) -> int:
        if not self.available:
            return 0
        return int(self._collection.count())

    def query(self, text: str, user_id: str, top_k: int = 8) -> list[VectorHit]:
        if not self.available:
            return []
        with self._lock:
            n = max(1, min(top_k, max(1, self._collection.count())))
            if self._collection.count() == 0:
                return []
            res = self._collection.query(
                query_texts=[text],
                n_results=n,
                where={"user_id": user_id},
            )
        hits: list[VectorHit] = []
        ids = res.get("ids", [[]])[0]
        docs = res.get("documents", [[]])[0]
        metas = res.get("metadatas", [[]])[0]
        dists = res.get("distances", [[]])[0]
        for i, mid in enumerate(ids):
            # Chroma cosine distance = 1 - cosine_similarity, range [0, 2].
            # Keep the raw cosine similarity so unrelated text scores near 0
            # instead of being squashed toward the middle of the range.
            sim = max(0.0, 1.0 - float(dists[i])) if i < len(dists) else 0.0
            hits.append(VectorHit(mid, sim, docs[i], metas[i] or {}))
        return hits

    def reset(self) -> None:
        if not self.available:
            return
        with self._lock:
            try:
                self._client.delete_collection(self._name)
            except Exception:
                pass
            from chromadb.utils import embedding_functions
            self._collection = self._client.get_or_create_collection(
                name=self._name,
                embedding_function=embedding_functions.ONNXMiniLM_L6_V2(),
                metadata={"hnsw:space": "cosine"},
            )

    def close(self) -> None:
        """
        Release the Chroma client and its embedding session.

        Chroma keeps a process-wide cache of PersistentClient instances plus an
        ONNX runtime session per client. Without this, long test runs that build
        many isolated Runtimes accumulate them until the process is OOM-killed.
        """
        try:
            if self._client is not None:
                # Drop this path from Chroma's shared client registry.
                import chromadb
                reset = getattr(chromadb.api.shared_system_client.SharedSystemClient,
                                "_identifier_to_system", None)
                if isinstance(reset, dict):
                    reset.pop(self._path, None)
                stop = getattr(self._client, "stop", None)
                if callable(stop):
                    stop()
        except Exception:  # pragma: no cover - best-effort teardown
            pass
        finally:
            self._collection = None
            self._client = None
