"""
V10.1 — Cognitive Self-Maintenance runtime integration.

This module is the SMALL integration layer between the normal conversational
turn (Cognition.process_turn) and the existing V10.0.1 maintenance services.
It owns no persistence, no event store, no identity and no personal state:
everything durable already lives in the canonical V10 tables and the canonical
EventBus. The structures here are per-turn coordination values only.

Design rules (docs/V10.1-MASTER-SPEC.md):

- deterministic, explainable relevance gate — no token-frequency heuristics,
  no model-generated hidden reasoning, no "feels important";
- at most ONE top-level maintenance invocation per conversational turn
  (enforced twice: by this coordinator's call-site and by the UNIQUE
  correlation constraint on the canonical maintenance_runs table);
- narrowed service families — irrelevant families never execute;
- re-audit after a confirmed update has a hard depth of 1, encoded in the
  correlation id itself so the bound is provable from persisted data;
- the coordinator NEVER subscribes to the EventBus, so a maintenance event
  can never recursively trigger another audit;
- a maintenance failure must never break the user's conversation.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .v10 import _proposition
from ..schemas.v10 import ContradictionClass, ProposalType

log = logging.getLogger(__name__)

# ----------------------------------------------------------------- statuses
# §12 of the V10.1 directive: these six runtime outcomes are distinct.
NO_MAINTENANCE_NEEDED = "NO_MAINTENANCE_NEEDED"
MAINTENANCE_COMPLETED_NO_FINDINGS = "MAINTENANCE_COMPLETED_NO_FINDINGS"
MAINTENANCE_COMPLETED_WITH_FINDINGS = "MAINTENANCE_COMPLETED_WITH_FINDINGS"
MAINTENANCE_DEGRADED = "MAINTENANCE_DEGRADED"
MAINTENANCE_FAILED = "MAINTENANCE_FAILED"
WAITING_FOR_CONFIRMATION = "WAITING_FOR_CONFIRMATION"

# ------------------------------------------------------------- trigger kinds
STATE_CHANGE_SIGNAL = "STATE_CHANGE_SIGNAL"
CONFLICT_QUERY = "CONFLICT_QUERY"
PREDICTION_OUTCOME = "PREDICTION_OUTCOME"
UNKNOWN_RESOLUTION = "UNKNOWN_RESOLUTION"
DECISION_OUTCOME = "DECISION_OUTCOME"
WORLD_DEPENDENCY_CHANGE = "WORLD_DEPENDENCY_CHANGE"
EXPLICIT_AUDIT_REQUEST = "EXPLICIT_AUDIT_REQUEST"

# The re-audit marker. A correlation carrying this suffix identifies a bounded
# depth-1 re-audit; the coordinator refuses to audit it again (depth cap).
REAUDIT_SUFFIX = "::reaudit1"

# Cognitive object types whose creation or questioning makes the personal
# model materially involved in this turn (directive §2.A).
_STATE_TYPES = frozenset({
    "PREFERENCE", "VALUE", "GOAL", "BOUNDARY", "BELIEF", "ASSUMPTION",
    "COMMITMENT", "DECISION", "INTENT", "CORRECTION", "CONTRADICTION",
})
_OUTCOME_TYPES = frozenset({"OUTCOME", "OBSERVATION"})
_EVIDENCE_TYPES = frozenset({"FACT", "OBSERVATION", "CORRECTION", "CLAIM"})

# Deterministic, inspectable phrase policies. These are explicit intent
# signals, not token-frequency guesses: each pattern names the user asking the
# system about conflict, change, prior state, or an explicit audit.
# The V9 meaning compiler classifies OUTCOME/OBSERVATION objects by these
# exact marker phrases but stores the full utterance. Stripping the marker
# before the canonical proposition projection is deterministic normalisation
# of the SAME compiler rule vocabulary — not semantic guessing.
_OUTCOME_MARKER = re.compile(
    r"^(?:the\s+outcome\s+was|it\s+resulted\s+in|what\s+happened\s+was|"
    r"it\s+turns\s+out(?:\s+that)?|ended\s+up|"
    r"i\s+noticed(?:\s+that)?|i\s+observed(?:\s+that)?|i\s+saw\s+that)"
    r"\s*[:,]?\s*", re.I)

_CONFLICT_QUERY = re.compile(
    r"\b(?:conflict(?:s|ing)?|contradict(?:s|ion|ory|ing)?|inconsistent|"
    r"clash(?:es)?\s+with|(?:did|have)\s+(?:i|my)\s+.{0,40}\bchange(?:d)?|"
    r"is\s+(?:that|this)\s+consistent|what\s+did\s+i\s+(?:say|value|prefer|"
    r"believe|decide|want)\s)", re.I)
_EXPLICIT_AUDIT = re.compile(
    r"\b(?:audit|review|inspect|check)\s+(?:my|your|the)\s+"
    r"(?:personal\s+)?(?:model|state|beliefs|assumptions|memory\s+of\s+me)\b|"
    r"\brun\s+(?:a\s+)?maintenance\b", re.I)

# Service families → the truthful surface stages that describe their work.
FAMILY_STAGES: dict[str, tuple[str, ...]] = {
    "debt": ("CHECKING_FOR_STALE_STATE",),
    "contradictions": ("COMPARING_PERSONAL_STATE", "CHECKING_CONTRADICTIONS"),
    "unknowns": ("CHECKING_OPEN_UNKNOWNS",),
    "model_errors": ("COMPARING_PREDICTION_TO_OUTCOME", "ANALYZING_MODEL_ERROR"),
    "drift": ("COMPARING_PERSONAL_STATE",),
}

_TRIGGER_DOMAINS: dict[str, tuple[str, ...]] = {
    STATE_CHANGE_SIGNAL: ("contradictions", "drift"),
    CONFLICT_QUERY: ("contradictions",),
    PREDICTION_OUTCOME: ("model_errors", "debt"),
    UNKNOWN_RESOLUTION: ("unknowns",),
    DECISION_OUTCOME: ("debt",),
    WORLD_DEPENDENCY_CHANGE: ("debt",),
    EXPLICIT_AUDIT_REQUEST: ("debt", "contradictions", "unknowns",
                             "model_errors", "drift"),
}
_TRIGGER_CONFIDENCE: dict[str, float] = {
    EXPLICIT_AUDIT_REQUEST: 1.0,
    STATE_CHANGE_SIGNAL: 0.9,
    CONFLICT_QUERY: 0.9,
    PREDICTION_OUTCOME: 0.85,
    DECISION_OUTCOME: 0.8,
    UNKNOWN_RESOLUTION: 0.8,
    WORLD_DEPENDENCY_CHANGE: 0.75,
}


@dataclass
class MaintenanceRelevance:
    """Explainable, deterministic relevance verdict for one turn."""

    relevant: bool = False
    trigger_kinds: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    relevant_domains: list[str] = field(default_factory=list)
    confidence: float = 0.0
    bounded_checks: list[str] = field(default_factory=list)

    @property
    def trigger_kind(self) -> str | None:
        return self.trigger_kinds[0] if self.trigger_kinds else None

    def add(self, kind: str, reason: str) -> None:
        if kind not in self.trigger_kinds:
            self.trigger_kinds.append(kind)
        self.reasons.append(reason)
        for domain in _TRIGGER_DOMAINS[kind]:
            if domain not in self.relevant_domains:
                self.relevant_domains.append(domain)
        self.relevant = True
        self.confidence = max(self.confidence, _TRIGGER_CONFIDENCE[kind])
        for domain in _TRIGGER_DOMAINS[kind]:
            for stage in FAMILY_STAGES[domain]:
                if stage not in self.bounded_checks:
                    self.bounded_checks.append(stage)

    def as_dict(self) -> dict[str, Any]:
        return {
            "relevant": self.relevant,
            "trigger_kind": self.trigger_kind,
            "trigger_kinds": list(self.trigger_kinds),
            "reasons": list(self.reasons),
            "relevant_domains": list(self.relevant_domains),
            "confidence": round(self.confidence, 2),
            "bounded_checks": list(self.bounded_checks),
        }


class MaintenanceRelevanceGate:
    """Deterministic gate deciding whether V10 maintenance may run this turn.

    Every signal below is derived from canonical data the turn ALREADY
    produced (compiled meaning, created objects, canonical predictions,
    decisions, unknowns, world entities). Nothing here consults a model or
    hidden reasoning, and an ordinary message triggers nothing.
    """

    def __init__(self, db, personal_state, unknowns) -> None:
        self.db = db
        self.personal_state = personal_state
        self.unknowns = unknowns

    # The gate reads canonical V9 rows through the same scope predicate the
    # V10 services use; user_id here is always the verified namespace.
    def evaluate(self, user_id: str, message: str, *,
                 meaning: dict[str, Any] | None,
                 world_entities: list[dict[str, Any]] | None = None) -> MaintenanceRelevance:
        rel = MaintenanceRelevance()
        semantic = (meaning or {}).get("semantic") or {}
        created = (meaning or {}).get("created_objects") or []
        created_types = {str(o.get("type")) for o in created}
        candidate_types = {str(c.get("type")) for c in semantic.get("candidates", [])
                           if c.get("material")}

        # G — explicit audit request (checked first: strongest signal).
        if _EXPLICIT_AUDIT.search(message):
            rel.add(EXPLICIT_AUDIT_REQUEST,
                    "The user explicitly asked to inspect or maintain the personal model.")

        # A — personal state change or questioning.
        touched = (created_types | candidate_types) & _STATE_TYPES
        if touched:
            rel.add(STATE_CHANGE_SIGNAL,
                    f"The turn materially involves personal-state object type(s): "
                    f"{', '.join(sorted(touched))}.")

        # B — conflict / prior-state query.
        if _CONFLICT_QUERY.search(message):
            rel.add(CONFLICT_QUERY,
                    "The user asked about conflict, consistency, or previously "
                    "recorded personal state.")

        # C / E — an observed outcome relating to open predictions / decisions.
        if created_types & _OUTCOME_TYPES:
            if self.db.query_one(
                    "SELECT 1 FROM predictions WHERE user_id=? AND status='open' LIMIT 1",
                    (user_id,)):
                rel.add(PREDICTION_OUTCOME,
                        "The turn recorded an observed outcome while open "
                        "canonical predictions exist.")
            if self.db.query_one(
                    "SELECT 1 FROM decisions WHERE user_id=? AND status='open' LIMIT 1",
                    (user_id,)):
                rel.add(DECISION_OUTCOME,
                        "The turn recorded an observed outcome while open "
                        "canonical decisions exist.")
        # Incorrect-but-unanalysed predictions also make the model-error
        # family relevant, even without a new outcome object this turn.
        if self.db.query_one(
                "SELECT 1 FROM predictions p WHERE p.user_id=? AND p.status='incorrect' "
                "AND NOT EXISTS (SELECT 1 FROM model_error_records m "
                "WHERE m.user_id=p.user_id AND m.prediction_id=p.id) LIMIT 1",
                (user_id,)):
            rel.add(PREDICTION_OUTCOME,
                    "A canonical prediction was evaluated incorrect and has no "
                    "recorded model-error analysis yet.")

        # D — new canonical evidence while explicit unknowns are open.
        if created_types & _EVIDENCE_TYPES:
            open_unknowns = self.unknowns.list(user_id, status="OPEN", limit=50)
            if open_unknowns:
                rel.add(UNKNOWN_RESOLUTION,
                        f"{len(open_unknowns)} explicit unknown(s) are open and the "
                        "turn produced new canonical evidence.")

        # F — a stale world dependency referenced by this turn's world objects.
        touched_ids = {str(e.get("id")) for e in (world_entities or []) if e.get("id")}
        if touched_ids:
            placeholders = ",".join("?" * len(touched_ids))
            stale = self.db.query_one(
                f"SELECT 1 FROM world_entities WHERE user_id=? AND stale=1 "
                f"AND state='active' AND id IN ({placeholders}) LIMIT 1",
                (user_id, *sorted(touched_ids)))
            if stale:
                rel.add(WORLD_DEPENDENCY_CHANGE,
                        "A world dependency referenced by this turn is marked "
                        "stale by the canonical World Model.")
        return rel


# ---------------------------------------------------------------- summaries
# User-facing sentences derived ONLY from canonical finding records. This is
# not model reasoning: each mapping is a deterministic label for a canonical
# classification, presented with real evidence references.
_CONTRA_SUMMARY: dict[str, str] = {
    ContradictionClass.TRUE_CONTRADICTION.value:
        "I found that this may conflict with an earlier recorded statement.",
    ContradictionClass.VALUE_EVOLUTION.value:
        "This appears to be a change in priority rather than a direct contradiction.",
    ContradictionClass.CONTEXTUAL_TRADEOFF.value:
        "These look like competing goals or values whose context is not recorded.",
    ContradictionClass.TEMPORARY_EXCEPTION.value:
        "This looks like a bounded temporary exception, not a durable change.",
    ContradictionClass.INSUFFICIENT_CONTEXT.value:
        "I don't have enough evidence to decide whether these statements refer "
        "to the same situation.",
    ContradictionClass.SUPERSESSION.value:
        "A newer canonical statement explicitly replaces an earlier one.",
    ContradictionClass.DIFFERENT_SCOPE.value:
        "These statements apply to different recorded contexts.",
    ContradictionClass.DIFFERENT_TIME.value:
        "These statements apply to different time periods.",
}


def summarize_findings(audit_result: dict[str, Any],
                       prediction_matches: list[dict[str, Any]],
                       unknown_matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Safe conversation-level summaries from canonical findings."""
    lines: list[dict[str, Any]] = []
    for record in audit_result.get("contradictions", []) or []:
        text = _CONTRA_SUMMARY.get(record.get("classification"))
        if text:
            lines.append({"kind": "CONTRADICTION",
                          "classification": record.get("classification"),
                          "summary": text,
                          "finding_id": record.get("id"),
                          "evidence_refs": record.get("evidence_refs", [])})
    for debt in audit_result.get("debt", []) or []:
        lines.append({"kind": "COGNITIVE_DEBT",
                      "classification": debt.get("debt_type"),
                      "summary": debt.get("reason"),
                      "finding_id": debt.get("id"),
                      "evidence_refs": debt.get("evidence_refs", [])})
    for unknown in audit_result.get("unknowns", []) or []:
        lines.append({"kind": "UNKNOWN",
                      "classification": unknown.get("status"),
                      "summary": unknown.get("what_unknown"),
                      "finding_id": unknown.get("id"),
                      "evidence_refs": unknown.get("missing_evidence", [])})
    for error in audit_result.get("model_errors", []) or []:
        lines.append({"kind": "MODEL_ERROR",
                      "classification": error.get("error_class"),
                      "summary": "A prediction and its observation were compared "
                                 f"and classified as {error.get('error_class')}.",
                      "finding_id": error.get("id"),
                      "evidence_refs": error.get("evidence_refs", [])})
    for match in prediction_matches:
        lines.append({"kind": "PREDICTION_OUTCOME",
                      "classification": "OUTCOME_OBSERVED",
                      "summary": "An earlier prediction now appears to have an "
                                 "observed outcome.",
                      "finding_id": match.get("prediction_id"),
                      "evidence_refs": [
                          {"kind": "prediction", "id": match.get("prediction_id")},
                          {"kind": "cognitive_object", "id": match.get("outcome_object_id")}]})
    for match in unknown_matches:
        lines.append({"kind": "UNKNOWN_RESOLUTION",
                      "classification": "EVIDENCE_AVAILABLE",
                      "summary": "I found an open question that this new "
                                 "information may resolve.",
                      "finding_id": match.get("unknown_id"),
                      "evidence_refs": [
                          {"kind": "cognitive_object", "id": match.get("evidence_object_id")}]})
    return lines


