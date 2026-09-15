"""
V8.2 — Memory influence ledger: MEMORY → RETRIEVAL → INFLUENCE → DECISION →
OUTCOME → CONSEQUENCE → MEMORY IMPACT.

The rule that makes this honest (§8):

  * Retrieval alone is NOT influence. A memory is only recorded as influential
    when the turn actually used it (it won arbitration and entered the context
    that produced the response).
  * Influence alone is NOT an outcome. Reputation moves only when an outcome is
    OBSERVED and attribution is defensible.
  * When attribution is not defensible we record INSUFFICIENT EVIDENCE and
    change nothing.

Every row here is real: written from work that happened, never pre-seeded.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

# How an outcome relates to the memory that influenced the decision.
SUPPORTED = "SUPPORTED"          # reality matched what the memory implied
CONTRADICTED = "CONTRADICTED"    # reality went against the memory
NEUTRAL = "NEUTRAL"              # observed, but says nothing about the memory
INSUFFICIENT = "INSUFFICIENT EVIDENCE"

VERDICTS = (SUPPORTED, CONTRADICTED, NEUTRAL, INSUFFICIENT)

REPUTATION_UP = "INCREASE"
REPUTATION_DOWN = "DECREASE"
REPUTATION_SAME = "UNCHANGED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class InfluenceLedger:
    """Records what a memory actually influenced, and what came of it."""

    def __init__(self, db, bus, reputation, causal) -> None:
        self.db = db
        self.bus = bus
        self.reputation = reputation
        self.causal = causal

    # -------------------------------------------------------------- influence
    def record_influence(self, user_id: str, memory_id: str, *,
                         influenced_kind: str, influenced_id: str,
                         how: str, weight: float = 0.5,
                         correlation_id: str | None = None) -> dict[str, Any]:
        """
        Record that a memory MATERIALLY influenced something.

        Callers must only invoke this when the memory genuinely entered the
        decision path — not merely because retrieval returned it.
        """
        influence_id = f"inf_{uuid.uuid4().hex[:12]}"
        weight = max(0.0, min(1.0, float(weight)))
        self.db.execute(
            "INSERT INTO memory_influences (id,user_id,memory_id,influenced_kind,"
            "influenced_id,how,weight,outcome_verdict,outcome_detail,"
            "reputation_effect,correlation_id,created_at,resolved_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
            (influence_id, user_id, memory_id, influenced_kind, influenced_id,
             how[:400], weight, None, None, None, correlation_id, _now()))

        # Reputation records the influence count, but NOT a quality judgement.
        self.reputation.record_influence(user_id, memory_id)

        try:
            self.causal.link(user_id, "memory", memory_id, influenced_kind,
                             influenced_id, relation="influenced", weight=weight,
                             correlation_id=correlation_id)
        except ValueError:
            # Unknown causal node kind: keep the influence row, skip the edge.
            pass

        self.bus.emit(user_id, "memory.influenced",
                      f"Memory shaped this {influenced_kind}: {how[:120]}",
                      subject_kind="memory", subject_id=memory_id,
                      correlation_id=correlation_id,
                      payload={"influence_id": influence_id,
                               "influenced_kind": influenced_kind,
                               "influenced_id": influenced_id,
                               "weight": weight, "how": how[:200]})
        return self.get(influence_id)  # type: ignore[return-value]

    # ---------------------------------------------------------------- outcome
    def record_outcome(self, user_id: str, influence_id: str, *,
                       verdict: str, detail: str,
                       evidence: list[str] | None = None,
                       correlation_id: str | None = None) -> dict[str, Any]:
        """
        Attach an OBSERVED outcome to a recorded influence.

        Reputation only moves for SUPPORTED / CONTRADICTED. NEUTRAL and
        INSUFFICIENT EVIDENCE are recorded faithfully and change nothing.
        """
        if verdict not in VERDICTS:
            raise ValueError(f"Unknown outcome verdict: {verdict!r}")

        row = self.db.query_one(
            "SELECT * FROM memory_influences WHERE id=? AND user_id=?",
            (influence_id, user_id))
        if row is None:
            raise KeyError(influence_id)
        if row["outcome_verdict"] is not None:
            raise ValueError(
                f"Influence {influence_id} already has a recorded outcome.")

        evidence = evidence or []
        if verdict in (SUPPORTED, CONTRADICTED) and not evidence:
            # Attribution without evidence is not defensible — downgrade rather
            # than let an unevidenced claim move reputation.
            verdict = INSUFFICIENT
            detail = (f"{detail} (downgraded to INSUFFICIENT EVIDENCE: no "
                      "supporting evidence was supplied)")

        effect = {SUPPORTED: REPUTATION_UP, CONTRADICTED: REPUTATION_DOWN}.get(
            verdict, REPUTATION_SAME)

        self.db.execute(
            "UPDATE memory_influences SET outcome_verdict=?, outcome_detail=?,"
            " outcome_evidence=?, reputation_effect=?, resolved_at=? WHERE id=?",
            (verdict, detail[:500], json.dumps(evidence, default=str), effect,
             _now(), influence_id))

        memory_id = row["memory_id"]
        reputation_state: dict[str, Any] | None = None
        if effect == REPUTATION_UP:
            reputation_state = self.reputation.record_outcome(
                user_id, memory_id, True, correlation_id=correlation_id)
        elif effect == REPUTATION_DOWN:
            reputation_state = self.reputation.record_outcome(
                user_id, memory_id, False, correlation_id=correlation_id)
        else:
            reputation_state = self.reputation.get(user_id, memory_id)

        self.bus.emit(
            user_id, "outcome.recorded",
            f"Outcome for a memory-influenced {row['influenced_kind']}: {verdict}",
            subject_kind="memory", subject_id=memory_id,
            correlation_id=correlation_id,
            payload={"influence_id": influence_id, "verdict": verdict,
                     "reputation_effect": effect, "detail": detail[:200],
                     "evidence": evidence})

        if effect != REPUTATION_SAME:
            self.bus.emit(
                user_id, "memory.impact",
                f"Reputation {effect.lower()} for memory {memory_id}",
                subject_kind="memory", subject_id=memory_id,
                correlation_id=correlation_id,
                payload={"effect": effect, "verdict": verdict,
                         "reputation": reputation_state})
            try:
                self.causal.link(user_id, "outcome", influence_id, "memory",
                                 memory_id,
                                 relation="supported" if effect == REPUTATION_UP
                                 else "contradicted",
                                 weight=0.7, correlation_id=correlation_id)
            except ValueError:
                pass

        result = self.get(influence_id)
        assert result is not None
        result["reputation"] = reputation_state
        return result

    # ------------------------------------------------------------------ reads
    def get(self, influence_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM memory_influences WHERE id=?",
                                (influence_id,))
        if row is None:
            return None
        d = dict(row)
        try:
            d["outcome_evidence"] = json.loads(d.get("outcome_evidence") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["outcome_evidence"] = []
        return d

    def for_memory(self, user_id: str, memory_id: str,
                   limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id FROM memory_influences WHERE user_id=? AND memory_id=?"
            " ORDER BY id DESC LIMIT ?", (user_id, memory_id, limit))
        return [r for r in (self.get(row["id"]) for row in rows) if r]

    def pending(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Influences with no observed outcome yet — honestly unresolved."""
        rows = self.db.query(
            "SELECT id FROM memory_influences WHERE user_id=?"
            " AND outcome_verdict IS NULL ORDER BY id DESC LIMIT ?",
            (user_id, limit))
        return [r for r in (self.get(row["id"]) for row in rows) if r]

    def recent(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id FROM memory_influences WHERE user_id=? ORDER BY id DESC"
            " LIMIT ?", (user_id, limit))
        return [r for r in (self.get(row["id"]) for row in rows) if r]

    # --------------------------------------------------------------- analysis
    def impact(self, user_id: str, memory_id: str) -> dict[str, Any]:
        """
        The causal story of one memory, reported only as far as evidence allows.
        """
        influences = self.for_memory(user_id, memory_id)
        resolved = [i for i in influences if i.get("outcome_verdict")]
        supported = [i for i in resolved if i["outcome_verdict"] == SUPPORTED]
        contradicted = [i for i in resolved if i["outcome_verdict"] == CONTRADICTED]
        rep = self.reputation.get(user_id, memory_id)

        if not influences:
            summary = ("This memory has not been recorded as influencing any "
                       "decision yet.")
        elif not resolved:
            summary = (f"Influenced {len(influences)} decision(s), but no outcome "
                       "has been observed — INSUFFICIENT EVIDENCE to judge it.")
        else:
            summary = (f"Influenced {len(influences)} decision(s); "
                       f"{len(supported)} outcome(s) supported it and "
                       f"{len(contradicted)} contradicted it.")

        return {
            "memory_id": memory_id,
            "influences": influences,
            "influence_count": len(influences),
            "resolved_count": len(resolved),
            "supported": len(supported),
            "contradicted": len(contradicted),
            "unresolved": len(influences) - len(resolved),
            "reputation": rep,
            "summary": summary,
        }
