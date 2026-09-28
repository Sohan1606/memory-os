"""ZORQ Phase 3A executable domain schemas and contracts.

This module intentionally implements schemas/contracts only. It does not provide
persistent storage, MEMORY//OS production integration, voice, browser automation,
network research, daemon behavior, or action execution. The Phase 2.6 Action
Kernel remains the sole execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType, UnionType
from typing import Any, ClassVar, Mapping, Protocol, TypeVar, get_args, get_origin, get_type_hints
from collections.abc import Mapping as MappingABC
import json
import re

SCHEMA_VERSION = "zorq.phase3a.v1"
SERIALIZATION_FORMAT = "json-compatible-dict/v1"
BACKWARDS_COMPATIBILITY_POLICY = (
    "Phase 3A accepts only explicitly supported schema versions; unknown fields "
    "and unknown enum values fail closed. Migrations must be explicit."
)

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
T = TypeVar("T", bound="ContractRecord")


class ContractValidationError(ValueError):
    """Raised when an executable Phase 3A contract is invalid."""


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class PrivacyClass(str, Enum):
    PUBLIC = "PUBLIC"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"
    REGULATED = "REGULATED"
    THIRD_PARTY = "THIRD_PARTY"
    SYSTEM_SECURITY = "SYSTEM_SECURITY"


class RetentionMode(str, Enum):
    DEFAULT_RETAIN = "DEFAULT_RETAIN"
    DEFAULT_DO_NOT_RETAIN = "DEFAULT_DO_NOT_RETAIN"
    PER_CONVERSATION_OVERRIDE = "PER_CONVERSATION_OVERRIDE"
    PER_MESSAGE_OVERRIDE = "PER_MESSAGE_OVERRIDE"
    TEMPORARY = "TEMPORARY"
    PROJECT_SCOPED = "PROJECT_SCOPED"
    EXPLICIT_REMEMBER = "EXPLICIT_REMEMBER"
    EXPLICIT_DO_NOT_REMEMBER = "EXPLICIT_DO_NOT_REMEMBER"


class RetentionOverride(str, Enum):
    INHERIT = "INHERIT"
    RETAIN = "RETAIN"
    DO_NOT_RETAIN = "DO_NOT_RETAIN"
    TEMPORARY = "TEMPORARY"


class DeletionBehavior(str, Enum):
    DELETE_ALL_REPRESENTATIONS = "DELETE_ALL_REPRESENTATIONS"
    TOMBSTONE_SOURCE = "TOMBSTONE_SOURCE"
    RETAIN_SOURCE_DELETE_DERIVED = "RETAIN_SOURCE_DELETE_DERIVED"
    RETAIN_BY_POLICY = "RETAIN_BY_POLICY"


class LifecycleState(str, Enum):
    CURRENT = "CURRENT"
    HISTORICAL = "HISTORICAL"
    SUPERSEDED = "SUPERSEDED"
    CONFLICTED = "CONFLICTED"
    UNVERIFIED = "UNVERIFIED"
    DELETED = "DELETED"
    DELETION_PARTIAL = "DELETION_PARTIAL"
    DELETION_UNKNOWN = "DELETION_UNKNOWN"
    RETAINED_BY_POLICY = "RETAINED_BY_POLICY"


class VerificationState(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    UNVERIFIED = "UNVERIFIED"
    PARTIAL = "PARTIAL"


class SourceType(str, Enum):
    CONVERSATION_MESSAGE = "CONVERSATION_MESSAGE"
    UPLOADED_DOCUMENT = "UPLOADED_DOCUMENT"
    FILE = "FILE"
    EXPLICIT_FACT = "EXPLICIT_FACT"
    ACTION_OBSERVATION = "ACTION_OBSERVATION"
    EXTERNAL_EVIDENCE = "EXTERNAL_EVIDENCE"
    SYSTEM_EVENT = "SYSTEM_EVENT"


class DerivationMethod(str, Enum):
    SOURCE = "SOURCE"
    USER_ASSERTION = "USER_ASSERTION"
    STRUCTURED_EXTRACTION = "STRUCTURED_EXTRACTION"
    SUMMARY = "SUMMARY"
    INFERENCE = "INFERENCE"
    VERIFICATION = "VERIFICATION"
    IMPORT = "IMPORT"


class MemoryKind(str, Enum):
    EPISODIC = "EPISODIC"
    SEMANTIC = "SEMANTIC"
    PROCEDURAL = "PROCEDURAL"
    PERSONAL = "PERSONAL"
    PROJECT = "PROJECT"
    DECISION = "DECISION"
    EVENT = "EVENT"
    RELATIONSHIP = "RELATIONSHIP"
    WORLD_KNOWLEDGE = "WORLD_KNOWLEDGE"
    SYSTEM = "SYSTEM"
    OUTCOME = "OUTCOME"


class ConversationState(str, Enum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    PAUSED = "PAUSED"
    WAITING = "WAITING"
    RESUMING = "RESUMING"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"


class GenerationState(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    GENERATING = "GENERATING"
    GENERATED = "GENERATED"
    INTERRUPTED = "INTERRUPTED"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"


class SpeechState(str, Enum):
    IDLE = "IDLE"
    SPEAKING = "SPEAKING"
    INTERRUPTED = "INTERRUPTED"
    PAUSED = "PAUSED"
    RESUMING = "RESUMING"
    CANCELED = "CANCELED"
    COMPLETED = "COMPLETED"


class ResumePolicy(str, Enum):
    RESUME_EXACT = "RESUME_EXACT"
    SUMMARIZE_THEN_RESUME = "SUMMARIZE_THEN_RESUME"
    REGENERATE_ALLOWED = "REGENERATE_ALLOWED"
    DO_NOT_RESUME = "DO_NOT_RESUME"


class InteractionCommandType(str, Enum):
    STOP = "STOP"
    PAUSE = "PAUSE"
    CONTINUE = "CONTINUE"
    CANCEL = "CANCEL"
    REPEAT = "REPEAT"
    GO_BACK = "GO_BACK"
    SKIP = "SKIP"
    CHANGE_TOPIC = "CHANGE_TOPIC"


class InteractionTarget(str, Enum):
    SPEECH = "SPEECH"
    RESPONSE_GENERATION = "RESPONSE_GENERATION"
    ACTION = "ACTION"
    CONVERSATION = "CONVERSATION"
    MEMORY_OPERATION = "MEMORY_OPERATION"
    UNKNOWN = "UNKNOWN"


class RetrievalMode(str, Enum):
    EXACT = "EXACT"
    SEMANTIC = "SEMANTIC"
    TEMPORAL = "TEMPORAL"
    RELATIONAL = "RELATIONAL"
    CAUSAL_HISTORICAL = "CAUSAL_HISTORICAL"


class GovernanceStatus(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HOLD = "HOLD"
    UNAVAILABLE = "UNAVAILABLE"
    CONTRADICTORY = "CONTRADICTORY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DeletionStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    RETAINED_BY_POLICY = "RETAINED_BY_POLICY"


class DeletionPropagationTarget(str, Enum):
    SOURCE = "SOURCE"
    DERIVED_MEMORY = "DERIVED_MEMORY"
    EMBEDDINGS = "EMBEDDINGS"
    INDEXES = "INDEXES"
    GRAPHS = "GRAPHS"
    CACHES = "CACHES"
    BACKUPS = "BACKUPS"


class ProviderTrustClass(str, Enum):
    LOCAL_ONLY = "LOCAL_ONLY"
    TRUSTED_PRIVATE = "TRUSTED_PRIVATE"
    EXTERNAL_CONTRACTED = "EXTERNAL_CONTRACTED"
    EXTERNAL_UNTRUSTED = "EXTERNAL_UNTRUSTED"


class EgressPolicy(str, Enum):
    NO_EGRESS = "NO_EGRESS"
    MINIMIZED = "MINIMIZED"
    REDACTED = "REDACTED"
    OWNER_APPROVED = "OWNER_APPROVED"


class MemoryActivationTrigger(str, Enum):
    USER_REQUESTED_SEARCH = "USER_REQUESTED_SEARCH"
    SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION = "SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION"


class MemoryRelevanceSignalType(str, Enum):
    SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"
    TEMPORAL_RELEVANCE = "TEMPORAL_RELEVANCE"
    ENTITY_OVERLAP = "ENTITY_OVERLAP"
    PROJECT_OVERLAP = "PROJECT_OVERLAP"
    GOAL_OVERLAP = "GOAL_OVERLAP"
    DECISION_OVERLAP = "DECISION_OVERLAP"
    CAUSAL_RELATIONSHIP = "CAUSAL_RELATIONSHIP"
    REPEATED_PATTERN = "REPEATED_PATTERN"
    EXPLICIT_PRIOR_REFERENCE = "EXPLICIT_PRIOR_REFERENCE"
    CURRENT_STATE_COMPATIBILITY = "CURRENT_STATE_COMPATIBILITY"


class MemoryRelevanceLevel(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"
    UNCERTAIN = "UNCERTAIN"


class MemoryActivationDecisionState(str, Enum):
    NOT_RELEVANT = "NOT_RELEVANT"
    RELEVANT = "RELEVANT"
    ACTIVATED = "ACTIVATED"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    BLOCKED_BY_PRIVACY = "BLOCKED_BY_PRIVACY"
    CONFLICTED = "CONFLICTED"
    STALE = "STALE"
    UNCERTAIN = "UNCERTAIN"


class MemoryActivationPolicyDecision(str, Enum):
    SHOW = "SHOW"
    USE_INTERNAL_ONLY = "USE_INTERNAL_ONLY"
    REDACT = "REDACT"
    BLOCK = "BLOCK"


class MemoryValidity(str, Enum):
    HISTORICALLY_VALID = "HISTORICALLY_VALID"
    CURRENTLY_VALID = "CURRENTLY_VALID"
    HISTORICALLY_RELEVANT_CURRENTLY_INVALID = "HISTORICALLY_RELEVANT_CURRENTLY_INVALID"
    SUPERSEDED = "SUPERSEDED"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"


class EntityType(str, Enum):
    PERSON = "PERSON"
    PROJECT = "PROJECT"
    GOAL = "GOAL"
    FILE = "FILE"
    DOCUMENT = "DOCUMENT"
    SYSTEM = "SYSTEM"
    EVENT = "EVENT"
    ORGANIZATION = "ORGANIZATION"
    CONCEPT = "CONCEPT"


class RelationshipType(str, Enum):
    RELATES_TO = "RELATES_TO"
    OWNS = "OWNS"
    MEMBER_OF = "MEMBER_OF"
    DEPENDS_ON = "DEPENDS_ON"
    CAUSED = "CAUSED"
    SUPERSEDES = "SUPERSEDES"
    REFERENCES = "REFERENCES"
    PART_OF = "PART_OF"


class OutcomeState(str, Enum):
    ATTEMPTED = "ATTEMPTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"
    CANCELED = "CANCELED"


class EvidenceStrength(str, Enum):
    PRIMARY = "PRIMARY"
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    CONFLICTED = "CONFLICTED"
    UNKNOWN = "UNKNOWN"


class SourceClassification(str, Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    TERTIARY = "TERTIARY"
    UNKNOWN = "UNKNOWN"


class EvolutionProposalStatus(str, Enum):
    PROPOSED = "PROPOSED"
    APPROVED_FOR_TEST = "APPROVED_FOR_TEST"
    CANARY = "CANARY"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    ROLLED_BACK = "ROLLED_BACK"


# ---------------------------------------------------------------------------
# Serialization / validation helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, MappingABC):
        return MappingProxyType({str(k): _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, tuple):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, list):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(v) for v in value)
    if isinstance(value, frozenset):
        return frozenset(_deep_freeze(v) for v in value)
    return value


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, ContractRecord):
        return value.to_dict()
    if is_dataclass(value):
        return {f.name: _to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, MappingABC):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_to_jsonable(v) for v in value]
    return value


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ContractValidationError(f"expected datetime, got {type(value).__name__}")
    _ensure_utc(dt, "datetime")
    return dt.astimezone(timezone.utc)


def _from_jsonable(value: Any, hint: Any) -> Any:
    if hint is Any or hint is object:
        return _deep_freeze(value)
    origin = get_origin(hint)
    args = get_args(hint)
    if origin in (UnionType,):
        origin = None
    if origin is None and args:
        pass
    if origin is None and isinstance(hint, UnionType):
        args = get_args(hint)
    if args and (origin is None or origin is UnionType):
        # Optional / PEP 604 union. Only support one non-None concrete target.
        non_none = [a for a in args if a is not type(None)]
        if value is None and len(non_none) != len(args):
            return None
        if len(non_none) == 1:
            return _from_jsonable(value, non_none[0])
    if isinstance(hint, type) and issubclass(hint, Enum):
        try:
            return hint(value)
        except Exception as exc:
            raise ContractValidationError(f"unknown enum value {value!r} for {hint.__name__}") from exc
    if hint is datetime:
        return _parse_datetime(value)
    if hint in (str, int, float, bool):
        if not isinstance(value, hint):
            raise ContractValidationError(f"expected {hint.__name__}, got {type(value).__name__}")
        return value
    if origin is tuple:
        item_hint = args[0] if args else Any
        if not isinstance(value, (list, tuple)):
            raise ContractValidationError("expected sequence")
        return tuple(_from_jsonable(v, item_hint) for v in value)
    if origin in (dict, Mapping, MappingABC):
        key_hint = args[0] if args else str
        val_hint = args[1] if len(args) > 1 else Any
        if key_hint not in (str, Any):
            raise ContractValidationError("only string mapping keys are supported")
        if not isinstance(value, MappingABC):
            raise ContractValidationError("expected mapping")
        return MappingProxyType({str(k): _from_jsonable(v, val_hint) for k, v in value.items()})
    if isinstance(hint, type) and issubclass(hint, ContractRecord):
        if not isinstance(value, MappingABC):
            raise ContractValidationError(f"expected mapping for {hint.__name__}")
        return hint.from_dict(dict(value))
    return _deep_freeze(value)


def _ensure_id(value: str, field_name: str = "id") -> None:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ContractValidationError(f"invalid {field_name}: {value!r}")


def _ensure_optional_id(value: str | None, field_name: str) -> None:
    if value is not None:
        _ensure_id(value, field_name)


def _ensure_utc(dt: datetime, field_name: str) -> None:
    if not isinstance(dt, datetime) or dt.tzinfo is None or dt.utcoffset() is None:
        raise ContractValidationError(f"{field_name} must be timezone-aware UTC")
    if dt.utcoffset().total_seconds() != 0:
        raise ContractValidationError(f"{field_name} must be canonical UTC")


def _ensure_range(start: datetime | None, end: datetime | None, start_name: str = "start", end_name: str = "end") -> None:
    if start is not None:
        _ensure_utc(start, start_name)
    if end is not None:
        _ensure_utc(end, end_name)
    if start is not None and end is not None and end < start:
        raise ContractValidationError(f"{end_name} must be >= {start_name}")


@dataclass(frozen=True, kw_only=True)
class ContractRecord:
    """Base for executable Phase 3A contracts.

    Unknown fields are rejected by from_dict. Mutable mappings/lists/sets are
    recursively copied and frozen at construction time to prevent caller-owned
    mutable aliases from becoming durable contract state.
    """

    schema_version: str = SCHEMA_VERSION
    supported_schema_versions: ClassVar[tuple[str, ...]] = (SCHEMA_VERSION,)
    serialization_format: ClassVar[str] = SERIALIZATION_FORMAT
    backwards_compatibility_policy: ClassVar[str] = BACKWARDS_COMPATIBILITY_POLICY

    def __post_init__(self) -> None:
        if self.schema_version not in self.supported_schema_versions:
            raise ContractValidationError(f"unsupported schema_version: {self.schema_version}")
        for f in fields(self):
            value = getattr(self, f.name)
            frozen = _deep_freeze(value)
            if frozen is not value:
                object.__setattr__(self, f.name, frozen)
        for f in fields(self):
            name = f.name
            value = getattr(self, name)
            if name == "schema_version":
                continue
            if name.endswith("_id") and isinstance(value, str):
                _ensure_id(value, name)
            if name.endswith("_ids") and isinstance(value, tuple):
                for item in value:
                    _ensure_id(item, name)
            if isinstance(value, datetime):
                _ensure_utc(value, name)
        valid_from = getattr(self, "valid_from", None)
        valid_until = getattr(self, "valid_until", None)
        if isinstance(valid_from, datetime) or isinstance(valid_until, datetime):
            _ensure_range(valid_from, valid_until, "valid_from", "valid_until")

    def to_dict(self) -> dict[str, Any]:
        data = {f.name: _to_jsonable(getattr(self, f.name)) for f in fields(self)}
        data["schema_type"] = self.__class__.__name__
        return data

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    @classmethod
    def from_dict(cls: type[T], data: Mapping[str, Any]) -> T:
        if not isinstance(data, MappingABC):
            raise ContractValidationError("contract input must be a mapping")
        data = dict(data)
        schema_type = data.pop("schema_type", cls.__name__)
        if schema_type != cls.__name__:
            raise ContractValidationError(f"schema_type mismatch: {schema_type!r} != {cls.__name__!r}")
        allowed = {f.name for f in fields(cls)}
        unknown = set(data) - allowed
        if unknown:
            raise ContractValidationError(f"unknown fields for {cls.__name__}: {sorted(unknown)}")
        hints = get_type_hints(cls)
        kwargs: dict[str, Any] = {}
        for f in fields(cls):
            if f.name not in data:
                continue
            kwargs[f.name] = _from_jsonable(data[f.name], hints.get(f.name, Any))
        return cls(**kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_json(cls: type[T], data: str) -> T:
        return cls.from_dict(json.loads(data))


# ---------------------------------------------------------------------------
# Reusable value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TemporalExtent(ContractRecord):
    event_time: datetime | None = None
    event_start: datetime | None = None
    event_end: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    observed_at: datetime | None = None
    ingested_at: datetime | None = None
    updated_at: datetime | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    local_display_time: str = ""
    timezone_name: str = "UTC"
    utc_offset_minutes: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_range(self.event_start, self.event_end, "event_start", "event_end")
        if self.utc_offset_minutes < -14 * 60 or self.utc_offset_minutes > 14 * 60:
            raise ContractValidationError("utc_offset_minutes outside plausible timezone offset")

    def includes(self, moment: datetime) -> bool:
        _ensure_utc(moment, "moment")
        start = self.event_start or self.event_time or self.valid_from
        end = self.event_end or self.event_time or self.valid_until
        if start is not None and moment < start:
            return False
        if end is not None and moment > end:
            return False
        return True

    def is_current_at(self, moment: datetime) -> bool:
        _ensure_utc(moment, "moment")
        if self.valid_from is not None and moment < self.valid_from:
            return False
        if self.valid_until is not None and moment >= self.valid_until:
            return False
        return True


@dataclass(frozen=True)
class Provenance(ContractRecord):
    provenance_id: str
    owner_id: str
    source_type: SourceType
    source_id: str
    created_at: datetime
    observed_at: datetime
    conversation_id: str | None = None
    message_id: str | None = None
    parent_source_ids: tuple[str, ...] = ()
    derived_from_ids: tuple[str, ...] = ()
    confidence: float = 1.0
    verification_state: VerificationState = VerificationState.UNVERIFIED
    derivation_method: DerivationMethod = DerivationMethod.SOURCE
    supersedes: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractValidationError("confidence must be between 0 and 1")
        _ensure_optional_id(self.conversation_id, "conversation_id")
        _ensure_optional_id(self.message_id, "message_id")


# ---------------------------------------------------------------------------
# Identity / session
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class User(ContractRecord):
    user_id: str
    owner_id: str
    created_at: datetime
    display_name: str = ""
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL
    retention_policy_id: str = "default-retention"


@dataclass(frozen=True)
class OwnerIdentity(ContractRecord):
    owner_id: str
    user_id: str
    created_at: datetime
    valid_from: datetime
    valid_until: datetime | None = None
    assurance_policy: str = "owner-auth-required"
    lifecycle_state: LifecycleState = LifecycleState.CURRENT


@dataclass(frozen=True)
class Session(ContractRecord):
    session_id: str
    owner_id: str
    principal_id: str
    device_id: str
    issued_at: datetime
    expires_at: datetime
    security_epoch: int
    created_at: datetime
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_range(self.issued_at, self.expires_at, "issued_at", "expires_at")
        if self.expires_at <= self.issued_at:
            raise ContractValidationError("expires_at must be after issued_at")
        if self.security_epoch < 0:
            raise ContractValidationError("security_epoch must be non-negative")


@dataclass(frozen=True)
class Device(ContractRecord):
    device_id: str
    owner_id: str
    created_at: datetime
    enrolled_at: datetime
    trust_class: str = "D1"
    platform: str = "unknown"
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    privacy_class: PrivacyClass = PrivacyClass.SYSTEM_SECURITY


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Conversation(ContractRecord):
    conversation_id: str
    owner_id: str
    created_at: datetime
    started_at: datetime
    title: str = ""
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL
    retention_policy_id: str = "default-retention"


@dataclass(frozen=True)
class ConversationBranch(ContractRecord):
    branch_id: str
    conversation_id: str
    owner_id: str
    created_at: datetime
    topic: str
    parent_branch_id: str | None = None
    start_sequence: int = 0
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.parent_branch_id, "parent_branch_id")
        if self.start_sequence < 0:
            raise ContractValidationError("start_sequence must be non-negative")


@dataclass(frozen=True)
class Message(ContractRecord):
    message_id: str
    conversation_id: str
    branch_id: str
    owner_id: str
    principal_id: str
    sequence: int
    role: str
    content: str
    created_at: datetime
    observed_at: datetime
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL
    retention_mode: RetentionMode = RetentionMode.DEFAULT_RETAIN
    local_display_time: str = ""
    timezone_name: str = "UTC"
    utc_offset_minutes: int = 0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.sequence < 0:
            raise ContractValidationError("message sequence must be non-negative")
        if not self.role:
            raise ContractValidationError("role is required")
        if self.utc_offset_minutes < -14 * 60 or self.utc_offset_minutes > 14 * 60:
            raise ContractValidationError("utc_offset_minutes outside plausible timezone offset")


@dataclass(frozen=True)
class Response(ContractRecord):
    response_id: str
    conversation_id: str
    branch_id: str
    owner_id: str
    message_id: str
    created_at: datetime
    content: str
    generation_state: GenerationState = GenerationState.GENERATED
    referenced_evidence_ids: tuple[str, ...] = ()
    referenced_memory_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResponseCursor(ContractRecord):
    cursor_id: str
    response_id: str
    conversation_id: str
    branch_id: str
    owner_id: str
    created_at: datetime
    updated_at: datetime
    generation_state: GenerationState
    speech_state: SpeechState
    text_position: int
    semantic_position: str
    last_spoken_boundary: str
    interruption_reason: str = ""
    resume_policy: ResumePolicy = ResumePolicy.RESUME_EXACT
    referenced_evidence_ids: tuple[str, ...] = ()
    referenced_memory_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.text_position < 0:
            raise ContractValidationError("text_position must be non-negative")
        if self.updated_at < self.created_at:
            raise ContractValidationError("updated_at must be >= created_at")

    def interrupt(self, reason: str, at: datetime) -> "ResponseCursor":
        _ensure_utc(at, "at")
        return ResponseCursor(
            cursor_id=self.cursor_id,
            response_id=self.response_id,
            conversation_id=self.conversation_id,
            branch_id=self.branch_id,
            owner_id=self.owner_id,
            created_at=self.created_at,
            updated_at=at,
            generation_state=GenerationState.INTERRUPTED,
            speech_state=SpeechState.INTERRUPTED,
            text_position=self.text_position,
            semantic_position=self.semantic_position,
            last_spoken_boundary=self.last_spoken_boundary,
            interruption_reason=reason,
            resume_policy=self.resume_policy,
            referenced_evidence_ids=self.referenced_evidence_ids,
            referenced_memory_ids=self.referenced_memory_ids,
        )

    def pause(self, reason: str, at: datetime) -> "ResponseCursor":
        _ensure_utc(at, "at")
        return ResponseCursor(
            cursor_id=self.cursor_id,
            response_id=self.response_id,
            conversation_id=self.conversation_id,
            branch_id=self.branch_id,
            owner_id=self.owner_id,
            created_at=self.created_at,
            updated_at=at,
            generation_state=GenerationState.PAUSED,
            speech_state=SpeechState.PAUSED,
            text_position=self.text_position,
            semantic_position=self.semantic_position,
            last_spoken_boundary=self.last_spoken_boundary,
            interruption_reason=reason,
            resume_policy=self.resume_policy,
            referenced_evidence_ids=self.referenced_evidence_ids,
            referenced_memory_ids=self.referenced_memory_ids,
        )

    def resume(self, at: datetime) -> "ResponseCursor":
        _ensure_utc(at, "at")
        if self.speech_state not in (SpeechState.INTERRUPTED, SpeechState.PAUSED, SpeechState.RESUMING):
            raise ContractValidationError("only interrupted/paused cursors can resume")
        return ResponseCursor(
            cursor_id=self.cursor_id,
            response_id=self.response_id,
            conversation_id=self.conversation_id,
            branch_id=self.branch_id,
            owner_id=self.owner_id,
            created_at=self.created_at,
            updated_at=at,
            generation_state=GenerationState.RESUMING,
            speech_state=SpeechState.RESUMING,
            text_position=self.text_position,
            semantic_position=self.semantic_position,
            last_spoken_boundary=self.last_spoken_boundary,
            interruption_reason=self.interruption_reason,
            resume_policy=self.resume_policy,
            referenced_evidence_ids=self.referenced_evidence_ids,
            referenced_memory_ids=self.referenced_memory_ids,
        )

    def cancel(self, reason: str, at: datetime) -> "ResponseCursor":
        _ensure_utc(at, "at")
        return ResponseCursor(
            cursor_id=self.cursor_id,
            response_id=self.response_id,
            conversation_id=self.conversation_id,
            branch_id=self.branch_id,
            owner_id=self.owner_id,
            created_at=self.created_at,
            updated_at=at,
            generation_state=GenerationState.CANCELED,
            speech_state=SpeechState.CANCELED,
            text_position=self.text_position,
            semantic_position=self.semantic_position,
            last_spoken_boundary=self.last_spoken_boundary,
            interruption_reason=reason,
            resume_policy=ResumePolicy.DO_NOT_RESUME,
            referenced_evidence_ids=self.referenced_evidence_ids,
            referenced_memory_ids=self.referenced_memory_ids,
        )

    def complete(self, at: datetime, text_position: int | None = None) -> "ResponseCursor":
        _ensure_utc(at, "at")
        return ResponseCursor(
            cursor_id=self.cursor_id,
            response_id=self.response_id,
            conversation_id=self.conversation_id,
            branch_id=self.branch_id,
            owner_id=self.owner_id,
            created_at=self.created_at,
            updated_at=at,
            generation_state=GenerationState.COMPLETED,
            speech_state=SpeechState.COMPLETED,
            text_position=self.text_position if text_position is None else text_position,
            semantic_position=self.semantic_position,
            last_spoken_boundary=self.last_spoken_boundary,
            interruption_reason=self.interruption_reason,
            resume_policy=self.resume_policy,
            referenced_evidence_ids=self.referenced_evidence_ids,
            referenced_memory_ids=self.referenced_memory_ids,
        )


@dataclass(frozen=True)
class ConversationCheckpoint(ContractRecord):
    checkpoint_id: str
    conversation_id: str
    owner_id: str
    created_at: datetime
    branch_id: str | None = None
    topic: str = ""
    unresolved_questions: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    referenced_memory_ids: tuple[str, ...] = ()
    active_task_id: str | None = None
    response_cursor: ResponseCursor | None = None
    historical_action_state: Mapping[str, Any] = field(default_factory=dict)
    referenced_session_ids: tuple[str, ...] = ()
    referenced_confirmation_ids: tuple[str, ...] = ()
    referenced_grant_ids: tuple[str, ...] = ()
    referenced_lease_ids: tuple[str, ...] = ()
    referenced_security_epoch: int | None = None
    active_session_id: str | None = None
    active_confirmation_id: str | None = None
    active_grant_id: str | None = None
    active_lease_id: str | None = None
    pending_authorization_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.branch_id, "branch_id")
        _ensure_optional_id(self.active_task_id, "active_task_id")
        forbidden = {
            "active_session_id": self.active_session_id,
            "active_confirmation_id": self.active_confirmation_id,
            "active_grant_id": self.active_grant_id,
            "active_lease_id": self.active_lease_id,
            "pending_authorization_id": self.pending_authorization_id,
        }
        present = [name for name, value in forbidden.items() if value is not None]
        if present:
            raise ContractValidationError(
                "ConversationCheckpoint may reference old authority historically, "
                f"but must not restore executable authority: {present}"
            )
        if self.referenced_security_epoch is not None and self.referenced_security_epoch < 0:
            raise ContractValidationError("referenced_security_epoch must be non-negative")


@dataclass(frozen=True)
class InteractionControlCommand(ContractRecord):
    command_id: str
    owner_id: str
    conversation_id: str
    created_at: datetime
    command_type: InteractionCommandType
    target: InteractionTarget
    reason: str = ""
    action_id: str | None = None
    response_id: str | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.action_id, "action_id")
        _ensure_optional_id(self.response_id, "response_id")
        if self.command_type == InteractionCommandType.STOP and self.target == InteractionTarget.UNKNOWN:
            raise ContractValidationError("STOP requires explicit target/context")

    @property
    def is_speech_stop(self) -> bool:
        return self.command_type == InteractionCommandType.STOP and self.target == InteractionTarget.SPEECH

    @property
    def requires_action_plane_cancellation(self) -> bool:
        return self.command_type in (InteractionCommandType.STOP, InteractionCommandType.CANCEL) and self.target == InteractionTarget.ACTION


# ---------------------------------------------------------------------------
# Continuity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemorySource(ContractRecord):
    source_id: str
    owner_id: str
    source_owner_id: str
    source_type: SourceType
    created_at: datetime
    observed_at: datetime
    ingested_at: datetime
    privacy_class: PrivacyClass
    retention_mode: RetentionMode
    retention_policy_id: str
    content_ref: str
    conversation_id: str | None = None
    message_id: str | None = None
    project_id: str | None = None
    checksum: str = ""
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.conversation_id, "conversation_id")
        _ensure_optional_id(self.message_id, "message_id")
        _ensure_optional_id(self.project_id, "project_id")
        if not self.content_ref:
            raise ContractValidationError("content_ref is required")


@dataclass(frozen=True)
class Memory(ContractRecord):
    memory_id: str
    owner_id: str
    source_id: str
    memory_kind: MemoryKind
    created_at: datetime
    observed_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    privacy_class: PrivacyClass
    retention_policy_id: str
    lifecycle_state: LifecycleState
    provenance: Provenance
    content: Mapping[str, Any]
    confidence: float = 1.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.provenance.owner_id != self.owner_id:
            raise ContractValidationError("provenance owner_id must match memory owner_id")
        if self.provenance.source_id != self.source_id:
            raise ContractValidationError("memory must reference its provenance source_id")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractValidationError("confidence must be between 0 and 1")


@dataclass(frozen=True)
class TimelineEvent(ContractRecord):
    event_id: str
    owner_id: str
    event_time: datetime
    created_at: datetime
    title: str
    provenance: Provenance
    source_ids: tuple[str, ...] = ()
    memory_ids: tuple[str, ...] = ()
    conversation_ids: tuple[str, ...] = ()
    action_ids: tuple[str, ...] = ()
    outcome_ids: tuple[str, ...] = ()
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.provenance.owner_id != self.owner_id:
            raise ContractValidationError("provenance owner_id must match timeline owner_id")


@dataclass(frozen=True)
class Entity(ContractRecord):
    entity_id: str
    owner_id: str
    entity_type: EntityType
    name: str
    created_at: datetime
    provenance: Provenance
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL
    attributes: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.name:
            raise ContractValidationError("entity name is required")
        if self.provenance.owner_id != self.owner_id:
            raise ContractValidationError("provenance owner_id must match entity owner_id")


@dataclass(frozen=True)
class Relationship(ContractRecord):
    relationship_id: str
    owner_id: str
    subject_entity_id: str
    object_entity_id: str
    relationship_type: RelationshipType
    created_at: datetime
    provenance: Provenance
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    confidence: float = 1.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.subject_entity_id == self.object_entity_id:
            raise ContractValidationError("relationship endpoints must be distinct")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractValidationError("confidence must be between 0 and 1")
        if self.provenance.owner_id != self.owner_id:
            raise ContractValidationError("provenance owner_id must match relationship owner_id")


# ---------------------------------------------------------------------------
# Intelligence and world model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Goal(ContractRecord):
    goal_id: str
    owner_id: str
    created_at: datetime
    title: str
    description: str
    provenance: Provenance
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL


@dataclass(frozen=True)
class Project(ContractRecord):
    project_id: str
    owner_id: str
    created_at: datetime
    title: str
    provenance: Provenance
    goal_ids: tuple[str, ...] = ()
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL


@dataclass(frozen=True)
class Decision(ContractRecord):
    decision_id: str
    owner_id: str
    project_id: str | None
    created_at: datetime
    decided_at: datetime
    title: str
    rationale: str
    provenance: Provenance
    alternatives: tuple[str, ...] = ()
    supersedes: tuple[str, ...] = ()
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.project_id, "project_id")


@dataclass(frozen=True)
class Plan(ContractRecord):
    plan_id: str
    owner_id: str
    created_at: datetime
    title: str
    provenance: Provenance
    decision_id: str | None = None
    step_ids: tuple[str, ...] = ()
    proposed_action_ids: tuple[str, ...] = ()
    lifecycle_state: LifecycleState = LifecycleState.UNVERIFIED

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.decision_id, "decision_id")


@dataclass(frozen=True)
class Observation(ContractRecord):
    observation_id: str
    owner_id: str
    created_at: datetime
    observed_at: datetime
    source_type: SourceType
    provenance: Provenance
    content: Mapping[str, Any]
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL


@dataclass(frozen=True)
class Evidence(ContractRecord):
    evidence_id: str
    owner_id: str
    source_identifier: str
    source_type: SourceType
    accessed_at: datetime
    provenance: Provenance
    publisher: str = ""
    author: str = ""
    uri: str = ""
    published_at: datetime | None = None
    updated_source_at: datetime | None = None
    jurisdiction_context: str = ""
    source_classification: SourceClassification = SourceClassification.UNKNOWN
    evidence_strength: EvidenceStrength = EvidenceStrength.UNKNOWN
    limitations: tuple[str, ...] = ()
    claim_refs: tuple[str, ...] = ()
    verification_state: VerificationState = VerificationState.UNVERIFIED


@dataclass(frozen=True)
class Recommendation(ContractRecord):
    recommendation_id: str
    owner_id: str
    created_at: datetime
    title: str
    rationale: str
    provenance: Provenance
    evidence_ids: tuple[str, ...] = ()
    required_authorization: str = "none"
    lifecycle_state: LifecycleState = LifecycleState.UNVERIFIED


# ---------------------------------------------------------------------------
# Action / outcome references and governance schemas
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Action(ContractRecord):
    action_id: str
    owner_id: str
    principal_id: str
    created_at: datetime
    capability_id: str
    operation: str
    parameters_ref: str
    proposed_by: str
    lifecycle_state: LifecycleState = LifecycleState.UNVERIFIED
    privacy_class: PrivacyClass = PrivacyClass.PERSONAL


@dataclass(frozen=True)
class ActionSnapshotRef(ContractRecord):
    action_snapshot_ref_id: str
    owner_id: str
    action_id: str
    action_digest: str
    created_at: datetime
    action_kernel_version: str = "0.2.6"

    def __post_init__(self) -> None:
        super().__post_init__()
        if not re.fullmatch(r"[0-9a-f]{64}", self.action_digest):
            raise ContractValidationError("action_digest must be a 64-character lowercase hex digest")


@dataclass(frozen=True)
class Outcome(ContractRecord):
    outcome_id: str
    owner_id: str
    action_id: str
    created_at: datetime
    completed_at: datetime | None
    verified_at: datetime | None
    outcome_state: OutcomeState
    verification_state: VerificationState
    action_snapshot_ref: ActionSnapshotRef
    evidence_ids: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.action_snapshot_ref.owner_id != self.owner_id:
            raise ContractValidationError("snapshot ref owner must match outcome owner")
        if self.action_snapshot_ref.action_id != self.action_id:
            raise ContractValidationError("outcome action_id must match snapshot action_id")


@dataclass(frozen=True)
class Capability(ContractRecord):
    capability_id: str
    owner_id: str
    created_at: datetime
    version: str
    operations: tuple[str, ...]
    risk_class: str
    lifecycle_state: LifecycleState = LifecycleState.CURRENT


@dataclass(frozen=True)
class Grant(ContractRecord):
    grant_id: str
    owner_id: str
    principal_id: str
    capability_id: str
    created_at: datetime
    valid_from: datetime
    valid_until: datetime | None
    permission_scope: Mapping[str, Any]
    risk_ceiling: str
    lifecycle_state: LifecycleState = LifecycleState.CURRENT


@dataclass(frozen=True)
class Confirmation(ContractRecord):
    confirmation_id: str
    owner_id: str
    principal_id: str
    session_id: str
    action_id: str
    action_digest: str
    issued_at: datetime
    expires_at: datetime
    assurance_level: str
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_range(self.issued_at, self.expires_at, "issued_at", "expires_at")
        if self.expires_at <= self.issued_at:
            raise ContractValidationError("confirmation expires_at must be after issued_at")
        if not re.fullmatch(r"[0-9a-f]{64}", self.action_digest):
            raise ContractValidationError("action_digest must be a 64-character lowercase hex digest")


@dataclass(frozen=True)
class Lease(ContractRecord):
    lease_id: str
    owner_id: str
    principal_id: str
    device_id: str
    action_id: str
    action_digest: str
    issued_at: datetime
    expires_at: datetime
    security_epoch: int
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_range(self.issued_at, self.expires_at, "issued_at", "expires_at")
        if self.expires_at <= self.issued_at:
            raise ContractValidationError("lease expires_at must be after issued_at")
        if self.security_epoch < 0:
            raise ContractValidationError("security_epoch must be non-negative")
        if not re.fullmatch(r"[0-9a-f]{64}", self.action_digest):
            raise ContractValidationError("action_digest must be a 64-character lowercase hex digest")


# ---------------------------------------------------------------------------
# Memory policy, retrieval, deletion, MEMORY//OS boundary, firewall
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MemoryCapturePolicy(ContractRecord):
    policy_id: str
    owner_id: str
    created_at: datetime
    default_conversation_retention: RetentionMode
    explicit_remember: RetentionOverride
    explicit_do_not_remember: RetentionOverride
    temporary_conversation: RetentionOverride
    sensitive_memory: RetentionOverride
    project_scoped_memory: RetentionOverride
    deletion_behavior: DeletionBehavior
    retention_period_days: int | None = None
    per_conversation_overrides: Mapping[str, RetentionOverride] = field(default_factory=dict)
    per_message_overrides: Mapping[str, RetentionOverride] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.default_conversation_retention not in (RetentionMode.DEFAULT_RETAIN, RetentionMode.DEFAULT_DO_NOT_RETAIN):
            raise ContractValidationError("default_conversation_retention must be DEFAULT_RETAIN or DEFAULT_DO_NOT_RETAIN")
        if self.retention_period_days is not None and self.retention_period_days <= 0:
            raise ContractValidationError("retention_period_days must be positive when set")


@dataclass(frozen=True)
class MemoryRetrievalRequest(ContractRecord):
    retrieval_id: str
    owner_id: str
    principal_id: str
    created_at: datetime
    modes: tuple[RetrievalMode, ...]
    query_text: str
    scope: Mapping[str, Any] = field(default_factory=dict)
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    entity_ids: tuple[str, ...] = ()
    project_id: str | None = None
    purpose: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.modes:
            raise ContractValidationError("at least one retrieval mode is required")
        _ensure_range(self.temporal_start, self.temporal_end, "temporal_start", "temporal_end")
        _ensure_optional_id(self.project_id, "project_id")


@dataclass(frozen=True)
class MemoryRetrievalHit(ContractRecord):
    hit_id: str
    owner_id: str
    record_id: str
    source_id: str
    created_at: datetime
    relevance: float
    temporal_metadata: TemporalExtent
    governance_status: GovernanceStatus
    provenance: Provenance
    conflict_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (0.0 <= self.relevance <= 1.0):
            raise ContractValidationError("relevance must be between 0 and 1")


@dataclass(frozen=True)
class MemoryRetrievalResult(ContractRecord):
    result_id: str
    retrieval_id: str
    owner_id: str
    created_at: datetime
    governance_status: GovernanceStatus
    hits: tuple[MemoryRetrievalHit, ...]
    message: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        for hit in self.hits:
            if hit.owner_id != self.owner_id:
                raise ContractValidationError("retrieval hit owner_id mismatch")


@dataclass(frozen=True)
class DeletionRequest(ContractRecord):
    deletion_id: str
    owner_id: str
    principal_id: str
    created_at: datetime
    scope_type: str
    authentication_context: str
    item_ids: tuple[str, ...] = ()
    conversation_id: str | None = None
    project_id: str | None = None
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    requested_propagation: tuple[DeletionPropagationTarget, ...] = (
        DeletionPropagationTarget.SOURCE,
        DeletionPropagationTarget.DERIVED_MEMORY,
        DeletionPropagationTarget.EMBEDDINGS,
        DeletionPropagationTarget.INDEXES,
        DeletionPropagationTarget.GRAPHS,
        DeletionPropagationTarget.CACHES,
        DeletionPropagationTarget.BACKUPS,
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.conversation_id, "conversation_id")
        _ensure_optional_id(self.project_id, "project_id")
        _ensure_range(self.temporal_start, self.temporal_end, "temporal_start", "temporal_end")
        if not self.authentication_context:
            raise ContractValidationError("authentication_context is required")
        if not self.requested_propagation:
            raise ContractValidationError("requested_propagation is required")


@dataclass(frozen=True)
class DeletionResult(ContractRecord):
    result_id: str
    deletion_id: str
    owner_id: str
    created_at: datetime
    status: DeletionStatus
    propagated: Mapping[str, DeletionStatus]
    retained_by_policy: tuple[str, ...] = ()
    unknown_targets: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.status == DeletionStatus.COMPLETED:
            incomplete = [k for k, v in self.propagated.items() if v != DeletionStatus.COMPLETED]
            if incomplete or self.retained_by_policy or self.unknown_targets:
                raise ContractValidationError("COMPLETED deletion cannot have incomplete/retained/unknown propagation")


@dataclass(frozen=True)
class MemoryGovernanceRequest(ContractRecord):
    request_id: str
    owner_id: str
    principal_id: str
    created_at: datetime
    operation: str
    purpose: str
    privacy_class: PrivacyClass
    retention_policy_id: str
    source_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryGovernanceDecision(ContractRecord):
    decision_id: str
    request_id: str
    owner_id: str
    created_at: datetime
    status: GovernanceStatus
    reason: str
    policy_version: str


@dataclass(frozen=True)
class MemoryStorageRequest(ContractRecord):
    storage_request_id: str
    owner_id: str
    created_at: datetime
    source: MemorySource
    derived_memory: Memory | None
    governance_decision_id: str

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.source.owner_id != self.owner_id:
            raise ContractValidationError("source owner_id mismatch")
        if self.derived_memory is not None and self.derived_memory.owner_id != self.owner_id:
            raise ContractValidationError("derived memory owner_id mismatch")


@dataclass(frozen=True)
class DerivedViewReference(ContractRecord):
    view_id: str
    owner_id: str
    created_at: datetime
    source_ids: tuple[str, ...]
    view_type: str
    governance_inherited: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.source_ids:
            raise ContractValidationError("derived view must reference at least one source")
        if not self.governance_inherited:
            raise ContractValidationError("derived views must inherit source governance")


class MemoryOSAdapterContract(Protocol):
    """Versioned MEMORY//OS adapter boundary for Phase 3B+ implementations."""

    contract_version: str

    def govern(self, request: MemoryGovernanceRequest) -> MemoryGovernanceDecision: ...

    def retrieve(self, request: MemoryRetrievalRequest) -> MemoryRetrievalResult: ...

    def store(self, request: MemoryStorageRequest) -> MemoryGovernanceDecision: ...

    def delete(self, request: DeletionRequest) -> DeletionResult: ...


