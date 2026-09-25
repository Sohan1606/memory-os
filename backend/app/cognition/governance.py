"""
V10.2 — Evidence-Governed Adaptive Cognitive Policy (CORE + runtime).

This module is a GOVERNANCE LAYER ABOVE the released V8.2
``CognitivePolicyEngine``. It is not a second policy engine and never stores a
competing effective policy value: current effective policy lives in the
``policies`` table and is read only through ``CognitivePolicyEngine``.

Binding rules implemented here (docs/V10.2-MASTER-SPEC.md):

- **Policy state is not evidence for policy adaptation** (§6.B, §10.6).
  ``PolicyEvidenceAssembler`` emits only admissible canonical records:
  evaluated predictions, model-error records, resolved decision outcomes,
  intervention outcomes (accepted/rejected/suppressed), autonomy dispositions
  with observed consequences, live Skills/Principles (by reference), and
  canonical outcome events. Policy rows and policy/governance events are
  structurally inadmissible.
- **Learning signals are deterministic** (§7): pure counting/matching over
  structured canonical fields. No model call, no free-text keyword guessing,
  no second learning engine.
- **Class-B gating** (§6.1-§6.5): N ≥ 3 supporting episodes, distinct
  correlation ids, temporal spread, freshness, conflict ratio, and an explicit
  ``INSUFFICIENT_EVIDENCE`` verdict. A single anecdote never creates a
  candidate; current policy state never increases the count.
- **Governance lifecycle** (§8): CANDIDATE → VALIDATING → PROPOSED → ACCEPTED
  → ACTIVE with the spec's allowed exits. Only ``PolicyGovernanceService``
  mutates governance state, append-only, every transition on the canonical
  EventBus as ``governance.transition``.
- **Confirmation boundary** (§9): PROPOSED → ACCEPTED happens only through
  ``confirm()`` — explicit confirmation plus the existing AutonomyGovernor.
  ACCEPTED/ACTIVE/PROPOSED/VALIDATING are unreachable through the generic
  ``transition()`` method. No auto-accept window exists.
- **Canonical mutation** (C8/C9): an accepted adaptation of an existing
  dimension is applied through ``CognitivePolicyEngine.observe()`` with the
  ``v10_2_governed_adaptation`` origin label (never recorded as a direct user
  utterance), so the engine's vocabulary validation, confidence rules,
  reversibility and its own ``policy.updated`` event all still hold.
- **Runtime bounds** (§10): one top-level governance evaluation per turn
  correlation (persisted, idempotent), terminal correlation markers, no
  subscriptions, no scheduler, no recursion. Sibling of the V10.1
  ``MaintenanceRuntimeCoordinator``, not a duplicate of it.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .events import EventBus
from .maintenance_runtime import REAUDIT_SUFFIX
from .policy_engine import (DIMENSIONS, ORIGIN_GOVERNED, STRONG,
                            CognitivePolicyEngine)
from .autonomy import AutonomyGovernor

log = logging.getLogger(__name__)

# ----------------------------------------------------------- CORE constants
# Every constant is fixed and named per spec §22.2. Class-B minimums are never
# weaker than the engine's released WEAK_EVIDENCE_REQUIRED = 3.

DOMAINS = ("ATTENTION", "DECISION", "AUTONOMY", "MODEL_HANDLING")

# Targets come from finite, enumerated vocabularies. ATTENTION/DECISION/AUTONOMY
# target existing CognitivePolicyEngine dimensions; MODEL_HANDLING is the new
# V10.2 domain (§5.4) and is GOVERNANCE-ONLY in this build — see
# MODEL_HANDLING_ACTIVATABLE.
DOMAIN_TARGETS: dict[str, tuple[str, ...]] = {
    "ATTENTION": ("interruption_tolerance", "silence_tolerance"),
    "DECISION": ("planning_preference", "recommendation_preference"),
    "AUTONOMY": ("autonomy_preference",),
    "MODEL_HANDLING": ("model_handling_strategy",),
}

# Finite strategy vocabulary for the MODEL_HANDLING domain, fixed at CORE
# phase. These describe bounded handling among strategies the system already
# has; none enables an unavailable capability or silences honest degradation.
MODEL_HANDLING_STRATEGIES = ("prefer_deterministic", "require_verification",
                             "degrade_conservatively")

# MODEL_HANDLING has NO safe consumer seam in this build: there is no generic
# model-handling policy engine, and the CapabilityRouter's truthfulness must
# stay untouched. The domain is therefore governance-only — candidates may be
# derived, validated and proposed, but activation is refused. This is an
# honest boundary, not a missing feature silently faked.
MODEL_HANDLING_ACTIVATABLE = False

# Class-B evidence gate (§6.1). N ≥ 3 independent episodes, never weaker than
# the engine's WEAK_EVIDENCE_REQUIRED.
MIN_EPISODES = 3
MIN_DISTINCT_CORRELATIONS = 3
# Temporal spread guard: episodes must span at least two distinct days for the
# ATTENTION/DECISION/AUTONOMY domains (§6.1).
MIN_DISTINCT_DAYS = 2
# Freshness horizons per domain (§6.3): stale evidence is retained but stops
# counting toward sufficiency.
FRESHNESS_DAYS: dict[str, int] = {
    "ATTENTION": 90, "DECISION": 90, "AUTONOMY": 90, "MODEL_HANDLING": 30,
}
# A candidate whose fresh conflicting evidence exceeds this ratio of its fresh
# support cannot leave VALIDATING (§6.4) and is reported with both sides.
CONFLICT_RATIO_MAX = 0.34
# At most one candidate may be created per bounded turn (§10.3).
MAX_CANDIDATES_PER_TURN = 1
# Safety asymmetry (§12.4): measured HARMFUL observations may trigger a
# deterministic WEAKEN below the general measurement minimum of 3.
HARM_WEAKEN_MIN = 2

# Terminal correlation marker for bounded governance work, the sibling of the
# V10.1 `::reaudit1` marker. A correlation carrying this suffix never triggers
# maintenance, re-audit, or further governance evaluation (§10.4).
GOVERNANCE_MARKER = "::pgov1"

# The strength a user-confirmed governed adaptation carries into the engine.
# Numerically equal to STRONG (the user explicitly confirmed that exact
# change), but persisted with the distinct ORIGIN_GOVERNED label so governed
# adaptations are never recorded as explicit user utterances.
GOVERNED_STRENGTH = STRONG

# Governance lifecycle (§8.2). ACTIVE → REJECTED exists only for explicit user
# rejection of an applied adaptation (§13); it is reachable only through
# ``reject()``.
STATES = ("CANDIDATE", "VALIDATING", "PROPOSED", "ACCEPTED", "ACTIVE",
          "WEAKENED", "DEFERRED", "SUPERSEDED", "EXPIRED", "REJECTED")
TRANSITIONS: dict[str, frozenset[str]] = {
    "CANDIDATE": frozenset({"VALIDATING", "REJECTED"}),
    "VALIDATING": frozenset({"PROPOSED", "REJECTED"}),
    "PROPOSED": frozenset({"ACCEPTED", "REJECTED"}),
    "ACCEPTED": frozenset({"ACTIVE", "REJECTED"}),
    "ACTIVE": frozenset({"WEAKENED", "DEFERRED", "SUPERSEDED", "EXPIRED",
                         "REJECTED"}),
    "WEAKENED": frozenset({"DEFERRED", "SUPERSEDED", "EXPIRED"}),
    "DEFERRED": frozenset({"PROPOSED", "SUPERSEDED", "EXPIRED"}),
    "SUPERSEDED": frozenset(),
    "EXPIRED": frozenset(),
    "REJECTED": frozenset(),
}
# States reachable only through a dedicated authority method (confirmation
# boundary, deterministic validation) — never through generic transition().
RESTRICTED_TRANSITIONS = frozenset({"ACCEPTED", "ACTIVE", "PROPOSED",
                                    "VALIDATING", "CANDIDATE"})

# Runtime statuses (sibling discipline to the V10.1 maintenance statuses).
NO_GOVERNANCE_NEEDED = "NO_GOVERNANCE_NEEDED"
GOVERNANCE_COMPLETED = "GOVERNANCE_COMPLETED"
WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"
PROPOSAL_CREATED = "PROPOSAL_CREATED"
GOVERNANCE_TERMINAL = "GOVERNANCE_TERMINAL"
GOVERNANCE_FAILED = "GOVERNANCE_FAILED"

# ------------------------------------------------------------------ evidence
# Canonical class-B evidence kinds. Every item is an actual canonical record
# that genuinely occurred, scope-bound to the verified user namespace.
EVIDENCE_KINDS = ("evaluated_prediction", "model_error", "decision_outcome",
                  "intervention_outcome", "autonomy_disposition",
                  "learning_reference", "outcome_record")

# Canonical EventBus record types that are admissible outcome evidence. Policy
# and governance events are deliberately absent: policy state is never
# evidence for policy adaptation (§10.6), and an adaptation's own events can
# never support its promotion.
ADMISSIBLE_EVENT_TYPES = ("outcome.observed", "intervention.accepted",
                          "intervention.rejected")
# Outcome-shaped event types used only by the runtime relevance pre-gate.
OUTCOME_SIGNAL_EVENT_TYPES = (
    "intervention.presented", "intervention.suppressed",
    "intervention.accepted", "intervention.rejected",
    "action.authorized", "action.denied", "action.executed", "action.failed",
    "prediction.evaluated", "prediction.correct", "prediction.incorrect",
    "outcome.observed",
)
# Event types that advance the per-user evaluation watermark: the incremental
# cursor mirroring the V10.1 "unevaluated canonical record" trigger. Outcome
# records land between turns (a reaction to an intervention, a resolved
# decision), so the turn that should evaluate them is the NEXT one — the
# watermark makes that deterministic without evaluating on ordinary turns.
WATERMARK_EVENT_TYPES = OUTCOME_SIGNAL_EVENT_TYPES + (
    "outcome.recorded", "model_error.classified",
)


@dataclass(frozen=True)
class Evidence:
    """One admissible canonical record, scoped and provenance-carrying (§6.2)."""

    id: str                  # canonical reference "kind:record_id"
    kind: str                # EVIDENCE_KINDS member
    record_id: str           # resolvable canonical record id
    provenance: str          # the canonical service that produced the record
    correlation_id: str      # turn correlation (record id fallback)
    timestamp: str           # canonical record timestamp
    epistemic_state: str     # OBSERVED at the leaf
    scope: str               # verified user namespace
    payload: dict[str, Any] = field(default_factory=dict)

    def as_ref(self) -> dict[str, str]:
        """The frozen-by-reference form stored on a governed adaptation."""
        return {"kind": self.kind, "id": self.record_id,
                "provenance": self.provenance,
                "correlation_id": self.correlation_id}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace(" ", "T")
    for candidate in (text, text[:19]):
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def _day(value: Any) -> str | None:
    parsed = _parse_ts(value)
    return parsed.date().isoformat() if parsed else None


def _age_days(value: Any, as_of: datetime) -> float | None:
    parsed = _parse_ts(value)
    if parsed is None:
        return None
    return max(0.0, (as_of - parsed).total_seconds() / 86400.0)


def evaluate_evidence_gate(domain: str, supporting: list[Evidence],
                           conflicting: list[Evidence], *,
                           as_of: datetime | None = None) -> dict[str, Any]:
    """Deterministic class-B sufficiency gate (§6.1-§6.4).

    Pure counting over structured evidence. Never consults policy state, never
    calls a model, and returns an explicit verdict with counts — including
    ``INSUFFICIENT_EVIDENCE`` with the honest "n of N episodes, window W" form.
    """
    as_of = as_of or datetime.now(timezone.utc)
    horizon = FRESHNESS_DAYS.get(domain, 90)

    def fresh(items: list[Evidence]) -> list[Evidence]:
        out, seen = [], set()
        for item in items:
            age = _age_days(item.timestamp, as_of)
            if age is None or age > horizon or item.id in seen:
                continue
            seen.add(item.id)
            out.append(item)
        return out

    fresh_support = fresh(supporting)
    fresh_conflict = fresh(conflicting)
    episodes = len(fresh_support)
    correlations = len({e.correlation_id for e in fresh_support})
    days = len({_day(e.timestamp) for e in fresh_support} - {None})
    conflict_ratio = round(len(fresh_conflict) / max(1, episodes), 3)

    threshold = {"min_episodes": MIN_EPISODES,
                 "min_distinct_correlations": MIN_DISTINCT_CORRELATIONS,
                 "min_distinct_days": (
                     0 if domain == "MODEL_HANDLING" else MIN_DISTINCT_DAYS),
                 "freshness_days": horizon,
                 "max_conflict_ratio": CONFLICT_RATIO_MAX}
    counts = {"episode_count": episodes,
              "distinct_correlations": correlations,
              "distinct_days": days,
              "conflict_count": len(fresh_conflict),
              "conflict_ratio": conflict_ratio,
              "window_days": horizon,
              "stale_excluded": (len(supporting) - episodes)}

    if (episodes < MIN_EPISODES or correlations < MIN_DISTINCT_CORRELATIONS
            or (domain != "MODEL_HANDLING" and days < MIN_DISTINCT_DAYS)):
        return {"verdict": "INSUFFICIENT_EVIDENCE", "threshold": threshold,
                "counts": counts,
                "reason": (f"INSUFFICIENT EVIDENCE — {episodes} of "
                           f"{MIN_EPISODES} episodes, {correlations} distinct "
                           f"correlation(s), {days} distinct day(s), window "
                           f"{horizon}d.")}
    if conflict_ratio > CONFLICT_RATIO_MAX:
        return {"verdict": "CONFLICTING_EVIDENCE", "threshold": threshold,
                "counts": counts,
                "supporting_ids": [e.id for e in fresh_support],
                "conflicting_ids": [e.id for e in fresh_conflict],
                "reason": (f"CONFLICTING EVIDENCE — {len(fresh_conflict)} "
                           f"conflicting episode(s) vs {episodes} supporting "
                           f"(ratio {conflict_ratio} > {CONFLICT_RATIO_MAX}).")}
    return {"verdict": "SUFFICIENT", "threshold": threshold, "counts": counts,
            "supporting_ids": [e.id for e in fresh_support],
            "conflicting_ids": [e.id for e in fresh_conflict],
            "reason": (f"{episodes} fresh independent episodes across "
                       f"{correlations} correlation(s) and {days} day(s); "
                       f"{len(fresh_conflict)} conflicting.")}


class PolicyEvidenceAssembler:
    """Deterministic, read-only assembly of admissible class-B evidence (§6.B).

    Assembles ONLY canonical records that genuinely occurred, scope-bound at
    the query level to the verified user namespace. Policy rows are never
    queried: policy state is context, never evidence. Nothing here mutates,
    calls a model, or scrapes free text.
    """

    def __init__(self, db, bus: EventBus, *, tenant_id: str = "local") -> None:
        self.db = db
        self.bus = bus
        self.tenant_id = tenant_id

    # ------------------------------------------------------- scope helpers
    def _tenant(self, user_id: str) -> str:
        """Resolve the tenant from the canonical V8.5 identity namespace."""
        row = self.db.query_one(
            "SELECT tenant_id FROM auth_users WHERE namespace=?", (user_id,))
        return str(row["tenant_id"]) if row else self.tenant_id

    def _latest_event(self, user_id: str, types: tuple[str, ...],
                      subject_kind: str, subject_id: str):
        placeholders = ",".join("?" * len(types))
        return self.db.query_one(
            f"SELECT * FROM cognitive_events WHERE user_id=? AND type IN "
            f"({placeholders}) AND subject_kind=? AND subject_id=? "
            f"ORDER BY id DESC LIMIT 1",
            (user_id, *types, subject_kind, subject_id))

    def _events(self, user_id: str, types: tuple[str, ...], *,
                correlation_id: str | None = None, limit: int = 200) -> list[Any]:
        """Canonical event rows as light objects (id, type, subject_id,
        correlation_id, created_at, payload parsed)."""
        placeholders = ",".join("?" * len(types))
        sql = (f"SELECT * FROM cognitive_events WHERE user_id=? AND type IN "
               f"({placeholders})")
        params: list[Any] = [user_id, *types]
        if correlation_id is not None:
            sql += " AND correlation_id=?"
            params.append(correlation_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        class _Event:
            __slots__ = ("id", "type", "subject_id", "subject_kind",
                         "correlation_id", "created_at", "summary", "payload")

            def __init__(self, row) -> None:
                self.id = row["id"]
                self.type = row["type"]
                self.subject_id = row["subject_id"]
                self.subject_kind = row["subject_kind"]
                self.correlation_id = row["correlation_id"]
                self.created_at = row["created_at"]
                self.summary = row["summary"]
                try:
                    self.payload = json.loads(row["payload"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    self.payload = {}

        return [_Event(r) for r in self.db.query(sql, params)]

    # ------------------------------------------------------------- assembly
    def assemble(self, user_id: str, *, correlation_id: str | None = None,
                 limit: int = 200) -> list[Evidence]:
        """Assemble the admissible evidence set for a verified user namespace.

        When ``correlation_id`` is given, only records canonically correlated
        to that turn are returned (used by measurement, never by candidacy —
        a single turn can never satisfy the independence gate).
        """
        out: dict[str, Evidence] = {}
        tenant = self._tenant(user_id)

        def keep(item: Evidence) -> None:
            if correlation_id is not None and item.correlation_id != correlation_id:
                return
            out.setdefault(item.id, item)

        # Evaluated predictions only (§6.B/L1): an unevaluated prediction is
        # not evidence. The PredictionEngine remains the single evaluator.
        for row in self.db.query(
                "SELECT * FROM predictions WHERE user_id=? AND status IN "
                "('correct','incorrect') AND evaluated_at IS NOT NULL "
                "ORDER BY datetime(evaluated_at) DESC LIMIT ?",
                (user_id, limit)):
            event = self._latest_event(
                user_id, ("prediction.evaluated", "prediction.correct",
                          "prediction.incorrect"),
                "prediction", row["id"])
            keep(Evidence(
                id=f"evaluated_prediction:{row['id']}", kind="evaluated_prediction",
                record_id=row["id"], provenance="PredictionEngine",
                correlation_id=(event["correlation_id"] if event and event["correlation_id"]
                                else row["id"]),
                timestamp=row["evaluated_at"], epistemic_state="OBSERVED",
                scope=user_id,
                payload={"statement": row["statement"],
                         "status": row["status"],
                         "confidence": float(row["confidence"] or 0.0),
                         "error": (float(row["error"]) if "error" in row.keys()
                                   and row["error"] is not None else None),
                         "surprise": (float(row["surprise"]) if "surprise" in row.keys()
                                      and row["surprise"] is not None else None)}))

        # Model-error records (ModelErrorService classifications).
        for row in self.db.query(
                "SELECT * FROM model_error_records WHERE user_id=? AND tenant_id=? "
                "ORDER BY datetime(created_at) DESC LIMIT ?",
                (user_id, tenant, limit)):
            keep(Evidence(
                id=f"model_error:{row['id']}", kind="model_error",
                record_id=row["id"], provenance="ModelErrorService",
                correlation_id=(row["correlation_id"] or row["id"]),
                timestamp=row["created_at"], epistemic_state="OBSERVED",
                scope=user_id,
                payload={"error_class": row["error_class"],
                         "prediction_id": row["prediction_id"],
                         "expected_state": row["expected_state"],
                         "actual_observation": row["actual_observation"]}))

        # Resolved decision outcomes (DecisionLog). `positive` is read from the
        # canonical outcome.recorded event payload — the structured field the
        # DecisionLog itself persists on resolution.
        for row in self.db.query(
                "SELECT * FROM decisions WHERE user_id=? AND status='resolved' "
                "AND actual_outcome IS NOT NULL "
                "ORDER BY datetime(resolved_at) DESC LIMIT ?",
                (user_id, limit)):
            event = self._latest_event(user_id, ("outcome.recorded",),
                                        "decision", row["id"])
            positive = None
            if event:
                try:
                    payload = json.loads(event["payload"] or "{}")
                    positive = payload.get("positive")
                except (json.JSONDecodeError, TypeError):
                    positive = None
            keep(Evidence(
                id=f"decision_outcome:{row['id']}", kind="decision_outcome",
                record_id=row["id"], provenance="DecisionLog",
                correlation_id=(event["correlation_id"] if event and event["correlation_id"]
                                else row["id"]),
                timestamp=row["resolved_at"] or row["created_at"],
                epistemic_state="OBSERVED", scope=user_id,
                payload={"summary": row["summary"], "chosen": row["chosen"],
                         "actual_outcome": row["actual_outcome"],
                         "regret": (float(row["regret"]) if row["regret"] is not None
                                    else None),
                         "positive": positive}))

        # Intervention outcomes: the canonical intervention row plus the
        # recorded user reaction (accepted/rejected) or the system's own
        # suppression. A presented intervention with no recorded reaction is
        # evidence of the presentation only — never guessed as accepted.
        for row in self.db.query(
                "SELECT * FROM interventions WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)):
            reaction = self._latest_event(
                user_id, ("intervention.accepted", "intervention.rejected"),
                "intervention", row["id"])
            verdict: str | None
            correlation = None
            if reaction:
                verdict = ("accepted" if reaction["type"] == "intervention.accepted"
                           else "rejected")
                correlation = reaction["correlation_id"]
            elif row["decision"] in ("ignore", "monitor"):
                verdict = "suppressed"
            else:
                verdict = None  # presented, no recorded user reaction
            if correlation is None:
                considered = self._latest_event(
                    user_id, ("intervention.considered", "intervention.presented",
                              "intervention.suppressed"),
                    "intervention", row["id"])
                correlation = (considered["correlation_id"]
                               if considered and considered["correlation_id"] else None)
            keep(Evidence(
                id=f"intervention_outcome:{row['id']}",
                kind="intervention_outcome", record_id=row["id"],
                provenance="AttentionEngine",
                correlation_id=correlation or row["id"],
                timestamp=row["created_at"], epistemic_state="OBSERVED",
                scope=user_id,
                payload={"topic": row["topic"], "decision": row["decision"],
                         "verdict": verdict, "rationale": row["rationale"]}))

        # Autonomy dispositions with observed consequences: the canonical
        # action.authorized/denied event, paired with the terminal
        # action.executed/action.failed record of the SAME turn. A disposition
        # without an observed consequence is retained but unclassified.
        for event in self._events(
                user_id, ("action.authorized", "action.denied"), limit=limit):
            payload = dict(event.payload or {})
            consequence = None
            for terminal in self._events(
                    user_id, ("action.executed", "action.failed"),
                    correlation_id=event.correlation_id, limit=50):
                if terminal.subject_id == event.subject_id:
                    consequence = ("clean" if terminal.type == "action.executed"
                                   else "failed")
                    break
            keep(Evidence(
                id=f"autonomy_disposition:{event.id}",
                kind="autonomy_disposition", record_id=str(event.id),
                provenance="AutonomyGovernor",
                correlation_id=event.correlation_id or str(event.id),
                timestamp=event.created_at, epistemic_state="OBSERVED",
                scope=user_id,
                payload={"action": event.subject_id,
                         "disposition": payload.get("disposition"),
                         "risk": payload.get("risk"),
                         "allowed": payload.get("allowed"),
                         "consequence": consequence}))

        # Live Skills/Principles, referenced never copied (§13).
        for row in self.db.query(
                "SELECT * FROM knowledge_items WHERE user_id=? AND kind IN "
                "('skill','principle') AND lifecycle IN "
                "('trusted','used','reinforced') ORDER BY datetime(updated_at) DESC "
                "LIMIT ?", (user_id, max(10, limit // 4))):
            keep(Evidence(
                id=f"learning_reference:{row['id']}", kind="learning_reference",
                record_id=row["id"], provenance="KnowledgeService",
                correlation_id=row["id"], timestamp=row["updated_at"],
                epistemic_state="OBSERVED", scope=user_id,
                payload={"item_kind": row["kind"], "name": row["name"],
                         "statement": row["statement"],
                         "lifecycle": row["lifecycle"]}))

        # Canonical outcome events that have no dedicated table (the EventBus
        # record IS the canonical record here). Only the explicit allowlist —
        # policy.* and governance.* are structurally inadmissible.
        for event in self._events(user_id, ADMISSIBLE_EVENT_TYPES, limit=limit):
            keep(Evidence(
                id=f"outcome_record:{event.id}", kind="outcome_record",
                record_id=str(event.id), provenance="EventBus",
                correlation_id=event.correlation_id or str(event.id),
                timestamp=event.created_at, epistemic_state="OBSERVED",
                scope=user_id,
                payload={"event_type": event.type,
                         "subject_kind": event.subject_kind,
                         "subject_id": event.subject_id,
                         "summary": event.summary}))

        return sorted(out.values(), key=lambda e: (e.timestamp, e.id))


# ------------------------------------------------------------ learning signal
# Deterministic derivation rules (§7). Each rule is pure counting/matching over
# structured evidence fields — no model call, no keyword guessing.

@dataclass(frozen=True)
class _SignalRule:
    name: str
    domain: str | None
    target: str | None
    proposed_value: str | None
    supporting: Callable[[Evidence], bool]
    conflicting: Callable[[Evidence], bool]
    derivation: str
    expected_effect: dict[str, Any] | None
    value_resolver: Callable[[dict[str, str]], str | None] | None = None


def _autonomy_step_up(current: dict[str, str]) -> str | None:
    """One conservative step toward more autonomy, never a jump (§5.3)."""
    return {"ask_first": "act_low_risk", "act_low_risk": "act"}.get(
        current.get("autonomy_preference", "ask_first"))


_SIGNAL_RULES: tuple[_SignalRule, ...] = (
    _SignalRule(
        name="REPEATED_FALSE_POSITIVE_INTERVENTION", domain="ATTENTION",
        target="interruption_tolerance", proposed_value="low",
        supporting=lambda e: (e.kind == "intervention_outcome"
                              and e.payload.get("verdict") == "rejected"),
        conflicting=lambda e: (e.kind == "intervention_outcome"
                               and e.payload.get("verdict") == "accepted"),
        derivation=("intervention_outcome.verdict == 'rejected' (canonical "
                    "intervention.rejected reaction records) × N≥3 fresh "
                    "independent episodes"),
        expected_effect={"success": ("intervention_accepted",),
                         "harm": ("intervention_rejected",)}),
    _SignalRule(
        name="REPEATED_SUCCESSFUL_INTERVENTION", domain="ATTENTION",
        target="interruption_tolerance", proposed_value="high",
        supporting=lambda e: (e.kind == "intervention_outcome"
                              and e.payload.get("verdict") == "accepted"),
        conflicting=lambda e: (e.kind == "intervention_outcome"
                               and e.payload.get("verdict") == "rejected"),
        derivation=("intervention_outcome.verdict == 'accepted' (canonical "
                    "intervention.accepted reaction records) × N≥3 fresh "
                    "independent episodes"),
        expected_effect={"success": ("intervention_accepted",),
                         "harm": ("intervention_rejected",)}),
    _SignalRule(
        name="REPEATED_PREDICTION_FAILURE_OF_CLASS", domain=None, target=None,
        proposed_value=None,
        supporting=lambda e: (e.kind == "evaluated_prediction"
                              and e.payload.get("status") == "incorrect"),
        conflicting=lambda e: (e.kind == "evaluated_prediction"
                               and e.payload.get("status") == "correct"),
        derivation=("evaluated_prediction.status == 'incorrect' (predictions "
                    "evaluated through PredictionEngine.observe/evaluate) "
                    "× N≥3 fresh independent episodes"),
        expected_effect=None),
    _SignalRule(
        name="REPEATED_SUCCESSFUL_DECISION_PATTERN", domain=None, target=None,
        proposed_value=None,
        supporting=lambda e: (e.kind == "decision_outcome"
                              and e.payload.get("positive") is True),
        conflicting=lambda e: (e.kind == "decision_outcome"
                               and e.payload.get("positive") is False),
        derivation=("decision_outcome.positive is True (resolved DecisionLog "
                    "rows with their canonical outcome.recorded verdict) "
                    "× N≥3 fresh independent episodes"),
        expected_effect=None),
    _SignalRule(
        name="REPEATED_AUTONOMY_SUCCESS", domain="AUTONOMY",
        target="autonomy_preference", proposed_value=None,
        supporting=lambda e: (e.kind == "autonomy_disposition"
                              and e.payload.get("disposition") == "ACT"
                              and e.payload.get("consequence") == "clean"),
        conflicting=lambda e: (e.kind == "autonomy_disposition"
                               and e.payload.get("consequence") == "failed"),
        derivation=("autonomy_disposition.disposition == 'ACT' with an "
                    "observed clean consequence (action.executed of the same "
                    "turn) × N≥3 fresh independent episodes"),
        expected_effect={"success": ("autonomy_clean",),
                         "harm": ("autonomy_failed",)},
        value_resolver=_autonomy_step_up),
    _SignalRule(
        name="REPEATED_AUTONOMY_FAILURE", domain="AUTONOMY",
        target="autonomy_preference", proposed_value="ask_first",
        supporting=lambda e: (e.kind == "autonomy_disposition"
                              and e.payload.get("disposition") in ("ACT", "ASK")
                              and e.payload.get("consequence") == "failed"),
        conflicting=lambda e: (e.kind == "autonomy_disposition"
                               and e.payload.get("consequence") == "clean"),
        derivation=("autonomy_disposition.disposition in ('ACT','ASK') with an "
                    "observed failed consequence (action.failed of the same "
                    "turn) × N≥3 fresh independent episodes"),
        expected_effect={"success": (),
                         "harm": ("autonomy_failed",)}),
)

_SIGNAL_PRIORITY = ("ATTENTION", "AUTONOMY", "DECISION", "MODEL_HANDLING")


class LearningSignalDeriver:
    """Deterministic learning-signal derivation from assembled evidence (§7).

    This is NOT a second learning engine: it consumes canonical records (the
    LearningEngine's outputs only by reference) and produces only governance
    inputs. Signals are INFERRED statements with their exact evidence
    references and derivation rule; they have no runtime influence themselves.
    """

    def derive(self, evidence: list[Evidence], *, user_id: str,
               policy_context: dict[str, str] | None = None,
               as_of: datetime | None = None) -> list[dict[str, Any]]:
        context = policy_context or {}
        signals: list[dict[str, Any]] = []

        def emit(rule: _SignalRule, supporting: list[Evidence],
                 conflicting: list[Evidence], *,
                 extra: dict[str, Any] | None = None) -> None:
            domain = rule.domain
            gate = evaluate_evidence_gate(domain or "MODEL_HANDLING",
                                          supporting, conflicting, as_of=as_of)
            value = rule.proposed_value
            if value is None and rule.value_resolver is not None:
                value = rule.value_resolver(context)
            proposable = bool(rule.target and value)
            signals.append({
                "signal": rule.name,
                "domain": domain,
                "target": rule.target,
                "proposed_value": value,
                "candidacy": "PROPOSABLE" if proposable else "NONE",
                "epistemic_state": "INFERRED",
                "derivation_rule": rule.derivation,
                "threshold": gate["threshold"],
                "counts": gate["counts"],
                "scope": user_id,
                "supporting_evidence_ids": [e.id for e in supporting],
                "conflicting_evidence_ids": [e.id for e in conflicting],
                "correlation_ids": sorted({e.correlation_id for e in supporting}),
                "evidence": [e.as_ref() for e in supporting],
                "expected_effect": rule.expected_effect,
                "verdict": gate["verdict"],
                "reason": gate["reason"],
                **(extra or {}),
            })

        for rule in _SIGNAL_RULES:
            supporting = [e for e in evidence if rule.supporting(e)]
            conflicting = [e for e in evidence if rule.conflicting(e)]
            if not supporting and not conflicting:
                continue
            emit(rule, supporting, conflicting)

        # REPEATED_MODEL_ERROR_CLASS: one signal per structured error class,
        # scoped to that class (§7/L2). Governance-only domain.
        classes: dict[str, list[Evidence]] = {}
        for e in evidence:
            if e.kind == "model_error" and e.payload.get("error_class"):
                classes.setdefault(e.payload["error_class"], []).append(e)
        for error_class, rows in sorted(classes.items()):
            gate = evaluate_evidence_gate("MODEL_HANDLING", rows, [], as_of=as_of)
            signals.append({
                "signal": "REPEATED_MODEL_ERROR_CLASS",
                "domain": "MODEL_HANDLING",
                "target": "model_handling_strategy",
                "proposed_value": "prefer_deterministic",
                "candidacy": "PROPOSABLE",
                "epistemic_state": "INFERRED",
                "derivation_rule": (f"model_error.error_class == '{error_class}' "
                                    "× N≥3 fresh episodes (ModelErrorService "
                                    "classifications)"),
                "error_class": error_class,
                "threshold": gate["threshold"],
                "counts": gate["counts"],
                "scope": user_id,
                "supporting_evidence_ids": [e.id for e in rows],
                "conflicting_evidence_ids": [],
                "correlation_ids": sorted({e.correlation_id for e in rows}),
                "evidence": [e.as_ref() for e in rows],
                "expected_effect": {"success": (), "harm": ("model_error_class",)},
                "verdict": gate["verdict"],
                "reason": gate["reason"],
            })

        def priority(item: dict[str, Any]) -> tuple[int, str]:
            domain = item.get("domain")
            rank = (_SIGNAL_PRIORITY.index(domain)
                    if domain in _SIGNAL_PRIORITY else len(_SIGNAL_PRIORITY))
            return rank, str(item["signal"])

        return sorted(signals, key=priority)

    @staticmethod
    def sufficient(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Signals whose deterministic gate passed and that may propose."""
        return [s for s in signals
                if s.get("verdict") == "SUFFICIENT" and s.get("candidacy") == "PROPOSABLE"]


# ---------------------------------------------------------------- governance
class PolicyGovernanceService:
    """The single mutation authority for the governance lifecycle (§8).

    It never writes effective policy directly: an accepted adaptation of an
    existing dimension is applied by calling the existing
    ``CognitivePolicyEngine.observe()`` path, so the engine's validation,
    events and reversibility still hold. Governance history is append-only;
    there is no destructive deletion path for governance records.
    """

    def __init__(self, db, bus: EventBus, policy_engine: CognitivePolicyEngine,
                 autonomy: AutonomyGovernor, *,
                 evidence_assembler: PolicyEvidenceAssembler | None = None,
                 signal_deriver: LearningSignalDeriver | None = None,
                 lifecycle=None, tenant_id: str = "local") -> None:
        self.db = db
        self.bus = bus
        self.policy = policy_engine
        self.autonomy = autonomy
        self.assembler = evidence_assembler
        self.deriver = signal_deriver or LearningSignalDeriver()
        self.lifecycle = lifecycle
        self.tenant_id = tenant_id

    # ------------------------------------------------------- scope helpers
    def _tenant(self, user_id: str) -> str:
        row = self.db.query_one(
            "SELECT tenant_id FROM auth_users WHERE namespace=?", (user_id,))
        return str(row["tenant_id"]) if row else self.tenant_id

    def _row(self, user_id: str, candidate_id: str):
        return self.db.query_one(
            "SELECT * FROM policy_governance WHERE id=? AND user_id=? AND tenant_id=?",
            (candidate_id, user_id, self._tenant(user_id)))

    # ------------------------------------------------------------- reading
    def get(self, user_id: str, candidate_id: str) -> dict[str, Any] | None:
        row = self._row(user_id, candidate_id)
        return self._shape(row) if row else None

    def list(self, user_id: str, *, state: str | None = None,
             limit: int = 100) -> list[dict[str, Any]]:
        sql = ("SELECT * FROM policy_governance WHERE user_id=? AND tenant_id=?")
        params: list[Any] = [user_id, self._tenant(user_id)]
        if state:
            sql += " AND state=?"
            params.append(state)
        sql += " ORDER BY datetime(created_at) DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [self._shape(r) for r in self.db.query(sql, params)]

    def history(self, user_id: str,
                candidate_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM policy_governance_history WHERE candidate_id=? AND "
            "user_id=? AND tenant_id=? ORDER BY id",
            (candidate_id, user_id, self._tenant(user_id)))]

    @staticmethod
    def _shape(row) -> dict[str, Any]:
        d = dict(row)
        for key, default in (("evidence_refs", []), ("signal_json", {}),
                             ("expected_effect", {}), ("validation", {})):
            raw = d.get(key)
            if isinstance(raw, (list, dict)):
                continue
            try:
                d[key] = json.loads(raw) if raw else default
            except (json.JSONDecodeError, TypeError):
                d[key] = default
        return d

    def open_proposals(self, user_id: str) -> list[dict[str, Any]]:
        return self.list(user_id, state="PROPOSED")

    def run_record(self, user_id: str, correlation_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM policy_governance_runs WHERE user_id=? AND correlation_id=?",
            (user_id, correlation_id))
        if row is None:
            return None
        try:
            result = json.loads(row["result_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            result = {}
        return {"id": row["id"], "correlation_id": row["correlation_id"],
                "status": row["status"], "result": result,
                "started_at": row["started_at"], "completed_at": row["completed_at"]}

    # -------------------------------------------------------------- create
    def create_candidate(self, user_id: str, *, domain: str, target: str,
                         proposed_value: str, reason: str, signal: dict[str, Any],
                         correlation_id: str,
                         provenance: str = "runtime_derivation") -> dict[str, Any]:
        """Create a governed candidate from a derived learning signal (§8.2).

        The signal must carry its exact canonical evidence references; a
        candidate with unreferenced support is invalid by construction.
        Creation performs no behavioral change: the candidate is then
        deterministically validated (§6 re-check, scope validation, conflict
        scan) and only reaches PROPOSED when every gate passes.
        """
        if domain not in DOMAINS:
            raise ValueError(f"Unsupported governance domain: {domain!r}")
        if target not in DOMAIN_TARGETS[domain]:
            raise ValueError(
                f"Target {target!r} is not valid for domain {domain!r}; "
                f"expected one of {list(DOMAIN_TARGETS[domain])}")
        if domain == "MODEL_HANDLING":
            vocabulary = MODEL_HANDLING_STRATEGIES
        else:
            vocabulary = DIMENSIONS[target].values
        if proposed_value not in vocabulary:
            raise ValueError(
                f"Value {proposed_value!r} is not in the finite vocabulary for "
                f"{target!r}; expected one of {list(vocabulary)}")
        if not correlation_id:
            raise ValueError("correlation_id is required")
        if not isinstance(signal, dict) or not signal.get("evidence"):
            raise ValueError("A candidate requires its signal's canonical "
                             "evidence references; unreferenced support is "
                             "invalid.")
        if not reason or not str(reason).strip():
            raise ValueError("reason is required")

        current_value = None
        if domain != "MODEL_HANDLING":
            current_value = self.policy.get(user_id, target)["value"]
            if proposed_value == current_value:
                raise ValueError(
                    f"Proposed value equals the current effective value for "
                    f"{target!r}; there is nothing to adapt.")

        cid = f"adapt_{uuid.uuid4().hex[:16]}"
        stamp = _now()
        evidence_refs = json.dumps(signal["evidence"])
        expected = signal.get("expected_effect") or {}
        self.db.execute(
            "INSERT INTO policy_governance (id,user_id,tenant_id,domain,target,"
            "proposed_value,current_value,state,reason,evidence_refs,signal_json,"
            "expected_effect,validation,provenance,correlation_id,created_at,"
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cid, user_id, self._tenant(user_id), domain, target, proposed_value,
             current_value, "CANDIDATE", reason[:500], evidence_refs,
             json.dumps(signal), json.dumps(expected), "{}", provenance,
             correlation_id, stamp, stamp))
        self._history(user_id, cid, None, "CANDIDATE",
                      reason=f"Created from derived signal "
                             f"{signal.get('signal', 'unknown')}.",
                      evidence_refs=signal["evidence"], correlation_id=correlation_id)
        self._emit_transition(user_id, cid, None, "CANDIDATE",
                              reason=f"Created from derived signal "
                                     f"{signal.get('signal', 'unknown')}.",
                              evidence_refs=signal["evidence"],
                              correlation_id=correlation_id)
        row = self._row(user_id, cid)
        assert row is not None
        return self._validate(user_id, row, correlation_id=correlation_id)

    # ---------------------------------------------------------- validation
    def _validate(self, user_id: str, row, *, correlation_id: str) -> dict[str, Any]:
        """CANDIDATE → VALIDATING → (PROPOSED | parked with a verdict).

        Deterministic re-check: every referenced record must still resolve in
        the verified scope, the §6 gate must still pass on freshly assembled
        evidence, and no fresh class-A user instruction may disagree.
        """
        candidate = self._shape(row)
        self.db.execute(
            "UPDATE policy_governance SET state='VALIDATING', updated_at=? "
            "WHERE id=? AND user_id=? AND tenant_id=?",
            (_now(), candidate["id"], user_id, self._tenant(user_id)))
        self._history(user_id, candidate["id"], "CANDIDATE", "VALIDATING",
                      reason="Deterministic validation: evidence re-check, scope "
                             "validation, conflict scan.",
                      evidence_refs=candidate["evidence_refs"],
                      correlation_id=correlation_id)
        self._emit_transition(user_id, candidate["id"], "CANDIDATE", "VALIDATING",
                              reason="Deterministic validation started.",
                              evidence_refs=candidate["evidence_refs"],
                              correlation_id=correlation_id)

        verdict, reason = self._validation_verdict(user_id, candidate)
        validation = {"verdict": verdict, "reason": reason,
                      "checked_at": _now()}
        self.db.execute(
            "UPDATE policy_governance SET validation=?, updated_at=? "
            "WHERE id=? AND user_id=? AND tenant_id=?",
            (json.dumps(validation), _now(), candidate["id"], user_id,
             self._tenant(user_id)))
        if verdict != "SUFFICIENT":
            # Parked in VALIDATING with an explicit insufficient/conflicting
            # verdict — never silently converted into a weaker claim (§6.4).
            self._history(user_id, candidate["id"], "VALIDATING", "VALIDATING",
                          reason=f"Parked: {reason}",
                          evidence_refs=candidate["evidence_refs"],
                          correlation_id=correlation_id)
            return self.get(user_id, candidate["id"])  # type: ignore[return-value]

        fresh = self._row(user_id, candidate["id"])
        return self._transition(
            user_id, self._shape(fresh), "PROPOSED",
            reason=f"All evidence gates passed: {reason}",
            evidence_refs=candidate["evidence_refs"],
            correlation_id=correlation_id)

    def _validation_verdict(self, user_id: str,
                            candidate: dict[str, Any]) -> tuple[str, str]:
        domain = candidate["domain"]
        target = candidate["target"]
        # 1. Every referenced record must still resolve in the verified scope.
        for ref in candidate["evidence_refs"]:
            if not self._ref_resolves(user_id, ref):
                return ("INVALID",
                        f"Evidence reference {ref} no longer resolves in the "
                        f"verified scope.")
        # 2. Fresh class-A conflict scan (§6.4): a newer explicit user
        # instruction that disagrees parks the candidate. Policy state is used
        # as CONTEXT here — it never counts as evidence.
        if domain != "MODEL_HANDLING":
            policy = self.policy.get(user_id, target)
            newest = max((self._ref_timestamp(ref) or ""
                          for ref in candidate["evidence_refs"]), default="")
            for entry in policy.get("evidence") or []:
                if (float(entry.get("strength", 0)) >= STRONG
                        and entry.get("origin", "user") == "user"
                        and entry.get("value") not in (candidate["proposed_value"],)
                        and str(entry.get("at", "")) >= newest):
                    return ("CONFLICTING_EVIDENCE",
                            f"A fresh explicit user preference sets "
                            f"{target}={entry.get('value')!r}; RECORDED class-A "
                            f"authority outranks class-B inference.")
        # 3. Re-check the candidate's own evidence rule over freshly assembled
        # evidence, with the §12.1 evidence-role separation applied: outcome
        # records of turns already influenced by an adaptation of this target
        # are outcome evidence and are excluded here.
        if self.assembler is None:
            return ("SUFFICIENT", "Validation service assembled (no re-check "
                                  "assembler wired); creation-time gate result "
                                  "retained.")
        evidence = self.assembler.assemble(user_id)
        excluded = self.consulted_turns_by_target(user_id).get(target, set())
        signal_record = candidate.get("signal_json") or {}
        supporting_ids = set(signal_record.get("supporting_evidence_ids") or [])
        conflicting_ids = set(signal_record.get("conflicting_evidence_ids") or [])
        supporting = [e for e in evidence
                      if e.id in supporting_ids and e.correlation_id not in excluded]
        conflicting = [e for e in evidence
                       if e.id in conflicting_ids and e.correlation_id not in excluded]
        gate = evaluate_evidence_gate(domain, supporting, conflicting)
        if gate["verdict"] == "SUFFICIENT":
            return ("SUFFICIENT", gate["reason"])
        return (gate["verdict"],
                f"The supporting evidence no longer passes the §6 gate over "
                f"fresh canonical evidence: {gate['reason']}")

    def _ref_resolves(self, user_id: str, ref: dict[str, Any]) -> bool:
        record_id = str(ref.get("id") or "")
        if not record_id:
            return False
        sources = {
            "evaluated_prediction": ("predictions", "id"),
            "model_error": ("model_error_records", "id"),
            "decision_outcome": ("decisions", "id"),
            "intervention_outcome": ("interventions", "id"),
            "learning_reference": ("knowledge_items", "id"),
            "autonomy_disposition": ("cognitive_events", "id"),
            "outcome_record": ("cognitive_events", "id"),
        }
        table, column = sources.get(str(ref.get("kind")),
                                    ("cognitive_events", "id"))
        row = self.db.query_one(
            f"SELECT 1 FROM {table} WHERE {column}=? AND user_id=? LIMIT 1",
            (record_id, user_id))
        return row is not None

    # ------------------------------------------------- lifecycle transitions
    def _history(self, user_id: str, candidate_id: str, previous_state, new_state,
                 *, reason: str, evidence_refs, correlation_id: str) -> None:
        refs = evidence_refs if isinstance(evidence_refs, list) else []
        self.db.execute(
            "INSERT INTO policy_governance_history (candidate_id,user_id,"
            "tenant_id,previous_state,new_state,reason,evidence_refs,"
            "correlation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (candidate_id, user_id, self._tenant(user_id), previous_state,
             new_state, str(reason)[:500], json.dumps(refs), correlation_id,
             _now()))

    def _emit_transition(self, user_id: str, candidate_id: str, previous_state,
                         new_state, *, reason: str, evidence_refs,
                         correlation_id: str) -> None:
        refs = [ref for ref in (evidence_refs or [])
                if isinstance(ref, dict) and ref.get("id")]
        self.bus.emit(
            user_id, "governance.transition",
            f"Policy governance: {previous_state or 'created'} → {new_state}",
            subject_kind="policy_governance", subject_id=candidate_id,
            correlation_id=correlation_id,
            payload={"candidate_id": candidate_id,
                     "previous_state": previous_state,
                     "new_state": new_state,
                     "reason": str(reason)[:500],
                     "evidence_refs": refs,
                     "correlation_id": correlation_id,
                     "user_id": user_id,
                     "tenant_id": self._tenant(user_id)})

    def _transition(self, user_id: str, candidate: dict[str, Any],
                    new_state: str, *, reason: str, evidence_refs,
                    correlation_id: str, superseded_by: str | None = None) -> dict[str, Any]:
        previous = candidate["state"]
        if new_state not in STATES:
            raise ValueError(f"Unknown governance state: {new_state!r}")
        if new_state not in TRANSITIONS.get(previous, frozenset()):
            raise ValueError(
                f"Illegal governance transition {previous!r} → {new_state!r}; "
                f"allowed: {sorted(TRANSITIONS.get(previous, frozenset()))}")
        if not reason or not str(reason).strip():
            raise ValueError("reason is required")
        if not correlation_id:
            raise ValueError("correlation_id is required")
        refs = [ref for ref in (evidence_refs or [])
                if isinstance(ref, dict) and ref.get("id")]
        if new_state in ("ACCEPTED", "ACTIVE") and not refs:
            raise ValueError("Decision transitions require evidence references.")
        stamp = _now()
        extra = ""
        if superseded_by:
            extra += ", superseded_by=?"
        if new_state == "ACCEPTED":
            extra += ", accepted_at=?"
        if new_state == "ACTIVE":
            extra += ", activated_at=?"
        params: list[Any] = [new_state, stamp]
        if superseded_by:
            params.append(superseded_by)
        if new_state == "ACCEPTED":
            params.append(stamp)
        if new_state == "ACTIVE":
            params.append(stamp)
        params.extend([candidate["id"], user_id, self._tenant(user_id)])
        self.db.execute(
            f"UPDATE policy_governance SET state=?, updated_at=?{extra} "
            f"WHERE id=? AND user_id=? AND tenant_id=?", params)
        self._history(user_id, candidate["id"], previous, new_state,
                      reason=reason, evidence_refs=refs,
                      correlation_id=correlation_id)
        self._emit_transition(user_id, candidate["id"], previous, new_state,
                              reason=reason, evidence_refs=refs,
                              correlation_id=correlation_id)
        return self.get(user_id, candidate["id"])  # type: ignore[return-value]

    def transition(self, user_id: str, candidate_id: str, new_state: str, *,
                   reason: str, evidence_refs: list[dict[str, Any]],
                   correlation_id: str, superseded_by: str | None = None) -> dict[str, Any]:
        """Generic lifecycle transition for non-authority edges only.

        ACCEPTED/ACTIVE/PROPOSED/VALIDATING are decision or validation
        boundaries reachable only through ``confirm()`` / deterministic
        validation — a direct attempt raises and changes nothing (§8.2).
        """
        if new_state in RESTRICTED_TRANSITIONS:
            raise ValueError(
                f"Transition to {new_state!r} is an authority boundary; it is "
                f"reachable only through the confirmation/validation methods "
                f"of PolicyGovernanceService.")
        row = self._row(user_id, candidate_id)
        if row is None:
            raise KeyError(candidate_id)
        return self._transition(user_id, self._shape(row), new_state,
                                reason=reason, evidence_refs=evidence_refs,
                                correlation_id=correlation_id,
                                superseded_by=superseded_by)

    # -------------------------------------------- confirmation (authority)
    def confirm(self, user_id: str, candidate_id: str, *, confirmation: bool,
                reason: str | None = None,
                correlation_id: str | None = None) -> dict[str, Any]:
        """PROPOSED → ACCEPTED → ACTIVE through the real boundary (§9).

        Requires an explicit confirmation flag AND the existing
        AutonomyGovernor. The adaptation is applied through the engine's own
        observe() path (origin ``v10_2_governed_adaptation``); the engine
        emits its own canonical ``policy.updated``. No auto-accept exists.
        """
        row = self._row(user_id, candidate_id)
        if row is None:
            raise KeyError(candidate_id)
        candidate = self._shape(row)
        if candidate["state"] in ("ACTIVE", "ACCEPTED"):
            return candidate  # already decided; idempotent
        if candidate["state"] != "PROPOSED":
            raise ValueError(
                f"Cannot confirm a candidate in state {candidate['state']!r}; "
                f"only PROPOSED adaptations await a decision.")
        if not confirmation:
            raise ValueError(
                "Explicit confirmation is required: a governed adaptation is "
                "never accepted without the user's explicit decision.")
        if candidate["domain"] == "MODEL_HANDLING" and not MODEL_HANDLING_ACTIVATABLE:
            raise ValueError(
                "MODEL_HANDLING is governance-only in this build: there is no "
                "safe capability-execution consumer seam for a finite "
                "model-handling strategy, so activation is refused rather "
                "than faked. The candidate remains proposed.")

        governance = self.autonomy.authorize(
            user_id, f"policy_governance:{candidate_id}",
            risk_class="update_memory", confidence=0.9, reversible=True,
            correlation_id=correlation_id)
        if governance.get("disposition") == "BLOCKED":
            # Nothing changed and nothing is claimed: the candidate stays
            # PROPOSED and no governance.transition event is emitted.
            return {"candidate": candidate, "blocked": True,
                    "governance": governance}

        cid = correlation_id or candidate["correlation_id"]
        confirm_reason = (f"User confirmed the adaptation"
                          + (f" — {reason}" if reason else "")
                          + f" (evidence: {candidate['reason'][:200]})")
        candidate = self._transition(
            user_id, candidate, "ACCEPTED", reason=confirm_reason,
            evidence_refs=candidate["evidence_refs"], correlation_id=cid)

        # Conflicting siblings are resolved at acceptance (§13): at most one
        # adaptation may hold a target; the others are superseded or rejected
        # with a cross-reference.
        for other in self.list(user_id):
            if other["id"] == candidate_id:
                continue
            if other["target"] != candidate["target"]:
                continue
            if other["state"] == "ACTIVE":
                self._transition(
                    user_id, other, "SUPERSEDED",
                    reason=f"Superseded by confirmed adaptation {candidate_id}.",
                    evidence_refs=candidate["evidence_refs"], correlation_id=cid,
                    superseded_by=candidate_id)
            elif other["state"] == "PROPOSED" and other["proposed_value"] != candidate["proposed_value"]:
                self._transition(
                    user_id, other, "REJECTED",
                    reason=f"Conflicting candidate: {candidate_id} was accepted "
                           f"for this target.",
                    evidence_refs=candidate["evidence_refs"], correlation_id=cid)

        if candidate["domain"] != "MODEL_HANDLING":
            self._stage(user_id, "APPLYING_ADAPTIVE_POLICY", "ACTIVE",
                        correlation_id=cid,
                        detail=f"Applying {candidate['target']} → "
                               f"{candidate['proposed_value']} through the "
                               f"canonical policy engine.")
            try:
                self.policy.observe(
                    user_id, candidate["target"], candidate["proposed_value"],
                    strength=GOVERNED_STRENGTH,
                    evidence=reason or candidate["reason"],
                    correlation_id=cid, origin=ORIGIN_GOVERNED)
            except ValueError as exc:
                self._stage(user_id, "APPLYING_ADAPTIVE_POLICY", "FAILED",
                            correlation_id=cid, detail=str(exc)[:200])
                raise ValueError(
                    f"The canonical policy engine refused the adaptation "
                    f"({exc}); the candidate stays ACCEPTED and nothing was "
                    f"applied.") from exc
        candidate = self._transition(
            user_id, candidate, "ACTIVE",
            reason="Measurement plan registered; applied through the canonical "
                   "engine path." if candidate["domain"] != "MODEL_HANDLING"
            else "Measurement plan registered (governance-only domain).",
            evidence_refs=candidate["evidence_refs"], correlation_id=cid)
        self._stage(user_id, "APPLYING_ADAPTIVE_POLICY", "COMPLETED",
                    correlation_id=cid)
        return candidate

    def reject(self, user_id: str, candidate_id: str, *, reason: str,
               correlation_id: str | None = None) -> dict[str, Any]:
        """Explicit rejection; the rejection itself is retained evidence (§13).

        Rejecting an ACTIVE adaptation restores current behavior through the
        engine's canonical revert() path (the released legacy semantics —
        including its documented row deletion — are unchanged; §3.1/§22.9).
        """
        row = self._row(user_id, candidate_id)
        if row is None:
            raise KeyError(candidate_id)
        candidate = self._shape(row)
        cid = correlation_id or candidate["correlation_id"]
        if candidate["state"] == "ACTIVE" and candidate["domain"] != "MODEL_HANDLING":
            self.policy.revert(user_id, candidate["target"], correlation_id=cid)
        return self._transition(
            user_id, candidate, "REJECTED",
            reason=f"User rejected the adaptation — {reason}",
            evidence_refs=candidate["evidence_refs"], correlation_id=cid)

    def defer(self, user_id: str, candidate_id: str, *, reason: str,
              correlation_id: str | None = None) -> dict[str, Any]:
        row = self._row(user_id, candidate_id)
        if row is None:
            raise KeyError(candidate_id)
        candidate = self._shape(row)
        return self._transition(
            user_id, candidate, "DEFERRED",
            reason=f"User deferred the adaptation — {reason}",
            evidence_refs=candidate["evidence_refs"],
            correlation_id=correlation_id or candidate["correlation_id"])

    def weaken(self, user_id: str, candidate_id: str, *, reason: str,
               evidence_refs: list[dict[str, Any]], correlation_id: str) -> dict[str, Any]:
        """ACTIVE → WEAKENED. Deactivation that changes current behavior flows
        through the engine's canonical revert() path, never around it (§8.2)."""
        row = self._row(user_id, candidate_id)
        if row is None:
            raise KeyError(candidate_id)
        candidate = self._shape(row)
        if candidate["state"] == "ACTIVE" and candidate["domain"] != "MODEL_HANDLING":
            self.policy.revert(user_id, candidate["target"], correlation_id=correlation_id)
        return self._transition(
            user_id, candidate, "WEAKENED", reason=reason,
            evidence_refs=evidence_refs or candidate["evidence_refs"],
            correlation_id=correlation_id)

    def repropose(self, user_id: str, candidate_id: str, *,
                  correlation_id: str) -> dict[str, Any]:
        """DEFERRED → PROPOSED after the deterministic gates re-pass (§8.2)."""
        row = self._row(user_id, candidate_id)
        if row is None:
            raise KeyError(candidate_id)
        candidate = self._shape(row)
        if candidate["state"] != "DEFERRED":
            raise ValueError("Only DEFERRED adaptations can be re-proposed.")
        verdict, reason = self._validation_verdict(user_id, candidate)
        if verdict != "SUFFICIENT":
            raise ValueError(f"Cannot re-propose: {reason}")
        return self._transition(
            user_id, candidate, "PROPOSED",
            reason=f"Re-proposed on user instruction; gates re-passed: {reason}",
            evidence_refs=candidate["evidence_refs"], correlation_id=correlation_id)

    # ----------------------------------------------------- expiry and harm
    def expire_eligible(self, user_id: str, *, correlation_id: str,
                        as_of: datetime | None = None) -> list[dict[str, Any]]:
        """Interaction-driven expiry (§12.4): no scheduler, no polling."""
        as_of = as_of or datetime.now(timezone.utc)
        expired: list[dict[str, Any]] = []
        for candidate in self.list(user_id):
            if candidate["state"] not in ("ACTIVE", "WEAKENED", "DEFERRED"):
                continue
            horizon = FRESHNESS_DAYS.get(candidate["domain"], 90)
            evidence_stale = self._evidence_stale(candidate, as_of, horizon)
            consulted_recently = self._consulted_within(user_id, candidate["id"],
                                                         horizon, as_of)
            if candidate["state"] == "ACTIVE":
                if evidence_stale or not consulted_recently:
                    expired.append(self._transition(
                        user_id, candidate, "EXPIRED",
                        reason=("Supporting evidence has gone stale."
                                if evidence_stale else
                                f"No influenced turn within the {horizon}-day "
                                f"freshness horizon."),
                        evidence_refs=candidate["evidence_refs"],
                        correlation_id=correlation_id))
            elif evidence_stale:
                expired.append(self._transition(
                    user_id, candidate, "EXPIRED",
                    reason="Supporting evidence has gone stale.",
                    evidence_refs=candidate["evidence_refs"],
                    correlation_id=correlation_id))
        return expired

    def _evidence_stale(self, candidate: dict[str, Any], as_of: datetime,
                        horizon: int) -> bool:
        refs = candidate.get("evidence_refs") or []
        if not refs:
            return True
        freshest: float | None = None
        for ref in refs:
            stamp = self._ref_timestamp(ref)
            if stamp is None:
                continue
            age = _age_days(stamp, as_of)
            if age is not None:
                freshest = age if freshest is None else min(freshest, age)
        return freshest is None or freshest > horizon

    def _ref_timestamp(self, ref: dict[str, Any]) -> str | None:
        record_id = str(ref.get("id") or "")
        kind = str(ref.get("kind") or "")
        row = None
        if kind == "evaluated_prediction":
            row = self.db.query_one(
                "SELECT evaluated_at AS ts FROM predictions WHERE id=?", (record_id,))
        elif kind == "model_error":
            row = self.db.query_one(
                "SELECT created_at AS ts FROM model_error_records WHERE id=?", (record_id,))
        elif kind == "decision_outcome":
            row = self.db.query_one(
                "SELECT resolved_at AS ts FROM decisions WHERE id=?", (record_id,))
        elif kind == "intervention_outcome":
            row = self.db.query_one(
                "SELECT created_at AS ts FROM interventions WHERE id=?", (record_id,))
        elif kind in ("autonomy_disposition", "outcome_record"):
            row = self.db.query_one(
                "SELECT created_at AS ts FROM cognitive_events WHERE id=?", (record_id,))
        elif kind == "learning_reference":
            row = self.db.query_one(
                "SELECT updated_at AS ts FROM knowledge_items WHERE id=?", (record_id,))
        return row["ts"] if row else None

    def _consulted_within(self, user_id: str, adaptation_id: str, horizon: int,
                          as_of: datetime) -> bool:
        row = self.db.query_one(
            "SELECT created_at FROM policy_governance_consultations "
            "WHERE adaptation_id=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (adaptation_id, user_id))
        if row is None:
            return False
        age = _age_days(row["created_at"], as_of)
        return age is not None and age <= horizon

    def maybe_weaken_on_harm(self, user_id: str, *,
                             correlation_id: str) -> list[dict[str, Any]]:
        """Deterministic WEAKEN on measured harm (§12.4 safety asymmetry)."""
        weakened: list[dict[str, Any]] = []
        for candidate in self.list(user_id, state="ACTIVE"):
            harmful = self.db.query(
                "SELECT * FROM policy_governance_observations WHERE "
                "adaptation_id=? AND user_id=? AND category='HARMFUL_INFLUENCE'",
                (candidate["id"], user_id))
            if len(harmful) >= HARM_WEAKEN_MIN:
                refs = [{"kind": "governance_observation", "id": r["id"]}
                        for r in harmful]
                weakened.append(self.weaken(
                    user_id, candidate["id"],
                    reason=(f"Deterministic weaken: {len(harmful)} measured "
                            f"HARMFUL outcome(s) reached the safety threshold "
                            f"({HARM_WEAKEN_MIN})."),
                    evidence_refs=refs, correlation_id=correlation_id))
        return weakened

    # ------------------------------------------- consultation + measurement
    def record_consultation(self, user_id: str, adaptation_id: str, *,
                            consumer: str, effect: str,
                            turn_correlation_id: str,
                            matched_scope: dict[str, Any] | None = None,
                            correlation_id: str | None = None) -> dict[str, Any] | None:
        """Record that a REAL consumer operated under an adapted value (§11).

        Called only at the consumer seam with proof of actual use — never
        speculatively by the governance layer. Idempotent per
        (adaptation, turn, consumer); the consultation event is emitted only
        for a genuinely new consultation.
        """
        row = self._row(user_id, adaptation_id)
        if row is None or row["state"] != "ACTIVE":
            return None
        existing = self.db.query_one(
            "SELECT * FROM policy_governance_consultations WHERE "
            "adaptation_id=? AND turn_correlation_id=? AND consumer=? AND user_id=?",
            (adaptation_id, turn_correlation_id, consumer, user_id))
        if existing:
            return dict(existing)
        consultation_id = f"consult_{uuid.uuid4().hex[:14]}"
        scope = matched_scope or {"user_id": user_id,
                                  "tenant_id": self._tenant(user_id)}
        self.db.execute(
            "INSERT INTO policy_governance_consultations (id,user_id,tenant_id,"
            "adaptation_id,consumer,matched_scope,effect,turn_correlation_id,"
            "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (consultation_id, user_id, self._tenant(user_id), adaptation_id,
             consumer, json.dumps(scope), effect[:500], turn_correlation_id,
             _now()))
        self.bus.emit(
            user_id, "governance.consultation",
            f"An adapted policy shaped this turn ({row['target']}).",
            subject_kind="policy_governance", subject_id=adaptation_id,
            correlation_id=correlation_id or turn_correlation_id,
            payload={"adaptation_id": adaptation_id, "consumer": consumer,
                     "matched_scope": scope, "effect": effect[:500],
                     "turn_correlation_id": turn_correlation_id,
                     "user_id": user_id,
                     "tenant_id": self._tenant(user_id)})
        return {"id": consultation_id, "adaptation_id": adaptation_id,
                "consumer": consumer, "effect": effect,
                "turn_correlation_id": turn_correlation_id}

    def consultations(self, user_id: str, adaptation_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM policy_governance_consultations WHERE "
            "adaptation_id=? AND user_id=? ORDER BY id",
            (adaptation_id, user_id))]

    def consulted_turns_by_target(self, user_id: str) -> dict[str, set[str]]:
        """Turns whose outcome records are already OUTCOME evidence (§12.1).

        The canonical outcomes of a turn influenced under §11 are measurement
        evidence for that adaptation and must never be re-counted as
        adaptation evidence for another candidate of the same target
        (evidence-role separation, L5/§10.6).
        """
        out: dict[str, set[str]] = {}
        for row in self.db.query(
                "SELECT g.target AS target, c.turn_correlation_id AS tc "
                "FROM policy_governance_consultations c "
                "JOIN policy_governance g ON g.id = c.adaptation_id "
                "WHERE c.user_id=?", (user_id,)):
            out.setdefault(row["target"], set()).add(row["tc"])
        return out

    def consumed_evidence_by_target(self, user_id: str) -> dict[str, set[str]]:
        """Canonical record ids already frozen as adaptation evidence (§12.1).

        Adaptation evidence is frozen at acceptance and is never re-counted:
        a weakened/superseded/rejected adaptation's evidence cannot justify a
        new candidate of the same target. Only genuinely NEW canonical
        episodes may justify the next proposal (no evidence recycling loops).
        """
        out: dict[str, set[str]] = {}
        for row in self.db.query(
                "SELECT target, evidence_refs FROM policy_governance "
                "WHERE user_id=? AND tenant_id=?",
                (user_id, self._tenant(user_id))):
            try:
                refs = json.loads(row["evidence_refs"] or "[]")
            except (json.JSONDecodeError, TypeError):
                refs = []
            ids = {str(ref.get("id")) for ref in refs
                   if isinstance(ref, dict) and ref.get("id")}
            if ids:
                out.setdefault(row["target"], set()).update(ids)
        return out

    def record_observation(self, user_id: str, adaptation_id: str, *,
                           category: str, turn_correlation_id: str,
                           evidence_refs: list[dict[str, Any]],
                           detail: str,
                           correlation_id: str | None = None) -> dict[str, Any] | None:
        """Store one effectiveness observation (§12.3), evidence-linked."""
        if category not in MEASUREMENT_CATEGORIES:
            raise ValueError(f"Unknown measurement category: {category!r}")
        existing = self.db.query_one(
            "SELECT * FROM policy_governance_observations WHERE adaptation_id=? "
            "AND turn_correlation_id=? AND user_id=?",
            (adaptation_id, turn_correlation_id, user_id))
        if existing:
            return dict(existing)
        observation_id = f"obs_{uuid.uuid4().hex[:14]}"
        self.db.execute(
            "INSERT INTO policy_governance_observations (id,user_id,tenant_id,"
            "adaptation_id,category,turn_correlation_id,evidence_refs,detail,"
            "correlation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (observation_id, user_id, self._tenant(user_id), adaptation_id,
             category, turn_correlation_id, json.dumps(evidence_refs),
             detail[:500], correlation_id, _now()))
        self.bus.emit(
            user_id, "governance.observation",
            f"Measured an adapted policy's outcome: {category}.",
            subject_kind="policy_governance", subject_id=adaptation_id,
            correlation_id=correlation_id,
            payload={"adaptation_id": adaptation_id, "category": category,
                     "turn_correlation_id": turn_correlation_id,
                     "evidence_refs": evidence_refs,
                     "detail": detail[:300],
                     "user_id": user_id,
                     "tenant_id": self._tenant(user_id)})
        return {"id": observation_id, "adaptation_id": adaptation_id,
                "category": category, "turn_correlation_id": turn_correlation_id}

    def measurements(self, user_id: str, adaptation_id: str) -> dict[str, Any]:
        """Per-category measurement report (§12). Never an aggregate score.

        Adaptation evidence (frozen at acceptance) and outcome evidence
        (accumulated from influenced turns) are reported in distinct roles.
        """
        row = self._row(user_id, adaptation_id)
        if row is None:
            raise KeyError(adaptation_id)
        candidate = self._shape(row)
        observations = [dict(r) for r in self.db.query(
            "SELECT * FROM policy_governance_observations WHERE "
            "adaptation_id=? AND user_id=? ORDER BY id",
            (adaptation_id, user_id))]
        categories: dict[str, int] = {c: 0 for c in MEASUREMENT_CATEGORIES}
        for obs in observations:
            categories[obs["category"]] = categories.get(obs["category"], 0) + 1
        total = len(observations)
        summary = ("no measured outcomes yet" if total == 0 else
                   ", ".join(f"{v} {k}" for k, v in sorted(categories.items()) if v))
        return {
            "adaptation_id": adaptation_id,
            "state": candidate["state"],
            "target": candidate["target"],
            "adaptation_evidence": {
                "role": "ADAPTATION_EVIDENCE",
                "frozen_at": candidate.get("accepted_at"),
                "evidence_refs": candidate["evidence_refs"],
                "note": "Frozen at acceptance; never re-counted as outcome "
                        "evidence."},
            "outcome_evidence": {
                "role": "OUTCOME_EVIDENCE",
                "observation_count": total,
                "categories": categories,
                "observations": [
                    {"id": o["id"], "category": o["category"],
                     "turn_correlation_id": o["turn_correlation_id"],
                     "evidence_refs": json.loads(o["evidence_refs"] or "[]"),
                     "detail": o["detail"]} for o in observations],
                "note": "Accumulated only from later influenced turns; never "
                        "admissible as adaptation evidence."},
            "summary": summary,
        }

    # ------------------------------------------------------------- surface
    def _stage(self, user_id: str, stage: str, status: str, *,
               correlation_id: str | None, detail: str | None = None) -> None:
        if self.lifecycle is None:
            return
        try:
            self.lifecycle.transition(user_id, "governance", correlation_id or "",
                                      stage, status, detail=detail)
        except Exception:  # surface emission must never break governance
            log.exception("Surface transition failed for %s/%s", stage, status)


