"""
Learning: experience → skill → principle, adaptive policy, and self-evaluation.

Nothing is promoted on a single observation. A principle requires repeated,
consistent evidence, and every promotion is recorded as an event so the user can
see exactly why the system changed how it behaves.
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import datetime, timezone
from typing import Any

# Evidence thresholds for the maturity ladder.
HYPOTHESIS_AT = 2
PATTERN_AT = 3
PRINCIPLE_AT = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LearningEngine:
    """Derives policies, skills and principles from recorded events only."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    # ---------------------------------------------------------------- policy
    def set_policy(self, user_id: str, key: str, value: str, *,
                   rationale: str = "", correlation_id: str | None = None) -> dict[str, Any]:
        """
        Record how the system should behave with this user.

        Repeated evidence for the same policy increments evidence_count rather
        than creating duplicates.
        """
        now = _now()
        existing = self.db.query_one(
            "SELECT * FROM policies WHERE user_id=? AND key=?", (user_id, key))
        if existing:
            changed = existing["value"] != value
            self.db.execute(
                "UPDATE policies SET value=?, rationale=?, evidence_count=evidence_count+1,"
                " updated_at=? WHERE id=?", (value, rationale, now, existing["id"]))
            if changed:
                self.bus.emit(user_id, "policy.updated",
                              f"How I work with you: {key} → {value}",
                              subject_kind="policy", subject_id=existing["id"],
                              correlation_id=correlation_id,
                              payload={"key": key, "from": existing["value"],
                                       "to": value, "rationale": rationale})
            return self.get_policy(user_id, key)  # type: ignore[return-value]

        pid = f"pol_{uuid.uuid4().hex[:10]}"
        self.db.execute(
            "INSERT INTO policies (id,user_id,key,value,rationale,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?)", (pid, user_id, key, value, rationale, now, now))
        self.bus.emit(user_id, "policy.proposed",
                      f"Adapting how I work with you: {key} = {value}",
                      subject_kind="policy", subject_id=pid,
                      correlation_id=correlation_id,
                      payload={"key": key, "value": value, "rationale": rationale})
        return self.get_policy(user_id, key)  # type: ignore[return-value]

    def get_policy(self, user_id: str, key: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM policies WHERE user_id=? AND key=?",
                                (user_id, key))
        return dict(row) if row else None

    def policies(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM policies WHERE user_id=? ORDER BY key", (user_id,))]

    def revert_policy(self, user_id: str, key: str,
                      correlation_id: str | None = None) -> bool:
        """Policies must be reversible - the user can always say 'stop doing that'."""
        row = self.db.query_one("SELECT * FROM policies WHERE user_id=? AND key=?",
                                (user_id, key))
        if row is None:
            return False
        self.db.execute("DELETE FROM policies WHERE id=?", (row["id"],))
        self.bus.emit(user_id, "policy.updated", f"Reverted behaviour: {key}",
                      subject_kind="policy", subject_id=row["id"],
                      correlation_id=correlation_id,
                      payload={"key": key, "reverted_from": row["value"]})
        return True

    # ------------------------------------------------------ pattern mining
    def consolidate(self, user_id: str, bus_events: list[Any] | None = None,
                    correlation_id: str | None = None) -> dict[str, Any]:
        """
        Background analysis ("memory dreaming").

        Mines the real event log for repeated patterns and proposes - never
        asserts - skills, principles and policy changes.
        """
        events = bus_events if bus_events is not None else self.db.query(
            "SELECT type, summary FROM cognitive_events WHERE user_id=? "
            "ORDER BY id DESC LIMIT 400", (user_id,))
        types = Counter(
            (e.type if hasattr(e, "type") else e["type"]) for e in events)

        proposed: list[dict[str, Any]] = []

        # Repeatedly declining suggestions => lower interruption tolerance.
        rejected = types.get("intervention.rejected", 0)
        if rejected >= PATTERN_AT:
            proposed.append(self._propose(
                user_id, "principle",
                "This user prefers fewer proactive interruptions.",
                evidence=rejected, correlation_id=correlation_id))
            self.set_policy(user_id, "interruption_tolerance", "low",
                            rationale=f"{rejected} suggestions were declined.",
                            correlation_id=correlation_id)

        # Repeated contradictions => beliefs are going stale.
        contradictions = types.get("memory.contradiction", 0)
        if contradictions >= PATTERN_AT:
            proposed.append(self._propose(
                user_id, "principle",
                "This user's preferences change often; verify before relying on"
                " older memories.",
                evidence=contradictions, correlation_id=correlation_id))

        # Repeated wrong predictions => be less assertive.
        wrong = types.get("prediction.incorrect", 0)
        correct = types.get("prediction.correct", 0)
        if wrong >= PATTERN_AT and wrong > correct:
            proposed.append(self._propose(
                user_id, "principle",
                "Predictions in this domain have been unreliable; state them with"
                " lower confidence.",
                evidence=wrong, correlation_id=correlation_id))

        # Repeated successful tool use => a genuine skill.
        executed = types.get("action.executed", 0)
        failed = types.get("action.failed", 0)
        if executed >= PRINCIPLE_AT and executed > failed * 2:
            proposed.append(self._propose(
                user_id, "skill",
                "Reliably completes memory operations on request.",
                evidence=executed, correlation_id=correlation_id))

        return {"proposed": proposed, "event_sample": sum(types.values()),
                "note": "Proposals require further evidence before being relied upon."
                if proposed else "No repeated pattern reached the evidence threshold."}

    def _propose(self, user_id: str, kind: str, statement: str, *,
                 evidence: int, correlation_id: str | None = None) -> dict[str, Any]:
        event = "principle.proposed" if kind == "principle" else "skill.proposed"
        validated = evidence >= PRINCIPLE_AT
        self.bus.emit(user_id, event, statement, subject_kind=kind,
                      subject_id=f"{kind}:{abs(hash(statement)) % 10**10}",
                      correlation_id=correlation_id,
                      payload={"evidence_count": evidence, "validated": validated})
        if validated:
            self.bus.emit(user_id,
                          "principle.validated" if kind == "principle" else "skill.validated",
                          statement, subject_kind=kind,
                          subject_id=f"{kind}:{abs(hash(statement)) % 10**10}",
                          correlation_id=correlation_id,
                          payload={"evidence_count": evidence})
        return {"kind": kind, "statement": statement, "evidence": evidence,
                "status": "validated" if validated else "hypothesis"}

    # ------------------------------------------------------- self-evaluation
    def self_evaluation(self, user_id: str) -> dict[str, Any]:
        """
        Answer honest questions about the system's own performance.

        Every answer is derived from counted events; where there is not enough
        evidence, the answer says so.
        """
        counts = Counter(
            r["type"] for r in self.db.query(
                "SELECT type FROM cognitive_events WHERE user_id=?", (user_id,)))

        def ratio(good: str, bad: str, question: str, threshold: int = 3):
            g, b = counts.get(good, 0), counts.get(bad, 0)
            total = g + b
            if total < threshold:
                return {"question": question, "answer": "INSUFFICIENT EVIDENCE",
                        "evidence": total}
            return {"question": question, "answer": f"{g}/{total}",
                    "rate": round(g / total, 3), "evidence": total}

        retrieved = counts.get("memory.retrieved", 0)
        surfaced = sum(counts.get(k, 0) for k in
                       ("intervention.presented",))
        suppressed = counts.get("intervention.suppressed", 0)

        return {
            "predictions_calibrated": ratio("prediction.correct", "prediction.incorrect",
                                            "Are my predictions calibrated?"),
            "suggestions_accepted": ratio("intervention.accepted", "intervention.rejected",
                                          "Are my suggestions being accepted?"),
            "actions_successful": ratio("action.executed", "action.failed",
                                        "Are my actions succeeding?"),
            "memory_useful": {
                "question": "Am I retrieving useful memories?",
                "answer": (f"{retrieved} retrievals recorded" if retrieved
                           else "INSUFFICIENT EVIDENCE"),
                "evidence": retrieved},
            "interruption_restraint": {
                "question": "Am I interrupting too often?",
                "answer": (f"{suppressed} suppressed vs {surfaced} surfaced"
                           if (suppressed + surfaced) else "INSUFFICIENT EVIDENCE"),
                "evidence": suppressed + surfaced},
            "learning_from_failure": {
                "question": "Am I learning from failure?",
                "answer": (f"{counts.get('surprise.detected', 0)} surprises analysed"
                           if counts.get("surprise.detected") else "INSUFFICIENT EVIDENCE"),
                "evidence": counts.get("surprise.detected", 0)},
        }