@dataclass(frozen=True)
class MemoryFirewallRequest(ContractRecord):
    firewall_request_id: str
    owner_id: str
    principal_id: str
    created_at: datetime
    task_purpose: str
    requested_memory_scope: Mapping[str, Any]
    provider_trust_class: ProviderTrustClass
    privacy_class: PrivacyClass
    egress_policy: EgressPolicy
    requested_memory_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryFirewallResult(ContractRecord):
    firewall_result_id: str
    firewall_request_id: str
    owner_id: str
    created_at: datetime
    allowed_memory_ids: tuple[str, ...]
    denied_fields: tuple[str, ...]
    redacted_fields: tuple[str, ...]
    minimization_summary: str
    provider_context: Mapping[str, Any]
    provenance: Provenance
    governance_status: GovernanceStatus

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.provider_context and self.governance_status != GovernanceStatus.ALLOW:
            raise ContractValidationError("provider_context must be empty unless governance_status is ALLOW")




# ---------------------------------------------------------------------------
# Contextual memory activation contracts (Phase 3A.1)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CurrentContextFrame(ContractRecord):
    """Structured current conversation context that may trigger memory activation.

    This is a contract only. Phase 3A.1 does not implement NLP extraction or
    background memory search.
    """

    context_id: str
    owner_id: str
    conversation_id: str
    branch_id: str | None
    created_at: datetime
    current_topic: str = ""
    active_question: str = ""
    entity_ids: tuple[str, ...] = ()
    project_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    current_assumptions: tuple[str, ...] = ()
    current_user_state_refs: tuple[str, ...] = ()
    referenced_document_ids: tuple[str, ...] = ()
    active_conversation_branch: str = ""
    temporal_context: TemporalExtent | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.branch_id, "branch_id")
        if not (self.current_topic or self.active_question or self.entity_ids or self.project_ids or self.goal_ids or self.decision_ids):
            raise ContractValidationError("current context must contain at least one activation cue")


