"""MemoryService — the single source of truth for long-term memory.

Coordinates SQLite (metadata, versions, relationships, audit events) and
ChromaDB (vectors). Every mutation path in the product - chat, tools, voice,
final CTA, the memory console - flows through this one service.
"""
from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from ..config import settings
from ..persistence.db import Database
from . import policy
from .models import Memory, RetrievalResult
from .vector_store import VectorStore, keyword_score, tokenize

# Query words that signal the user is asking about a specific memory category.
CATEGORY_INTENT: dict[str, tuple[str, ...]] = {
    "PROJECT": ("project", "projects", "building", "build", "working", "app", "system"),
    "PREFERENCE": ("prefer", "preference", "preferences", "like", "likes", "favourite", "favorite"),
    "COMMUNICATION_STYLE": ("explain", "explanation", "explanations", "style", "answer",
                            "answers", "concise", "detail", "tone", "communicate"),
    "GOAL": ("goal", "goals", "learning", "learn", "aim", "aiming", "exploring", "certification"),
    "IDENTITY": ("who", "name", "role", "job", "identity", "timezone", "live"),
    "HABIT": ("habit", "habits", "routine", "usually", "daily", "every day"),
    "RELATIONSHIP": ("team", "colleague", "manager", "partner", "friend", "who i work"),
    "FACT": ("use", "uses", "using", "stack", "language", "tool", "tools"),
    "CONTEXT": (),
}


def detect_category_intent(query: str) -> set[str]:
    """Return categories the query is explicitly asking about."""
    low = query.lower()
    hits = set()
    for cat, words in CATEGORY_INTENT.items():
        if any(w in low for w in words):
            hits.add(cat)
    return hits


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.now(timezone.utc)


