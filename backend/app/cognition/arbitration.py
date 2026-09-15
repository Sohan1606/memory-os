"""
V8.2 — Memory Arbitration V2.

Extends the V8.1 MemoryArbiter rather than replacing it. Adds:

  * explicit user-correction evidence as a first-class factor
  * contradiction history (counted from the real event log)
  * scope matching (does the memory's scope fit the current query?)
  * freshness / staleness penalties distinct from plain recency
  * explicit version relationships (superseded_by)
  * a structured ArbitrationRecord persisted so "why did you use that memory?"
    can be answered from evidence long after the turn

CONFIDENCE (is it true?) and REPUTATION (has using it worked?) remain separate
fields and are never blended into one number.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Source authority. An explicit correction outranks a passive inference.
AUTHORITY: dict[str, float] = {
    "correction": 1.0, "user-correction": 1.0, "explicit": 0.9, "manual": 0.85,
    "agent-tool": 0.75, "conversation": 0.7, "voice": 0.7,
    "memory-manager": 0.6, "consolidation": 0.65, "seed": 0.5, "inference": 0.45,
}

REPUTATION_WEIGHT: dict[str, float] = {
    "TRUSTED": 1.0, "RELIABLE": 0.85, "CONTEXTUAL": 0.6, "UNCERTAIN": 0.35,
    "OUTDATED": 0.2, "CONTRADICTED": 0.1, "INSUFFICIENT EVIDENCE": 0.5,
}

# Lifecycle states that must never win arbitration or re-enter retrieval.
BLOCKED_LIFECYCLE = ("retired", "quarantined")

# Scoring weights. Documented here as the single source of truth.
W_CONFIDENCE = 0.20
W_RECENCY = 0.15
W_AUTHORITY = 0.18
W_REPUTATION = 0.12
W_SPECIFICITY = 0.08
W_SCOPE = 0.12
W_FRESHNESS = 0.08
W_CORRECTION = 0.07

STALE_AFTER_DAYS = 120.0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat(timespec="seconds")


def _age_days(value: Any) -> float:
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (_now() - dt).total_seconds() / 86_400)
    except (TypeError, ValueError):
        return 30.0


def _tokens(text: str) -> set[str]:
    import re
    return {w for w in re.findall(r"[a-z0-9]{3,}", str(text).lower())}


@dataclass
class Candidate:
    """One memory competing to inform the answer, with all its scored factors."""

    memory: dict[str, Any]
    score: float = 0.0
    confidence: float = 0.0
    reputation: str = "INSUFFICIENT EVIDENCE"
    lifecycle: str = "candidate"
    factors: dict[str, float] = field(default_factory=dict)
    evidence: list[str] = field(default_factory=list)
    blocked: bool = False
    blocked_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"memory_id": self.memory.get("id"),
                "content": self.memory.get("content"),
                "score": round(self.score, 4),
                "confidence": round(self.confidence, 3),
                "reputation": self.reputation, "lifecycle": self.lifecycle,
                "factors": {k: round(v, 3) for k, v in self.factors.items()},
                "evidence": self.evidence,
                "blocked": self.blocked, "blocked_reason": self.blocked_reason}


@dataclass
class ArbitrationRecord:
    """The full, inspectable outcome of one arbitration."""

    id: str
    user_id: str
    query: str
    winner: Candidate | None
    losers: list[Candidate]
    blocked: list[Candidate]
    conflict: bool
    uncertainty: float
    reason: str
    created_at: str
    correlation_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "user_id": self.user_id, "query": self.query,
            "winner": self.winner.as_dict() if self.winner else None,
            "losers": [c.as_dict() for c in self.losers],
            "blocked": [c.as_dict() for c in self.blocked],
            "candidates": ([self.winner.as_dict()] if self.winner else [])
                          + [c.as_dict() for c in self.losers]
                          + [c.as_dict() for c in self.blocked],
            "conflict": self.conflict,
            "uncertainty": round(self.uncertainty, 3),
            "reason": self.reason, "created_at": self.created_at,
            "correlation_id": self.correlation_id,
        }


class ArbiterV2:
    """
    Evidence-weighted arbitration between competing memories.

    Reads reputation and contradiction history from real stored state; writes a
    persistent ArbitrationRecord so the decision can be re-examined later.
    """

    def __init__(self, db, bus, reputation) -> None:
        self.db = db
        self.bus = bus
        self.reputation = reputation

    # ---------------------------------------------------------------- scoring
    def _contradiction_count(self, memory_id: str) -> int:
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM cognitive_events WHERE subject_kind='memory'"
            " AND subject_id=? AND type='memory.contradiction'", (memory_id,))
        return int(row["n"]) if row else 0

    def _was_corrected_into(self, memory_id: str) -> bool:
        """True when this memory is the RESULT of an explicit user correction."""
        row = self.db.query_one(
            "SELECT COUNT(*) AS n FROM memory_versions WHERE memory_id=?"
            " AND (lower(reason) LIKE '%correct%' OR lower(reason) LIKE '%supersed%'"
            "      OR lower(reason) LIKE '%user said%')", (memory_id,))
        return bool(row and int(row["n"]) > 0)

    def _superseded(self, memory: dict[str, Any]) -> bool:
        return str(memory.get("status") or "active") == "superseded"

    def score(self, user_id: str, memory: dict[str, Any],
              query: str = "") -> Candidate:
        """Score one candidate from stored evidence only."""
        mem_id = str(memory.get("id"))
        rep = self.reputation.get(user_id, mem_id)
        lifecycle = str(rep.get("lifecycle") or "candidate")
        cand = Candidate(memory=memory, reputation=str(rep.get("reputation")),
                         lifecycle=lifecycle)

        # --- hard blocks: contradicted/quarantined memories never win silently
        if lifecycle in BLOCKED_LIFECYCLE:
            cand.blocked = True
            cand.blocked_reason = (
                f"Memory lifecycle is '{lifecycle}' — excluded from normal use.")
        elif self._superseded(memory):
            cand.blocked = True
            cand.blocked_reason = (
                "Memory was superseded by a newer version and is history only.")

        confidence = float(memory.get("confidence", 0.6) or 0.6)
        cand.confidence = confidence

        age = _age_days(memory.get("updated_at"))
        recency = 1.0 / (1.0 + age / 30.0)
        freshness = max(0.0, 1.0 - (age / STALE_AFTER_DAYS))

        authority = AUTHORITY.get(str(memory.get("source") or "conversation"), 0.6)
        rep_weight = REPUTATION_WEIGHT.get(cand.reputation, 0.5)

        words = len(str(memory.get("content", "")).split())
        specificity = min(1.0, words / 18.0)

        # Scope match: overlap between the query and the memory's own terms.
        q_tokens = _tokens(query)
        m_tokens = _tokens(memory.get("content", ""))
        scope = (len(q_tokens & m_tokens) / max(1, len(q_tokens))) if q_tokens else 0.5
        scope = min(1.0, scope)

        contradictions = self._contradiction_count(mem_id)
        contradiction_penalty = min(0.4, 0.15 * contradictions)

        corrected = 1.0 if self._was_corrected_into(mem_id) else 0.0

        score = (W_CONFIDENCE * confidence
                 + W_RECENCY * recency
                 + W_AUTHORITY * authority
                 + W_REPUTATION * rep_weight
                 + W_SPECIFICITY * specificity
                 + W_SCOPE * scope
                 + W_FRESHNESS * freshness
                 + W_CORRECTION * corrected
                 - contradiction_penalty)

        if cand.blocked:
            score = 0.0

        cand.score = max(0.0, score)
        cand.factors = {
            "confidence": confidence, "recency": recency, "authority": authority,
            "reputation_weight": rep_weight, "specificity": specificity,
            "scope_match": scope, "freshness": freshness,
            "explicit_correction": corrected,
            "contradiction_penalty": contradiction_penalty,
        }

        evidence: list[str] = []
        if corrected:
            evidence.append("This memory records an explicit user correction.")
        if contradictions:
            evidence.append(
                f"Contradicted {contradictions} time(s) in recorded history.")
        if age > STALE_AFTER_DAYS:
            evidence.append(
                f"Not confirmed for {int(age)} days — treated as possibly stale.")
        if rep.get("evidence", 0) == 0:
            evidence.append(
                "No outcome evidence yet, so reputation is INSUFFICIENT EVIDENCE.")
        else:
            evidence.append(
                f"Reputation {cand.reputation} from {rep.get('supporting', 0)} "
                f"supporting and {rep.get('contradicting', 0)} contradicting "
                "outcome(s).")
        evidence.append(f"Source '{memory.get('source')}' has authority "
                        f"{authority:.2f}.")
        if cand.blocked_reason:
            evidence.append(cand.blocked_reason)
        cand.evidence = evidence
        return cand

    # ------------------------------------------------------------- arbitrate
    def arbitrate(self, user_id: str, candidates: list[dict[str, Any]], *,
                  query: str = "", correlation_id: str | None = None,
                  persist: bool = True) -> dict[str, Any]:
        """
        Resolve competing memories into one structured, inspectable decision.

        Always returns a record — including when there is nothing to arbitrate —
        so callers never have to special-case a None.
        """
        scored = [self.score(user_id, c, query) for c in candidates]
        blocked = [c for c in scored if c.blocked]
        live = [c for c in scored if not c.blocked]
        live.sort(key=lambda c: c.score, reverse=True)

        winner = live[0] if live else None
        losers = live[1:]

        # Uncertainty: how close is the race, and how thin is the evidence?
        if winner is None:
            uncertainty = 1.0
        elif not losers:
            uncertainty = max(0.0, 1.0 - winner.score)
        else:
            margin = winner.score - losers[0].score
            uncertainty = max(0.0, min(1.0, 1.0 - margin * 2.0))

        # A genuine conflict: two live candidates whose scores are close AND
        # whose content pulls in different directions.
        conflict = bool(
            winner and losers
            and (winner.score - losers[0].score) < 0.08
            and _tokens(winner.memory.get("content", ""))
            != _tokens(losers[0].memory.get("content", "")))

        reason = self._explain(winner, losers, blocked, uncertainty, conflict)

        record = ArbitrationRecord(
            id=f"arb_{uuid.uuid4().hex[:12]}", user_id=user_id, query=query,
            winner=winner, losers=losers, blocked=blocked, conflict=conflict,
            uncertainty=uncertainty, reason=reason, created_at=_iso(),
            correlation_id=correlation_id)

        if persist and (winner or blocked):
            self._persist(record)

        return record.as_dict()

    @staticmethod
    def _explain(winner: Candidate | None, losers: list[Candidate],
                 blocked: list[Candidate], uncertainty: float,
                 conflict: bool) -> str:
        if winner is None:
            if blocked:
                return ("INSUFFICIENT EVIDENCE — every candidate memory is "
                        "contradicted, superseded or quarantined, so none was used.")
            return "No competing memories to arbitrate."

        top_factor = max(winner.factors.items(),
                         key=lambda kv: kv[1] if kv[0] != "contradiction_penalty"
                         else -1)
        parts = [
            f"Selected memory {winner.memory.get('id')} (score {winner.score:.3f}); "
            f"strongest factor was {top_factor[0].replace('_', ' ')} "
            f"at {top_factor[1]:.2f}."]
        if losers:
            parts.append(
                f"{len(losers)} other candidate(s) scored lower, closest at "
                f"{losers[0].score:.3f}.")
        if blocked:
            parts.append(
                f"{len(blocked)} candidate(s) were excluded: "
                + "; ".join(filter(None, (c.blocked_reason for c in blocked[:2]))))
        if conflict:
            parts.append("Scores are close enough that this is a genuine conflict — "
                         "treat the winner as provisional.")
        if uncertainty >= 0.6:
            parts.append(f"Uncertainty is high ({uncertainty:.2f}); the evidence "
                         "does not strongly separate the candidates.")
        return " ".join(parts)

    # ---------------------------------------------------------------- storage
    def _persist(self, record: ArbitrationRecord) -> None:
        self.db.execute(
            "INSERT INTO arbitration_records (id,user_id,query,winner_id,"
            "candidates,conflict,uncertainty,reason,correlation_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (record.id, record.user_id, record.query[:400],
             record.winner.memory.get("id") if record.winner else None,
             json.dumps(record.as_dict()["candidates"], default=str),
             1 if record.conflict else 0, record.uncertainty, record.reason,
             record.correlation_id, record.created_at))
        if record.winner:
            self.bus.emit(
                record.user_id, "arbitration.resolved",
                f"Chose between {len(record.losers) + 1} competing memories",
                subject_kind="memory", subject_id=record.winner.memory.get("id"),
                correlation_id=record.correlation_id,
                payload={"arbitration_id": record.id,
                         "uncertainty": round(record.uncertainty, 3),
                         "conflict": record.conflict, "reason": record.reason})
        if record.conflict:
            self.bus.emit(
                record.user_id, "arbitration.conflict",
                "Competing memories could not be cleanly separated",
                subject_kind="arbitration", subject_id=record.id,
                correlation_id=record.correlation_id,
                payload={"uncertainty": round(record.uncertainty, 3)})

    # ------------------------------------------------------------------ reads
    def get(self, arbitration_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM arbitration_records WHERE id=?",
                                (arbitration_id,))
        if row is None:
            return None
        d = dict(row)
        try:
            d["candidates"] = json.loads(d["candidates"] or "[]")
        except json.JSONDecodeError:
            d["candidates"] = []
        d["conflict"] = bool(d["conflict"])
        return d

    def recent(self, user_id: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id FROM arbitration_records WHERE user_id=?"
            " ORDER BY id DESC LIMIT ?", (user_id, limit))
        return [r for r in (self.get(row["id"]) for row in rows) if r]

    def for_memory(self, memory_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """Every arbitration this memory took part in (won or lost)."""
        rows = self.db.query(
            "SELECT id FROM arbitration_records WHERE winner_id=? OR candidates"
            " LIKE ? ORDER BY id DESC LIMIT ?",
            (memory_id, f'%"{memory_id}"%', limit))
        return [r for r in (self.get(row["id"]) for row in rows) if r]
