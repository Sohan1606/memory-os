"""
Living world model: goals, projects, commitments, people, risks, resources and
the dependencies between them.

Entities are derived from real conversation and carry honest confidence. Nothing
here is seeded with invented business data.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

KINDS = ("goal", "project", "commitment", "person", "risk", "resource", "constraint")
STATES = ("active", "at_risk", "blocked", "completed", "abandoned", "dormant")

# Deliberately conservative patterns: we would rather miss an entity than
# fabricate one. Anything matched still goes in with modest confidence.
_GOAL = re.compile(
    r"\b(?:i (?:want|need|aim|plan|intend|hope) to|my goal is to|i'm trying to|"
    r"i am trying to|working towards)\s+(.{4,90})", re.I)
_PROJECT = re.compile(
    r"\b(?:working on|building|shipping|launching|maintaining)\s+(?:a |an |the |my )?"
    r"(.{3,70})", re.I)
_COMMITMENT = re.compile(
    r"\b(?:i(?:'| a)?m going to|i will|i have to|i must|i need to|due|deadline|"
    r"by (?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|tomorrow|"
    r"next week|the end of))\b(.{0,80})", re.I)
_RISK = re.compile(
    r"\b(?:worried|concerned|risk|blocked|blocker|stuck|behind schedule|"
    r"might not|won't make|slipping)\b(.{0,80})", re.I)

_STOP_TAIL = re.compile(r"[.!?].*$", re.S)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: str) -> str:
    text = _STOP_TAIL.sub("", text).strip(" ,;:-—")
    return re.sub(r"\s+", " ", text)


class WorldModel:
    """Persistent, evented world state. Every mutation emits a cognitive event."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------- mutation
    def upsert(self, user_id: str, kind: str, label: str, *, state: str = "active",
               detail: str | None = None, confidence: float = 0.6,
               due_at: str | None = None, source: str = "conversation",
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Create an entity, or update the existing one with the same kind+label.

        Matching is case-insensitive on the label so repeated mentions reinforce
        one entity instead of spawning near-duplicates.
        """
        if kind not in KINDS:
            raise ValueError(f"Unknown world entity kind: {kind!r}")
        label = _clean(label)[:120]
        if len(label) < 3:
            raise ValueError("World entity label is too short to be meaningful.")

        existing = self.db.query_one(
            "SELECT * FROM world_entities WHERE user_id=? AND kind=?"
            " AND lower(label)=lower(?)", (user_id, kind, label))

        if existing:
            new_conf = min(0.97, max(float(existing["confidence"]), confidence) + 0.05)
            self.db.execute(
                "UPDATE world_entities SET state=?, detail=COALESCE(?, detail),"
                " confidence=?, due_at=COALESCE(?, due_at), updated_at=? WHERE id=?",
                (state, detail, new_conf, due_at, _now(), existing["id"]))
            entity = self.get(existing["id"])
            assert entity is not None
            self.bus.emit(user_id, "world.updated",
                          f"Updated {kind}: {label}", subject_kind="world",
                          subject_id=entity["id"], correlation_id=correlation_id,
                          payload={"kind": kind, "state": state, "confidence": new_conf})
            return entity

        entity_id = f"w_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO world_entities (id,user_id,kind,label,state,detail,confidence,"
            "due_at,source,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (entity_id, user_id, kind, label, state, detail, confidence, due_at,
             source, now, now))

        event = "goal.created" if kind == "goal" else (
            "commitment.created" if kind == "commitment" else "world.created")
        self.bus.emit(user_id, event, f"Tracking {kind}: {label}",
                      subject_kind="world", subject_id=entity_id,
                      correlation_id=correlation_id,
                      payload={"kind": kind, "confidence": confidence})
        entity = self.get(entity_id)
        assert entity is not None
        return entity

    def set_state(self, user_id: str, entity_id: str, state: str,
                  reason: str | None = None,
                  correlation_id: str | None = None) -> dict[str, Any] | None:
        if state not in STATES:
            raise ValueError(f"Unknown world state: {state!r}")
        row = self.db.query_one(
            "SELECT * FROM world_entities WHERE id=? AND user_id=?", (entity_id, user_id))
        if row is None:
            return None
        self.db.execute("UPDATE world_entities SET state=?, updated_at=? WHERE id=?",
                        (state, _now(), entity_id))

        kind = row["kind"]
        if kind == "goal":
            event = {"completed": "goal.completed", "abandoned": "goal.abandoned",
                     "active": "goal.reactivated"}.get(state, "goal.updated")
        elif kind == "commitment":
            event = {"completed": "commitment.completed"}.get(state, "commitment.updated")
        else:
            event = "world.updated"

        self.bus.emit(user_id, event, f"{row['label']} → {state}",
                      subject_kind="world", subject_id=entity_id,
                      correlation_id=correlation_id,
                      payload={"state": state, "reason": reason})
        return self.get(entity_id)

    def link(self, source_id: str, target_id: str, kind: str = "relates_to") -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO world_links (source_id,target_id,kind) VALUES (?,?,?)",
            (source_id, target_id, kind))

    # ----------------------------------------------------------------- reads
    def get(self, entity_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM world_entities WHERE id=?", (entity_id,))
        return dict(row) if row else None

    def list(self, user_id: str, kind: str | None = None,
             state: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM world_entities WHERE user_id=?"
        params: list[Any] = [user_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if state:
            sql += " AND state=?"
            params.append(state)
        sql += " ORDER BY datetime(updated_at) DESC"
        return [dict(r) for r in self.db.query(sql, params)]

    def graph(self, user_id: str) -> dict[str, Any]:
        nodes = self.list(user_id)
        ids = {n["id"] for n in nodes}
        edges = [dict(r) for r in self.db.query(
            "SELECT * FROM world_links WHERE source_id IN"
            f" ({','.join('?' * len(ids))})", list(ids))] if ids else []
        edges = [e for e in edges if e["target_id"] in ids]
        return {"nodes": nodes, "edges": edges}

    def delete(self, user_id: str, entity_id: str) -> bool:
        row = self.db.query_one(
            "SELECT * FROM world_entities WHERE id=? AND user_id=?", (entity_id, user_id))
        if row is None:
            return False
        self.db.execute("DELETE FROM world_entities WHERE id=?", (entity_id,))
        self.db.execute("DELETE FROM world_links WHERE source_id=? OR target_id=?",
                        (entity_id, entity_id))
        self.bus.emit(user_id, "world.corrected", f"Removed {row['kind']}: {row['label']}",
                      subject_kind="world", subject_id=entity_id)
        return True

    # ------------------------------------------------------------ extraction
    def observe(self, user_id: str, text: str,
                correlation_id: str | None = None) -> list[dict[str, Any]]:
        """
        Derive world entities from one user utterance.

        Conservative by design: questions never create entities, and each match
        enters with modest confidence that only grows through repetition.
        """
        found: list[dict[str, Any]] = []
        stripped = text.strip()
        if not stripped or stripped.endswith("?"):
            return found

        def add(kind: str, raw: str, confidence: float, state: str = "active") -> None:
            label = _clean(raw)
            if len(label) < 4 or len(label.split()) > 14:
                return
            if any(f["label"].lower() == label.lower() and f["kind"] == kind
                   for f in found):
                return
            try:
                found.append(self.upsert(user_id, kind, label, state=state,
                                         confidence=confidence,
                                         correlation_id=correlation_id))
            except ValueError:
                pass

        for m in _GOAL.finditer(stripped):
            add("goal", m.group(1), 0.6)
        for m in _PROJECT.finditer(stripped):
            add("project", m.group(1), 0.55)
        if _RISK.search(stripped):
            add("risk", stripped[:80], 0.5, state="at_risk")
        if _COMMITMENT.search(stripped) and len(stripped) < 200:
            add("commitment", stripped[:80], 0.5)
        return found

    def due_soon(self, user_id: str, days: int = 7) -> list[dict[str, Any]]:
        """Commitments/goals with a due date inside the window - drives risk."""
        horizon = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        return [dict(r) for r in self.db.query(
            "SELECT * FROM world_entities WHERE user_id=? AND due_at IS NOT NULL"
            " AND due_at <= ? AND state NOT IN ('completed','abandoned')"
            " ORDER BY due_at ASC", (user_id, horizon))]

    def summary(self, user_id: str) -> dict[str, Any]:
        rows = self.db.query(
            "SELECT kind, state, COUNT(*) AS n FROM world_entities WHERE user_id=?"
            " GROUP BY kind, state", (user_id,))
        by_kind: dict[str, dict[str, int]] = {}
        total = 0
        for r in rows:
            by_kind.setdefault(r["kind"], {})[r["state"]] = r["n"]
            total += r["n"]
        return {"total": total, "by_kind": by_kind,
                "at_risk": len(self.list(user_id, state="at_risk"))}
