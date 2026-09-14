"""
Causal cognitive memory.

Tracks the real chain: MEMORY → RETRIEVAL → INFLUENCE → DECISION → ACTION →
OUTCOME → LEARNING, so the system can answer "what did this memory influence?"
and "what happened because it was used?" from recorded evidence.

Impact metrics are only reported when evidence supports them. Everything else
returns INSUFFICIENT EVIDENCE.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

RELATIONS = ("influenced", "caused", "contradicted", "supported", "enabled",
             "blocked", "informed")
KINDS = ("memory", "decision", "action", "outcome", "prediction", "world",
         "intent", "principle", "policy")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CausalGraph:
    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def link(self, user_id: str, cause_kind: str, cause_id: str,
             effect_kind: str, effect_id: str, *, relation: str = "influenced",
             weight: float = 0.5, correlation_id: str | None = None) -> dict[str, Any]:
        if cause_kind not in KINDS or effect_kind not in KINDS:
            raise ValueError(f"Unknown causal node kind: {cause_kind}/{effect_kind}")
        if relation not in RELATIONS:
            raise ValueError(f"Unknown causal relation: {relation!r}")

        self.db.execute(
            "INSERT INTO causal_links (user_id,cause_kind,cause_id,effect_kind,"
            "effect_id,relation,weight,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (user_id, cause_kind, cause_id, effect_kind, effect_id, relation,
             max(0.0, min(1.0, weight)), _now()))
        self.bus.emit(user_id, "causal.linked",
                      f"{cause_kind}:{cause_id} {relation} {effect_kind}:{effect_id}",
                      subject_kind=cause_kind, subject_id=cause_id,
                      correlation_id=correlation_id,
                      payload={"effect_kind": effect_kind, "effect_id": effect_id,
                               "relation": relation, "weight": weight})
        return {"cause_kind": cause_kind, "cause_id": cause_id,
                "effect_kind": effect_kind, "effect_id": effect_id,
                "relation": relation, "weight": weight}

    def downstream(self, cause_kind: str, cause_id: str) -> list[dict[str, Any]]:
        """What did this influence? (forward traversal)"""
        return [dict(r) for r in self.db.query(
            "SELECT * FROM causal_links WHERE cause_kind=? AND cause_id=?"
            " ORDER BY id ASC", (cause_kind, cause_id))]

    def upstream(self, effect_kind: str, effect_id: str) -> list[dict[str, Any]]:
        """What influenced this? (reverse traversal)"""
        return [dict(r) for r in self.db.query(
            "SELECT * FROM causal_links WHERE effect_kind=? AND effect_id=?"
            " ORDER BY id ASC", (effect_kind, effect_id))]

    def chain(self, kind: str, node_id: str, depth: int = 4) -> dict[str, Any]:
        """
        Walk the causal chain forward from a node, cycle-safe.

        Returns a nested structure the Causality UI can render directly.
        """
        seen: set[tuple[str, str]] = set()

        def walk(k: str, i: str, d: int) -> dict[str, Any]:
            key = (k, i)
            if d <= 0 or key in seen:
                return {"kind": k, "id": i, "effects": []}
            seen.add(key)
            effects = []
            for link in self.downstream(k, i):
                effects.append({
                    "relation": link["relation"], "weight": link["weight"],
                    "node": walk(link["effect_kind"], link["effect_id"], d - 1),
                })
            return {"kind": k, "id": i, "effects": effects}

        return walk(kind, node_id, depth)

    def impact(self, user_id: str, memory_id: str) -> dict[str, Any]:
        """
        Evidence-based impact report for one memory.

        Every figure is a count of recorded links/events. Where there is no
        evidence we say so explicitly instead of inventing a number.
        """
        links = self.downstream("memory", memory_id)
        rep = self.db.query_one(
            "SELECT * FROM memory_reputation WHERE memory_id=?", (memory_id,))

        decisions = [l for l in links if l["effect_kind"] == "decision"]
        actions = [l for l in links if l["effect_kind"] == "action"]
        outcomes = [l for l in links if l["effect_kind"] == "outcome"]

        if not links and rep is None:
            return {"memory_id": memory_id, "evidence": False,
                    "detail": "INSUFFICIENT EVIDENCE",
                    "note": "This memory has not yet influenced a recorded decision."}

        return {
            "memory_id": memory_id,
            "evidence": True,
            "retrievals": int(rep["retrievals"]) if rep else 0,
            "influences": int(rep["influences"]) if rep else len(links),
            "decisions_influenced": len(decisions),
            "actions_influenced": len(actions),
            "outcomes_influenced": len(outcomes),
            "positive_outcomes": int(rep["positive_outcomes"]) if rep else 0,
            "negative_outcomes": int(rep["negative_outcomes"]) if rep else 0,
            # Deliberately absent: time saved / cost avoided. We cannot measure
            # those here, so we do not report them.
            "time_saved": "INSUFFICIENT EVIDENCE",
            "cost_avoided": "INSUFFICIENT EVIDENCE",
        }


class DecisionLog:
    """Decisions with alternatives, expectation, outcome, trade-offs and regret."""

    def __init__(self, db, bus, causal: CausalGraph) -> None:
        self.db = db
        self.bus = bus
        self.causal = causal

    def record(self, user_id: str, summary: str, *, context: str | None = None,
               alternatives: list[str] | None = None, chosen: str | None = None,
               expected_outcome: str | None = None,
               influenced_by: list[str] | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        did = f"d_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO decisions (id,user_id,summary,context,alternatives,chosen,"
            "expected_outcome,status,created_at) VALUES (?,?,?,?,?,?,?, 'open', ?)",
            (did, user_id, summary, context,
             "\n".join(alternatives or []), chosen, expected_outcome, _now()))
        self.bus.emit(user_id, "action.proposed", summary, subject_kind="decision",
                      subject_id=did, correlation_id=correlation_id,
                      payload={"alternatives": alternatives or [], "chosen": chosen})

        # Wire the memories that informed this decision into the causal graph.
        for memory_id in influenced_by or []:
            self.causal.link(user_id, "memory", memory_id, "decision", did,
                             relation="influenced", weight=0.6,
                             correlation_id=correlation_id)
        return self.get(did)  # type: ignore[return-value]

    def resolve(self, user_id: str, decision_id: str, actual_outcome: str, *,
                positive: bool, tradeoffs: str | None = None,
                lesson: str | None = None,
                correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Close a decision with what actually happened.

        Regret is computed as the gap between expectation and reality, not as a
        mood: a positive outcome yields 0.0, a negative one 1.0 scaled by whether
        an explicit expectation existed to be violated.
        """
        row = self.db.query_one("SELECT * FROM decisions WHERE id=? AND user_id=?",
                                (decision_id, user_id))
        if row is None or row["status"] != "open":
            return None

        regret = 0.0 if positive else (0.8 if row["expected_outcome"] else 0.5)
        self.db.execute(
            "UPDATE decisions SET actual_outcome=?, tradeoffs=?, regret=?, lesson=?,"
            " status='resolved', resolved_at=? WHERE id=?",
            (actual_outcome, tradeoffs, regret, lesson, _now(), decision_id))

        self.bus.emit(user_id, "outcome.recorded", actual_outcome,
                      subject_kind="decision", subject_id=decision_id,
                      correlation_id=correlation_id,
                      payload={"positive": positive, "regret": regret})
        if tradeoffs:
            self.bus.emit(user_id, "tradeoff.detected", tradeoffs,
                          subject_kind="decision", subject_id=decision_id,
                          correlation_id=correlation_id)
        if regret >= 0.5:
            self.bus.emit(user_id, "regret.detected",
                          f"Outcome fell short of what was expected: {actual_outcome}",
                          subject_kind="decision", subject_id=decision_id,
                          correlation_id=correlation_id, payload={"regret": regret})

        # Propagate the outcome back to every memory that informed the decision.
        outcome_id = f"o_{uuid.uuid4().hex[:12]}"
        self.causal.link(user_id, "decision", decision_id, "outcome", outcome_id,
                         relation="caused", weight=1.0, correlation_id=correlation_id)
        return self.get(decision_id)

    def get(self, decision_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM decisions WHERE id=?", (decision_id,))
        if row is None:
            return None
        d = dict(row)
        d["alternatives"] = [a for a in (d.get("alternatives") or "").split("\n") if a]
        return d

    def list(self, user_id: str) -> list[dict[str, Any]]:
        return [self.get(r["id"]) for r in self.db.query(  # type: ignore[misc]
            "SELECT id FROM decisions WHERE user_id=? ORDER BY datetime(created_at) DESC",
            (user_id,))]
