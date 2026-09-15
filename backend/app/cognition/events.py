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

# --------------------------------------------------------------- v8.2 events
# Cognitive-core additions. Every one of these is emitted from work that
# genuinely happened; none are decorative.
CONTEXT_BUILD = ("context.assembled", "context.truncated", "context.degraded")
ARBITRATION = ("arbitration.resolved", "arbitration.conflict")
INFLUENCE = ("memory.influenced", "memory.impact")
EXECUTION = ("execution.model_call", "execution.tool_decision",
             "execution.tool_result", "execution.model_revision",
             "execution.final_response", "execution.cancelled",
             "execution.limit_reached")
ROUTING = ("routing.selected", "routing.degraded", "routing.not_configured")
CONTINUITY = ("continuity.resumed", "continuity.item_opened",
              "continuity.item_closed")
POLICY_V82 = ("policy.reverted",)
TRUST_V82 = ("trust.capability_changed", "trust.recovered")
FOCUS = ("focus.changed", "focus.resolved")
NEED_V82 = ("need.evaluated",)
CONTROL = ("control.command", "control.refused")

# --------------------------------------------------------------- v8.3 events
# Continuous-cognition additions (§3). Every category below is emitted from
# work that genuinely executed. Background cycles that found nothing still
# emit background.cycle_completed with an honest empty finding set.
PERCEPTION_V83 = ("perception.normalised", "perception.unsupported",
                  "perception.degraded", "document.ingested", "document.parsed",
                  "document.indexed", "document.stale", "document.replaced",
                  "document.removed")
WORLD_V83 = ("world.reconciled", "world.superseded", "world.merged",
             "world.flagged", "world.downgraded", "world.stale",
             "world.confirmed", "world.change_recorded")
MISSION_V83 = ("mission.activated", "mission.blocked", "mission.unblocked",
               "mission.waiting", "mission.paused", "mission.resumed",
               "mission.failed", "mission.step_added", "mission.step_completed",
               "mission.replanned", "mission.reviewed")
ATTENTION = ("attention.changed", "attention.evaluated", "attention.suppressed")
INTERVENTION_V83 = ("intervention.deferred", "intervention.escalated")
OBSERVATION = ("observation.recorded", "observation.linked",
               "observation.promoted", "observation.discarded")
OUTCOME_V83 = ("outcome.observed", "outcome.unresolved")
SIMULATION = ("simulation.started", "simulation.completed",
              "simulation.discarded", "simulation.committed")
BACKGROUND = ("background.cycle_started", "background.cycle_completed",
              "background.cycle_skipped", "background.cycle_cancelled",
              "background.cycle_failed", "background.disabled",
              "background.enabled", "background.paused", "background.resumed")
MAINTENANCE_V83 = ("memory.maintenance_started", "memory.maintenance_finding",
                   "memory.maintenance_completed", "memory.revalidated",
                   "memory.downgraded")
TIMEMACHINE = ("history.reconstructed", "history.unavailable")
CONNECTOR = ("connector.declared", "connector.unavailable")
RESEARCH = ("research.requested", "research.unavailable")

