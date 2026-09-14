"""
Memory biology: lifecycle, reputation and arbitration.

Confidence and reputation are deliberately separate:
  CONFIDENCE - how likely this memory is to be TRUE
  REPUTATION - how well ACTING on it has actually worked out

Lifecycle progresses on evidence, never on a timer alone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LIFECYCLE = ("candidate", "validating", "trusted", "reinforced", "uncertain",
             "outdated", "contradicted", "retired")

REPUTATION_LABELS = ("TRUSTED", "RELIABLE", "CONTEXTUAL", "UNCERTAIN",
                     "OUTDATED", "CONTRADICTED", "INSUFFICIENT EVIDENCE")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ReputationStore:
    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def _ensure(self, user_id: str, memory_id: str) -> None:
        if self.db.query_one("SELECT memory_id FROM memory_reputation WHERE memory_id=?",
                             (memory_id,)) is None:
            self.db.execute(
                "INSERT INTO memory_reputation (memory_id,user_id,lifecycle,updated_at)"
                " VALUES (?,?,?,?)", (memory_id, user_id, "candidate", _now()))

    def _bump(self, user_id: str, memory_id: str, column: str, by: int = 1) -> None:
        self._ensure(user_id, memory_id)
        self.db.execute(
            f"UPDATE memory_reputation SET {column}={column}+?, updated_at=?"
            " WHERE memory_id=?", (by, _now(), memory_id))

    # ------------------------------------------------------------- evidence
    def record_retrieval(self, user_id: str, memory_id: str) -> None:
        self._bump(user_id, memory_id, "retrievals")

    def record_influence(self, user_id: str, memory_id: str) -> None:
        self._bump(user_id, memory_id, "influences")

    def record_outcome(self, user_id: str, memory_id: str, positive: bool,
                       correlation_id: str | None = None) -> dict[str, Any]:
        """Attribute a real outcome back to a memory that informed it."""
        self._bump(user_id, memory_id,
                   "positive_outcomes" if positive else "negative_outcomes")
        self._bump(user_id, memory_id, "supporting" if positive else "contradicting")
        state = self.reevaluate(user_id, memory_id, correlation_id=correlation_id)
        self.bus.emit(user_id,
                      "memory.validated" if positive else "memory.weakened",
                      f"Outcome was {'positive' if positive else 'negative'} "
                      f"for memory {memory_id}",
                      subject_kind="memory", subject_id=memory_id,
                      correlation_id=correlation_id, payload=state)
        return state

    def record_contradiction(self, user_id: str, memory_id: str,
                             correlation_id: str | None = None) -> None:
        self._bump(user_id, memory_id, "contradicting")
        self.bus.emit(user_id, "memory.contradiction",
                      f"Memory {memory_id} was contradicted by newer information.",
                      subject_kind="memory", subject_id=memory_id,
                      correlation_id=correlation_id)
        self.reevaluate(user_id, memory_id, correlation_id=correlation_id)

    # ------------------------------------------------------------ lifecycle
    def reevaluate(self, user_id: str, memory_id: str,
                   correlation_id: str | None = None) -> dict[str, Any]:
        """
        Recompute lifecycle from recorded evidence only.

        A memory is never promoted to 'trusted' on a single observation - that
        is the whole point of the maturity ladder.
        """
        self._ensure(user_id, memory_id)
        row = self.db.query_one("SELECT * FROM memory_reputation WHERE memory_id=?",
                                (memory_id,))
        assert row is not None
        supporting = int(row["supporting"])
        contradicting = int(row["contradicting"])
        retrievals = int(row["retrievals"])
        previous = row["lifecycle"]

        if contradicting >= 2 and contradicting > supporting:
            lifecycle = "contradicted"
        elif contradicting > supporting:
            lifecycle = "uncertain"
        elif supporting >= 3:
            lifecycle = "reinforced"
        elif supporting >= 1:
            lifecycle = "trusted"
        elif retrievals >= 2:
            lifecycle = "validating"
        else:
            lifecycle = "candidate"

        if lifecycle != previous:
            self.db.execute(
                "UPDATE memory_reputation SET lifecycle=?, updated_at=? WHERE memory_id=?",
                (lifecycle, _now(), memory_id))
            self.bus.emit(user_id, "memory.updated",
                          f"Memory lifecycle: {previous} → {lifecycle}",
                          subject_kind="memory", subject_id=memory_id,
                          correlation_id=correlation_id,
                          payload={"from": previous, "to": lifecycle})
        return self.get(user_id, memory_id)

    def get(self, user_id: str, memory_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM memory_reputation WHERE memory_id=?",
                                (memory_id,))
        if row is None:
            return {"memory_id": memory_id, "lifecycle": "candidate",
                    "reputation": "INSUFFICIENT EVIDENCE", "evidence": 0,
                    "retrievals": 0, "influences": 0,
                    "positive_outcomes": 0, "negative_outcomes": 0}

        supporting = int(row["supporting"])
        contradicting = int(row["contradicting"])
        evidence = supporting + contradicting
        if evidence == 0:
            reputation = "INSUFFICIENT EVIDENCE"
        elif contradicting >= 2 and contradicting > supporting:
            reputation = "CONTRADICTED"
        elif contradicting > supporting:
            reputation = "UNCERTAIN"
        elif supporting >= 3 and contradicting == 0:
            reputation = "TRUSTED"
        elif supporting >= 2:
            reputation = "RELIABLE"
        else:
            reputation = "CONTEXTUAL"

        return {"memory_id": memory_id, "lifecycle": row["lifecycle"],
                "reputation": reputation, "evidence": evidence,
                "supporting": supporting, "contradicting": contradicting,
                "retrievals": int(row["retrievals"]),
                "influences": int(row["influences"]),
                "positive_outcomes": int(row["positive_outcomes"]),
                "negative_outcomes": int(row["negative_outcomes"])}

    def retire(self, user_id: str, memory_id: str,
               correlation_id: str | None = None) -> dict[str, Any]:
        self._ensure(user_id, memory_id)
        self.db.execute(
            "UPDATE memory_reputation SET lifecycle='retired', updated_at=?"
            " WHERE memory_id=?", (_now(), memory_id))
        self.bus.emit(user_id, "memory.retired", f"Retired memory {memory_id}",
                      subject_kind="memory", subject_id=memory_id,
                      correlation_id=correlation_id)
        return self.get(user_id, memory_id)


class MemoryArbiter:
    """
    Resolves competing memories into one structured, inspectable decision.

    A general preference, a recent explicit exception and a current operational
    fact must not silently fight - the winner and the losers are both reported.
    """

    def __init__(self, reputation: ReputationStore) -> None:
        self.reputation = reputation

    # Source authority: an explicit user correction outranks a passive inference.
    AUTHORITY = {"correction": 1.0, "explicit": 0.9, "conversation": 0.7,
                 "memory-manager": 0.6, "seed": 0.5, "inference": 0.45,
                 "voice": 0.7, "manual": 0.85}

    def arbitrate(self, user_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """
        candidates: [{id, content, confidence, updated_at, source, category}, ...]
        Returns the winner plus a full explanation of why the others lost.
        """
        if not candidates:
            return {"winner": None, "considered": [], "conflict": False,
                    "explanation": "No competing memories."}

        now = datetime.now(timezone.utc)
        scored: list[dict[str, Any]] = []
        for c in candidates:
            rep = self.reputation.get(user_id, c["id"])
            authority = self.AUTHORITY.get(str(c.get("source") or "conversation"), 0.6)

            try:
                updated = datetime.fromisoformat(str(c.get("updated_at")))
                if updated.tzinfo is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age_days = max(0.0, (now - updated).total_seconds() / 86_400)
            except (TypeError, ValueError):
                age_days = 30.0
            recency = 1.0 / (1.0 + age_days / 30.0)

            rep_weight = {"TRUSTED": 1.0, "RELIABLE": 0.85, "CONTEXTUAL": 0.6,
                          "UNCERTAIN": 0.35, "CONTRADICTED": 0.1,
                          "INSUFFICIENT EVIDENCE": 0.5}[rep["reputation"]]

            specificity = min(1.0, len(str(c.get("content", "")).split()) / 18.0)

            score = (0.30 * float(c.get("confidence", 0.6))
                     + 0.25 * recency
                     + 0.20 * authority
                     + 0.15 * rep_weight
                     + 0.10 * specificity)

            scored.append({
                "memory": c, "score": round(score, 4),
                "reputation": rep["reputation"], "lifecycle": rep["lifecycle"],
                "factors": {
                    "confidence": round(float(c.get("confidence", 0.6)), 2),
                    "recency": round(recency, 2), "authority": round(authority, 2),
                    "reputation_weight": rep_weight,
                    "specificity": round(specificity, 2),
                },
            })

        scored.sort(key=lambda s: s["score"], reverse=True)
        winner = scored[0]
        conflict = len(scored) > 1 and (scored[0]["score"] - scored[1]["score"]) < 0.12

        if len(scored) == 1:
            explanation = "Only one relevant memory; no conflict."
        else:
            runner = scored[1]
            reasons = []
            wf, rf = winner["factors"], runner["factors"]
            if wf["recency"] > rf["recency"] + 0.05:
                reasons.append("it is more recent")
            if wf["authority"] > rf["authority"] + 0.05:
                reasons.append("it came from a more authoritative source")
            if wf["confidence"] > rf["confidence"] + 0.05:
                reasons.append("it is held with higher confidence")
            if wf["reputation_weight"] > rf["reputation_weight"] + 0.05:
                reasons.append("acting on it has worked out better before")
            if wf["specificity"] > rf["specificity"] + 0.05:
                reasons.append("it is more specific")
            explanation = (
                f"Selected because {', and '.join(reasons)}." if reasons
                else "Selected on a narrow overall margin; treat as genuinely ambiguous.")

        return {"winner": winner, "considered": scored, "conflict": conflict,
                "explanation": explanation,
                "note": ("Close call - the alternatives are nearly as strong."
                         if conflict else None)}