@dataclass(frozen=True)
class MemoryRelevanceSignal(ContractRecord):
    signal_id: str
    owner_id: str
    created_at: datetime
    signal_type: MemoryRelevanceSignalType
    relevance: MemoryRelevanceLevel
    explanation: str
    evidence_refs: tuple[str, ...] = ()
    confidence: float = 0.0

    def __post_init__(self) -> None:
        super().__post_init__()
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractValidationError("confidence must be between 0 and 1")
        if self.relevance != MemoryRelevanceLevel.NONE and not self.explanation:
            raise ContractValidationError("relevant signals require an explanation")


@dataclass(frozen=True)
class MemoryActivationThresholdPolicy(ContractRecord):
    """Future configurable activation policy; no scoring engine is implemented."""

    activation_policy_id: str
    owner_id: str
    created_at: datetime
    high_relevance_policy: MemoryActivationPolicyDecision = MemoryActivationPolicyDecision.USE_INTERNAL_ONLY
    medium_relevance_policy: MemoryActivationPolicyDecision = MemoryActivationPolicyDecision.USE_INTERNAL_ONLY
    low_relevance_policy: MemoryActivationPolicyDecision = MemoryActivationPolicyDecision.BLOCK
    sensitive_requires_governance: bool = True
    user_visible_requires_show_policy: bool = True
    notes: str = ""


