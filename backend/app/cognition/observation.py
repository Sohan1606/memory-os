"""
Canonical observations (§17).

An observation is *evidence that something was noticed*. It is deliberately NOT
a memory. Memories are curated, durable beliefs about the user; observations are
raw, timestamped, provenance-carrying records of what actually reached the
system. Promoting an observation to a memory is an explicit, separate act.

    perception / outcome / world change / user statement
                          │
                          ▼
                    Observation  ──(explicit promotion only)──> Memory

Honesty rules enforced here:
  * `epistemic_status` is one of OBSERVED / INFERRED / PREDICTED / SIMULATED /
    UNKNOWN and is never silently upgraded. An inference does not become an
    observation because it was stored.
  * Absence of evidence is never recorded as evidence. There is no API here
    that turns "the user said nothing" into an observation.
  * Every observation carries where it came from, so a later belief can always
    be traced back to something that genuinely happened.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

# The five epistemic states the whole system shares (§39).
EPISTEMIC = ("OBSERVED", "INFERRED", "PREDICTED", "SIMULATED", "UNKNOWN")

# Where an observation physically came from.
SOURCES = ("conversation", "perception", "document", "outcome", "world",
           "mission", "background", "user_report", "system")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ObservationLog:
    """Append-only evidence log. Never mutated in place, only superseded."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------- recording
    def record(self, user_id: str, content: str, *, source: str,
               origin: str, epistemic_status: str = "OBSERVED",
               confidence: float = 0.6, scope: str | None = None,
               provenance: dict[str, Any] | None = None,
               subject_kind: str | None = None, subject_id: str | None = None,
               observed_at: str | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Record one piece of evidence.

        `origin` is the concrete thing it came from - a filename, a message id,
        a prediction id - so the trail is reconstructable. `source` is the
        category. Both are required: an observation with no traceable origin is
        not an observation.
        """
        if epistemic_status not in EPISTEMIC:
            raise ValueError(f"Unknown epistemic status: {epistemic_status!r}")
        if source not in SOURCES:
            raise ValueError(f"Unknown observation source: {source!r}")
        content = (content or "").strip()
        if not content:
            raise ValueError("An observation must have content.")
        if not (origin or "").strip():
            raise ValueError("An observation must record its origin.")

        oid = f"obs_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO observations (id,user_id,source,origin,content,"
            "epistemic_status,confidence,scope,provenance,subject_kind,"
            "subject_id,correlation_id,observed_at,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, user_id, source, origin, content[:4000], epistemic_status,
             max(0.0, min(1.0, float(confidence))), scope,
             json.dumps(provenance or {}), subject_kind, subject_id,
             correlation_id, observed_at or now, now))

        self.bus.emit(user_id, "observation.recorded", content[:140],
                      subject_kind="observation", subject_id=oid,
                      correlation_id=correlation_id,
                      payload={"source": source, "origin": origin,
                               "epistemic_status": epistemic_status,
                               "confidence": round(float(confidence), 3)})
        if subject_kind and subject_id:
            self.bus.emit(user_id, "observation.linked",
                          f"Observation concerns {subject_kind} {subject_id}",
                          subject_kind="observation", subject_id=oid,
                          correlation_id=correlation_id,
                          payload={"subject_kind": subject_kind,
                                   "subject_id": subject_id})
        return self.get(oid)  # type: ignore[return-value]

    # --------------------------------------------------------------- reading
    def get(self, observation_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM observations WHERE id=?",
                                (observation_id,))
        return self._row(row) if row else None

    def list(self, user_id: str, *, source: str | None = None,
             epistemic_status: str | None = None,
             subject_kind: str | None = None, subject_id: str | None = None,
             limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM observations WHERE user_id=?"
        params: list[Any] = [user_id]
        if source:
            sql += " AND source=?"
            params.append(source)
        if epistemic_status:
            sql += " AND epistemic_status=?"
            params.append(epistemic_status)
        if subject_kind:
            sql += " AND subject_kind=?"
            params.append(subject_kind)
        if subject_id:
            sql += " AND subject_id=?"
            params.append(subject_id)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(int(limit))
        return [self._row(r) for r in self.db.query(sql, params)]

    def evidence_for(self, user_id: str, subject_kind: str,
                     subject_id: str) -> dict[str, Any]:
        """
        All evidence bearing on one subject, separated by epistemic status so a
        caller can never mistake an inference for an observation.
        """
        items = self.list(user_id, subject_kind=subject_kind,
                          subject_id=subject_id, limit=200)
        buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in EPISTEMIC}
        for item in items:
            buckets[item["epistemic_status"]].append(item)
        observed = buckets["OBSERVED"]
        return {
            "subject_kind": subject_kind,
            "subject_id": subject_id,
            "total": len(items),
            "by_status": {k: len(v) for k, v in buckets.items()},
            "observations": items,
            "verdict": ("INSUFFICIENT EVIDENCE" if not observed
                        else f"{len(observed)} direct observation(s)"),
        }

    # ------------------------------------------------------------- promotion
    def promote(self, user_id: str, observation_id: str, memory_service, *,
                category: str = "fact", importance: float = 0.5,
                correlation_id: str | None = None) -> dict[str, Any]:
        """
        Turn an observation into a durable memory. This is always explicit -
        nothing in the pipeline promotes automatically, because "the system saw
        it" and "this is worth remembering about you" are different claims.

        Only OBSERVED evidence may be promoted. Promoting an INFERRED or
        SIMULATED record would launder a guess into a fact.
        """
        obs = self.get(observation_id)
        if obs is None:
            raise KeyError(observation_id)
        if obs["epistemic_status"] != "OBSERVED":
            return {
                "promoted": False,
                "reason": (f"Only OBSERVED evidence can become a memory; this "
                           f"is {obs['epistemic_status']}."),
            }
        created = memory_service.create(
            user_id=user_id, content=obs["content"], category=category,
            importance=importance, confidence=obs["confidence"],
            source=f"observation:{observation_id}")
        memory = created.get("memory", created)
        self.bus.emit(user_id, "observation.promoted",
                      f"Observation became a memory: {obs['content'][:100]}",
                      subject_kind="observation", subject_id=observation_id,
                      correlation_id=correlation_id,
                      payload={"memory_id": memory.get("id")})
        return {"promoted": True, "memory": memory, "observation": obs}

    def discard(self, user_id: str, observation_id: str, reason: str,
                correlation_id: str | None = None) -> bool:
        """
        Mark an observation as discarded. The row is NOT deleted - evidence is
        never destroyed, only annotated (§25).
        """
        obs = self.get(observation_id)
        if obs is None:
            return False
        provenance = dict(obs["provenance"])
        provenance["discarded"] = {"reason": reason, "at": _now()}
        self.db.execute("UPDATE observations SET provenance=? WHERE id=?",
                        (json.dumps(provenance), observation_id))
        self.bus.emit(user_id, "observation.discarded", reason,
                      subject_kind="observation", subject_id=observation_id,
                      correlation_id=correlation_id)
        return True

    def stats(self, user_id: str) -> dict[str, Any]:
        rows = self.db.query(
            "SELECT epistemic_status, source, COUNT(*) AS n FROM observations"
            " WHERE user_id=? GROUP BY epistemic_status, source", (user_id,))
        by_status: dict[str, int] = {}
        by_source: dict[str, int] = {}
        for r in rows:
            by_status[r["epistemic_status"]] = by_status.get(r["epistemic_status"], 0) + r["n"]
            by_source[r["source"]] = by_source.get(r["source"], 0) + r["n"]
        total = sum(by_status.values())
        return {
            "total": total,
            "by_status": by_status,
            "by_source": by_source,
            "detail": ("No observations recorded yet." if not total
                       else f"{total} observation(s) on record."),
        }

    # ---------------------------------------------------------------- helper
    @staticmethod
    def _row(row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["provenance"] = json.loads(item.get("provenance") or "{}")
        except (TypeError, ValueError):
            item["provenance"] = {}
        return item
