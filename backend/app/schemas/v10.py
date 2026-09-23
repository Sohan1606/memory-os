"""V10 cognitive self-maintenance contracts.

These schemas are deliberately separate from the V9 semantic schemas: V10
records findings about canonical V9 state, but never replace that state.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class V10Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DebtType(str, Enum):
    UNRESOLVED_DECISION = "UNRESOLVED_DECISION"
    OVERDUE_COMMITMENT = "OVERDUE_COMMITMENT"
    STALE_ASSUMPTION = "STALE_ASSUMPTION"
    UNVALIDATED_PREDICTION = "UNVALIDATED_PREDICTION"
    CONTRADICTORY_GOAL = "CONTRADICTORY_GOAL"
    CONFLICTING_PREFERENCE = "CONFLICTING_PREFERENCE"
    UNRESOLVED_CORRECTION = "UNRESOLVED_CORRECTION"
    OUTDATED_PRINCIPLE = "OUTDATED_PRINCIPLE"
    MISSING_OUTCOME = "MISSING_OUTCOME"
    WEAKENED_CLAIM = "WEAKENED_CLAIM"
    STALE_WORLD_DEPENDENCY = "STALE_WORLD_DEPENDENCY"
    MATERIAL_AMBIGUITY = "MATERIAL_AMBIGUITY"


class DebtStatus(str, Enum):
    OPEN = "OPEN"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    DEFERRED = "DEFERRED"
    RESOLVED = "RESOLVED"


class DebtSeverity(str, Enum):
    INFORMATIONAL = "INFORMATIONAL"
    ROUTINE = "ROUTINE"
    MATERIAL = "MATERIAL"
    HIGH_IMPACT = "HIGH_IMPACT"


class ContradictionClass(str, Enum):
    TRUE_CONTRADICTION = "TRUE_CONTRADICTION"
    CONTEXTUAL_TRADEOFF = "CONTEXTUAL_TRADEOFF"
    TEMPORARY_EXCEPTION = "TEMPORARY_EXCEPTION"
    VALUE_EVOLUTION = "VALUE_EVOLUTION"
    SUPERSESSION = "SUPERSESSION"
    DIFFERENT_SCOPE = "DIFFERENT_SCOPE"
    DIFFERENT_TIME = "DIFFERENT_TIME"
    INSUFFICIENT_CONTEXT = "INSUFFICIENT_CONTEXT"
    NOT_A_CONTRADICTION = "NOT_A_CONTRADICTION"


class ContradictionStatus(str, Enum):
    # DETECTED/CLASSIFIED are represented by the corresponding EventBus
    # events; persisted records are explicitly unresolved until a finding
    # lifecycle action occurs. OPEN remains the wire-compatible unresolved
    # storage value for existing V10 clients.
    DETECTED = "DETECTED"
    CLASSIFIED = "CLASSIFIED"
    UNRESOLVED = "UNRESOLVED"
    DISMISSED = "DISMISSED"
    RESOLVED_FINDING = "RESOLVED_FINDING"
    SUPERSEDED = "SUPERSEDED"
    STATE_UPDATED = "STATE_UPDATED"
    # Backward-compatible API alias for clients that used the initial V10
    # prototype vocabulary. New records do not use OPEN/RESOLVED.
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


class UnknownStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"


class ModelErrorClass(str, Enum):
    USER_MODEL_ERROR = "USER_MODEL_ERROR"
    WORLD_MODEL_ERROR = "WORLD_MODEL_ERROR"
    TIMING_ERROR = "TIMING_ERROR"
    CAUSAL_MODEL_ERROR = "CAUSAL_MODEL_ERROR"
    MISSING_INFORMATION = "MISSING_INFORMATION"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    OBSERVATION_ERROR = "OBSERVATION_ERROR"
    RANDOM_OUTCOME = "RANDOM_OUTCOME"
    UNRESOLVED = "UNRESOLVED"


class ProposalType(str, Enum):
    CLOSE_DEBT = "CLOSE_DEBT"
    RESOLVE_CONTRADICTION = "RESOLVE_CONTRADICTION"
    CONFIRM_UNKNOWN = "CONFIRM_UNKNOWN"
    REJECT_HYPOTHESIS = "REJECT_HYPOTHESIS"
    WEAKEN_BELIEF = "WEAKEN_BELIEF"
    SUPERSEDE_OBJECT = "SUPERSEDE_OBJECT"
    RETIRE_ASSUMPTION = "RETIRE_ASSUMPTION"
    RESCOPE_OBJECT = "RESCOPE_OBJECT"
    RECHECK_WORLD_DEPENDENCY = "RECHECK_WORLD_DEPENDENCY"
    RECORD_OUTCOME = "RECORD_OUTCOME"
    RECONSTRUCT_DECISION = "RECONSTRUCT_DECISION"
    DEFER_MAINTENANCE = "DEFER_MAINTENANCE"


class ProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"
    APPLIED = "APPLIED"
    BLOCKED = "BLOCKED"
    EXPIRED = "EXPIRED"


class AutonomyOutcome(str, Enum):
    ASK = "ASK"
    APPLY_SAFE = "APPLY_SAFE"
    WAIT = "WAIT"
    DO_NOTHING = "DO_NOTHING"
    BLOCKED = "BLOCKED"


class MaintenanceAction(V10Model):
    reason: str | None = Field(default=None, max_length=500)
    until: str | None = Field(default=None, max_length=80)
    classification: ContradictionClass | None = None
    error_class: ModelErrorClass | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=50)


class ModelErrorCreate(V10Model):
    prediction_id: str | None = Field(default=None, max_length=120)
    assumption_object_id: str | None = Field(default=None, max_length=120)
    expected_state: str = Field(min_length=1, max_length=4000)
    actual_observation: str = Field(min_length=1, max_length=4000)
    classification: ModelErrorClass | None = None
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    execution_error: bool = False
    world_changed: bool = False
    timing_error: bool = False
    observation_error: bool = False
    causal_error: bool = False
    random_outcome: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    learning_candidate: str | None = Field(default=None, max_length=1000)


class MaintenanceAuditRequest(V10Model):
    thread_id: str | None = Field(default=None, max_length=120)
    correlation_id: str | None = Field(default=None, max_length=120)
    include_resolved: bool = False


class V10ActionRequest(V10Model):
    reason: str | None = Field(default=None, max_length=500)
    until: str | None = Field(default=None, max_length=80)
    classification: ContradictionClass | None = None


class ProposalActionRequest(V10Model):
    confirmation: bool = False
    reason: str | None = Field(default=None, max_length=500)
    until: str | None = Field(default=None, max_length=80)


class CognitiveDebtCreate(V10Model):
    debt_type: DebtType
    severity: DebtSeverity = DebtSeverity.ROUTINE
    object_ids: list[str] = Field(default_factory=list, max_length=50)
    evidence_refs: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    reason: str = Field(min_length=1, max_length=1000)
    suggested_action: ProposalType | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