# Measurement categories (§12.3) — deterministic, never an aggregate score.
MEASUREMENT_CATEGORIES = ("SUCCESSFUL_INFLUENCE", "NEUTRAL_INFLUENCE",
                          "HARMFUL_INFLUENCE", "INSUFFICIENT_EVIDENCE",
                          "CONFLICTING_OUTCOMES")

# Deterministic expected-outcome classes per (target, value). A turn's outcome
# evidence maps to success/harm classes; the comparison is expectation vs
# observed canonical records (§12.2) — never model self-assessment.
EXPECTATION_CLASSES: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {
    ("interruption_tolerance", "low"): {"success": ("intervention_accepted",),
                                        "harm": ("intervention_rejected",),
                                        "family": "intervention"},
    ("interruption_tolerance", "high"): {"success": ("intervention_accepted",),
                                         "harm": ("intervention_rejected",),
                                         "family": "intervention"},
    ("silence_tolerance", "low"): {"success": ("intervention_accepted",),
                                   "harm": ("intervention_rejected",),
                                   "family": "intervention"},
    ("silence_tolerance", "high"): {"success": ("intervention_accepted",),
                                    "harm": ("intervention_rejected",),
                                    "family": "intervention"},
    ("autonomy_preference", "act_low_risk"): {"success": ("autonomy_clean",),
                                              "harm": ("autonomy_failed",),
                                              "family": "autonomy"},
    ("autonomy_preference", "act"): {"success": ("autonomy_clean",),
                                     "harm": ("autonomy_failed",),
                                     "family": "autonomy"},
    ("autonomy_preference", "ask_first"): {"success": (),
                                           "harm": ("autonomy_failed",),
                                           "family": "autonomy"},
}