class MemoryService:
    def __init__(self, db: Database, vectors: VectorStore) -> None:
        self.db = db
        self.vectors = vectors

    # --------------------------------------------------------------- helpers
    def _relations(self, memory_id: str) -> list[str]:
        rows = self.db.query(
            "SELECT target_id FROM memory_relationships WHERE source_id = ?", (memory_id,)
        )
        return [r["target_id"] for r in rows]

    def _event(self, user_id: str, memory_id: str | None, event_type: str,
               source: str = "system", metadata: dict[str, Any] | None = None) -> None:
        self.db.execute(
            "INSERT INTO memory_events (user_id, memory_id, event_type, source, metadata, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, memory_id, event_type, source, json.dumps(metadata or {}), _now()),
        )

    def _sync_vector(self, m: Memory) -> None:
        self.vectors.upsert(m.id, m.content, {
            "user_id": m.user_id, "category": m.category, "importance": m.importance,
            "confidence": m.confidence, "status": m.status, "version": m.version,
            "updated_at": m.updated_at,
        })

    def _link(self, a: str, b: str) -> None:
        if a == b:
            return
        for s, t in ((a, b), (b, a)):
            self.db.execute(
                "INSERT OR IGNORE INTO memory_relationships (source_id, target_id, kind)"
                " VALUES (?, ?, 'related')", (s, t))

    def _auto_relate(self, memory: Memory, limit: int = 3) -> None:
        """Link a new memory to its nearest existing neighbours (real similarity)."""
        candidates = self.search(memory.user_id, memory.content, top_k=limit + 1,
                                 include_weak=True, record_event=False)
        for res in candidates:
            if res.memory.id == memory.id:
                continue
            if res.score >= 0.30:
                self._link(memory.id, res.memory.id)
            if len(self._relations(memory.id)) >= limit:
                break

    # ----------------------------------------------------------------- reads
    def get(self, memory_id: str) -> Memory | None:
        row = self.db.query_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        if not row:
            return None
        return Memory.from_row(row, self._relations(memory_id))

    def list(self, user_id: str, category: str | None = None,
             status: str = "active", query: str | None = None) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE user_id = ?"
        params: list[Any] = [user_id]
        if status != "all":
            sql += " AND status = ?"
            params.append(status)
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY datetime(updated_at) DESC"
        rows = self.db.query(sql, params)
        mems = [Memory.from_row(r, self._relations(r["id"])) for r in rows]
        if query:
            q = query.lower().strip()
            mems = [m for m in mems if q in m.content.lower() or q in m.category.lower()]
        return mems

    def versions(self, memory_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT version, content, category, reason, created_at FROM memory_versions"
            " WHERE memory_id = ? ORDER BY version ASC", (memory_id,))
        return [dict(r) for r in rows]

    def events(self, user_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id, memory_id, event_type, source, metadata, created_at FROM memory_events"
            " WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["metadata"] = json.loads(d["metadata"] or "{}")
            except json.JSONDecodeError:
                d["metadata"] = {}
            out.append(d)
        return out

    def graph(self, user_id: str) -> dict[str, Any]:
        mems = self.list(user_id)
        ids = {m.id for m in mems}
        edges, seen = [], set()
        for m in mems:
            for target in m.related_memory_ids:
                if target not in ids:          # never emit dangling edges
                    continue
                key = tuple(sorted((m.id, target)))
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"source": key[0], "target": key[1]})
        return {"nodes": [m.to_dict() for m in mems], "edges": edges}

    # --------------------------------------------------------------- retrieval
    def search(self, user_id: str, query: str, top_k: int | None = None,
               include_weak: bool = True, record_event: bool = True,
               category: str | None = None) -> list[RetrievalResult]:
        query = (query or "").strip()
        if not query:
            return []
        top_k = top_k or settings.retrieval_top_k
        pool = self.list(user_id, category=category)
        if not pool:
            return []
        by_id = {m.id: m for m in pool}

        semantic: dict[str, float] = {}
        if self.vectors.available:
            for hit in self.vectors.query(query, user_id, top_k=max(top_k * 3, 10)):
                if hit.memory_id in by_id:
                    semantic[hit.memory_id] = hit.score

        now = datetime.now(timezone.utc)
        q_tokens = set(tokenize(query))
        intent = detect_category_intent(query)
        results: list[RetrievalResult] = []
        for m in pool:
            sem = semantic.get(m.id, 0.0)
            kw = keyword_score(query, m.content)
            age_days = max(0.0, (now - _parse(m.updated_at)).total_seconds() / 86400)
            recency = math.exp(-age_days / 45.0)
            cat_match = 1.0 if m.category in intent else 0.0
            score = (settings.w_semantic * sem + settings.w_keyword * kw +
                     settings.w_category * cat_match +
                     settings.w_importance * m.importance +
                     settings.w_confidence * m.confidence +
                     settings.w_recency * recency)

            # Require genuine query overlap: importance alone must never surface
            # an unrelated memory.
            if sem < settings.min_semantic and kw <= 0.0 and not cat_match:
                continue

            reasons: list[str] = []
            if sem >= 0.5:
                reasons.append(f"Semantic similarity {sem:.2f}")
            elif sem > 0:
                reasons.append(f"Weak semantic similarity {sem:.2f}")
            shared = q_tokens & set(tokenize(m.content))
            if shared:
                reasons.append("Shared terms: " + ", ".join(sorted(shared)[:4]))
            if query.lower() in m.content.lower():
                reasons.append("Exact phrase match")
            if cat_match:
                reasons.append(f"Category intent: {m.category.replace('_', ' ').title()}")
            if m.importance >= 0.8:
                reasons.append("High importance memory")
            if recency > 0.8:
                reasons.append("Recently updated")

            strength = "strong" if score >= settings.strong_match_threshold else "weak"
            if strength == "weak" and score < settings.weak_match_threshold:
                continue
            if strength == "weak" and not include_weak:
                continue
            results.append(RetrievalResult(m, score, sem, kw, reasons or ["Partial term overlap"], strength))

        results.sort(key=lambda r: r.score, reverse=True)
        results = results[:top_k]
        if record_event:
            for r in results:
                self._event(user_id, r.memory.id, "MEMORY_RETRIEVED", "retrieval",
                            {"query": query, "score": round(r.score, 4)})
        return results

    def retrieval_path(self, user_id: str, query: str,
                       results: list[RetrievalResult]) -> list[dict[str, Any]]:
        """Build a path that only ever references memories that exist now."""
        path: list[dict[str, Any]] = [{"kind": "query", "label": query}]
        seen: set[str] = set()
        for res in results[:3]:
            path.append({"kind": "memory", "id": res.memory.id,
                         "label": res.memory.content, "category": res.memory.category,
                         "score": round(res.score, 4)})
            seen.add(res.memory.id)
        for res in results[:2]:
            for rid in res.memory.related_memory_ids:
                if rid in seen:
                    continue
                rel = self.get(rid)
                if rel is None or rel.status != "active":
                    continue
                path.append({"kind": "related", "id": rel.id, "label": rel.content,
                             "category": rel.category, "via": res.memory.id})
                seen.add(rid)
                break
        if len(results) > 0:
            path.append({"kind": "context", "label": f"{len(seen)} memories assembled into context"})
            path.append({"kind": "response", "label": "Adapted response"})
        return path

    # --------------------------------------------------------------- mutation
    def find_duplicate(self, user_id: str, content: str) -> tuple[Memory, float] | None:
        best: tuple[Memory, float] | None = None
        for res in self.search(user_id, content, top_k=5, record_event=False):
            sim = max(res.semantic, keyword_score(content, res.memory.content))
            if best is None or sim > best[1]:
                best = (res.memory, sim)
        if best and best[1] >= settings.duplicate_threshold:
            return best
        return None

    def find_conflict(self, user_id: str, content: str, category: str) -> tuple[Memory, float] | None:
        """Find a memory that the new statement likely supersedes.

        A conflict needs topical closeness AND a contradiction signal. When the
        new statement carries an explicit contradiction (an antonym pair such as
        dark/light, or a "switched to" phrasing) a lower similarity bar applies,
        because the wording of a correction often differs from the original.
        """
        same_cat = [m for m in self.list(user_id) if m.category == category]
        pool = self.list(user_id) if policy.looks_like_change(content) else same_cat
        if not pool:
            return None

        vec_scores: dict[str, float] = {}
        if self.vectors.available:
            for hit in self.vectors.query(content, user_id, top_k=15):
                vec_scores[hit.memory_id] = hit.score

        best: tuple[Memory, float] | None = None
        for m in pool:
            sim = max(vec_scores.get(m.id, 0.0), keyword_score(content, m.content))
            if sim >= settings.duplicate_threshold:
                continue  # that is a duplicate, handled separately
            contradicts = self._contradicts(m.content, content)
            floor = settings.contradiction_threshold if contradicts else settings.conflict_threshold
            if sim >= floor and (contradicts or m.category == category):
                if best is None or sim > best[1]:
                    best = (m, sim)
        return best

    def create(self, user_id: str, content: str, category: str | None = None,
               importance: float = 0.7, confidence: float = 0.8,
               source: str = "conversation", thread_id: str | None = None,
               allow_duplicate: bool = False, auto_relate: bool = True) -> dict[str, Any]:
        """Create / reinforce / update-on-conflict. Returns the action taken."""
        content = " ".join((content or "").strip().split())
        if not content:
            raise ValueError("Memory content must not be empty.")
        category = (category or policy.classify(content)).upper()

        if not allow_duplicate:
            dup = self.find_duplicate(user_id, content)
            if dup:
                mem, sim = dup
                return {"action": "reinforced", "memory": self.reinforce(mem.id).to_dict(),
                        "similarity": round(sim, 4)}
            conflict = self.find_conflict(user_id, content, category)
            if conflict is not None and (
                policy.looks_like_change(content)
                or self._contradicts(conflict[0].content, content)
            ):
                mem, sim = conflict
                updated = self.update(mem.id, content=content,
                                      reason="Superseded by newer conflicting signal",
                                      event_type="MEMORY_SUPERSEDED")
                return {"action": "updated", "memory": updated.to_dict(),
                        "previous": mem.to_dict(), "similarity": round(sim, 4),
                        "conflict": True}

        now = _now()
        mem = Memory(id=f"mem_{uuid.uuid4().hex[:12]}", user_id=user_id, content=content,
                     category=category, importance=importance, confidence=confidence,
                     source=source, thread_id=thread_id, created_at=now, updated_at=now)
        self.db.execute(
            "INSERT INTO memories (id, user_id, content, category, importance, confidence,"
            " status, version, source, thread_id, reinforcement_count, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mem.id, mem.user_id, mem.content, mem.category, mem.importance, mem.confidence,
             mem.status, mem.version, mem.source, mem.thread_id, 0, now, now))
        self.db.execute(
            "INSERT INTO memory_versions (memory_id, version, content, category, reason, created_at)"
            " VALUES (?,?,?,?,?,?)", (mem.id, 1, mem.content, mem.category, "Initial memory", now))
        self._sync_vector(mem)
        self._event(user_id, mem.id, "MEMORY_CREATED", source, {"category": mem.category})
        if auto_relate:
            self._auto_relate(mem)
        return {"action": "created", "memory": self.get(mem.id).to_dict()}

    @staticmethod
    def _contradicts(old: str, new: str) -> bool:
        """Detect opposing values inside the same preference dimension."""
        pairs = [("dark", "light"), ("concise", "detailed"), ("brief", "verbose"),
                 ("morning", "evening"), ("remote", "office"), ("python", "typescript")]
        o, n = old.lower(), new.lower()
        for a, b in pairs:
            if (a in o and b in n) or (b in o and a in n):
                return True
        return policy.looks_like_change(new)

    def reinforce(self, memory_id: str) -> Memory:
        mem = self.get(memory_id)
        if not mem:
            raise KeyError(memory_id)
        now = _now()
        self.db.execute(
            "UPDATE memories SET reinforcement_count = reinforcement_count + 1,"
            " confidence = MIN(1.0, confidence + 0.05), updated_at = ? WHERE id = ?", (now, memory_id))
        updated = self.get(memory_id)
        self._sync_vector(updated)
        self._event(mem.user_id, memory_id, "MEMORY_REINFORCED", "memory-service",
                    {"reinforcement_count": updated.reinforcement_count})
        return updated

    def update(self, memory_id: str, content: str | None = None, category: str | None = None,
               importance: float | None = None, confidence: float | None = None,
               status: str | None = None,
               reason: str = "Manual edit", event_type: str = "MEMORY_UPDATED") -> Memory:
        mem = self.get(memory_id)
        if not mem:
            raise KeyError(memory_id)
        new_content = " ".join(content.strip().split()) if content else mem.content
        if content is not None and not new_content:
            raise ValueError("Memory content must not be empty.")
        new_category = (category or mem.category).upper()
        content_changed = new_content != mem.content or new_category != mem.category
        version = mem.version + 1 if content_changed else mem.version
        now = _now()
        self.db.execute(
            "UPDATE memories SET content=?, category=?, importance=?, confidence=?,"
            " status=?, version=?, updated_at=? WHERE id=?",
            (new_content, new_category,
             mem.importance if importance is None else importance,
             mem.confidence if confidence is None else confidence,
             mem.status if status is None else status,
             version, now, memory_id))
        if content_changed:
            self.db.execute(
                "INSERT INTO memory_versions (memory_id, version, content, category, reason, created_at)"
                " VALUES (?,?,?,?,?,?)", (memory_id, version, new_content, new_category, reason, now))
        updated = self.get(memory_id)
        self._sync_vector(updated)
        self._event(mem.user_id, memory_id, event_type, "memory-service",
                    {"reason": reason, "version": version,
                     "previous_content": mem.content if content_changed else None})
        return updated

    def set_importance(self, memory_id: str, important: bool) -> Memory:
        return self.update(memory_id, importance=0.95 if important else 0.5,
                           reason="Importance toggled")

    def delete(self, memory_id: str) -> bool:
        mem = self.get(memory_id)
        if not mem:
            return False
        self.db.execute("DELETE FROM memory_relationships WHERE source_id=? OR target_id=?",
                        (memory_id, memory_id))
        self.db.execute("DELETE FROM memory_versions WHERE memory_id=?", (memory_id,))
        self.db.execute("DELETE FROM memories WHERE id=?", (memory_id,))
        self.vectors.delete(memory_id)
        self._event(mem.user_id, memory_id, "MEMORY_DELETED", "memory-service",
                    {"content": mem.content})
        return True

    def consolidate(self, user_id: str, memory_ids: list[str],
                    content: str | None = None) -> dict[str, Any]:
        sources = [m for m in (self.get(i) for i in memory_ids) if m and m.user_id == user_id]
        if len(sources) < 2:
            raise ValueError("Consolidation requires at least two existing memories.")
        if content is None:
            fragments = [s.content.rstrip(".") for s in sources]
            content = "; ".join(fragments) + "."
        category = max({s.category for s in sources},
                       key=lambda c: sum(1 for s in sources if s.category == c))
        created = self.create(user_id, content, category=category,
                              importance=max(s.importance for s in sources),
                              confidence=min(1.0, max(s.confidence for s in sources) + 0.05),
                              source="consolidation", allow_duplicate=True, auto_relate=False)
        new_id = created["memory"]["id"]
        for s in sources:
            self._link(new_id, s.id)
            self.db.execute("UPDATE memories SET status='superseded', updated_at=? WHERE id=?",
                            (_now(), s.id))
            sup = self.get(s.id)
            self._sync_vector(sup)
            self._event(user_id, s.id, "MEMORY_SUPERSEDED", "consolidation",
                        {"consolidated_into": new_id})
        self._event(user_id, new_id, "MEMORY_CONSOLIDATED", "consolidation",
                    {"source_memory_ids": [s.id for s in sources]})
        return {"memory": self.get(new_id).to_dict(),
                "source_memory_ids": [s.id for s in sources]}

    # ------------------------------------------------------------ bulk admin
    def export(self, user_id: str) -> dict[str, Any]:
        mems = self.list(user_id, status="all")
        return {
            "version": 1,
            "exported_at": _now(),
            "user_id": user_id,
            "memories": [m.to_dict() for m in mems],
            "versions": {m.id: self.versions(m.id) for m in mems},
            "events": self.events(user_id, limit=1000),
        }

    def delete_all(self, user_id: str) -> int:
        mems = self.list(user_id, status="all")
        for m in mems:
            self.delete(m.id)
        return len(mems)

    def reset(self, user_id: str, seeds: Iterable[dict[str, Any]]) -> int:
        self.delete_all(user_id)
        self.db.execute("DELETE FROM memory_events WHERE user_id = ?", (user_id,))
        self.db.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
        self.db.execute("DELETE FROM conversations WHERE user_id = ?", (user_id,))
        return self.seed(user_id, seeds)

    def seed(self, user_id: str, seeds: Iterable[dict[str, Any]]) -> int:
        seeds = list(seeds)
        id_map: dict[str, str] = {}
        now = datetime.now(timezone.utc)
        for s in seeds:
            created = (now - timedelta(days=s.get("days_ago", 10))).isoformat(timespec="seconds")
            mem = Memory(
                id=f"mem_{uuid.uuid4().hex[:12]}", user_id=user_id, content=s["content"],
                category=s["category"], importance=s.get("importance", 0.7),
                confidence=s.get("confidence", 0.8), source=s.get("source", "seed"),
                created_at=created, updated_at=created)
            self.db.execute(
                "INSERT INTO memories (id, user_id, content, category, importance, confidence,"
                " status, version, source, thread_id, reinforcement_count, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mem.id, user_id, mem.content, mem.category, mem.importance, mem.confidence,
                 "active", 1, mem.source, None, 0, created, created))
            self.db.execute(
                "INSERT INTO memory_versions (memory_id, version, content, category, reason, created_at)"
                " VALUES (?,?,?,?,?,?)", (mem.id, 1, mem.content, mem.category, "Seeded memory", created))
            self._sync_vector(mem)
            self._event(user_id, mem.id, "MEMORY_CREATED", "seed", {"category": mem.category})
            id_map[s["key"]] = mem.id
        for s in seeds:
            for rel in s.get("related", []):
                if rel in id_map:
                    self._link(id_map[s["key"]], id_map[rel])
        return len(seeds)

    def stats(self, user_id: str) -> dict[str, Any]:
        mems = self.list(user_id)
        by_cat: dict[str, int] = {}
        for m in mems:
            by_cat[m.category] = by_cat.get(m.category, 0) + 1
        return {
            "total": len(mems),
            "by_category": by_cat,
            "vector_count": self.vectors.count(),
            "retrieval_mode": self.vectors.mode,
        }