@dataclass(frozen=True)
class MemoryActivationRequest(ContractRecord):
    activation_id: str
    owner_id: str
    principal_id: str
    created_at: datetime
    conversation_id: str
    current_context: CurrentContextFrame
    purpose: str
    task_scope: Mapping[str, Any]
    requested_memory_scope: Mapping[str, Any]
    privacy_class: PrivacyClass
    provider_trust_class: ProviderTrustClass
    trigger: MemoryActivationTrigger
    temporal_context: TemporalExtent | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.current_context.owner_id != self.owner_id:
            raise ContractValidationError("current_context owner_id must match activation owner_id")
        if self.current_context.conversation_id != self.conversation_id:
            raise ContractValidationError("current_context conversation_id must match activation conversation_id")
        if not self.purpose:
            raise ContractValidationError("activation purpose is required")


@dataclass(frozen=True)
class MemoryActivationCandidate(ContractRecord):
    candidate_id: str
    activation_id: str
    owner_id: str
    source_id: str
    created_at: datetime
    relevance: MemoryRelevanceLevel
    relevance_reasons: tuple[str, ...]
    semantic_relation: str
    temporal_relation: str
    provenance: Provenance
    entity_relation_ids: tuple[str, ...] = ()
    project_relation_ids: tuple[str, ...] = ()
    goal_relation_ids: tuple[str, ...] = ()
    decision_relation_ids: tuple[str, ...] = ()
    historical_current_validity: MemoryValidity = MemoryValidity.UNKNOWN
    historical_current_state: LifecycleState = LifecycleState.UNVERIFIED
    conflict_state: LifecycleState = LifecycleState.UNVERIFIED
    sensitivity: PrivacyClass = PrivacyClass.PERSONAL
    relevance_signals: tuple[MemoryRelevanceSignal, ...] = ()
    memory_id: str | None = None
    temporal_extent: TemporalExtent | None = None
    confidence: float = 0.0
    governance_status: GovernanceStatus = GovernanceStatus.NOT_APPLICABLE

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_optional_id(self.memory_id, "memory_id")
        if self.provenance.owner_id != self.owner_id:
            raise ContractValidationError("candidate provenance owner_id must match candidate owner_id")
        if self.provenance.source_id != self.source_id:
            raise ContractValidationError("candidate provenance source_id must match candidate source_id")
        if not (0.0 <= self.confidence <= 1.0):
            raise ContractValidationError("confidence must be between 0 and 1")
        for signal in self.relevance_signals:
            if signal.owner_id != self.owner_id:
                raise ContractValidationError("relevance signal owner_id mismatch")
        if self.relevance != MemoryRelevanceLevel.NONE and not self.relevance_reasons:
            raise ContractValidationError("relevant candidates require relevance_reasons")