def classify_turn_outcome(target: str, proposed_value: str,
                          turn_evidence: list[Evidence],
                          *, error_class: str | None = None) -> tuple[str, list[dict[str, Any]], str]:
    """Deterministic expectation-vs-reality comparison for one influenced turn.

    Returns (category, evidence_refs, detail). Zero outcome evidence is
    INSUFFICIENT_EVIDENCE — never guessed.
    """
    classes = EXPECTATION_CLASSES.get((target, proposed_value))
    if classes is None:
        if target == "model_handling_strategy":
            classes = {"success": (), "harm": ("model_error_class",),
                       "family": "model_error"}
        else:
            # Presentation dimensions have no canonical outcome record: the
            # honest per-turn category is INSUFFICIENT_EVIDENCE, stated as
            # such rather than fabricated.
            return ("INSUFFICIENT_EVIDENCE", [],
                    "No canonical outcome record captures presentation "
                    "effectiveness for this dimension.")

    present: set[str] = set()
    refs: list[dict[str, Any]] = []
    for e in turn_evidence:
        if e.kind == "intervention_outcome":
            verdict = e.payload.get("verdict")
            if verdict == "accepted":
                present.add("intervention_accepted")
                refs.append(e.as_ref())
            elif verdict == "rejected":
                present.add("intervention_rejected")
                refs.append(e.as_ref())
        elif e.kind == "autonomy_disposition":
            consequence = e.payload.get("consequence")
            if consequence == "clean":
                present.add("autonomy_clean")
                refs.append(e.as_ref())
            elif consequence == "failed":
                present.add("autonomy_failed")
                refs.append(e.as_ref())
        elif e.kind == "model_error":
            if error_class and e.payload.get("error_class") == error_class:
                present.add("model_error_class")
                refs.append(e.as_ref())

    success = bool(set(classes["success"]) & present)
    harm = bool(set(classes["harm"]) & present)
    if success and harm:
        return ("CONFLICTING_OUTCOMES", refs,
                "Mixed outcomes within the unit; reported with both sides.")
    if success:
        return ("SUCCESSFUL_INFLUENCE", refs,
                "Declared expected effect observed in this influenced turn.")
    if harm:
        return ("HARMFUL_INFLUENCE", refs,
                "Outcome contradicted the declared expected effect.")
    family = classes.get("family")
    family_present = bool(refs) or (
        family == "intervention" and any(
            e.kind == "intervention_outcome" for e in turn_evidence))
    if family_present:
        return ("NEUTRAL_INFLUENCE", refs,
                "Behavior differed; outcome neither matched nor contradicted "
                "the expectation.")
    return ("INSUFFICIENT_EVIDENCE", [],
            "No outcome was observable for this unit; counted as a coverage "
            "gap, never guessed.")