class MaintenanceRuntimeCoordinator:
    """Connects one conversational turn to bounded V10 maintenance.

    Holds references to the SAME service instances the composition root
    already owns — it is not a parallel maintenance implementation, and it
    keeps no mutable module/process state. It is invoked from exactly two
    places: Cognition.process_turn (once per turn) and the proposal
    confirmation boundary (bounded re-audit, depth 1).
    """

    def __init__(self, db, bus, personal_state, self_maintenance, proposals,
                 unknowns, lifecycle) -> None:
        self.db = db
        self.bus = bus
        self.personal_state = personal_state
        self.self_maintenance = self_maintenance
        self.proposals = proposals
        self.unknowns = unknowns
        self.lifecycle = lifecycle
        self.gate = MaintenanceRelevanceGate(db, personal_state, unknowns)

    # ------------------------------------------------------------- the turn
    def run_for_turn(self, user_id: str, message: str, *, correlation_id: str,
                     thread_id: str, meaning: dict[str, Any] | None,
                     world_entities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """One bounded maintenance pass for one conversational turn."""
        relevance = self.gate.evaluate(user_id, message, meaning=meaning,
                                       world_entities=world_entities)
        context: dict[str, Any] = {
            "correlation_id": correlation_id,
            "relevance": relevance.as_dict(),
            "status": NO_MAINTENANCE_NEEDED,
            "findings": [], "proposals": [], "audit": None,
            "reaudit_depth": 0,
        }
        self.bus.emit(
            user_id, "maintenance.relevance_determined",
            ("Maintenance is relevant to this turn."
             if relevance.relevant else
             "Maintenance is not relevant to this turn."),
            subject_kind="maintenance_run", subject_id=correlation_id,
            correlation_id=correlation_id, payload=relevance.as_dict())
        if not relevance.relevant:
            return context
        # Depth guard: a re-audit correlation must never trigger a new audit.
        if correlation_id.endswith(REAUDIT_SUFFIX):
            context["status"] = NO_MAINTENANCE_NEEDED
            context["relevance"]["reasons"].append(
                "Re-audit correlations are terminal; no further audit is allowed.")
            return context
        try:
            audit = self.self_maintenance.audit(
                user_id, correlation_id=correlation_id,
                families=relevance.relevant_domains,
                lifecycle=self.lifecycle, thread_id=thread_id)
            prediction_matches = self._match_prediction_outcomes(
                user_id, meaning, relevance, correlation_id=correlation_id)
            unknown_matches = self._match_unknown_evidence(
                user_id, meaning, relevance, correlation_id=correlation_id)
            findings = summarize_findings(audit, prediction_matches, unknown_matches)
            open_proposals = [p for p in (audit.get("proposals") or [])
                              if p.get("status") in ("PROPOSED", "DEFERRED")]
            context["audit"] = {
                "correlation_id": audit.get("correlation_id"),
                "status": audit.get("status"),
                "families": list(relevance.relevant_domains),
                "counts": {
                    "debt": len(audit.get("debt") or []),
                    "contradictions": len(audit.get("contradictions") or []),
                    "unknowns": len(audit.get("unknowns") or []),
                    "model_errors": len(audit.get("model_errors") or []),
                    "drift": len(audit.get("drift") or []),
                },
            }
            context["findings"] = findings
            context["proposals"] = [
                {"id": p["id"], "proposal_type": p["proposal_type"],
                 "status": p["status"], "reason": p["reason"],
                 "uncertainty": p.get("uncertainty"),
                 "evidence_refs": p.get("evidence_refs", []),
                 "reversible": bool(p.get("reversible"))}
                for p in open_proposals]
            if open_proposals:
                context["status"] = WAITING_FOR_CONFIRMATION
                self._stage(user_id, thread_id, correlation_id,
                            "WAITING_FOR_CONFIRMATION", "ACTIVE",
                            detail=f"{len(open_proposals)} maintenance proposal(s) "
                                   "await your explicit decision.")
            elif findings:
                context["status"] = MAINTENANCE_COMPLETED_WITH_FINDINGS
            else:
                context["status"] = MAINTENANCE_COMPLETED_NO_FINDINGS
        except Exception as exc:
            log.warning("Bounded maintenance audit failed for %s: %s",
                        correlation_id, exc)
            context["status"] = MAINTENANCE_FAILED
            context["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return context

    # -------------------------------------------------- prediction ↔ outcome
    def _match_prediction_outcomes(self, user_id: str, meaning: dict[str, Any] | None,
                                   relevance: MaintenanceRelevance, *,
                                   correlation_id: str) -> list[dict[str, Any]]:
        """Deterministically link a new outcome object to open predictions.

        The link requires a canonical proposition-subject match — token
        overlap alone never links. No prediction is evaluated here: the V8
        PredictionEngine remains authoritative, so the runtime only proposes
        RECORD_OUTCOME through the confirmation-gated proposal service.
        """
        if PREDICTION_OUTCOME not in relevance.trigger_kinds:
            return []
        outcomes = [o for o in (meaning or {}).get("created_objects") or []
                    if str(o.get("type")) in _OUTCOME_TYPES]
        if not outcomes:
            return []
        matches: list[dict[str, Any]] = []
        rows = self.db.query(
            "SELECT * FROM predictions WHERE user_id=? AND status='open' "
            "ORDER BY created_at ASC LIMIT 100", (user_id,))
        for outcome in outcomes:
            stripped = _OUTCOME_MARKER.sub("", str(outcome.get("content", "")))
            outcome_prop = _proposition({**outcome, "content": stripped})
            if not outcome_prop or outcome_prop.get("uncertain"):
                continue
            for row in rows:
                pred_prop = _proposition(
                    {"content": row["statement"], "type": "PREDICTION",
                     "modality": "ASSERTED", "metadata": {}})
                if not pred_prop:
                    continue
                if (pred_prop["subject"] == outcome_prop["subject"]
                        and pred_prop["predicate"] == outcome_prop["predicate"]):
                    try:
                        self.proposals.propose(
                            user_id,
                            proposal_type=ProposalType.RECORD_OUTCOME.value,
                            target_object_ids=[row["id"]],
                            current_state={"prediction_status": row["status"]},
                            proposed_state={"next": "record the observed outcome "
                                                    "against this prediction"},
                            reason="A new observation matches an open canonical "
                                   "prediction; confirming records the outcome.",
                            evidence_refs=[
                                {"kind": "prediction", "id": row["id"]},
                                {"kind": "cognitive_object", "id": outcome["id"]}],
                            uncertainty="Whether the observation settles the "
                                        "prediction is a user decision.",
                            reversible=True, correlation_id=correlation_id)
                    except (KeyError, ValueError) as exc:
                        log.info("Prediction-outcome proposal skipped: %s", exc)
                        continue
                    matches.append({"prediction_id": row["id"],
                                    "outcome_object_id": outcome["id"]})
        return matches

    # ------------------------------------------------------ unknown evidence
    def _match_unknown_evidence(self, user_id: str, meaning: dict[str, Any] | None,
                                relevance: MaintenanceRelevance, *,
                                correlation_id: str) -> list[dict[str, Any]]:
        """Link new canonical evidence to open unknowns; never auto-resolve.

        Resolution stays confirmation-gated: sufficient-looking evidence only
        produces a CONFIRM_UNKNOWN proposal. Insufficient evidence changes
        nothing and the unknown remains OPEN.
        """
        if UNKNOWN_RESOLUTION not in relevance.trigger_kinds:
            return []
        new_evidence = [o for o in (meaning or {}).get("created_objects") or []
                        if str(o.get("type")) in _EVIDENCE_TYPES]
        if not new_evidence:
            return []
        matches: list[dict[str, Any]] = []
        for unknown in self.unknowns.list(user_id, status="OPEN", limit=50):
            related_props = []
            for oid in unknown.get("relevant_object_ids", []):
                obj = self.personal_state.get(user_id, oid)
                if obj:
                    prop = _proposition(obj)
                    if prop:
                        related_props.append(prop)
            for evidence in new_evidence:
                stripped = _OUTCOME_MARKER.sub("", str(evidence.get("content", "")))
                ev_prop = _proposition({**evidence, "content": stripped})
                if not ev_prop or ev_prop.get("uncertain"):
                    continue
                if any(p["subject"] == ev_prop["subject"]
                       and p["predicate"] == ev_prop["predicate"]
                       for p in related_props):
                    try:
                        self.proposals.propose(
                            user_id,
                            proposal_type=ProposalType.CONFIRM_UNKNOWN.value,
                            target_object_ids=[unknown["id"]],
                            current_state={"unknown_status": unknown["status"]},
                            proposed_state={"unknown_status": "RESOLVED"},
                            reason="New canonical evidence appears to answer this "
                                   "open question; confirm to resolve it.",
                            evidence_refs=[
                                {"kind": "cognitive_object", "id": evidence["id"]}],
                            uncertainty="Only you can confirm the evidence "
                                        "actually settles the question.",
                            reversible=True, correlation_id=correlation_id)
                    except (KeyError, ValueError) as exc:
                        log.info("Unknown-resolution proposal skipped: %s", exc)
                        continue
                    matches.append({"unknown_id": unknown["id"],
                                    "evidence_object_id": evidence["id"]})
                    break
        return matches

    # -------------------------------------------------------------- re-audit
    def reaudit_after_confirmation(self, user_id: str,
                                   proposal: dict[str, Any], *,
                                   correlation_id: str | None = None) -> dict[str, Any] | None:
        """Exactly one bounded re-audit after a confirmed, applied proposal.

        The re-audit correlation is derived from the proposal's original
        correlation with a terminal suffix, so (a) the maintenance_runs UNIQUE
        constraint makes it idempotent and (b) the depth cap of 1 is provable:
        a suffix-marked correlation is refused by run_for_turn and by this
        method alike.
        """
        if proposal.get("status") != "APPLIED":
            return None
        base = proposal.get("correlation_id") or correlation_id or proposal["id"]
        if base.endswith(REAUDIT_SUFFIX):
            return None  # depth cap: never re-audit a re-audit
        reaudit_cid = f"{base}{REAUDIT_SUFFIX}"
        # Verify the canonical transition actually happened before re-auditing.
        current_version = self.personal_state.current(user_id).get("version")
        self.bus.emit(
            user_id, "maintenance.reaudit_started",
            "Started the bounded post-confirmation re-audit.",
            subject_kind="maintenance_proposal", subject_id=proposal["id"],
            correlation_id=reaudit_cid,
            payload={"base_correlation_id": base, "depth": 1,
                     "personal_state_version": current_version})
        thread_id = "maintenance"
        self._stage(user_id, thread_id, reaudit_cid, "RE_AUDITING_MODEL", "ACTIVE")
        try:
            audit = self.self_maintenance.audit(
                user_id, correlation_id=reaudit_cid,
                families=["debt", "contradictions", "unknowns"],
                lifecycle=self.lifecycle, thread_id=thread_id)
            self._stage(user_id, thread_id, reaudit_cid, "RE_AUDITING_MODEL",
                        "COMPLETED")
            result = {"correlation_id": reaudit_cid, "status": audit.get("status"),
                      "depth": 1, "personal_state_version": current_version,
                      "counts": {
                          "debt": len(audit.get("debt") or []),
                          "contradictions": len(audit.get("contradictions") or []),
                          "unknowns": len(audit.get("unknowns") or [])}}
            self.bus.emit(
                user_id, "maintenance.reaudit_completed",
                "Completed the bounded post-confirmation re-audit.",
                subject_kind="maintenance_proposal", subject_id=proposal["id"],
                correlation_id=reaudit_cid, payload=result)
            return result
        except Exception as exc:
            self._stage(user_id, thread_id, reaudit_cid, "RE_AUDITING_MODEL",
                        "FAILED", detail=f"Re-audit failed: {type(exc).__name__}")
            self.bus.emit(
                user_id, "maintenance.reaudit_completed",
                "The bounded re-audit failed honestly.",
                subject_kind="maintenance_proposal", subject_id=proposal["id"],
                correlation_id=reaudit_cid,
                payload={"status": "FAILED", "depth": 1,
                         "error": str(exc)[:200]})
            return {"correlation_id": reaudit_cid, "status": "FAILED", "depth": 1,
                    "error": f"{type(exc).__name__}: {str(exc)[:200]}"}

    # --------------------------------------------------------------- helpers
    def _stage(self, user_id: str, thread_id: str, correlation_id: str,
               stage: str, status: str, *, detail: str | None = None) -> None:
        try:
            self.lifecycle.transition(user_id, thread_id, correlation_id,
                                      stage, status, detail=detail)
        except Exception:  # surface emission must never break maintenance
            log.exception("Surface transition failed for %s/%s", stage, status)
