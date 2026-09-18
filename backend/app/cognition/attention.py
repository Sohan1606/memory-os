"""
Attention engine V2 (§15-§16).

V8.2's `AttentionEngine` scores a single decision from importance × urgency ×
confidence. It works, and V8.3 keeps it as the scoring core. What it lacked:

  * no notion of what the user is *currently* doing (interrupting mid-task is
    more expensive than mentioning something at a natural boundary)
  * no memory of how the user reacted last time
  * no learned silence - the cost of interrupting was a fixed constant

`AttentionEngineV2` wraps the V8.2 engine and adds those three, then keeps the
full ladder:

    IGNORE < MONITOR < PREPARE < MENTION < ASK < ACT

DO_NOTHING is a first-class, legitimate outcome, not a failure to decide. Every
suppression is recorded with its reasoning, because "chose to stay quiet" is a
real decision the user is entitled to inspect.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

LEVELS = ("IGNORE", "MONITOR", "PREPARE", "MENTION", "ASK", "ACT")
DO_NOTHING = "DO_NOTHING"

# Mapping from the V8.2 lower-case decisions to the V8.3 ladder.
_FROM_V82 = {"ignore": "IGNORE", "monitor": "MONITOR", "prepare": "PREPARE",
             "mention": "MENTION", "ask": "ASK", "act": "ACT"}

# How strongly a recent rejection raises the bar for interrupting again.
REJECTION_PENALTY = 0.12
MAX_REJECTION_PENALTY = 0.36
# How far back we look when learning the user's tolerance.
LEARNING_WINDOW = 40
# Below this many recorded reactions we do not claim to have learned anything.
MIN_REACTIONS_TO_LEARN = 4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AttentionEngineV2:
    """Relevance-gated attention with learned, evidence-based silence."""

    def __init__(self, db, bus, attention, missions=None, focus=None) -> None:
        self.db = db
        self.bus = bus
        self.base = attention          # V8.2 AttentionEngine - the scoring core
        self.missions = missions
        self.focus = focus

    # ------------------------------------------------------- learned silence
    def silence_policy(self, user_id: str) -> dict[str, Any]:
        """
        What the recorded reactions actually say about interrupting this user.

        With too little evidence this returns INSUFFICIENT EVIDENCE and the
        default interruption cost - it does not invent a preference.
        """
        rows = self.db.query(
            "SELECT type, payload FROM cognitive_events WHERE user_id=?"
            " AND type IN ('intervention.accepted','intervention.rejected')"
            " ORDER BY id DESC LIMIT ?", (user_id, LEARNING_WINDOW))
        accepted = sum(1 for r in rows if r["type"] == "intervention.accepted")
        rejected = sum(1 for r in rows if r["type"] == "intervention.rejected")
        total = accepted + rejected

        if total < MIN_REACTIONS_TO_LEARN:
            return {
                "learned": False,
                "accepted": accepted, "rejected": rejected, "total": total,
                "interruption_cost": 0.4,
                "verdict": "INSUFFICIENT EVIDENCE",
                "detail": (f"Only {total} recorded reaction(s); at least "
                           f"{MIN_REACTIONS_TO_LEARN} are needed before "
                           f"adapting how often I interrupt."),
            }

        # The more often interruptions were rejected, the more one costs.
        ratio = rejected / total
        penalty = min(MAX_REJECTION_PENALTY, ratio * MAX_REJECTION_PENALTY)
        cost = round(min(0.85, 0.4 + penalty), 3)
        if ratio >= 0.6:
            verdict = "PREFERS SILENCE"
        elif ratio <= 0.2:
            verdict = "RECEPTIVE"
        else:
            verdict = "MIXED"
        return {
            "learned": True,
            "accepted": accepted, "rejected": rejected, "total": total,
            "rejection_ratio": round(ratio, 3),
            "interruption_cost": cost,
            "verdict": verdict,
            "detail": (f"{rejected} of {total} recent interruptions were "
                       f"rejected; interruption cost set to {cost:.2f}."),
        }

    # --------------------------------------------------------------- deciding
    def evaluate(self, user_id: str, topic: str, *, importance: float,
                 urgency: float, confidence: float,
                 mission_id: str | None = None,
                 interruption_cost: float | None = None,
                 relevance: float | None = None,
                 correlation_id: str | None = None) -> dict[str, Any]:
        """
        Decide what to do about `topic`, on the full ladder.

        `relevance` (0-1) is how related this is to what the user is doing right
        now. Low relevance does not silence something important, but it does
        make interrupting more expensive.
        """
        policy = self.silence_policy(user_id)
        cost = (interruption_cost if interruption_cost is not None
                else policy["interruption_cost"])

        # Interrupting about something unrelated to the current focus costs more.
        if relevance is not None:
            cost = round(min(0.9, cost + (1.0 - max(0.0, min(1.0, relevance))) * 0.15), 3)

        # A topic attached to an active mission is inherently more relevant.
        mission_note = None
        if mission_id and self.missions is not None:
            mission = self.missions.get(user_id, mission_id)
            if mission and mission["state"] == "active":
                cost = round(max(0.1, cost - 0.08), 3)
                mission_note = (f"Related to active mission "
                                f"'{mission['title']}'.")
            elif mission and mission["state"] in ("paused", "abandoned"):
                cost = round(min(0.9, cost + 0.15), 3)
                mission_note = (f"Mission '{mission['title']}' is "
                                f"{mission['state']}; raising the bar.")

        base = self.base.consider(
            user_id, topic, importance=importance, urgency=urgency,
            confidence=confidence, interruption_cost=cost,
            correlation_id=correlation_id)

        level = _FROM_V82.get(base["decision"], "MONITOR")
        expected_value = importance * urgency * confidence

        # DO_NOTHING is legitimate and distinct from IGNORE: IGNORE means "not
        # worth tracking", DO_NOTHING means "real, but no action is right now".
        if level == "IGNORE" and expected_value >= 0.08:
            level = DO_NOTHING

        reasoning = [
            f"importance {importance:.2f} × urgency {urgency:.2f} × confidence "
            f"{confidence:.2f} = {expected_value:.2f}",
            f"interruption cost {cost:.2f} ({policy['verdict']})",
        ]
        if relevance is not None:
            reasoning.append(f"relevance to current focus {relevance:.2f}")
        if mission_note:
            reasoning.append(mission_note)

        result = {
            "level": level,
            "decision": base["decision"],       # V8.2 compatibility
            "intervention_id": base.get("id"),
            "topic": topic,
            "expected_value": round(expected_value, 3),
            "interruption_cost": cost,
            "silence_policy": policy["verdict"],
            "reasoning": reasoning,
            "explanation": "; ".join(reasoning),
            "acted": level in ("MENTION", "ASK", "ACT"),
        }

        self.bus.emit(user_id, "attention.evaluated", f"{topic} → {level}",
                      subject_kind="intervention",
                      subject_id=base.get("id"),
                      correlation_id=correlation_id,
                      payload={"level": level,
                               "expected_value": result["expected_value"],
                               "interruption_cost": cost})
        if level in ("IGNORE", DO_NOTHING, "MONITOR"):
            self.bus.emit(user_id, "attention.suppressed",
                          f"Stayed quiet about: {topic}",
                          subject_kind="intervention",
                          subject_id=base.get("id"),
                          correlation_id=correlation_id,
                          payload={"level": level,
                                   "why": result["explanation"]})
            if base.get("id"):
                self.db.execute(
                    "UPDATE interventions SET suppressed_because=?, mission_id=?"
                    " WHERE id=?",
                    (result["explanation"], mission_id, base["id"]))
        elif mission_id and base.get("id"):
            self.db.execute("UPDATE interventions SET mission_id=? WHERE id=?",
                            (mission_id, base["id"]))
        return result

    # ---------------------------------------------------------------- focus
    def set_attention(self, user_id: str, subject_kind: str, subject_id: str,
                      label: str, *,
                      correlation_id: str | None = None) -> dict[str, Any]:
        """Record a change in what the system is paying attention to."""
        self.bus.emit(user_id, "attention.changed", label,
                      subject_kind=subject_kind, subject_id=subject_id,
                      correlation_id=correlation_id,
                      payload={"subject_kind": subject_kind,
                               "subject_id": subject_id})
        return {"subject_kind": subject_kind, "subject_id": subject_id,
                "label": label, "changed_at": _now()}

    def record_reaction(self, user_id: str, intervention_id: str,
                        accepted: bool, *, detail: str | None = None,
                        correlation_id: str | None = None) -> dict[str, Any]:
        """
        Record how the user actually reacted. This is the ONLY input to the
        learned silence policy - it never infers acceptance from silence.
        """
        event = ("intervention.accepted" if accepted
                 else "intervention.rejected")
        self.bus.emit(user_id, event, detail or ("Accepted" if accepted
                                                 else "Rejected"),
                      subject_kind="intervention", subject_id=intervention_id,
                      correlation_id=correlation_id,
                      payload={"accepted": accepted, "detail": detail})
        return {"recorded": True, "accepted": accepted,
                "policy": self.silence_policy(user_id)}

    # --------------------------------------------------------------- reading
    def recent(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM interventions WHERE user_id=? ORDER BY created_at DESC"
            " LIMIT ?", (user_id, int(limit)))
        return [dict(r) for r in rows]

    def suppressions(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        """Everything the system decided not to raise, and why."""
        rows = self.db.query(
            "SELECT * FROM interventions WHERE user_id=? AND"
            " suppressed_because IS NOT NULL ORDER BY created_at DESC LIMIT ?",
            (user_id, int(limit)))
        return [dict(r) for r in rows]