@dataclass(frozen=True)
class MemoryActivationDecision(ContractRecord):
    decision_id: str
    activation_id: str
    owner_id: str
    candidate: MemoryActivationCandidate
    created_at: datetime
    decision_state: MemoryActivationDecisionState
    policy_decision: MemoryActivationPolicyDecision
    governance_status: GovernanceStatus
    reason: str
    activated_for_internal_reasoning: bool = False
    user_visible_mention_allowed: bool = False
    explanation: str = ""

    def __post_init__(self) -> None:
        super().__post_init__()
        if self.candidate.owner_id != self.owner_id:
            raise ContractValidationError("candidate owner_id must match decision owner_id")
        if self.candidate.activation_id != self.activation_id:
            raise ContractValidationError("candidate activation_id must match decision activation_id")
        if not self.reason:
            raise ContractValidationError("activation decision reason is required")
        if self.decision_state == MemoryActivationDecisionState.ACTIVATED:
            if self.governance_status != GovernanceStatus.ALLOW:
                raise ContractValidationError("activated memory requires ALLOW governance")
            if self.policy_decision == MemoryActivationPolicyDecision.BLOCK:
                raise ContractValidationError("blocked policy cannot be activated")
            if self.candidate.relevance in (MemoryRelevanceLevel.NONE, MemoryRelevanceLevel.LOW):
                raise ContractValidationError("low/none relevance cannot be automatically activated")
            if not self.activated_for_internal_reasoning and not self.user_visible_mention_allowed:
                raise ContractValidationError("activated memory must be usable internally or visibly")
        if self.decision_state in (MemoryActivationDecisionState.BLOCKED_BY_POLICY, MemoryActivationDecisionState.BLOCKED_BY_PRIVACY):
            if self.activated_for_internal_reasoning or self.user_visible_mention_allowed:
                raise ContractValidationError("blocked memory cannot be activated or mentioned")
        if self.user_visible_mention_allowed and self.policy_decision != MemoryActivationPolicyDecision.SHOW:
            raise ContractValidationError("user-visible mention requires SHOW policy")
        if self.policy_decision == MemoryActivationPolicyDecision.SHOW and not self.candidate.source_id:
            raise ContractValidationError("SHOW policy requires source-backed candidate")