EVENT_TYPES: frozenset[str] = frozenset(
    CONVERSATION + INTENT + NEED + MEMORY + WORLD + GOAL + COMMITMENT + PLAN
    + PREDICTION + INTERVENTION + ACTION + OUTCOME + CAUSAL + PRINCIPLE
    + SKILL + POLICY + AUTONOMY + CONTEXT + RECOVERY
    + EXTRACTION + PERCEPTION + MAINTENANCE + MISSION + EXPERIMENT
    + CONTEXT_BUILD + ARBITRATION + INFLUENCE + EXECUTION + ROUTING
    + CONTINUITY + POLICY_V82 + TRUST_V82 + FOCUS + NEED_V82 + CONTROL
    + PERCEPTION_V83 + WORLD_V83 + MISSION_V83 + ATTENTION + INTERVENTION_V83
    + OBSERVATION + OUTCOME_V83 + SIMULATION + BACKGROUND + MAINTENANCE_V83
    + TIMEMACHINE + CONNECTOR + RESEARCH
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
    # ---------------------------------------------------------------- v8.2
    "context.assembled": "Gathered the context for this message",
    "context.truncated": "Trimmed context to stay within limits",
    "context.degraded": "Some context was unavailable",
    "arbitration.resolved": "Chose between competing memories",
    "arbitration.conflict": "Competing memories disagreed",
    "memory.influenced": "A memory shaped this decision",
    "memory.impact": "Updated a memory's track record",
    "execution.model_call": "Asked the language model",
    "execution.tool_decision": "Decided to use a tool",
    "execution.tool_result": "Got the tool result back",
    "execution.model_revision": "Reconsidered after the tool result",
    "execution.final_response": "Settled on a final answer",
    "execution.cancelled": "Stopped work early",
    "execution.limit_reached": "Hit the tool-loop limit and stopped",
    "routing.selected": "Chose how to run this",
    "routing.degraded": "Ran in a reduced mode",
    "routing.not_configured": "Could not run: capability not configured",
    "continuity.resumed": "Picked up where we left off",
    "continuity.item_opened": "Started tracking something to return to",
    "continuity.item_closed": "Closed off something we were tracking",
    "policy.reverted": "Undid a behaviour change",
    "trust.capability_changed": "Reliability estimate changed for a capability",
    "trust.recovered": "A capability became reliable again",
    "focus.changed": "You focused on something specific",
    "focus.resolved": "Understood which object you meant",
    "need.evaluated": "Checked whether I read your need correctly",
    "control.command": "You told me directly how to handle something",
    "control.refused": "Could not act on that instruction without more detail",
    # ----------------------------------------------------------- v8.3 labels
    "perception.normalised": "Normalised an input",
    "perception.unsupported": "Could not interpret an input",
    "perception.degraded": "Interpreted an input only partially",
    "document.ingested": "Received a document",
    "document.parsed": "Read a document",
    "document.indexed": "Indexed a document",
    "document.stale": "A document went out of date",
    "document.replaced": "A newer version replaced a document",
    "document.removed": "Removed a document",
    "world.reconciled": "Reconciled conflicting world information",
    "world.superseded": "Newer information replaced older",
    "world.merged": "Merged duplicate world entries",
    "world.flagged": "Flagged a world conflict for you",
    "world.downgraded": "Lowered confidence in world information",
    "world.stale": "World information may be out of date",
    "world.confirmed": "Confirmed world information is current",
    "world.change_recorded": "Recorded a world change",
    "mission.activated": "Started working a mission",
    "mission.blocked": "A mission became blocked",
    "mission.unblocked": "A mission is unblocked",
    "mission.waiting": "A mission is waiting on something",
    "mission.paused": "Paused a mission",
    "mission.resumed": "Resumed a mission",
    "mission.failed": "A mission failed",
    "mission.step_added": "Added a mission step",
    "mission.step_completed": "Completed a mission step",
    "mission.replanned": "Revised a mission plan",
    "mission.reviewed": "Reviewed a mission",
    "attention.changed": "What I'm paying attention to changed",
    "attention.evaluated": "Weighed whether to raise something",
    "attention.suppressed": "Chose to stay quiet",
    "intervention.deferred": "Held something for a better moment",
    "intervention.escalated": "Raised the urgency of something",
    "observation.recorded": "Recorded an observation",
    "observation.linked": "Linked an observation to what it concerns",
    "observation.promoted": "An observation became a memory",
    "observation.discarded": "Discarded an observation",
    "outcome.observed": "Observed how something turned out",
    "outcome.unresolved": "Still no evidence either way",
    "simulation.started": "Started a what-if",
    "simulation.completed": "Finished a what-if",
    "simulation.discarded": "Discarded a what-if",
    "simulation.committed": "Applied a what-if for real",
    "background.cycle_started": "Background upkeep started",
    "background.cycle_completed": "Background upkeep finished",
    "background.cycle_skipped": "Skipped background upkeep",
    "background.cycle_cancelled": "Cancelled background upkeep",
    "background.cycle_failed": "Background upkeep failed",
    "background.disabled": "Background cognition disabled",
    "background.enabled": "Background cognition enabled",
    "background.paused": "Background cognition paused",
    "background.resumed": "Background cognition resumed",
    "memory.maintenance_started": "Memory review started",
    "memory.maintenance_finding": "Found something worth reviewing",
    "memory.maintenance_completed": "Memory review finished",
    "memory.revalidated": "Re-checked a memory with you",
    "memory.downgraded": "Lowered confidence in a memory",
    "history.reconstructed": "Reconstructed an earlier state",
    "history.unavailable": "No history available for that point",
    "connector.declared": "Declared a connector interface",
    "connector.unavailable": "A connector is not connected",
    "research.requested": "Research was requested",
    "research.unavailable": "No research provider is configured",
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