# ------------------------------------------------------------------- runtime
class PolicyRuntimeCoordinator:
    """Turn-scoped, bounded governance evaluation (§10).

    Sibling of the V10.1 ``MaintenanceRuntimeCoordinator`` under the same turn
    correlation — it duplicates none of it and subscribes to nothing. Hard
    bounds: at most ONE top-level governance evaluation per turn correlation
    (idempotent, persisted), terminal correlation markers (``::reaudit1`` and
    ``::pgov1`` are both terminal), no recursion, no scheduler, no daemon, no
    process-global state. A governance failure never breaks the conversation.
    """

    def __init__(self, db, bus: EventBus, governance: PolicyGovernanceService,
                 assembler: PolicyEvidenceAssembler,
                 deriver: LearningSignalDeriver,
                 policy_engine: CognitivePolicyEngine, *,
                 lifecycle=None, tenant_id: str = "local") -> None:
        self.db = db
        self.bus = bus
        self.governance = governance
        self.assembler = assembler
        self.deriver = deriver
        self.policy = policy_engine
        self.lifecycle = lifecycle
        self.tenant_id = tenant_id

    # -------------------------------------------------------- §10.2 inspect
    def inspect(self, user_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
        """Read-only inspection: which ACTIVE adaptations match this scope.

        Performs no mutation, no model call and no evidence assembly. A
        no-match inspection emits nothing at all (§16).
        """
        matched = self.governance.list(user_id, state="ACTIVE")
        if matched and correlation_id:
            self._stage(user_id, correlation_id, "CHECKING_ADAPTIVE_POLICY",
                        "ACTIVE",
                        detail=f"{len(matched)} active adaptation(s) matched "
                               f"this turn's scope.")
            self._stage(user_id, correlation_id, "CHECKING_ADAPTIVE_POLICY",
                        "COMPLETED")
        return {"matched": matched, "count": len(matched)}

    def inspect_for_turn(self, user_id: str, *, correlation_id: str,
                         context_items: list[Any] | None = None) -> dict[str, Any]:
        """Inspection plus truthful consultation recording (§10.2/§11.2).

        A consultation is recorded only when a real consumer provably operated
        under the adapted value this turn: the canonical context bundle must
        contain the policy preference item carrying the ADAPTED value. If no
        consumer used it, no consultation exists — measurement records
        non-consultation rather than pretending effect.
        """
        inspection = self.inspect(user_id, correlation_id=correlation_id)
        consulted: list[dict[str, Any]] = []
        for adaptation in inspection["matched"]:
            proof = None
            for item in context_items or []:
                if (getattr(item, "kind", None) == "preference"
                        and getattr(item, "id", None) == adaptation["target"]
                        and str(getattr(item, "content", "")).endswith(
                            f": {adaptation['proposed_value']}")):
                    proof = item
                    break
            if proof is None:
                continue
            record = self.governance.record_consultation(
                user_id, adaptation["id"],
                consumer="context.preferences",
                effect=(f"Adapted preference '{adaptation['target']}: "
                        f"{adaptation['proposed_value']}' entered the bounded "
                        f"response context (source: policy-engine item)."),
                turn_correlation_id=correlation_id,
                correlation_id=correlation_id)
            if record:
                consulted.append(record)
        return {**inspection, "consulted": consulted}

    # ------------------------------------------------------ §10.3 evaluate
    def run_for_turn(self, user_id: str, message: str | None = None, *,
                     correlation_id: str, thread_id: str | None = None,
                     meaning: dict[str, Any] | None = None) -> dict[str, Any]:
        """One bounded governance evaluation for one conversational turn."""
        if not correlation_id:
            raise ValueError("correlation_id is required")
        # Depth guards: re-audit and governance-marker correlations are
        # terminal — no maintenance, re-audit or further governance may run.
        if correlation_id.endswith(REAUDIT_SUFFIX) or correlation_id.endswith(GOVERNANCE_MARKER):
            return {"correlation_id": correlation_id,
                    "status": GOVERNANCE_TERMINAL, "signals": [], "proposals": [],
                    "measurements": [], "expired": [], "weakened": [],
                    "terminal": True,
                    "reason": "Terminal correlation (bounded depth); no "
                              "governance evaluation is allowed."}
        # Idempotency: exactly one top-level evaluation per user+correlation,
        # provable from the persisted runs ledger.
        existing = self.governance.run_record(user_id, correlation_id)
        if existing is not None:
            result = dict(existing.get("result") or {})
            result["persisted"] = True
            result["status"] = existing["status"]
            return result

        run_id = f"grun_{uuid.uuid4().hex[:14]}"
        started = _now()
        self.db.execute(
            "INSERT INTO policy_governance_runs (id,user_id,tenant_id,"
            "correlation_id,status,result_json,started_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (run_id, user_id, self.governance._tenant(user_id), correlation_id,
             "RUNNING", "{}", started))

        result: dict[str, Any] = {
            "correlation_id": correlation_id, "status": GOVERNANCE_COMPLETED,
            "relevance": {"relevant": False, "reasons": []},
            "signals": [], "proposals": [], "parked": [],
            "measurements": [], "expired": [], "weakened": [],
            "terminal": False,
        }
        try:
            result = self._evaluate(user_id, message, correlation_id=correlation_id,
                                    result=result)
        except Exception as exc:  # never break the conversation turn
            log.warning("Bounded governance evaluation failed for %s: %s",
                        correlation_id, exc)
            result["status"] = GOVERNANCE_FAILED
            result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        self.db.execute(
            "UPDATE policy_governance_runs SET status=?, result_json=?, "
            "completed_at=? WHERE id=? AND user_id=?",
            (result["status"], json.dumps(result, default=str), _now(), run_id,
             user_id))
        return result

    # ------------------------------------------------------- internals
    def _last_watermark(self, user_id: str) -> int:
        row = self.db.query_one(
            "SELECT result_json FROM policy_governance_runs WHERE user_id=? "
            "AND status != 'RUNNING' ORDER BY rowid DESC LIMIT 1", (user_id,))
        if row is None:
            return 0
        try:
            return int(json.loads(row["result_json"] or "{}")
                       .get("evidence_watermark") or 0)
        except (json.JSONDecodeError, TypeError, ValueError):
            return 0

    def _newest_outcome_event_id(self, user_id: str) -> int:
        placeholders = ",".join("?" * len(WATERMARK_EVENT_TYPES))
        row = self.db.query_one(
            f"SELECT MAX(id) AS newest FROM cognitive_events WHERE user_id=? "
            f"AND type IN ({placeholders})", (user_id, *WATERMARK_EVENT_TYPES))
        return int(row["newest"] or 0) if row else 0

    def _evaluate(self, user_id: str, message: str | None, *,
                  correlation_id: str, result: dict[str, Any]) -> dict[str, Any]:
        active = self.governance.list(user_id, state="ACTIVE")
        turn_outcome_events = [
            e for e in self.bus.for_correlation(correlation_id)
            if e.type in OUTCOME_SIGNAL_EVENT_TYPES]
        explicit = bool(message and _EXPLICIT_GOVERNANCE.search(message))
        watermark = self._last_watermark(user_id)
        newest_outcome = self._newest_outcome_event_id(user_id)
        unevaluated = newest_outcome > watermark
        reasons: list[str] = []
        if active:
            reasons.append(f"{len(active)} ACTIVE adaptation(s) to measure, "
                           f"expire or consult.")
        if turn_outcome_events:
            reasons.append(f"This turn produced {len(turn_outcome_events)} "
                           f"canonical outcome record(s).")
        if unevaluated:
            reasons.append("Canonical outcome records exist that the last "
                           "governance evaluation has not seen.")
        if explicit:
            reasons.append("The user explicitly asked about adaptive policy.")
        result["relevance"] = {"relevant": bool(reasons), "reasons": reasons}
        if not reasons:
            result["status"] = NO_GOVERNANCE_NEEDED
            return result

        # 1. Effectiveness observations for previously influenced turns (§12).
        measured = self._measure_consultations(user_id, correlation_id)
        result["measurements"] = measured
        if measured:
            self._stage(user_id, correlation_id, "MEASURING_POLICY_OUTCOME",
                        "ACTIVE", detail=f"{len(measured)} observation(s) recorded.")
            self._stage(user_id, correlation_id, "MEASURING_POLICY_OUTCOME",
                        "COMPLETED")

        # 2. Deterministic harm / expiry handling (interaction-driven only).
        result["weakened"] = self.governance.maybe_weaken_on_harm(
            user_id, correlation_id=correlation_id)
        result["expired"] = self.governance.expire_eligible(
            user_id, correlation_id=correlation_id)

        # 3. Candidacy: only when new canonical outcome records exist (this
        # turn's or unevaluated ones) or the user explicitly asked — the
        # V10.1 relevance discipline, never a full evaluation on ordinary turns.
        if turn_outcome_events or explicit or unevaluated:
            self._stage(user_id, correlation_id, "COMPARING_POLICY_EVIDENCE",
                        "ACTIVE", detail="Assembling canonical class-B evidence.")
            evidence = self.assembler.assemble(user_id)
            self._stage(user_id, correlation_id, "COMPARING_POLICY_EVIDENCE",
                        "COMPLETED")
            result["evidence_watermark"] = newest_outcome
            signals = self.deriver.derive(
                evidence, user_id=user_id,
                policy_context=self.policy.effective(user_id))
            if signals:
                self._stage(user_id, correlation_id, "EVALUATING_POLICY_CHANGE",
                            "ACTIVE",
                            detail=f"{len(signals)} learning signal(s) derived.")
                self._stage(user_id, correlation_id, "EVALUATING_POLICY_CHANGE",
                            "COMPLETED")
            result["signals"] = [
                {k: s[k] for k in ("signal", "domain", "target", "proposed_value",
                                   "verdict", "reason", "counts", "candidacy")}
                for s in signals]
            created = 0
            existing_open = {(c["target"], c["proposed_value"])
                             for c in self.governance.list(user_id)
                             if c["state"] in ("CANDIDATE", "VALIDATING",
                                               "PROPOSED", "ACTIVE")}
            # §12.1 evidence-role separation: outcome records of turns already
            # influenced by an adaptation of a target are outcome evidence for
            # that adaptation — never adaptation evidence for a new candidate
            # of the same target.
            consulted = self.governance.consulted_turns_by_target(user_id)
            consumed = self.governance.consumed_evidence_by_target(user_id)
            evidence_by_id = {e.id: e for e in evidence}
            for signal in self.deriver.sufficient(signals):
                if created >= MAX_CANDIDATES_PER_TURN:
                    break
                key = (signal["target"], signal["proposed_value"])
                if key in existing_open:
                    continue
                excluded = consulted.get(signal["target"], set())
                used = consumed.get(signal["target"], set())
                supporting = [evidence_by_id[i] for i in signal["supporting_evidence_ids"]
                              if i in evidence_by_id
                              and evidence_by_id[i].correlation_id not in excluded
                              and evidence_by_id[i].record_id not in used]
                conflicting = [evidence_by_id[i] for i in signal["conflicting_evidence_ids"]
                               if i in evidence_by_id
                               and evidence_by_id[i].correlation_id not in excluded]
                gate = evaluate_evidence_gate(signal["domain"], supporting, conflicting)
                if gate["verdict"] != "SUFFICIENT":
                    continue  # the role-separated evidence does not justify candidacy
                signal = {**signal,
                          "supporting_evidence_ids": [e.id for e in supporting],
                          "conflicting_evidence_ids": [e.id for e in conflicting],
                          "evidence": [e.as_ref() for e in supporting],
                          "counts": gate["counts"],
                          "reason": gate["reason"]}
                candidate = self.governance.create_candidate(
                    user_id, domain=signal["domain"], target=signal["target"],
                    proposed_value=signal["proposed_value"],
                    reason=signal["reason"], signal=signal,
                    correlation_id=correlation_id)
                created += 1
                if candidate["state"] == "PROPOSED":
                    result["proposals"].append(self._proposal_view(candidate))
                else:
                    result["parked"].append(self._proposal_view(candidate))
                break  # at most one candidate per bounded turn

        open_proposals = self.governance.open_proposals(user_id)
        if open_proposals:
            if not result["proposals"]:
                result["proposals"] = [self._proposal_view(p)
                                       for p in open_proposals]
            result["status"] = (PROPOSAL_CREATED if created
                                else WAITING_FOR_CONFIRMATION)
            self._stage(user_id, correlation_id,
                        "WAITING_FOR_POLICY_CONFIRMATION", "ACTIVE",
                        detail=f"{len(open_proposals)} adaptation proposal(s) "
                               f"await your explicit decision.")
        elif result["proposals"]:
            result["status"] = PROPOSAL_CREATED
        return result

    def _measure_consultations(self, user_id: str,
                               correlation_id: str) -> list[dict[str, Any]]:
        """Record effectiveness observations for consultations from PREVIOUS
        turns (§10.3.1). The current turn's own consultation is deliberately
        skipped: its outcome records may not exist yet, and freezing an
        INSUFFICIENT_EVIDENCE verdict for it would be premature."""
        measured: list[dict[str, Any]] = []
        for candidate in self.governance.list(user_id, state="ACTIVE"):
            signal = candidate.get("signal_json") or {}
            for consultation in self.governance.consultations(
                    user_id, candidate["id"]):
                if consultation["turn_correlation_id"] == correlation_id:
                    continue  # this turn's consultation: measure it later
                already = self.db.query_one(
                    "SELECT 1 FROM policy_governance_observations WHERE "
                    "adaptation_id=? AND turn_correlation_id=? AND user_id=?",
                    (candidate["id"], consultation["turn_correlation_id"], user_id))
                if already:
                    continue
                turn_evidence = self.assembler.assemble(
                    user_id, correlation_id=consultation["turn_correlation_id"])
                category, refs, detail = classify_turn_outcome(
                    candidate["target"], candidate["proposed_value"],
                    turn_evidence,
                    error_class=signal.get("error_class"))
                observation = self.governance.record_observation(
                    user_id, candidate["id"], category=category,
                    turn_correlation_id=consultation["turn_correlation_id"],
                    evidence_refs=refs, detail=detail,
                    correlation_id=correlation_id)
                if observation:
                    measured.append(observation)
        return measured

    def revalidate_after_confirmation(self, user_id: str,
                                      candidate: dict[str, Any],
                                      *, correlation_id: str | None = None) -> dict[str, Any] | None:
        """Exactly one bounded, observational re-evaluation after a confirmed
        adaptation (§10.4) — the mirror of the V10.1 depth-1 re-audit.

        The correlation carries the terminal ``::pgov1`` marker, so it can
        never trigger maintenance, re-audit or further governance evaluation.
        Re-evaluation is observational only: it derives the domain's current
        signals and reports them; it never creates new candidates (no
        policy → policy → policy chains).
        """
        if candidate.get("state") != "ACTIVE":
            return None
        base = candidate.get("correlation_id") or correlation_id or candidate["id"]
        if base.endswith(REAUDIT_SUFFIX) or base.endswith(GOVERNANCE_MARKER):
            return None  # depth cap: never re-evaluate a re-evaluation
        rcid = f"{base}{GOVERNANCE_MARKER}"
        existing = self.governance.run_record(user_id, rcid)
        if existing is not None:
            return {"correlation_id": rcid, "depth": 1,
                    "status": existing["status"],
                    "signals": (existing.get("result") or {}).get("signals", []),
                    "persisted": True}
        run_id = f"grun_{uuid.uuid4().hex[:14]}"
        self.db.execute(
            "INSERT INTO policy_governance_runs (id,user_id,tenant_id,"
            "correlation_id,status,result_json,started_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, user_id, self.governance._tenant(user_id), rcid,
             "RUNNING", "{}", _now()))
        result: dict[str, Any] = {"correlation_id": rcid, "depth": 1,
                                  "status": GOVERNANCE_COMPLETED, "signals": []}
        try:
            evidence = self.assembler.assemble(user_id)
            signals = self.deriver.derive(
                evidence, user_id=user_id,
                policy_context=self.policy.effective(user_id))
            domain = candidate.get("domain")
            result["signals"] = [
                {k: s[k] for k in ("signal", "domain", "target",
                                   "proposed_value", "verdict", "reason")}
                for s in signals if s.get("domain") == domain]
        except Exception as exc:
            result["status"] = GOVERNANCE_FAILED
            result["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        self.db.execute(
            "UPDATE policy_governance_runs SET status=?, result_json=?, "
            "completed_at=? WHERE id=? AND user_id=?",
            (result["status"], json.dumps(result, default=str), _now(), run_id,
             user_id))
        return result

    @staticmethod
    def _proposal_view(candidate: dict[str, Any]) -> dict[str, Any]:
        return {"id": candidate["id"], "domain": candidate["domain"],
                "target": candidate["target"],
                "proposed_value": candidate["proposed_value"],
                "current_value": candidate.get("current_value"),
                "state": candidate["state"], "reason": candidate["reason"],
                "evidence_refs": candidate.get("evidence_refs", [])[:10],
                "validation": candidate.get("validation", {})}

    def _stage(self, user_id: str, correlation_id: str, stage: str,
               status: str, *, detail: str | None = None) -> None:
        if self.lifecycle is None:
            return
        try:
            self.lifecycle.transition(user_id, "governance", correlation_id,
                                      stage, status, detail=detail)
        except Exception:
            log.exception("Surface transition failed for %s/%s", stage, status)


# Deterministic explicit-request patterns for the relevance pre-gate — the
# user asking the system about its own adaptive policy governance.
_EXPLICIT_GOVERNANCE = re.compile(
    r"\b(?:adaptive polic(?:y|ies)|polic(?:y|ies) governance|governed polic"
    r"|how (?:are|should|do) you adapt|evaluat\w+ (?:your|the|this) polic)\b",
    re.I)