@dataclass(frozen=True)
class MemoryContextSelection(ContractRecord):
    selection_id: str
    activation_id: str
    owner_id: str
    created_at: datetime
    decisions: tuple[MemoryActivationDecision, ...]
    selected_candidate_ids: tuple[str, ...] = ()
    internal_context_candidate_ids: tuple[str, ...] = ()
    user_visible_candidate_ids: tuple[str, ...] = ()
    redacted_candidate_ids: tuple[str, ...] = ()
    blocked_candidate_ids: tuple[str, ...] = ()
    reasoning_context: Mapping[str, Any] = field(default_factory=dict)
    explanation: str = ""
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        decisions_by_candidate = {decision.candidate.candidate_id: decision for decision in self.decisions}
        for decision in self.decisions:
            if decision.owner_id != self.owner_id:
                raise ContractValidationError("decision owner_id mismatch in memory context selection")
            if decision.activation_id != self.activation_id:
                raise ContractValidationError("decision activation_id mismatch in memory context selection")
        unknown_refs = (set(self.selected_candidate_ids) | set(self.internal_context_candidate_ids) | set(self.user_visible_candidate_ids) | set(self.redacted_candidate_ids) | set(self.blocked_candidate_ids)) - set(decisions_by_candidate)
        if unknown_refs:
            raise ContractValidationError(f"selection references unknown candidates: {sorted(unknown_refs)}")
        for cid in self.selected_candidate_ids:
            if decisions_by_candidate[cid].decision_state != MemoryActivationDecisionState.ACTIVATED:
                raise ContractValidationError("selected candidates must have ACTIVATED decisions")
        for cid in self.internal_context_candidate_ids:
            decision = decisions_by_candidate[cid]
            if decision.decision_state != MemoryActivationDecisionState.ACTIVATED or not decision.activated_for_internal_reasoning:
                raise ContractValidationError("internal context candidates must be activated for internal reasoning")
        for cid in self.user_visible_candidate_ids:
            decision = decisions_by_candidate[cid]
            if decision.decision_state != MemoryActivationDecisionState.ACTIVATED or not decision.user_visible_mention_allowed:
                raise ContractValidationError("user-visible candidates must be activated and mention-allowed")
        for cid in self.redacted_candidate_ids:
            decision = decisions_by_candidate[cid]
            if decision.decision_state != MemoryActivationDecisionState.ACTIVATED:
                raise ContractValidationError("redacted candidates must have ACTIVATED decisions")
            if decision.policy_decision != MemoryActivationPolicyDecision.REDACT:
                raise ContractValidationError("redacted candidates require REDACT policy decision")
            if decision.governance_status != GovernanceStatus.ALLOW:
                raise ContractValidationError("redacted candidates require ALLOW governance")
        for cid in self.blocked_candidate_ids:
            if decisions_by_candidate[cid].decision_state not in (MemoryActivationDecisionState.BLOCKED_BY_POLICY, MemoryActivationDecisionState.BLOCKED_BY_PRIVACY):
                raise ContractValidationError("blocked_candidate_ids must refer to blocked decisions")
        overlap = set(self.redacted_candidate_ids) & set(self.blocked_candidate_ids)
        if overlap:
            raise ContractValidationError(f"blocked candidates cannot be treated as redacted context: {sorted(overlap)}")
        blocked_markers: set[str] = set()
        for cid in self.blocked_candidate_ids:
            blocked_decision = decisions_by_candidate[cid]
            blocked_markers.add(cid)
            blocked_markers.add(blocked_decision.candidate.source_id)
            if blocked_decision.candidate.memory_id:
                blocked_markers.add(blocked_decision.candidate.memory_id)
        if blocked_markers:
            reasoning_text = json.dumps(_to_jsonable(self.reasoning_context), sort_keys=True, ensure_ascii=False)
            leaked = sorted(marker for marker in blocked_markers if marker and marker in reasoning_text)
            if leaked:
                raise ContractValidationError(f"reasoning_context references blocked memory markers: {leaked}")
        if self.provenance is not None and self.provenance.owner_id != self.owner_id:
            raise ContractValidationError("selection provenance owner_id mismatch")


