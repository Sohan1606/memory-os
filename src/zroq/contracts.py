"""Shared, serializable contracts for the ZORQ Phase 2 core.

These dataclasses are protocol objects. They do not perform I/O, make policy
choices, authenticate a user, or invoke an operating system.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum, IntEnum
from types import MappingProxyType
from collections.abc import Mapping as MappingABC, Set as SetABC
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence


class RiskLevel(IntEnum):
    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3
    R4 = 4


class AssuranceLevel(IntEnum):
    A0 = 0
    A1 = 1
    A2 = 2
    A3 = 3
    A4 = 4


class PrincipalType(str, Enum):
    OWNER = "owner"
    DELEGATE = "delegate"
    SPECIALIST = "specialist"
    DEVICE = "device"
    SERVICE = "service"
    OPERATOR = "operator"


class ActionStatus(str, Enum):
    PROPOSED = "PROPOSED"
    AUTHORIZATION_CHECK = "AUTHORIZATION_CHECK"
    AUTHORIZATION_REQUIRED = "AUTHORIZATION_REQUIRED"
    READY = "READY"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    VERIFICATION = "VERIFICATION"
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    DENIED = "DENIED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    STOPPED = "STOPPED"


class MemoryState(str, Enum):
    READ = "read"
    PROPOSE = "propose"
    CONFIRMED_MUTATION = "confirmed_mutation"
    HISTORY_EVIDENCE = "history_evidence"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


class GovernanceState(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    HOLD = "hold"
    UNAVAILABLE = "unavailable"
    CONTRADICTORY = "contradictory"
    NOT_APPLICABLE = "not_applicable"


class ConfirmationMode(str, Enum):
    NONE = "none"
    EXPLICIT = "explicit"


class VerificationStatus(str, Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    FAILED = "failed"


class CapabilityState(str, Enum):
    REVIEWED = "reviewed"
    ENABLED = "enabled"
    SUSPENDED = "suspended"
    QUARANTINED = "quarantined"
    RETIRED = "retired"


@dataclass(frozen=True)
class DeviceIdentity:
    device_id: str
    owner_id: str
    device_class: str = "local"
    trust_tier: str = "D1"
    agent_version: str = "0.2.6"
    attested: bool = False


@dataclass(frozen=True)
class Session:
    session_id: str
    owner_id: str
    principal_id: str
    principal_type: PrincipalType
    device_id: str
    assurance: AssuranceLevel
    issued_at: datetime
    expires_at: datetime
    active: bool = True
    security_epoch: int = 0

    def is_valid(self, now: datetime | None = None) -> bool:
        now = now or utc_now()
        return self.active and self.issued_at <= now < self.expires_at


@dataclass(frozen=True)
class MemoryContext:
    state: MemoryState
    owner_id: str
    task_id: str
    items: tuple[Mapping[str, Any], ...] = ()
    provenance: tuple[Mapping[str, Any], ...] = ()
    message: str = ""
    contract_version: str = "MEMORY//OS-v10.2.0-adapter-v1"


@dataclass(frozen=True)
class Context:
    task_id: str
    owner_id: str
    session_id: str
    memory: MemoryContext
    time: datetime
    locale: str = "UTC"
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Intent:
    intent_id: str
    task_id: str
    owner_id: str
    user_text: str
    goal: str
    capability_id: str | None
    parameters: Mapping[str, Any]
    confidence: float
    ambiguous: bool = False
    missing_fields: tuple[str, ...] = ()
    source: str = "user"
    model_provider: str = "deterministic"


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    task_id: str
    title: str
    description: str
    capability_id: str | None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    risk: RiskLevel = RiskLevel.R0
    requires_authorization: bool = True
    source: str = "model"


@dataclass(frozen=True)
class PlanStep:
    step_id: str
    capability_id: str
    operation: str
    parameters: Mapping[str, Any]
    risk: RiskLevel
    purpose: str
    expected_effect: str
    verification: str
    dependencies: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    plan_id: str
    task_id: str
    goal: str
    steps: tuple[PlanStep, ...]
    assumptions: tuple[str, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    source: str = "planner"


@dataclass(frozen=True)
class PermissionGrant:
    grant_id: str
    owner_id: str
    principal_id: str
    capability_id: str
    operation: str
    capability_version: str = ""
    manifest_digest: str = ""
    policy_version: str = "zorq-phase2-policy-v1"
    allowed_roots: tuple[str, ...] = ()
    allowed_targets: tuple[str, ...] = ()
    purpose: str = ""
    risk_ceiling: RiskLevel = RiskLevel.R0
    confirmation_mode: ConfirmationMode = ConfirmationMode.EXPLICIT
    max_calls: int = 1
    expires_at: datetime | None = None
    active: bool = True

    def allows(self, action: "ActionRequest | ActionSnapshot", now: datetime | None = None) -> tuple[bool, str]:
        now = now or utc_now()
        if not self.active:
            return False, "grant_inactive"
        if self.expires_at is not None and now >= self.expires_at:
            return False, "grant_expired"
        if self.owner_id != action.owner_id or self.principal_id != action.principal_id:
            return False, "principal_mismatch"
        if self.capability_id != action.capability_id or self.operation != action.operation:
            return False, "capability_or_operation_not_granted"
        if self.capability_version and self.capability_version != action.capability_version:
            return False, "capability_version_mismatch"
        if action.risk > self.risk_ceiling:
            return False, "risk_above_grant_ceiling"
        if self.purpose and self.purpose != action.purpose:
            return False, "purpose_mismatch"
        path = action.parameters.get("path")
        if path is not None and self.allowed_roots:
            candidate = str(Path(path).resolve(strict=False))
            if not any(_is_within(candidate, root) for root in self.allowed_roots):
                return False, "path_outside_grant"
        target = action.parameters.get("target")
        if target is not None and self.allowed_targets and str(target) not in self.allowed_targets:
            return False, "target_not_granted"
        return True, "grant_allows"


@dataclass(frozen=True)
class CapabilityManifest:
    capability_id: str
    version: str
    operations: tuple[str, ...]
    permissions: tuple[str, ...]
    risk: RiskLevel
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]
    cancellation: str
    verification: str
    audit_events: tuple[str, ...]
    failure_behavior: str
    timeout_seconds: float
    resource_limits: Mapping[str, Any]
    state: CapabilityState = CapabilityState.ENABLED
    requires_memory_governance: bool = True
    confirmation_mode: ConfirmationMode = ConfirmationMode.EXPLICIT
    description: str = ""


@dataclass(frozen=True)
class ActionRequest:
    action_id: str
    task_id: str
    owner_id: str
    principal_id: str
    device_id: str
    capability_id: str
    capability_version: str
    operation: str
    parameters: Mapping[str, Any]
    purpose: str
    risk: RiskLevel
    expected_effect: str
    verification_requirement: str
    idempotency_key: str
    created_at: datetime
    timeout_seconds: float = 10.0

    def digest(self) -> str:
        return _action_digest(self)


@dataclass(frozen=True)
class ActionSnapshot:
    """Immutable-by-value execution snapshot created at the Kernel boundary.

    This is in-process object-integrity hardening, not cryptographic
    immutability and not a sandbox against malicious interpreter code.
    """

    action_id: str
    task_id: str
    owner_id: str
    principal_id: str
    device_id: str
    capability_id: str
    capability_version: str
    operation: str
    parameters: Mapping[str, Any]
    purpose: str
    risk: RiskLevel
    expected_effect: str
    verification_requirement: str
    idempotency_key: str
    created_at: datetime
    timeout_seconds: float = 10.0

    def digest(self) -> str:
        return _action_digest(self)


def _action_digest(action: "ActionRequest | ActionSnapshot") -> str:
    material = {
        "action_id": action.action_id,
        "task_id": action.task_id,
        "owner_id": action.owner_id,
        "principal_id": action.principal_id,
        "device_id": action.device_id,
        "capability_id": action.capability_id,
        "capability_version": action.capability_version,
        "operation": action.operation,
        "parameters": action.parameters,
        "purpose": action.purpose,
        "risk": action.risk.value,
        "expected_effect": action.expected_effect,
        "verification_requirement": action.verification_requirement,
        "timeout_seconds": action.timeout_seconds,
    }
    return sha256_digest(material)


def freeze_action_value(value: Any) -> Any:
    """Recursively freeze ActionSnapshot parameter values."""

    if isinstance(value, MappingABC):
        return MappingProxyType({key: freeze_action_value(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(freeze_action_value(item) for item in value)
    if isinstance(value, list):
        return tuple(freeze_action_value(item) for item in value)
    if isinstance(value, SetABC) and not isinstance(value, (str, bytes, bytearray, frozenset)):
        return frozenset(freeze_action_value(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(freeze_action_value(item) for item in value)
    if isinstance(value, (str, int, float, bool, type(None), datetime, Path, Enum)):
        return value
    raise TypeError(f"unsupported action parameter value type for snapshot: {type(value).__name__}")


def action_snapshot(action: ActionRequest | ActionSnapshot) -> ActionSnapshot:
    if isinstance(action, ActionSnapshot):
        return action
    return ActionSnapshot(
        action_id=action.action_id,
        task_id=action.task_id,
        owner_id=action.owner_id,
        principal_id=action.principal_id,
        device_id=action.device_id,
        capability_id=action.capability_id,
        capability_version=action.capability_version,
        operation=action.operation,
        parameters=freeze_action_value(action.parameters),
        purpose=action.purpose,
        risk=action.risk,
        expected_effect=action.expected_effect,
        verification_requirement=action.verification_requirement,
        idempotency_key=action.idempotency_key,
        created_at=action.created_at,
        timeout_seconds=action.timeout_seconds,
    )


@dataclass(frozen=True)
class Confirmation:
    confirmation_id: str
    principal_id: str
    session_id: str
    action_digest: str
    method: str
    issued_at: datetime
    expires_at: datetime
    assurance: AssuranceLevel
    accepted: bool = True
    policy_version: str = "zorq-phase2-policy-v1"
    security_epoch: int = 0

    def is_valid_for(self, action: ActionRequest | ActionSnapshot, session: Session, now: datetime | None = None) -> bool:
        now = now or utc_now()
        return (
            self.accepted
            and self.action_digest == action.digest()
            and self.principal_id == session.principal_id
            and self.session_id == session.session_id
            and self.issued_at <= now < self.expires_at
        )


@dataclass(frozen=True)
class GovernanceDecision:
    state: GovernanceState
    decision_id: str
    owner_id: str
    action_digest: str | None
    reason: str
    contract_version: str
    expires_at: datetime | None = None


@dataclass(frozen=True)
class AuthorizationDecision:
    allowed: bool
    decision_id: str
    reason: str
    risk: RiskLevel
    confirmation_required: bool
    governance: GovernanceState
    policy_version: str = "zorq-policy-v1"
    lease_id: str | None = None


@dataclass(frozen=True)
class ExecutionLease:
    lease_id: str
    action_id: str
    action_digest: str
    owner_id: str
    principal_id: str
    device_id: str
    capability_id: str
    capability_version: str
    operation: str
    issued_at: datetime
    expires_at: datetime
    max_calls: int = 1
    issuer_id: str = ""
    policy_version: str = "zorq-phase2-policy-v1"
    security_epoch: int = 0
    issuer_signature: str = ""

    def valid_for(
        self,
        action: ActionRequest | ActionSnapshot,
        now: datetime | None = None,
        policy_version: str | None = None,
        security_epoch: int | None = None,
    ) -> bool:
        now = now or utc_now()
        if policy_version is not None and self.policy_version != policy_version:
            return False
        if security_epoch is not None and self.security_epoch != security_epoch:
            return False
        return (
            self.action_id == action.action_id
            and self.action_digest == action.digest()
            and self.owner_id == action.owner_id
            and self.principal_id == action.principal_id
            and self.device_id == action.device_id
            and self.capability_id == action.capability_id
            and self.capability_version == action.capability_version
            and self.operation == action.operation
            and self.issued_at <= now < self.expires_at
            and self.max_calls > 0
            and bool(self.issuer_id)
            and bool(self.issuer_signature)
        )


@dataclass(frozen=True)
class ExecutionObservation:
    operation_id: str
    executed: bool
    effect_started: bool
    data: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    cancelled: bool = False


@dataclass(frozen=True)
class VerificationResult:
    status: VerificationStatus
    evidence: Mapping[str, Any]
    message: str
    confidence_class: str = "E0"


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    status: ActionStatus
    message: str
    authorization: AuthorizationDecision | None = None
    execution: ExecutionObservation | None = None
    verification: VerificationResult | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskResult:
    task_id: str
    status: str
    message: str
    intent: Intent | None = None
    plan: Plan | None = None
    action_results: tuple[ActionResult, ...] = ()
    proposals: tuple[Proposal, ...] = ()
    context: Context | None = None


@dataclass(frozen=True)
class AuditEvent:
    sequence: int
    event_id: str
    event_type: str
    timestamp: datetime
    actor: str
    task_id: str | None
    action_id: str | None
    payload: Mapping[str, Any]
    previous_hash: str
    event_hash: str


@dataclass(frozen=True)
class SecurityEvent:
    event_type: str
    severity: str
    actor: str
    action_id: str | None
    task_id: str | None
    reason: str
    timestamp: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _is_within(candidate: str, root: str) -> bool:
    try:
        Path(candidate).relative_to(Path(root).resolve(strict=False))
        return True
    except ValueError:
        return False


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(v) for v in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
