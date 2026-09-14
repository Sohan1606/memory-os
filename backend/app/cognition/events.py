"""
Canonical cognitive event bus.

Every meaningful state change in MEMORY//OS appends a structured event here.
Activity, Timeline, Causality, Learning and Research are all *derived* from this
log - no UI surface keeps its own parallel fake state.

Events are append-only. Subscribers are synchronous and must never raise into
the caller: an observability failure must not break a user conversation.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

log = logging.getLogger(__name__)

# --------------------------------------------------------------- event names
# Grouped by subsystem. This is the canonical vocabulary; anything emitted
# outside this set is rejected so the log cannot silently drift.
CONVERSATION = ("conversation.message", "conversation.response")
INTENT = ("intent.detected", "intent.updated", "intent.changed")
NEED = ("need.detected",)
MEMORY = (
    "memory.candidate", "memory.created", "memory.updated", "memory.reinforced",
    "memory.weakened", "memory.retired", "memory.restored", "memory.deleted",
    "memory.contradiction", "memory.validated", "memory.retrieved",
)
WORLD = ("world.created", "world.updated", "world.corrected")
GOAL = ("goal.created", "goal.updated", "goal.completed", "goal.abandoned",
        "goal.reactivated")
COMMITMENT = ("commitment.created", "commitment.updated", "commitment.completed",
              "commitment.missed")
PLAN = ("plan.created", "plan.updated", "plan.replanned")
PREDICTION = ("prediction.created", "prediction.evaluated", "prediction.correct",
              "prediction.incorrect")
INTERVENTION = ("intervention.considered", "intervention.suppressed",
                "intervention.presented", "intervention.accepted",
                "intervention.rejected")
ACTION = ("action.proposed", "action.authorized", "action.denied",
          "action.executed", "action.failed", "action.undone")
OUTCOME = ("outcome.recorded", "consequence.detected", "tradeoff.detected",
           "regret.detected", "surprise.detected")
CAUSAL = ("causal.linked", "causal.updated")
PRINCIPLE = ("principle.proposed", "principle.validated", "principle.updated")
SKILL = ("skill.proposed", "skill.validated", "skill.updated")
POLICY = ("policy.proposed", "policy.updated")
AUTONOMY = ("autonomy.changed", "trust.changed")
CONTEXT = ("context.ingested", "context.updated", "context.expired")
RECOVERY = ("recovery.started", "recovery.completed")
# v8.1 - model-assisted understanding, multimodal perception, background upkeep,
# memory health, missions and experiments.
EXTRACTION = ("extraction.completed", "extraction.failed")
PERCEPTION = ("perception.received", "perception.processed", "perception.rejected")
MAINTENANCE = ("maintenance.started", "maintenance.completed",
               "memory.quarantined", "memory.merged", "memory.stale")
MISSION = ("mission.created", "mission.updated", "mission.progressed",
           "mission.completed", "mission.abandoned")
EXPERIMENT = ("experiment.proposed", "experiment.started", "experiment.concluded")

EVENT_TYPES: frozenset[str] = frozenset(
    CONVERSATION + INTENT + NEED + MEMORY + WORLD + GOAL + COMMITMENT + PLAN
    + PREDICTION + INTERVENTION + ACTION + OUTCOME + CAUSAL + PRINCIPLE
    + SKILL + POLICY + AUTONOMY + CONTEXT + RECOVERY
    + EXTRACTION + PERCEPTION + MAINTENANCE + MISSION + EXPERIMENT
)

# Human-readable labels for the primary (non-technical) UI.
LABELS: dict[str, str] = {
    "conversation.message": "You spoke",
    "conversation.response": "Responded",
    "intent.detected": "Understood what you're working toward",
    "intent.updated": "Refined your objective",
    "intent.changed": "Noticed your objective changed",
    "need.detected": "Inferred what you need",
    "extraction.completed": "Understood this message with the language model",
    "extraction.failed": "Could not parse model understanding",
    "perception.received": "Received something to look at",
    "perception.processed": "Read what you shared",
    "perception.rejected": "Could not read what you shared",
    "maintenance.started": "Started routine memory upkeep",
    "maintenance.completed": "Finished routine memory upkeep",
    "memory.quarantined": "Set a questionable memory aside",
    "memory.merged": "Merged duplicate memories",
    "memory.stale": "Flagged a memory as possibly out of date",
    "mission.created": "Started tracking a long-term mission",
    "mission.updated": "Updated a mission",
    "mission.progressed": "Made progress on a mission",
    "mission.completed": "Completed a mission",
    "mission.abandoned": "Stopped tracking a mission",
    "experiment.proposed": "Suggested an experiment",
    "experiment.started": "Started an experiment",
    "experiment.concluded": "Concluded an experiment",
    "memory.candidate": "Noticed something worth remembering",
    "memory.created": "Remembered something new",
    "memory.updated": "Updated a memory",
    "memory.reinforced": "Reinforced a memory",
    "memory.weakened": "Weakened a memory",
    "memory.retired": "Retired an outdated memory",
    "memory.restored": "Restored a memory",
    "memory.deleted": "Forgot a memory",
    "memory.contradiction": "Found a contradiction",
    "memory.validated": "Validated a memory",
    "memory.retrieved": "Recalled relevant memory",
    "world.created": "Added to the world model",
    "world.updated": "Updated the world model",
    "world.corrected": "Corrected the world model",
    "goal.created": "Tracked a new goal",
    "goal.updated": "Updated a goal",
    "goal.completed": "Goal completed",
    "goal.abandoned": "Goal abandoned",
    "goal.reactivated": "Goal resumed",
    "commitment.created": "Tracked a commitment",
    "commitment.updated": "Updated a commitment",
    "commitment.completed": "Commitment met",
    "commitment.missed": "Commitment missed",
    "plan.created": "Formed a plan",
    "plan.updated": "Adjusted a plan",
    "plan.replanned": "Replanned",
    "prediction.created": "Made a prediction",
    "prediction.evaluated": "Checked a prediction against reality",
    "prediction.correct": "Prediction was right",
    "prediction.incorrect": "Prediction was wrong",
    "intervention.considered": "Considered speaking up",
    "intervention.suppressed": "Decided not to interrupt",
    "intervention.presented": "Raised something proactively",
    "intervention.accepted": "You accepted a suggestion",
    "intervention.rejected": "You declined a suggestion",
    "action.proposed": "Proposed an action",
    "action.authorized": "Action authorized",
    "action.denied": "Action needs your approval",
    "action.executed": "Performed an action",
    "action.failed": "An action failed",
    "action.undone": "Undid an action",
    "outcome.recorded": "Recorded an outcome",
    "consequence.detected": "Traced a consequence",
    "tradeoff.detected": "Identified a trade-off",
    "regret.detected": "Reassessed an earlier choice",
    "surprise.detected": "Reality differed from expectation",
    "causal.linked": "Linked cause and effect",
    "causal.updated": "Updated a causal link",
    "principle.proposed": "Proposed a principle",
    "principle.validated": "Validated a principle",
    "principle.updated": "Updated a principle",
    "skill.proposed": "Proposed a skill",
    "skill.validated": "Validated a skill",
    "skill.updated": "Updated a skill",
    "policy.proposed": "Proposed a behaviour change",
    "policy.updated": "Changed how I work with you",
    "autonomy.changed": "Autonomy level changed",
    "trust.changed": "Reliability estimate changed",
    "context.ingested": "Took in new context",
    "context.updated": "Context updated",
    "context.expired": "Context expired",
    "recovery.started": "Recovering from a failure",
    "recovery.completed": "Recovered",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class CognitiveEvent:
    """One immutable record of something the system actually did."""

    id: int
    user_id: str
    thread_id: str | None
    type: str
    subject_kind: str | None
    subject_id: str | None
    summary: str
    payload: dict[str, Any] = field(default_factory=dict)
    correlation_id: str | None = None
    created_at: str = ""

    @property
    def label(self) -> str:
        """Human-readable phrasing for the conversation-first UI."""
        return LABELS.get(self.type, self.type)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "user_id": self.user_id, "thread_id": self.thread_id,
            "type": self.type, "label": self.label,
            "subject_kind": self.subject_kind, "subject_id": self.subject_id,
            "summary": self.summary, "payload": self.payload,
            "correlation_id": self.correlation_id, "created_at": self.created_at,
        }


class EventBus:
    """Append-only cognitive event log backed by SQLite."""

    def __init__(self, db) -> None:
        self.db = db
        self._lock = threading.Lock()
        self._subscribers: list[Callable[[CognitiveEvent], None]] = []

    # ----------------------------------------------------------- subscribe
    def subscribe(self, fn: Callable[[CognitiveEvent], None]) -> None:
        """Register a synchronous subscriber (used by derived subsystems)."""
        self._subscribers.append(fn)

    # --------------------------------------------------------------- emit
    def emit(self, user_id: str, type: str, summary: str, *,
             thread_id: str | None = None, subject_kind: str | None = None,
             subject_id: str | None = None, payload: dict[str, Any] | None = None,
             correlation_id: str | None = None) -> CognitiveEvent:
        """
        Append an event. Unknown types are rejected loudly so the canonical
        vocabulary cannot drift through typos.
        """
        if type not in EVENT_TYPES:
            raise ValueError(f"Unknown cognitive event type: {type!r}")

        created = _now()
        body = json.dumps(payload or {}, default=str)
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO cognitive_events (user_id, thread_id, type, subject_kind,"
                " subject_id, summary, payload, correlation_id, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (user_id, thread_id, type, subject_kind, subject_id, summary,
                 body, correlation_id, created))
            event_id = int(cur.lastrowid or 0)

        event = CognitiveEvent(
            id=event_id, user_id=user_id, thread_id=thread_id, type=type,
            subject_kind=subject_kind, subject_id=subject_id, summary=summary,
            payload=payload or {}, correlation_id=correlation_id, created_at=created)

        for fn in self._subscribers:
            try:
                fn(event)
            except Exception:  # observability must never break the conversation
                log.exception("Cognitive event subscriber failed for %s", type)
        return event

    # --------------------------------------------------------------- reads
    def _row(self, r) -> CognitiveEvent:
        try:
            payload = json.loads(r["payload"]) if r["payload"] else {}
        except json.JSONDecodeError:
            payload = {}
        return CognitiveEvent(
            id=r["id"], user_id=r["user_id"], thread_id=r["thread_id"],
            type=r["type"], subject_kind=r["subject_kind"], subject_id=r["subject_id"],
            summary=r["summary"], payload=payload,
            correlation_id=r["correlation_id"], created_at=r["created_at"])

    def recent(self, user_id: str, limit: int = 100,
               types: list[str] | None = None) -> list[CognitiveEvent]:
        sql = "SELECT * FROM cognitive_events WHERE user_id = ?"
        params: list[Any] = [user_id]
        if types:
            sql += f" AND type IN ({','.join('?' * len(types))})"
            params.extend(types)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return [self._row(r) for r in self.db.query(sql, params)]

    def for_subject(self, subject_kind: str, subject_id: str) -> list[CognitiveEvent]:
        """Full history of one object - powers 'What changed?' and memory history."""
        return [self._row(r) for r in self.db.query(
            "SELECT * FROM cognitive_events WHERE subject_kind = ? AND subject_id = ?"
            " ORDER BY id ASC", (subject_kind, subject_id))]

    def for_correlation(self, correlation_id: str) -> list[CognitiveEvent]:
        """Every event emitted during one conversational turn."""
        return [self._row(r) for r in self.db.query(
            "SELECT * FROM cognitive_events WHERE correlation_id = ? ORDER BY id ASC",
            (correlation_id,))]

    def counts(self, user_id: str) -> dict[str, int]:
        rows = self.db.query(
            "SELECT type, COUNT(*) AS n FROM cognitive_events WHERE user_id = ?"
            " GROUP BY type", (user_id,))
        return {r["type"]: r["n"] for r in rows}

    def since(self, user_id: str, event_id: int, limit: int = 200) -> list[CognitiveEvent]:
        """Events newer than `event_id` - used for live activity polling."""
        return [self._row(r) for r in self.db.query(
            "SELECT * FROM cognitive_events WHERE user_id = ? AND id > ?"
            " ORDER BY id ASC LIMIT ?", (user_id, event_id, limit))]

    def purge_subject(self, subject_kind: str, subject_id: str) -> int:
        """
        Remove events for a subject (scoped forgetting).

        Used only by explicit user-requested deletion. Returns the row count so
        the caller can report honestly rather than pretending history vanished.
        """
        cur = self.db.execute(
            "DELETE FROM cognitive_events WHERE subject_kind = ? AND subject_id = ?",
            (subject_kind, subject_id))
        return cur.rowcount or 0