# ---------------------------------------------------------------------------
# Evolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Experiment(ContractRecord):
    experiment_id: str
    owner_id: str
    created_at: datetime
    title: str
    metric: str
    start_at: datetime | None = None
    end_at: datetime | None = None
    lifecycle_state: LifecycleState = LifecycleState.UNVERIFIED
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        super().__post_init__()
        _ensure_range(self.start_at, self.end_at, "start_at", "end_at")


@dataclass(frozen=True)
class EvolutionProposal(ContractRecord):
    evolution_proposal_id: str
    owner_id: str
    created_at: datetime
    target_component: str
    rationale: str
    status: EvolutionProposalStatus
    evidence_ids: tuple[str, ...] = ()
    test_plan: str = ""
    rollback_plan: str = ""
    approval_required: bool = True
    forbidden_security_mutation: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.approval_required:
            raise ContractValidationError("behavioral evolution requires approval")
        if self.forbidden_security_mutation:
            raise ContractValidationError("evolution proposal attempts forbidden security mutation")


# ---------------------------------------------------------------------------
# Cross-entity consistency helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainIndex(ContractRecord):
    """A small validation helper for Phase 3A tests and future stores.

    This is not a storage engine. It verifies cross-entity references supplied by
    a caller and fails closed on inconsistent owner/reference state.
    """

    index_id: str
    owner_id: str
    created_at: datetime
    memory_sources: Mapping[str, MemorySource] = field(default_factory=dict)
    memories: Mapping[str, Memory] = field(default_factory=dict)
    entities: Mapping[str, Entity] = field(default_factory=dict)
    relationships: Mapping[str, Relationship] = field(default_factory=dict)
    conversations: Mapping[str, Conversation] = field(default_factory=dict)
    responses: Mapping[str, Response] = field(default_factory=dict)
    response_cursors: Mapping[str, ResponseCursor] = field(default_factory=dict)
    projects: Mapping[str, Project] = field(default_factory=dict)
    decisions: Mapping[str, Decision] = field(default_factory=dict)
    actions: Mapping[str, Action] = field(default_factory=dict)
    outcomes: Mapping[str, Outcome] = field(default_factory=dict)
    timeline_events: Mapping[str, TimelineEvent] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__post_init__()
        collections = [
            self.memory_sources,
            self.memories,
            self.entities,
            self.relationships,
            self.conversations,
            self.responses,
            self.response_cursors,
            self.projects,
            self.decisions,
            self.actions,
            self.outcomes,
            self.timeline_events,
        ]
        for collection in collections:
            for key, item in collection.items():
                if getattr(item, "owner_id", None) != self.owner_id:
                    raise ContractValidationError(f"owner isolation violation for {key}")
        for memory in self.memories.values():
            if memory.source_id not in self.memory_sources:
                raise ContractValidationError(f"memory {memory.memory_id} references missing source {memory.source_id}")
        for rel in self.relationships.values():
            if rel.subject_entity_id not in self.entities or rel.object_entity_id not in self.entities:
                raise ContractValidationError(f"relationship {rel.relationship_id} references missing entity")
        for decision in self.decisions.values():
            if decision.project_id is not None and decision.project_id not in self.projects:
                raise ContractValidationError(f"decision {decision.decision_id} references missing project")
        for outcome in self.outcomes.values():
            if outcome.action_id not in self.actions:
                raise ContractValidationError(f"outcome {outcome.outcome_id} references missing action")
        for cursor in self.response_cursors.values():
            if cursor.response_id not in self.responses:
                raise ContractValidationError(f"cursor {cursor.cursor_id} references missing response")
            response = self.responses[cursor.response_id]
            if response.conversation_id not in self.conversations:
                raise ContractValidationError(f"response {response.response_id} references missing conversation")
            if response.conversation_id != cursor.conversation_id:
                raise ContractValidationError(f"cursor {cursor.cursor_id} conversation mismatch")
        for event in self.timeline_events.values():
            for sid in event.source_ids:
                if sid not in self.memory_sources:
                    raise ContractValidationError(f"timeline event {event.event_id} references missing source")
            for mid in event.memory_ids:
                if mid not in self.memories:
                    raise ContractValidationError(f"timeline event {event.event_id} references missing memory")


CONTRACT_TYPES: tuple[type[ContractRecord], ...] = (
    TemporalExtent,
    Provenance,
    User,
    OwnerIdentity,
    Session,
    Device,
    Conversation,
    ConversationBranch,
    Message,
    Response,
    ResponseCursor,
    ConversationCheckpoint,
    InteractionControlCommand,
    MemorySource,
    Memory,
    TimelineEvent,
    Entity,
    Relationship,
    Goal,
    Project,
    Decision,
    Plan,
    Observation,
    Evidence,
    Recommendation,
    Action,
    ActionSnapshotRef,
    Outcome,
    Capability,
    Grant,
    Confirmation,
    Lease,
    MemoryCapturePolicy,
    MemoryRetrievalRequest,
    MemoryRetrievalHit,
    MemoryRetrievalResult,
    DeletionRequest,
    DeletionResult,
    MemoryGovernanceRequest,
    MemoryGovernanceDecision,
    MemoryStorageRequest,
    DerivedViewReference,
    MemoryFirewallRequest,
    MemoryFirewallResult,
    CurrentContextFrame,
    MemoryRelevanceSignal,
    MemoryActivationThresholdPolicy,
    MemoryActivationRequest,
    MemoryActivationCandidate,
    MemoryActivationDecision,
    MemoryContextSelection,
    Experiment,
    EvolutionProposal,
    DomainIndex,
)
