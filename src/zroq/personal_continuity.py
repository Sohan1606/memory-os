"""ZORQ Phase 3B local personal-continuity runtime.

This module implements the first durable, governed personal continuity slice for
ZORQ.  It is intentionally offline-first and standard-library only.  It does
not modify or embed MEMORY//OS; when a real MEMORY//OS implementation is not
available, the :class:`DocumentedMemoryOSAdapter` is a versioned documented
contract harness that gates all local persistence/retrieval/deletion decisions
and reports that production MEMORY//OS verification is blocked by environment.

Memory records are data for reasoning. They never authorize actions, grants,
confirmations, Device Agent calls, or Action Kernel dispatch.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from enum import Enum
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Sequence

from .domain_contracts import (
    ContractValidationError,
    CurrentContextFrame,
    EgressPolicy,
    GovernanceStatus,
    LifecycleState,
    MemoryActivationCandidate,
    MemoryActivationDecision,
    MemoryActivationDecisionState,
    MemoryActivationPolicyDecision,
    MemoryActivationRequest,
    MemoryActivationTrigger,
    MemoryCapturePolicy,
    MemoryContextSelection,
    DeletionBehavior,
    MemoryFirewallRequest,
    MemoryFirewallResult,
    MemoryRelevanceLevel,
    MemoryRelevanceSignal,
    MemoryRelevanceSignalType,
    MemoryRetrievalRequest,
    MemoryRetrievalResult,
    MemoryRetrievalHit,
    MemoryValidity,
    PrivacyClass,
    Provenance,
    ProviderTrustClass,
    RetentionMode,
    RetentionOverride,
    SourceType,
    TemporalExtent,
    VerificationState,
)

PHASE3B_SCHEMA_VERSION = "zorq.phase3b.personal-continuity.v1"
MEMORYOS_ADAPTER_VERSION = "MEMORY//OS-v10.2.0-adapter-v1"
DOCUMENTED_CONTRACT_HARNESS_VERSION = "DOCUMENTED-CONTRACT-HARNESS-not-production-MEMORYOS-v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_utc(dt: datetime | None, field_name: str) -> None:
    if dt is not None and dt.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if dt is not None and dt.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be normalized to UTC")


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    _ensure_utc(dt, "datetime")
    return dt.isoformat().replace("+00:00", "Z")


def _from_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _plain_jsonable(data: Any) -> Any:
    if isinstance(data, Mapping):
        return {str(k): _plain_jsonable(v) for k, v in data.items()}
    if isinstance(data, (tuple, list)):
        return [_plain_jsonable(v) for v in data]
    if isinstance(data, set):
        return sorted(_plain_jsonable(v) for v in data)
    if isinstance(data, Enum):
        return data.value
    if isinstance(data, datetime):
        return _iso(data)
    return data


def _json(data: Any) -> str:
    return json.dumps(_plain_jsonable(data), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def _stable_id(*parts: Any, prefix: str = "id") -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\x00")
    return f"{prefix}-{h.hexdigest()[:24]}"


def _safe_id(value: str, prefix: str = "id") -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip())[:80].strip("-._:")
    if not text or not re.match(r"^[A-Za-z0-9]", text):
        return _stable_id(value, prefix=prefix)
    return text[:127]


def _redact_content(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_+-]*")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "for", "from", "has", "have",
    "how", "i", "in", "is", "it", "my", "of", "on", "or", "our", "should", "so", "that", "the", "this",
    "to", "was", "we", "what", "when", "with", "you", "your", "later", "now", "then", "about", "again",
}


def _tokens(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def _token_set(text: str) -> set[str]:
    return set(_tokens(text))


def _contains_any(text: str, words: Iterable[str]) -> bool:
    low = text.lower()
    return any(word.lower() in low for word in words)


def _resolve_calendar_timezone(calendar_timezone: str | None) -> tuple[str, ZoneInfo]:
    """Resolve an owner/query calendar timezone.

    Canonical storage remains UTC. Calendar-date interpretation uses the
    explicit query timezone when supplied, otherwise the owner context timezone,
    otherwise the documented UTC fallback.
    """
    name = calendar_timezone or "UTC"
    try:
        return name, ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown calendar timezone: {name}") from exc


def _date_range_for_day(day: date, calendar_timezone: str | None = "UTC") -> tuple[datetime, datetime]:
    _, tz = _resolve_calendar_timezone(calendar_timezone)
    local_start = datetime.combine(day, time.min, tzinfo=tz)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _year_range_for_calendar(year: int, calendar_timezone: str | None = "UTC") -> tuple[datetime, datetime]:
    _, tz = _resolve_calendar_timezone(calendar_timezone)
    local_start = datetime(year, 1, 1, tzinfo=tz)
    local_end = datetime(year + 1, 1, 1, tzinfo=tz)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _exact_timestamp_range(text: str, calendar_timezone: str | None = "UTC") -> tuple[datetime, datetime] | None:
    m = re.search(
        r"\b(\d{4})-(\d{1,2})-(\d{1,2})[T ](\d{1,2}):(\d{2})(?::(\d{2}))?(?:\s*(Z|[+-]\d{2}:?\d{2}|[A-Za-z_]+/[A-Za-z_]+))?\b",
        text,
    )
    if not m:
        return None
    year, month, day, hour, minute = (int(m.group(i)) for i in range(1, 6))
    second_text = m.group(6)
    second = int(second_text or 0)
    zone_text = m.group(7)
    precision = timedelta(seconds=1 if second_text is not None else 60)
    if zone_text == "Z":
        moment = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    elif zone_text and re.fullmatch(r"[+-]\d{2}:?\d{2}", zone_text):
        offset = zone_text if ":" in zone_text else zone_text[:3] + ":" + zone_text[3:]
        moment = datetime.fromisoformat(f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}{offset}")
    else:
        _, tz = _resolve_calendar_timezone(zone_text or calendar_timezone)
        moment = datetime(year, month, day, hour, minute, second, tzinfo=tz)
    start = moment.astimezone(timezone.utc)
    return start, start + precision


def _date_mentions(text: str) -> list[date]:
    mentions: list[tuple[int, date]] = []
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text):
        mentions.append((m.start(), date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    for m in re.finditer(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b", text):
        mentions.append((m.start(), date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
    for m in re.finditer(r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b", text):
        month = _MONTHS.get(m.group(2).lower())
        if month:
            mentions.append((m.start(), date(int(m.group(3)), month, int(m.group(1)))))
    return [d for _, d in sorted(mentions, key=lambda item: item[0])]


_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}


def parse_date_expression(text: str, calendar_timezone: str | None = "UTC") -> tuple[datetime, datetime] | None:
    """Parse common temporal expressions used by historical recall.

    Canonical stored timestamps remain UTC. Date-only expressions are calendar
    concepts: they are interpreted in the supplied owner/query timezone and then
    converted to UTC [start, end) bounds for storage queries. If no timezone is
    known, the documented fallback is UTC.

    Supports exact ISO timestamps, YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY,
    "27 September 2026", simple date ranges, and "during/in YEAR".
    """
    value = text.strip()
    timestamp_range = _exact_timestamp_range(value, calendar_timezone)
    if timestamp_range:
        return timestamp_range

    dates = _date_mentions(value)
    range_marked = bool(re.search(r"\b(from|between|through|until|date range)\b", value.lower())) or " to " in value.lower()
    if len(dates) >= 2 and range_marked:
        start, _ = _date_range_for_day(dates[0], calendar_timezone)
        _, end = _date_range_for_day(dates[1], calendar_timezone)
        return start, end
    if dates:
        return _date_range_for_day(dates[0], calendar_timezone)

    m = re.search(r"\b(?:during|in)\s+(\d{4})\b", value.lower())
    if m:
        return _year_range_for_calendar(int(m.group(1)), calendar_timezone)
    return None


# ---------------------------------------------------------------------------
# Runtime status and access context
# ---------------------------------------------------------------------------


class AdapterOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    HOLD = "HOLD"
    UNAVAILABLE = "UNAVAILABLE"
    CONTRADICTORY = "CONTRADICTORY"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class DeletionRuntimeStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    RETAINED_BY_POLICY = "RETAINED_BY_POLICY"


class RetrievalModeName(str, Enum):
    EXACT = "EXACT"
    LEXICAL = "LEXICAL"
    SEMANTIC = "SEMANTIC"
    TEMPORAL = "TEMPORAL"
    RELATIONAL = "RELATIONAL"
    CAUSAL = "CAUSAL"


class DerivedMemoryKind(str, Enum):
    PERSONAL_FACT = "PERSONAL_FACT"
    PREFERENCE = "PREFERENCE"
    GOAL = "GOAL"
    PROJECT_FACT = "PROJECT_FACT"
    DECISION = "DECISION"
    EVENT = "EVENT"
    RELATIONSHIP = "RELATIONSHIP"
    LESSON = "LESSON"
    OUTCOME = "OUTCOME"


@dataclass(frozen=True)
class MemoryAccessContext:
    owner_id: str
    principal_id: str
    authenticated_owner_id: str
    purpose: str
    provider_trust_class: ProviderTrustClass = ProviderTrustClass.LOCAL_ONLY
    egress_policy: EgressPolicy = EgressPolicy.NO_EGRESS
    allowed_owner_ids: tuple[str, ...] = ()
    owner_calendar_timezone: str | None = "UTC"
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _ensure_utc(self.created_at, "created_at")
        if self.owner_calendar_timezone is not None:
            _resolve_calendar_timezone(self.owner_calendar_timezone)
        allowed = self.allowed_owner_ids or (self.authenticated_owner_id,)
        object.__setattr__(self, "allowed_owner_ids", tuple(allowed))
        if self.owner_id != self.authenticated_owner_id:
            raise PermissionError("access context owner does not match authenticated owner")
        if self.owner_id not in self.allowed_owner_ids:
            raise PermissionError("access context owner is not in allowed owner set")


@dataclass(frozen=True)
class AdapterDecision:
    outcome: AdapterOutcome
    decision_id: str
    owner_id: str
    operation: str
    purpose: str
    reason: str
    policy_version: str = MEMORYOS_ADAPTER_VERSION
    constraints: Mapping[str, Any] = field(default_factory=dict)
    production_memoryos_verified: bool = False
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        _ensure_utc(self.created_at, "created_at")
        object.__setattr__(self, "constraints", MappingProxyType(dict(self.constraints)))

    @property
    def governance_status(self) -> GovernanceStatus:
        return {
            AdapterOutcome.ALLOW: GovernanceStatus.ALLOW,
            AdapterOutcome.DENY: GovernanceStatus.DENY,
            AdapterOutcome.HOLD: GovernanceStatus.HOLD,
            AdapterOutcome.UNAVAILABLE: GovernanceStatus.UNAVAILABLE,
            AdapterOutcome.CONTRADICTORY: GovernanceStatus.CONTRADICTORY,
            AdapterOutcome.NOT_APPLICABLE: GovernanceStatus.NOT_APPLICABLE,
        }[self.outcome]


# ---------------------------------------------------------------------------
# Records returned by the runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRecord:
    source_id: str
    owner_id: str
    conversation_id: str
    message_id: str
    role: str
    content: str
    sequence: int
    event_time: datetime
    ingested_at: datetime
    privacy_class: PrivacyClass
    retention_mode: RetentionMode
    provenance: Mapping[str, Any]
    lifecycle_state: LifecycleState = LifecycleState.CURRENT
    project_id: str | None = None
    branch_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    local_display_time: str = ""
    timezone_name: str = "UTC"
    utc_offset_minutes: int = 0

    def __post_init__(self) -> None:
        _ensure_utc(self.event_time, "event_time")
        _ensure_utc(self.ingested_at, "ingested_at")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        object.__setattr__(self, "entity_ids", tuple(self.entity_ids))
        object.__setattr__(self, "goal_ids", tuple(self.goal_ids))
        object.__setattr__(self, "decision_ids", tuple(self.decision_ids))


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    owner_id: str
    title: str
    topic: str
    participant: str
    branch_id: str | None
    started_at: datetime
    ended_at: datetime | None
    privacy_class: PrivacyClass
    retention_mode: RetentionMode
    lifecycle_state: LifecycleState
    project_id: str | None = None


@dataclass(frozen=True)
class DerivedMemoryRecord:
    memory_id: str
    owner_id: str
    kind: DerivedMemoryKind
    text: str
    claim_key: str
    source_id: str
    conversation_id: str
    message_id: str
    event_time: datetime
    created_at: datetime
    valid_from: datetime | None
    valid_until: datetime | None
    lifecycle_state: LifecycleState
    privacy_class: PrivacyClass
    confidence: float
    provenance: Mapping[str, Any]
    project_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    supersedes_id: str | None = None
    conflict_group_id: str | None = None

    def __post_init__(self) -> None:
        _ensure_utc(self.event_time, "event_time")
        _ensure_utc(self.created_at, "created_at")
        _ensure_utc(self.valid_from, "valid_from")
        _ensure_utc(self.valid_until, "valid_until")
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))
        object.__setattr__(self, "entity_ids", tuple(self.entity_ids))
        object.__setattr__(self, "goal_ids", tuple(self.goal_ids))
        object.__setattr__(self, "decision_ids", tuple(self.decision_ids))


@dataclass(frozen=True)
class TimelineEventRecord:
    timeline_id: str
    owner_id: str
    event_type: str
    title: str
    description: str
    source_ids: tuple[str, ...]
    event_time: datetime
    created_at: datetime
    provenance: Mapping[str, Any]
    project_id: str | None = None
    lifecycle_state: LifecycleState = LifecycleState.CURRENT

    def __post_init__(self) -> None:
        _ensure_utc(self.event_time, "event_time")
        _ensure_utc(self.created_at, "created_at")
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance)))


@dataclass(frozen=True)
class ContinuityQuery:
    query_text: str
    modes: tuple[RetrievalModeName, ...] = (
        RetrievalModeName.EXACT,
        RetrievalModeName.LEXICAL,
        RetrievalModeName.SEMANTIC,
        RetrievalModeName.TEMPORAL,
        RetrievalModeName.RELATIONAL,
    )
    temporal_start: datetime | None = None
    temporal_end: datetime | None = None
    calendar_timezone: str | None = None
    source_ids: tuple[str, ...] = ()
    message_ids: tuple[str, ...] = ()
    conversation_id: str | None = None
    project_id: str | None = None
    entity_ids: tuple[str, ...] = ()
    goal_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    include_deleted: bool = False
    limit: int = 20

    def __post_init__(self) -> None:
        _ensure_utc(self.temporal_start, "temporal_start")
        _ensure_utc(self.temporal_end, "temporal_end")
        if self.calendar_timezone is not None:
            _resolve_calendar_timezone(self.calendar_timezone)
        if self.temporal_start and self.temporal_end and self.temporal_end < self.temporal_start:
            raise ValueError("temporal_end must be after temporal_start")
        object.__setattr__(self, "modes", tuple(self.modes))
        object.__setattr__(self, "source_ids", tuple(self.source_ids))
        object.__setattr__(self, "message_ids", tuple(self.message_ids))
        object.__setattr__(self, "entity_ids", tuple(self.entity_ids))
        object.__setattr__(self, "goal_ids", tuple(self.goal_ids))
        object.__setattr__(self, "decision_ids", tuple(self.decision_ids))


@dataclass(frozen=True)
class RetrievalResponse:
    status: AdapterOutcome
    decision: AdapterDecision
    query: ContinuityQuery
    source_records: tuple[SourceRecord, ...]
    derived_memories: tuple[DerivedMemoryRecord, ...] = ()
    timeline_events: tuple[TimelineEventRecord, ...] = ()
    source_scores: Mapping[str, float] = field(default_factory=dict)
    mode_status: Mapping[str, str] = field(default_factory=dict)
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_records", tuple(self.source_records))
        object.__setattr__(self, "derived_memories", tuple(self.derived_memories))
        object.__setattr__(self, "timeline_events", tuple(self.timeline_events))
        object.__setattr__(self, "source_scores", MappingProxyType(dict(self.source_scores)))
        object.__setattr__(self, "mode_status", MappingProxyType(dict(self.mode_status)))


@dataclass(frozen=True)
class HistoricalAnswer:
    query: str
    status: AdapterOutcome
    exact_source_content: tuple[SourceRecord, ...]
    summary: str
    inference: str
    evidence_source_ids: tuple[str, ...]
    mode_status: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "exact_source_content", tuple(self.exact_source_content))
        object.__setattr__(self, "evidence_source_ids", tuple(self.evidence_source_ids))
        object.__setattr__(self, "mode_status", MappingProxyType(dict(self.mode_status)))


@dataclass(frozen=True)
class ActivationRuntimeResult:
    status: AdapterOutcome
    request: MemoryActivationRequest
    retrieval: RetrievalResponse
    selection: MemoryContextSelection
    candidates: tuple[MemoryActivationCandidate, ...]
    retrieved_source_ids: tuple[str, ...]
    relevant_candidate_ids: tuple[str, ...]
    activated_candidate_ids: tuple[str, ...]
    used_in_reasoning_candidate_ids: tuple[str, ...]
    mentioned_to_user_candidate_ids: tuple[str, ...]
    minimized_reasoning_context: Mapping[str, Any]
    explanation: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidates", tuple(self.candidates))
        object.__setattr__(self, "retrieved_source_ids", tuple(self.retrieved_source_ids))
        object.__setattr__(self, "relevant_candidate_ids", tuple(self.relevant_candidate_ids))
        object.__setattr__(self, "activated_candidate_ids", tuple(self.activated_candidate_ids))
        object.__setattr__(self, "used_in_reasoning_candidate_ids", tuple(self.used_in_reasoning_candidate_ids))
        object.__setattr__(self, "mentioned_to_user_candidate_ids", tuple(self.mentioned_to_user_candidate_ids))
        object.__setattr__(self, "minimized_reasoning_context", MappingProxyType(dict(self.minimized_reasoning_context)))


@dataclass(frozen=True)
class DeletionReport:
    deletion_id: str
    owner_id: str
    status: DeletionRuntimeStatus
    decision: AdapterDecision
    propagated: Mapping[str, DeletionRuntimeStatus]
    deleted_source_ids: tuple[str, ...]
    deleted_memory_ids: tuple[str, ...]
    retained_by_policy: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "propagated", MappingProxyType(dict(self.propagated)))
        object.__setattr__(self, "deleted_source_ids", tuple(self.deleted_source_ids))
        object.__setattr__(self, "deleted_memory_ids", tuple(self.deleted_memory_ids))
        object.__setattr__(self, "retained_by_policy", tuple(self.retained_by_policy))


# ---------------------------------------------------------------------------
# Local concept embedding provider
# ---------------------------------------------------------------------------


class LocalConceptEmbeddingProvider:
    """Small deterministic local concept-vector provider.

    This is not a neural embedding model and does not call a provider/network.
    It is nevertheless distinct from lexical FTS because it expands text into a
    stable concept vector before similarity scoring.  Runtime status explicitly
    names this provider so callers do not mistake it for a vector database or a
    hosted semantic model.
    """

    provider_id = "local-concept-vector-v1"
    available = True

    _CONCEPTS: Mapping[str, tuple[str, ...]] = {
        "architecture": ("architecture", "architectural", "design", "redesign", "core", "system", "boundary", "structure"),
        "continuity": ("continuity", "coherent", "persistent", "history", "memory", "remember", "recall", "long-term", "longterm"),
        "zorq": ("zorq",),
        "governance": ("governance", "policy", "privacy", "retention", "deletion", "firewall"),
        "decision": ("decision", "decided", "choose", "chose", "chosen", "why", "because", "led"),
        "project": ("project", "roadmap", "phase", "implementation"),
        "preference": ("prefer", "preference", "like", "use", "using", "stopped"),
    }

    def vector(self, text: str) -> Counter[str]:
        vec: Counter[str] = Counter()
        toks = _tokens(text)
        for token in toks:
            vec[token] += 1.0
        text_low = text.lower()
        for concept, terms in self._CONCEPTS.items():
            if any(term in text_low for term in terms):
                vec[f"concept:{concept}"] += 2.0
        return vec

    def similarity(self, left: str, right: str) -> float:
        a = self.vector(left)
        b = self.vector(right)
        if not a or not b:
            return 0.0
        dot = sum(a[k] * b.get(k, 0.0) for k in a)
        na = math.sqrt(sum(v * v for v in a.values()))
        nb = math.sqrt(sum(v * v for v in b.values()))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)


# ---------------------------------------------------------------------------
# Capture policy engine
# ---------------------------------------------------------------------------


class MemoryCapturePolicyEngine:
    def __init__(self, policy: MemoryCapturePolicy) -> None:
        self.policy = policy

    @classmethod
    def default_retain(cls, owner_id: str) -> "MemoryCapturePolicyEngine":
        return cls(
            MemoryCapturePolicy(
                policy_id="policy-default-retain",
                owner_id=owner_id,
                created_at=utc_now(),
                default_conversation_retention=RetentionMode.DEFAULT_RETAIN,
                explicit_remember=RetentionOverride.RETAIN,
                explicit_do_not_remember=RetentionOverride.DO_NOT_RETAIN,
                temporary_conversation=RetentionOverride.TEMPORARY,
                sensitive_memory=RetentionOverride.RETAIN,
                project_scoped_memory=RetentionOverride.RETAIN,
                deletion_behavior=DeletionBehavior.DELETE_ALL_REPRESENTATIONS,
            )
        )

    def decide_retention(
        self,
        *,
        conversation_id: str,
        message_id: str | None,
        privacy_class: PrivacyClass,
        explicit_remember: bool = False,
        explicit_do_not_remember: bool = False,
        temporary: bool = False,
        project_id: str | None = None,
        override: RetentionOverride = RetentionOverride.INHERIT,
    ) -> tuple[bool, RetentionMode, str]:
        if override == RetentionOverride.RETAIN:
            return True, RetentionMode.PER_MESSAGE_OVERRIDE if message_id else RetentionMode.PER_CONVERSATION_OVERRIDE, "explicit override retain"
        if override in (RetentionOverride.DO_NOT_RETAIN, RetentionOverride.TEMPORARY):
            return False, RetentionMode.TEMPORARY if override == RetentionOverride.TEMPORARY else RetentionMode.PER_MESSAGE_OVERRIDE, "explicit override do not retain"
        if message_id and message_id in self.policy.per_message_overrides:
            return self.decide_retention(
                conversation_id=conversation_id,
                message_id=message_id,
                privacy_class=privacy_class,
                override=self.policy.per_message_overrides[message_id],
            )
        if conversation_id in self.policy.per_conversation_overrides:
            return self.decide_retention(
                conversation_id=conversation_id,
                message_id=message_id,
                privacy_class=privacy_class,
                override=self.policy.per_conversation_overrides[conversation_id],
            )
        if explicit_do_not_remember:
            return False, RetentionMode.EXPLICIT_DO_NOT_REMEMBER, "user explicitly requested do-not-remember"
        if temporary:
            return False, RetentionMode.TEMPORARY, "temporary conversation/message"
        if explicit_remember:
            return True, RetentionMode.EXPLICIT_REMEMBER, "user explicitly requested remember"
        if privacy_class in (PrivacyClass.SECRET, PrivacyClass.SYSTEM_SECURITY):
            if self.policy.sensitive_memory == RetentionOverride.RETAIN:
                return True, RetentionMode.DEFAULT_RETAIN, "sensitive memory retained by owner policy"
            return False, RetentionMode.EXPLICIT_DO_NOT_REMEMBER, "sensitive memory blocked by policy"
        if project_id and self.policy.project_scoped_memory == RetentionOverride.RETAIN:
            return True, RetentionMode.PROJECT_SCOPED, "project-scoped retention"
        if self.policy.default_conversation_retention == RetentionMode.DEFAULT_RETAIN:
            return True, RetentionMode.DEFAULT_RETAIN, "default retain policy"
        return False, RetentionMode.DEFAULT_DO_NOT_RETAIN, "default do-not-retain policy"


# ---------------------------------------------------------------------------
# SQLite source archive and derived views
# ---------------------------------------------------------------------------


class PersonalContinuityStore:
    """Durable local source archive and derived-view store.

    All public writes are expected to be reached through a MEMORY//OS adapter
    decision. The store itself still enforces owner filters on every query.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_info (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                INSERT OR REPLACE INTO schema_info(key, value) VALUES ('schema_version', 'zorq.phase3b.personal-continuity.v1');

                CREATE TABLE IF NOT EXISTS conversations (
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    participant TEXT NOT NULL,
                    branch_id TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    privacy_class TEXT NOT NULL,
                    retention_mode TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    project_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(owner_id, conversation_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    owner_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_time TEXT NOT NULL,
                    ingested_at TEXT NOT NULL,
                    privacy_class TEXT NOT NULL,
                    retention_mode TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    project_id TEXT,
                    branch_id TEXT,
                    entity_ids_json TEXT NOT NULL,
                    goal_ids_json TEXT NOT NULL,
                    decision_ids_json TEXT NOT NULL,
                    local_display_time TEXT NOT NULL,
                    timezone_name TEXT NOT NULL,
                    utc_offset_minutes INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    PRIMARY KEY(owner_id, source_id),
                    UNIQUE(owner_id, conversation_id, message_id),
                    FOREIGN KEY(owner_id, conversation_id) REFERENCES conversations(owner_id, conversation_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_messages_owner_time ON messages(owner_id, event_time);
                CREATE INDEX IF NOT EXISTS idx_messages_owner_conv_sequence ON messages(owner_id, conversation_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_messages_owner_project ON messages(owner_id, project_id);
                CREATE INDEX IF NOT EXISTS idx_messages_owner_lifecycle ON messages(owner_id, lifecycle_state);

                CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5(
                    owner_id UNINDEXED,
                    source_id UNINDEXED,
                    conversation_id UNINDEXED,
                    message_id UNINDEXED,
                    content
                );

                CREATE TABLE IF NOT EXISTS derived_memories (
                    owner_id TEXT NOT NULL,
                    memory_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    text TEXT NOT NULL,
                    claim_key TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    valid_from TEXT,
                    valid_until TEXT,
                    lifecycle_state TEXT NOT NULL,
                    privacy_class TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    provenance_json TEXT NOT NULL,
                    project_id TEXT,
                    entity_ids_json TEXT NOT NULL,
                    goal_ids_json TEXT NOT NULL,
                    decision_ids_json TEXT NOT NULL,
                    supersedes_id TEXT,
                    conflict_group_id TEXT,
                    PRIMARY KEY(owner_id, memory_id)
                );
                CREATE INDEX IF NOT EXISTS idx_derived_owner_claim ON derived_memories(owner_id, claim_key, lifecycle_state);
                CREATE INDEX IF NOT EXISTS idx_derived_owner_source ON derived_memories(owner_id, source_id);
                CREATE INDEX IF NOT EXISTS idx_derived_owner_time ON derived_memories(owner_id, event_time);
                CREATE VIRTUAL TABLE IF NOT EXISTS derived_fts USING fts5(
                    owner_id UNINDEXED,
                    memory_id UNINDEXED,
                    source_id UNINDEXED,
                    text
                );

                CREATE TABLE IF NOT EXISTS timeline_events (
                    owner_id TEXT NOT NULL,
                    timeline_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    project_id TEXT,
                    lifecycle_state TEXT NOT NULL,
                    PRIMARY KEY(owner_id, timeline_id)
                );
                CREATE INDEX IF NOT EXISTS idx_timeline_owner_time ON timeline_events(owner_id, event_time);
                CREATE INDEX IF NOT EXISTS idx_timeline_owner_project ON timeline_events(owner_id, project_id);

                CREATE TABLE IF NOT EXISTS relationship_index (
                    owner_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    PRIMARY KEY(owner_id, subject_id, relation_type, object_id, source_id)
                );
                CREATE INDEX IF NOT EXISTS idx_relationship_owner_subject ON relationship_index(owner_id, subject_id);
                CREATE INDEX IF NOT EXISTS idx_relationship_owner_object ON relationship_index(owner_id, object_id);

                CREATE TABLE IF NOT EXISTS embedding_index (
                    owner_id TEXT NOT NULL,
                    item_type TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    terms_json TEXT NOT NULL,
                    source_ids_json TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    PRIMARY KEY(owner_id, item_type, item_id)
                );

                CREATE TABLE IF NOT EXISTS deletion_audit (
                    owner_id TEXT NOT NULL,
                    deletion_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    scope_type TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    decision_outcome TEXT NOT NULL,
                    propagated_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    content_included INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(owner_id, deletion_id)
                );

                CREATE TABLE IF NOT EXISTS observability_events (
                    event_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                """
            )

    def observe(self, owner_id: str, event_type: str, metadata: Mapping[str, Any]) -> None:
        safe_metadata = {k: v for k, v in metadata.items() if k not in {"content", "raw_content", "secret"}}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO observability_events(event_id, owner_id, event_type, created_at, metadata_json) VALUES(?,?,?,?,?)",
                (_stable_id(owner_id, event_type, utc_now().isoformat(), prefix="obs"), owner_id, event_type, _iso(utc_now()), _json(safe_metadata)),
            )

    def begin_conversation(
        self,
        *,
        owner_id: str,
        conversation_id: str,
        started_at: datetime,
        title: str = "",
        topic: str = "",
        participant: str = "user",
        branch_id: str | None = None,
        privacy_class: PrivacyClass = PrivacyClass.PERSONAL,
        retention_mode: RetentionMode = RetentionMode.DEFAULT_RETAIN,
        project_id: str | None = None,
    ) -> ConversationRecord:
        _ensure_utc(started_at, "started_at")
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO conversations(owner_id, conversation_id, title, topic, participant, branch_id, started_at, ended_at,
                    privacy_class, retention_mode, lifecycle_state, project_id, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id, conversation_id) DO UPDATE SET
                    title=excluded.title,
                    topic=excluded.topic,
                    participant=excluded.participant,
                    branch_id=excluded.branch_id,
                    privacy_class=excluded.privacy_class,
                    retention_mode=excluded.retention_mode,
                    project_id=excluded.project_id,
                    updated_at=excluded.updated_at
                """,
                (
                    owner_id,
                    conversation_id,
                    title,
                    topic,
                    participant,
                    branch_id,
                    _iso(started_at),
                    None,
                    privacy_class.value,
                    retention_mode.value,
                    LifecycleState.CURRENT.value,
                    project_id,
                    _iso(now),
                    _iso(now),
                ),
            )
            conn.execute("COMMIT")
        self.observe(owner_id, "memory.conversation.begin", {"conversation_id": conversation_id, "project_id": project_id})
        return ConversationRecord(conversation_id, owner_id, title, topic, participant, branch_id, started_at, None, privacy_class, retention_mode, LifecycleState.CURRENT, project_id)

    def end_conversation(self, owner_id: str, conversation_id: str, ended_at: datetime) -> None:
        _ensure_utc(ended_at, "ended_at")
        with self._connect() as conn:
            conn.execute(
                "UPDATE conversations SET ended_at=?, updated_at=? WHERE owner_id=? AND conversation_id=?",
                (_iso(ended_at), _iso(utc_now()), owner_id, conversation_id),
            )
        self.observe(owner_id, "memory.conversation.end", {"conversation_id": conversation_id})

    def store_source_record(self, record: SourceRecord) -> tuple[SourceRecord, bool]:
        """Store a source record idempotently.

        Returns (record, inserted). A duplicate with identical stable IDs/content is
        not inserted twice. A duplicate stable ID with different content raises.
        """
        content_sha = hashlib.sha256(record.content.encode("utf-8")).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT content_sha256, content, lifecycle_state FROM messages WHERE owner_id=? AND source_id=?",
                (record.owner_id, record.source_id),
            ).fetchone()
            if existing:
                if existing["content_sha256"] != content_sha:
                    conn.execute("ROLLBACK")
                    raise ValueError("duplicate source_id has different content")
                conn.execute("COMMIT")
                self.observe(record.owner_id, "memory.source.idempotent_duplicate", {"source_id": record.source_id})
                return record, False
            conn.execute(
                """
                INSERT INTO messages(owner_id, source_id, conversation_id, message_id, role, content, sequence, event_time, ingested_at,
                    privacy_class, retention_mode, provenance_json, lifecycle_state, project_id, branch_id, entity_ids_json, goal_ids_json,
                    decision_ids_json, local_display_time, timezone_name, utc_offset_minutes, content_sha256)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.owner_id,
                    record.source_id,
                    record.conversation_id,
                    record.message_id,
                    record.role,
                    record.content,
                    record.sequence,
                    _iso(record.event_time),
                    _iso(record.ingested_at),
                    record.privacy_class.value,
                    record.retention_mode.value,
                    _json(record.provenance),
                    record.lifecycle_state.value,
                    record.project_id,
                    record.branch_id,
                    _json(record.entity_ids),
                    _json(record.goal_ids),
                    _json(record.decision_ids),
                    record.local_display_time,
                    record.timezone_name,
                    record.utc_offset_minutes,
                    content_sha,
                ),
            )
            conn.execute(
                "INSERT INTO message_fts(owner_id, source_id, conversation_id, message_id, content) VALUES(?,?,?,?,?)",
                (record.owner_id, record.source_id, record.conversation_id, record.message_id, record.content),
            )
            conn.execute(
                "INSERT OR REPLACE INTO embedding_index(owner_id, item_type, item_id, terms_json, source_ids_json, lifecycle_state) VALUES(?,?,?,?,?,?)",
                (record.owner_id, "source", record.source_id, _json(sorted(_token_set(record.content))), _json([record.source_id]), record.lifecycle_state.value),
            )
            conn.execute("COMMIT")
        self.observe(record.owner_id, "memory.source.store", {"source_id": record.source_id, "conversation_id": record.conversation_id})
        return record, True

    def store_timeline_event(self, event: TimelineEventRecord) -> TimelineEventRecord:
        if not event.source_ids:
            raise ValueError("timeline events require source evidence")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO timeline_events(owner_id, timeline_id, event_type, title, description, source_ids_json, event_time,
                    created_at, provenance_json, project_id, lifecycle_state)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.owner_id,
                    event.timeline_id,
                    event.event_type,
                    event.title,
                    event.description,
                    _json(event.source_ids),
                    _iso(event.event_time),
                    _iso(event.created_at),
                    _json(event.provenance),
                    event.project_id,
                    event.lifecycle_state.value,
                ),
            )
        self.observe(event.owner_id, "memory.timeline.store", {"timeline_id": event.timeline_id, "source_ids": list(event.source_ids)})
        return event

    def _source_from_row(self, row: sqlite3.Row) -> SourceRecord:
        return SourceRecord(
            source_id=row["source_id"],
            owner_id=row["owner_id"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            role=row["role"],
            content=row["content"],
            sequence=int(row["sequence"]),
            event_time=_from_iso(row["event_time"]),  # type: ignore[arg-type]
            ingested_at=_from_iso(row["ingested_at"]),  # type: ignore[arg-type]
            privacy_class=PrivacyClass(row["privacy_class"]),
            retention_mode=RetentionMode(row["retention_mode"]),
            provenance=_loads(row["provenance_json"], {}),
            lifecycle_state=LifecycleState(row["lifecycle_state"]),
            project_id=row["project_id"],
            branch_id=row["branch_id"],
            entity_ids=tuple(_loads(row["entity_ids_json"], [])),
            goal_ids=tuple(_loads(row["goal_ids_json"], [])),
            decision_ids=tuple(_loads(row["decision_ids_json"], [])),
            local_display_time=row["local_display_time"],
            timezone_name=row["timezone_name"],
            utc_offset_minutes=int(row["utc_offset_minutes"]),
        )

    def _derived_from_row(self, row: sqlite3.Row) -> DerivedMemoryRecord:
        return DerivedMemoryRecord(
            memory_id=row["memory_id"],
            owner_id=row["owner_id"],
            kind=DerivedMemoryKind(row["kind"]),
            text=row["text"],
            claim_key=row["claim_key"],
            source_id=row["source_id"],
            conversation_id=row["conversation_id"],
            message_id=row["message_id"],
            event_time=_from_iso(row["event_time"]),  # type: ignore[arg-type]
            created_at=_from_iso(row["created_at"]),  # type: ignore[arg-type]
            valid_from=_from_iso(row["valid_from"]),
            valid_until=_from_iso(row["valid_until"]),
            lifecycle_state=LifecycleState(row["lifecycle_state"]),
            privacy_class=PrivacyClass(row["privacy_class"]),
            confidence=float(row["confidence"]),
            provenance=_loads(row["provenance_json"], {}),
            project_id=row["project_id"],
            entity_ids=tuple(_loads(row["entity_ids_json"], [])),
            goal_ids=tuple(_loads(row["goal_ids_json"], [])),
            decision_ids=tuple(_loads(row["decision_ids_json"], [])),
            supersedes_id=row["supersedes_id"],
            conflict_group_id=row["conflict_group_id"],
        )

    def _timeline_from_row(self, row: sqlite3.Row) -> TimelineEventRecord:
        return TimelineEventRecord(
            timeline_id=row["timeline_id"],
            owner_id=row["owner_id"],
            event_type=row["event_type"],
            title=row["title"],
            description=row["description"],
            source_ids=tuple(_loads(row["source_ids_json"], [])),
            event_time=_from_iso(row["event_time"]),  # type: ignore[arg-type]
            created_at=_from_iso(row["created_at"]),  # type: ignore[arg-type]
            provenance=_loads(row["provenance_json"], {}),
            project_id=row["project_id"],
            lifecycle_state=LifecycleState(row["lifecycle_state"]),
        )

    def get_source(self, owner_id: str, source_id: str, include_deleted: bool = False) -> SourceRecord | None:
        sql = "SELECT * FROM messages WHERE owner_id=? AND source_id=?"
        params: list[Any] = [owner_id, source_id]
        if not include_deleted:
            sql += " AND lifecycle_state != ?"
            params.append(LifecycleState.DELETED.value)
        with self._connect() as conn:
            row = conn.execute(sql, params).fetchone()
        return self._source_from_row(row) if row else None

    def list_sources_for_conversation(self, owner_id: str, conversation_id: str, include_deleted: bool = False) -> tuple[SourceRecord, ...]:
        sql = "SELECT * FROM messages WHERE owner_id=? AND conversation_id=?"
        params: list[Any] = [owner_id, conversation_id]
        if not include_deleted:
            sql += " AND lifecycle_state != ?"
            params.append(LifecycleState.DELETED.value)
        sql += " ORDER BY sequence ASC, event_time ASC, source_id ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(self._source_from_row(row) for row in rows)

    def candidate_sources(self, owner_id: str, query: ContinuityQuery, limit: int | None = None) -> tuple[SourceRecord, ...]:
        clauses = ["owner_id=?"]
        params: list[Any] = [owner_id]
        if not query.include_deleted:
            clauses.append("lifecycle_state != ?")
            params.append(LifecycleState.DELETED.value)
        if query.conversation_id:
            clauses.append("conversation_id=?")
            params.append(query.conversation_id)
        if query.project_id:
            clauses.append("project_id=?")
            params.append(query.project_id)
        if query.temporal_start:
            clauses.append("event_time >= ?")
            params.append(_iso(query.temporal_start))
        if query.temporal_end:
            clauses.append("event_time < ?")
            params.append(_iso(query.temporal_end))
        if query.source_ids:
            placeholders = ",".join("?" for _ in query.source_ids)
            clauses.append(f"source_id IN ({placeholders})")
            params.extend(query.source_ids)
        if query.message_ids:
            placeholders = ",".join("?" for _ in query.message_ids)
            clauses.append(f"message_id IN ({placeholders})")
            params.extend(query.message_ids)
        sql = "SELECT * FROM messages WHERE " + " AND ".join(clauses) + " ORDER BY event_time DESC, sequence DESC"
        sql += f" LIMIT {int(limit or query.limit or 20)}"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(self._source_from_row(row) for row in rows)

    def lexical_sources(self, owner_id: str, query_text: str, include_deleted: bool = False, limit: int = 20) -> tuple[SourceRecord, ...]:
        terms = _tokens(query_text)[:12]
        if not terms:
            return ()
        match = " OR ".join(term.replace('"', '') for term in terms)
        source_ids: list[str] = []
        with self._connect() as conn:
            try:
                rows = conn.execute(
                    "SELECT source_id FROM message_fts WHERE message_fts MATCH ? AND owner_id=? LIMIT ?",
                    (match, owner_id, limit),
                ).fetchall()
                source_ids = [r["source_id"] for r in rows]
            except sqlite3.OperationalError:
                like = "%" + "%".join(terms[:3]) + "%"
                rows = conn.execute(
                    "SELECT source_id FROM messages WHERE owner_id=? AND lower(content) LIKE lower(?) LIMIT ?",
                    (owner_id, like, limit),
                ).fetchall()
                source_ids = [r["source_id"] for r in rows]
        records = [self.get_source(owner_id, sid, include_deleted=include_deleted) for sid in source_ids]
        return tuple(r for r in records if r is not None)

    def store_derived_memory(self, memory: DerivedMemoryRecord) -> DerivedMemoryRecord:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            # If this memory supersedes a previous CURRENT memory for the same claim, preserve old record as SUPERSEDED.
            if memory.supersedes_id:
                conn.execute(
                    "UPDATE derived_memories SET lifecycle_state=?, valid_until=? WHERE owner_id=? AND memory_id=?",
                    (LifecycleState.SUPERSEDED.value, _iso(memory.event_time), memory.owner_id, memory.supersedes_id),
                )
            conn.execute(
                """
                INSERT OR REPLACE INTO derived_memories(owner_id, memory_id, kind, text, claim_key, source_id, conversation_id, message_id,
                    event_time, created_at, valid_from, valid_until, lifecycle_state, privacy_class, confidence, provenance_json, project_id,
                    entity_ids_json, goal_ids_json, decision_ids_json, supersedes_id, conflict_group_id)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    memory.owner_id,
                    memory.memory_id,
                    memory.kind.value,
                    memory.text,
                    memory.claim_key,
                    memory.source_id,
                    memory.conversation_id,
                    memory.message_id,
                    _iso(memory.event_time),
                    _iso(memory.created_at),
                    _iso(memory.valid_from),
                    _iso(memory.valid_until),
                    memory.lifecycle_state.value,
                    memory.privacy_class.value,
                    memory.confidence,
                    _json(memory.provenance),
                    memory.project_id,
                    _json(memory.entity_ids),
                    _json(memory.goal_ids),
                    _json(memory.decision_ids),
                    memory.supersedes_id,
                    memory.conflict_group_id,
                ),
            )
            conn.execute("DELETE FROM derived_fts WHERE owner_id=? AND memory_id=?", (memory.owner_id, memory.memory_id))
            if memory.lifecycle_state != LifecycleState.DELETED:
                conn.execute(
                    "INSERT INTO derived_fts(owner_id, memory_id, source_id, text) VALUES(?,?,?,?)",
                    (memory.owner_id, memory.memory_id, memory.source_id, memory.text),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO embedding_index(owner_id, item_type, item_id, terms_json, source_ids_json, lifecycle_state) VALUES(?,?,?,?,?,?)",
                    (memory.owner_id, "derived", memory.memory_id, _json(sorted(_token_set(memory.text))), _json([memory.source_id]), memory.lifecycle_state.value),
                )
            conn.execute("COMMIT")
        self.observe(memory.owner_id, "memory.derived.store", {"memory_id": memory.memory_id, "source_id": memory.source_id})
        return memory

    def find_current_derived_by_claim(self, owner_id: str, claim_key: str) -> tuple[DerivedMemoryRecord, ...]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM derived_memories WHERE owner_id=? AND claim_key=? AND lifecycle_state=? ORDER BY event_time DESC",
                (owner_id, claim_key, LifecycleState.CURRENT.value),
            ).fetchall()
        return tuple(self._derived_from_row(row) for row in rows)

    def mark_conflicted(self, owner_id: str, memory_ids: Sequence[str], conflict_group_id: str) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE derived_memories SET lifecycle_state=?, conflict_group_id=? WHERE owner_id=? AND memory_id IN ({placeholders})",
                [LifecycleState.CONFLICTED.value, conflict_group_id, owner_id, *memory_ids],
            )
        self.observe(owner_id, "memory.conflict.mark", {"memory_ids": list(memory_ids), "conflict_group_id": conflict_group_id})

    def derived_for_sources(self, owner_id: str, source_ids: Sequence[str], include_deleted: bool = False) -> tuple[DerivedMemoryRecord, ...]:
        if not source_ids:
            return ()
        placeholders = ",".join("?" for _ in source_ids)
        sql = f"SELECT * FROM derived_memories WHERE owner_id=? AND source_id IN ({placeholders})"
        params: list[Any] = [owner_id, *source_ids]
        if not include_deleted:
            sql += " AND lifecycle_state != ?"
            params.append(LifecycleState.DELETED.value)
        sql += " ORDER BY event_time DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return tuple(self._derived_from_row(row) for row in rows)

    def search_derived(self, owner_id: str, query: ContinuityQuery, limit: int = 20) -> tuple[DerivedMemoryRecord, ...]:
        clauses = ["owner_id=?"]
        params: list[Any] = [owner_id]
        if not query.include_deleted:
            clauses.append("lifecycle_state != ?")
            params.append(LifecycleState.DELETED.value)
        if query.project_id:
            clauses.append("project_id=?")
            params.append(query.project_id)
        if query.temporal_start:
            clauses.append("event_time >= ?")
            params.append(_iso(query.temporal_start))
        if query.temporal_end:
            clauses.append("event_time < ?")
            params.append(_iso(query.temporal_end))
        sql = "SELECT * FROM derived_memories WHERE " + " AND ".join(clauses) + " ORDER BY event_time DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        records = [self._derived_from_row(row) for row in rows]
        if query.query_text and RetrievalModeName.LEXICAL in query.modes:
            q = _token_set(query.query_text)
            records.sort(key=lambda r: len(q & _token_set(r.text)), reverse=True)
        return tuple(records[:limit])

    def timeline_for_sources(self, owner_id: str, source_ids: Sequence[str]) -> tuple[TimelineEventRecord, ...]:
        if not source_ids:
            return ()
        out: list[TimelineEventRecord] = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM timeline_events WHERE owner_id=? AND lifecycle_state != ? ORDER BY event_time DESC LIMIT 200",
                (owner_id, LifecycleState.DELETED.value),
            ).fetchall()
        wanted = set(source_ids)
        for row in rows:
            if wanted & set(_loads(row["source_ids_json"], [])):
                out.append(self._timeline_from_row(row))
        return tuple(out)

    def delete_scope(
        self,
        *,
        owner_id: str,
        deletion_id: str,
        principal_id: str,
        scope_type: str,
        scope: Mapping[str, Any],
        decision: AdapterDecision,
        include_backups: bool = False,
    ) -> DeletionReport:
        source_ids = self._source_ids_for_deletion(owner_id, scope_type, scope)
        deleted_memory_ids: list[str] = []
        propagated: dict[str, DeletionRuntimeStatus] = {
            "source": DeletionRuntimeStatus.COMPLETED,
            "derived_memory": DeletionRuntimeStatus.COMPLETED,
            "indexes": DeletionRuntimeStatus.COMPLETED,
            "timeline": DeletionRuntimeStatus.COMPLETED,
            "relationship_graph": DeletionRuntimeStatus.COMPLETED,
            "embeddings": DeletionRuntimeStatus.COMPLETED,
            "caches": DeletionRuntimeStatus.COMPLETED,
        }
        if include_backups:
            propagated["backups"] = DeletionRuntimeStatus.UNKNOWN
        status = DeletionRuntimeStatus.PARTIAL if include_backups else DeletionRuntimeStatus.COMPLETED
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                rows = conn.execute(
                    f"SELECT memory_id FROM derived_memories WHERE owner_id=? AND source_id IN ({placeholders})",
                    [owner_id, *source_ids],
                ).fetchall()
                deleted_memory_ids = [r["memory_id"] for r in rows]
                conn.execute(
                    f"UPDATE messages SET content='', lifecycle_state=? WHERE owner_id=? AND source_id IN ({placeholders})",
                    [LifecycleState.DELETED.value, owner_id, *source_ids],
                )
                conn.execute(
                    f"DELETE FROM message_fts WHERE owner_id=? AND source_id IN ({placeholders})",
                    [owner_id, *source_ids],
                )
                conn.execute(
                    f"UPDATE derived_memories SET text='', lifecycle_state=? WHERE owner_id=? AND source_id IN ({placeholders})",
                    [LifecycleState.DELETED.value, owner_id, *source_ids],
                )
                conn.execute(
                    f"DELETE FROM derived_fts WHERE owner_id=? AND source_id IN ({placeholders})",
                    [owner_id, *source_ids],
                )
                conn.execute(
                    f"UPDATE timeline_events SET title='', description='', lifecycle_state=? WHERE owner_id=? AND (" + " OR ".join("source_ids_json LIKE ?" for _ in source_ids) + ")",
                    [LifecycleState.DELETED.value, owner_id, *[f'%"{sid}"%' for sid in source_ids]],
                )
                conn.execute(
                    f"DELETE FROM relationship_index WHERE owner_id=? AND source_id IN ({placeholders})",
                    [owner_id, *source_ids],
                )
                conn.execute(
                    f"DELETE FROM embedding_index WHERE owner_id=? AND item_id IN ({placeholders})",
                    [owner_id, *source_ids],
                )
                if deleted_memory_ids:
                    mem_placeholders = ",".join("?" for _ in deleted_memory_ids)
                    conn.execute(
                        f"DELETE FROM embedding_index WHERE owner_id=? AND item_id IN ({mem_placeholders})",
                        [owner_id, *deleted_memory_ids],
                    )
            if scope_type == "derived_memory" and scope.get("memory_ids"):
                memory_ids = tuple(scope["memory_ids"])
                placeholders = ",".join("?" for _ in memory_ids)
                conn.execute(
                    f"UPDATE derived_memories SET text='', lifecycle_state=? WHERE owner_id=? AND memory_id IN ({placeholders})",
                    [LifecycleState.DELETED.value, owner_id, *memory_ids],
                )
                conn.execute(
                    f"DELETE FROM derived_fts WHERE owner_id=? AND memory_id IN ({placeholders})",
                    [owner_id, *memory_ids],
                )
                conn.execute(
                    f"DELETE FROM embedding_index WHERE owner_id=? AND item_id IN ({placeholders})",
                    [owner_id, *memory_ids],
                )
                deleted_memory_ids.extend(memory_ids)
            conn.execute(
                """
                INSERT OR REPLACE INTO deletion_audit(owner_id, deletion_id, principal_id, requested_at, scope_type, scope_json,
                    decision_outcome, propagated_json, status, content_included)
                VALUES(?,?,?,?,?,?,?,?,?,0)
                """,
                (
                    owner_id,
                    deletion_id,
                    principal_id,
                    _iso(utc_now()),
                    scope_type,
                    _json(scope),
                    decision.outcome.value,
                    _json({k: v.value for k, v in propagated.items()}),
                    status.value,
                ),
            )
            conn.execute("COMMIT")
        self.observe(owner_id, "memory.delete", {"deletion_id": deletion_id, "scope_type": scope_type, "source_count": len(source_ids)})
        return DeletionReport(deletion_id, owner_id, status, decision, propagated, tuple(source_ids), tuple(deleted_memory_ids), message="implemented representations deleted or tombstoned")

    def _source_ids_for_deletion(self, owner_id: str, scope_type: str, scope: Mapping[str, Any]) -> tuple[str, ...]:
        clauses = ["owner_id=?", "lifecycle_state != ?"]
        params: list[Any] = [owner_id, LifecycleState.DELETED.value]
        if scope_type == "message":
            source_ids = tuple(scope.get("source_ids", ()))
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                clauses.append(f"source_id IN ({placeholders})")
                params.extend(source_ids)
            elif scope.get("message_ids"):
                ids = tuple(scope["message_ids"])
                placeholders = ",".join("?" for _ in ids)
                clauses.append(f"message_id IN ({placeholders})")
                params.extend(ids)
        elif scope_type == "conversation":
            clauses.append("conversation_id=?")
            params.append(scope["conversation_id"])
        elif scope_type == "project":
            clauses.append("project_id=?")
            params.append(scope["project_id"])
        elif scope_type == "date_range":
            if scope.get("temporal_start"):
                clauses.append("event_time >= ?")
                params.append(scope["temporal_start"])
            if scope.get("temporal_end"):
                clauses.append("event_time < ?")
                params.append(scope["temporal_end"])
        elif scope_type == "derived_memory":
            return ()
        else:
            raise ValueError(f"unsupported deletion scope_type: {scope_type}")
        with self._connect() as conn:
            rows = conn.execute("SELECT source_id FROM messages WHERE " + " AND ".join(clauses), params).fetchall()
        return tuple(row["source_id"] for row in rows)

    def export_owner(self, owner_id: str) -> Mapping[str, Any]:
        with self._connect() as conn:
            sources = [dict(row) for row in conn.execute("SELECT * FROM messages WHERE owner_id=? ORDER BY event_time, sequence", (owner_id,)).fetchall()]
            derived = [dict(row) for row in conn.execute("SELECT * FROM derived_memories WHERE owner_id=? ORDER BY event_time", (owner_id,)).fetchall()]
            timeline = [dict(row) for row in conn.execute("SELECT * FROM timeline_events WHERE owner_id=? ORDER BY event_time", (owner_id,)).fetchall()]
            deletion = [dict(row) for row in conn.execute("SELECT owner_id, deletion_id, principal_id, requested_at, scope_type, scope_json, decision_outcome, propagated_json, status, content_included FROM deletion_audit WHERE owner_id=? ORDER BY requested_at", (owner_id,)).fetchall()]
        return MappingProxyType(
            {
                "schema_version": PHASE3B_SCHEMA_VERSION,
                "owner_id": owner_id,
                "source_records": sources,
                "derived_memories": derived,
                "timeline_events": timeline,
                "deletion_states": deletion,
            }
        )

    def table_counts(self, owner_id: str) -> Mapping[str, int]:
        with self._connect() as conn:
            return MappingProxyType(
                {
                    "conversations": conn.execute("SELECT count(*) c FROM conversations WHERE owner_id=?", (owner_id,)).fetchone()["c"],
                    "messages": conn.execute("SELECT count(*) c FROM messages WHERE owner_id=?", (owner_id,)).fetchone()["c"],
                    "derived_memories": conn.execute("SELECT count(*) c FROM derived_memories WHERE owner_id=?", (owner_id,)).fetchone()["c"],
                    "timeline_events": conn.execute("SELECT count(*) c FROM timeline_events WHERE owner_id=?", (owner_id,)).fetchone()["c"],
                    "deletion_audit": conn.execute("SELECT count(*) c FROM deletion_audit WHERE owner_id=?", (owner_id,)).fetchone()["c"],
                }
            )


# ---------------------------------------------------------------------------
# Versioned documented MEMORY//OS adapter harness
# ---------------------------------------------------------------------------


class DocumentedMemoryOSAdapter:
    """Versioned adapter implementing the documented ZORQ ↔ MEMORY//OS boundary.

    If the real MEMORY//OS implementation is unavailable, this adapter remains a
    deterministic integration harness over the local continuity store. It is not
    a production MEMORY//OS verification and explicitly reports that fact.
    """

    contract_version = MEMORYOS_ADAPTER_VERSION

    def __init__(
        self,
        store: PersonalContinuityStore,
        policy_engine: MemoryCapturePolicyEngine,
        *,
        memoryos_available: bool = True,
        real_memoryos_verified: bool = False,
        contradictory: bool = False,
    ) -> None:
        self.store = store
        self.policy_engine = policy_engine
        self.memoryos_available = memoryos_available
        self.real_memoryos_verified = real_memoryos_verified
        self.contradictory = contradictory

    @property
    def integration_status(self) -> str:
        if not self.memoryos_available:
            return "UNAVAILABLE"
        if not self.real_memoryos_verified:
            return "DOCUMENTED_CONTRACT_HARNESS_NOT_REAL_MEMORYOS"
        return "AVAILABLE_VERIFIED"

    def govern(
        self,
        context: MemoryAccessContext,
        *,
        operation: str,
        purpose: str,
        privacy_class: PrivacyClass = PrivacyClass.PERSONAL,
        scope: Mapping[str, Any] | None = None,
    ) -> AdapterDecision:
        scope = scope or {}
        decision_id = _stable_id(context.owner_id, operation, purpose, _json(scope), utc_now().isoformat(), prefix="memgov")
        if not self.memoryos_available:
            outcome = AdapterOutcome.UNAVAILABLE
            reason = "MEMORY//OS implementation unavailable; governed operation not allowed"
        elif self.contradictory:
            outcome = AdapterOutcome.CONTRADICTORY
            reason = "MEMORY//OS documented contract harness returned contradictory governance"
        elif context.provider_trust_class != ProviderTrustClass.LOCAL_ONLY and context.egress_policy == EgressPolicy.NO_EGRESS:
            outcome = AdapterOutcome.DENY
            reason = "provider egress denied by local-only/no-egress policy"
        elif context.owner_id not in context.allowed_owner_ids:
            outcome = AdapterOutcome.DENY
            reason = "owner isolation denied"
        elif operation.startswith("store"):
            retain, retention_mode, policy_reason = self.policy_engine.decide_retention(
                conversation_id=str(scope.get("conversation_id", "conversation")),
                message_id=scope.get("message_id"),
                privacy_class=privacy_class,
                explicit_remember=bool(scope.get("explicit_remember", False)),
                explicit_do_not_remember=bool(scope.get("explicit_do_not_remember", False)),
                temporary=bool(scope.get("temporary", False)),
                project_id=scope.get("project_id"),
            )
            if retain:
                outcome = AdapterOutcome.ALLOW
                reason = policy_reason
                scope = {**scope, "retention_mode": retention_mode.value}
            else:
                outcome = AdapterOutcome.DENY
                reason = policy_reason
        elif operation in {"retrieve", "activate", "delete", "export", "derive", "timeline"}:
            outcome = AdapterOutcome.ALLOW
            reason = "documented MEMORY//OS governance harness allowed local-only governed operation"
        else:
            outcome = AdapterOutcome.NOT_APPLICABLE
            reason = "operation is not governed by this adapter"
        decision = AdapterDecision(
            outcome=outcome,
            decision_id=decision_id,
            owner_id=context.owner_id,
            operation=operation,
            purpose=purpose,
            reason=reason,
            policy_version=self.contract_version if self.real_memoryos_verified else DOCUMENTED_CONTRACT_HARNESS_VERSION,
            constraints=scope,
            production_memoryos_verified=self.real_memoryos_verified,
        )
        self.store.observe(context.owner_id, "memory.governance", {"decision_id": decision_id, "operation": operation, "outcome": outcome.value})
        return decision

    def store_source(self, context: MemoryAccessContext, record: SourceRecord, *, explicit_remember: bool = False, explicit_do_not_remember: bool = False, temporary: bool = False) -> tuple[AdapterDecision, SourceRecord | None, bool]:
        if record.owner_id != context.owner_id:
            raise PermissionError("cross-owner source storage denied")
        decision = self.govern(
            context,
            operation="store_source",
            purpose=context.purpose,
            privacy_class=record.privacy_class,
            scope={
                "conversation_id": record.conversation_id,
                "message_id": record.message_id,
                "source_id": record.source_id,
                "project_id": record.project_id,
                "explicit_remember": explicit_remember,
                "explicit_do_not_remember": explicit_do_not_remember,
                "temporary": temporary,
            },
        )
        if decision.outcome != AdapterOutcome.ALLOW:
            return decision, None, False
        stored, inserted = self.store.store_source_record(record)
        return decision, stored, inserted

    def retrieve(self, context: MemoryAccessContext, query: ContinuityQuery, engine: "PersonalContinuityEngine") -> RetrievalResponse:
        decision = self.govern(context, operation="retrieve", purpose=context.purpose, scope={"query": query.query_text, "modes": [m.value for m in query.modes]})
        if decision.outcome != AdapterOutcome.ALLOW:
            return RetrievalResponse(decision.outcome, decision, query, (), mode_status={"all": decision.outcome.value}, message=decision.reason)
        return engine._retrieve_after_governance(context, query, decision)

    def delete(self, context: MemoryAccessContext, *, deletion_id: str, scope_type: str, scope: Mapping[str, Any], include_backups: bool = False) -> DeletionReport:
        decision = self.govern(context, operation="delete", purpose=context.purpose, scope={"scope_type": scope_type, **dict(scope)})
        if decision.outcome != AdapterOutcome.ALLOW:
            return DeletionReport(deletion_id, context.owner_id, DeletionRuntimeStatus.FAILED, decision, {}, (), (), message=decision.reason)
        return self.store.delete_scope(owner_id=context.owner_id, deletion_id=deletion_id, principal_id=context.principal_id, scope_type=scope_type, scope=scope, decision=decision, include_backups=include_backups)

    def export(self, context: MemoryAccessContext) -> tuple[AdapterDecision, Mapping[str, Any] | None]:
        decision = self.govern(context, operation="export", purpose=context.purpose, scope={"owner_id": context.owner_id})
        if decision.outcome != AdapterOutcome.ALLOW:
            return decision, None
        return decision, self.store.export_owner(context.owner_id)


# ---------------------------------------------------------------------------
# Memory Firewall
# ---------------------------------------------------------------------------


class MemoryFirewall:
    """Minimizes memory before it enters reasoning/provider context."""

    def apply(
        self,
        context: MemoryAccessContext,
        *,
        request: MemoryActivationRequest,
        decisions: Sequence[MemoryActivationDecision],
    ) -> tuple[MemoryContextSelection, MemoryFirewallResult]:
        if context.provider_trust_class != ProviderTrustClass.LOCAL_ONLY and context.egress_policy == EgressPolicy.NO_EGRESS:
            selected: tuple[str, ...] = ()
            internal: tuple[str, ...] = ()
            user_visible: tuple[str, ...] = ()
            redacted: tuple[str, ...] = ()
            blocked = tuple(d.candidate.candidate_id for d in decisions)
            minimized: dict[str, Any] = {}
            status = GovernanceStatus.DENY
        else:
            selected_ids: list[str] = []
            internal_ids: list[str] = []
            visible_ids: list[str] = []
            redacted_ids: list[str] = []
            blocked_ids: list[str] = []
            contexts: list[Mapping[str, Any]] = []
            for decision in decisions:
                candidate = decision.candidate
                if decision.decision_state == MemoryActivationDecisionState.ACTIVATED:
                    selected_ids.append(candidate.candidate_id)
                    if decision.activated_for_internal_reasoning:
                        internal_ids.append(candidate.candidate_id)
                    if decision.user_visible_mention_allowed:
                        visible_ids.append(candidate.candidate_id)
                    if decision.policy_decision == MemoryActivationPolicyDecision.REDACT:
                        redacted_ids.append(candidate.candidate_id)
                        contexts.append(
                            {
                                "candidate_id": candidate.candidate_id,
                                "source_id": candidate.source_id,
                                "state": candidate.historical_current_state.value,
                                "policy": "REDACT",
                                "summary": "A governed historical memory is relevant, but details are redacted.",
                            }
                        )
                    elif decision.policy_decision in (MemoryActivationPolicyDecision.SHOW, MemoryActivationPolicyDecision.USE_INTERNAL_ONLY):
                        contexts.append(
                            {
                                "candidate_id": candidate.candidate_id,
                                "source_id": candidate.source_id,
                                "memory_id": candidate.memory_id,
                                "event_time": _iso(candidate.temporal_extent.event_time if candidate.temporal_extent else None),
                                "state": candidate.historical_current_state.value,
                                "validity": candidate.historical_current_validity.value,
                                "relevance": candidate.relevance.value,
                                "reason": _redact_content(decision.reason, 220),
                            }
                        )
                elif decision.decision_state in (MemoryActivationDecisionState.BLOCKED_BY_POLICY, MemoryActivationDecisionState.BLOCKED_BY_PRIVACY):
                    blocked_ids.append(candidate.candidate_id)
            selected = tuple(selected_ids)
            internal = tuple(internal_ids)
            user_visible = tuple(visible_ids)
            redacted = tuple(redacted_ids)
            blocked = tuple(blocked_ids)
            minimized = {"activation_id": request.activation_id, "items": contexts[:8], "provider_egress": context.egress_policy.value}
            status = GovernanceStatus.ALLOW
        provenance = Provenance(
            provenance_id=_stable_id(request.activation_id, "firewall", prefix="prov"),
            owner_id=request.owner_id,
            source_type=SourceType.SYSTEM_EVENT,
            source_id=request.activation_id,
            created_at=request.created_at,
            observed_at=request.created_at,
            confidence=1.0,
            verification_state=VerificationState.VERIFIED,
        )
        firewall_result = MemoryFirewallResult(
            firewall_result_id=_stable_id(request.activation_id, "firewall-result", prefix="fwres"),
            firewall_request_id=_stable_id(request.activation_id, "firewall-request", prefix="fwreq"),
            owner_id=request.owner_id,
            created_at=request.created_at,
            allowed_memory_ids=selected,
            denied_fields=("raw_archive", "full_history", "secrets"),
            redacted_fields=("content",) if redacted else (),
            minimization_summary="Only selected source IDs, temporal state, provenance and short policy-safe summaries are admitted.",
            provider_context=minimized if status == GovernanceStatus.ALLOW else {},
            provenance=provenance,
            governance_status=status,
        )
        selection = MemoryContextSelection(
            selection_id=_stable_id(request.activation_id, "selection", prefix="selection"),
            activation_id=request.activation_id,
            owner_id=request.owner_id,
            created_at=request.created_at,
            decisions=tuple(decisions),
            selected_candidate_ids=selected,
            internal_context_candidate_ids=internal,
            user_visible_candidate_ids=user_visible,
            redacted_candidate_ids=redacted,
            blocked_candidate_ids=blocked,
            reasoning_context=minimized,
            explanation="Contextual memory activation passed through the Memory Firewall." if selected else "No policy-permitted contextual memory was activated.",
            provenance=provenance,
        )
        return selection, firewall_result


# ---------------------------------------------------------------------------
# Personal Continuity Engine
# ---------------------------------------------------------------------------


class PersonalContinuityEngine:
    def __init__(
        self,
        store: PersonalContinuityStore,
        adapter: DocumentedMemoryOSAdapter,
        *,
        embedding_provider: LocalConceptEmbeddingProvider | None = None,
        firewall: MemoryFirewall | None = None,
    ) -> None:
        self.store = store
        self.adapter = adapter
        self.embedding_provider = embedding_provider or LocalConceptEmbeddingProvider()
        self.firewall = firewall or MemoryFirewall()

    def start_conversation(
        self,
        context: MemoryAccessContext,
        *,
        conversation_id: str,
        started_at: datetime,
        title: str = "",
        topic: str = "",
        participant: str = "user",
        branch_id: str | None = None,
        privacy_class: PrivacyClass = PrivacyClass.PERSONAL,
        project_id: str | None = None,
        explicit_remember: bool = False,
        temporary: bool = False,
    ) -> tuple[AdapterDecision, ConversationRecord | None]:
        decision = self.adapter.govern(
            context,
            operation="store_conversation",
            purpose=context.purpose,
            privacy_class=privacy_class,
            scope={"conversation_id": conversation_id, "project_id": project_id, "explicit_remember": explicit_remember, "temporary": temporary},
        )
        if decision.outcome != AdapterOutcome.ALLOW:
            return decision, None
        retention_mode = RetentionMode(decision.constraints.get("retention_mode", RetentionMode.DEFAULT_RETAIN.value))
        record = self.store.begin_conversation(
            owner_id=context.owner_id,
            conversation_id=conversation_id,
            started_at=started_at,
            title=title,
            topic=topic,
            participant=participant,
            branch_id=branch_id,
            privacy_class=privacy_class,
            retention_mode=retention_mode,
            project_id=project_id,
        )
        return decision, record

    def end_conversation(self, context: MemoryAccessContext, *, conversation_id: str, ended_at: datetime) -> None:
        self.store.end_conversation(context.owner_id, conversation_id, ended_at)

    def record_message(
        self,
        context: MemoryAccessContext,
        *,
        conversation_id: str,
        role: str,
        content: str,
        sequence: int,
        event_time: datetime,
        message_id: str | None = None,
        source_id: str | None = None,
        privacy_class: PrivacyClass = PrivacyClass.PERSONAL,
        project_id: str | None = None,
        branch_id: str | None = None,
        entity_ids: Sequence[str] = (),
        goal_ids: Sequence[str] = (),
        decision_ids: Sequence[str] = (),
        explicit_remember: bool = False,
        explicit_do_not_remember: bool = False,
        temporary: bool = False,
        local_display_time: str = "",
        timezone_name: str = "UTC",
        utc_offset_minutes: int = 0,
    ) -> tuple[AdapterDecision, SourceRecord | None, bool]:
        _ensure_utc(event_time, "event_time")
        message_id = message_id or f"msg-{sequence}"
        source_id = source_id or _stable_id(context.owner_id, conversation_id, message_id, prefix="src")
        retain, retention_mode, _reason = self.adapter.policy_engine.decide_retention(
            conversation_id=conversation_id,
            message_id=message_id,
            privacy_class=privacy_class,
            explicit_remember=explicit_remember,
            explicit_do_not_remember=explicit_do_not_remember,
            temporary=temporary,
            project_id=project_id,
        )
        if not retain:
            decision = self.adapter.govern(
                context,
                operation="store_source",
                purpose=context.purpose,
                privacy_class=privacy_class,
                scope={
                    "conversation_id": conversation_id,
                    "message_id": message_id,
                    "source_id": source_id,
                    "explicit_do_not_remember": explicit_do_not_remember,
                    "temporary": temporary,
                    "project_id": project_id,
                },
            )
            return decision, None, False
        provenance = {
            "source_id": source_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
            "role": role,
            "event_time": _iso(event_time),
            "ingested_at": _iso(utc_now()),
            "retention_mode": retention_mode.value,
            "schema_version": PHASE3B_SCHEMA_VERSION,
        }
        record = SourceRecord(
            source_id=source_id,
            owner_id=context.owner_id,
            conversation_id=conversation_id,
            message_id=message_id,
            role=role,
            content=content,
            sequence=sequence,
            event_time=event_time,
            ingested_at=utc_now(),
            privacy_class=privacy_class,
            retention_mode=retention_mode,
            provenance=provenance,
            lifecycle_state=LifecycleState.CURRENT,
            project_id=project_id,
            branch_id=branch_id,
            entity_ids=tuple(entity_ids),
            goal_ids=tuple(goal_ids),
            decision_ids=tuple(decision_ids),
            local_display_time=local_display_time,
            timezone_name=timezone_name,
            utc_offset_minutes=utc_offset_minutes,
        )
        result = self.adapter.store_source(context, record, explicit_remember=explicit_remember, explicit_do_not_remember=explicit_do_not_remember, temporary=temporary)
        if result[1] is not None:
            self._create_source_timeline_event(result[1])
        return result

    def ingest_conversation(
        self,
        context: MemoryAccessContext,
        *,
        conversation_id: str,
        messages: Sequence[Mapping[str, Any]],
        started_at: datetime,
        title: str = "",
        topic: str = "",
        project_id: str | None = None,
        privacy_class: PrivacyClass = PrivacyClass.PERSONAL,
    ) -> tuple[SourceRecord, ...]:
        decision, conv = self.start_conversation(
            context,
            conversation_id=conversation_id,
            started_at=started_at,
            title=title,
            topic=topic,
            privacy_class=privacy_class,
            project_id=project_id,
        )
        if decision.outcome != AdapterOutcome.ALLOW or conv is None:
            return ()
        stored: list[SourceRecord] = []
        for idx, message in enumerate(messages, start=1):
            event_time = message.get("event_time", started_at + timedelta(seconds=idx))
            result = self.record_message(
                context,
                conversation_id=conversation_id,
                role=str(message.get("role", "user")),
                content=str(message.get("content", "")),
                sequence=int(message.get("sequence", idx)),
                event_time=event_time,
                message_id=message.get("message_id"),
                privacy_class=PrivacyClass(message.get("privacy_class", privacy_class.value)) if isinstance(message.get("privacy_class", privacy_class.value), str) else message.get("privacy_class", privacy_class),
                project_id=message.get("project_id", project_id),
                entity_ids=tuple(message.get("entity_ids", ())),
                goal_ids=tuple(message.get("goal_ids", ())),
                decision_ids=tuple(message.get("decision_ids", ())),
            )
            if result[1] is not None:
                stored.append(result[1])
        return tuple(stored)

    def _create_source_timeline_event(self, record: SourceRecord) -> None:
        event = TimelineEventRecord(
            timeline_id=_stable_id(record.owner_id, record.source_id, "message-event", prefix="timeline"),
            owner_id=record.owner_id,
            event_type="conversation_message",
            title=f"{record.role} message in {record.conversation_id}",
            description=_redact_content(record.content, 220),
            source_ids=(record.source_id,),
            event_time=record.event_time,
            created_at=utc_now(),
            provenance={"source_id": record.source_id, "conversation_id": record.conversation_id, "message_id": record.message_id},
            project_id=record.project_id,
        )
        self.store.store_timeline_event(event)

    def retrieve(self, context: MemoryAccessContext, query: ContinuityQuery) -> RetrievalResponse:
        return self.adapter.retrieve(context, query, self)

    def _retrieve_after_governance(self, context: MemoryAccessContext, query: ContinuityQuery, decision: AdapterDecision) -> RetrievalResponse:
        mode_status: dict[str, str] = {mode.value: "AVAILABLE" for mode in query.modes}
        calendar_timezone = query.calendar_timezone or context.owner_calendar_timezone or "UTC"
        mode_status["calendar_timezone"] = calendar_timezone
        if query.calendar_timezone:
            mode_status["calendar_timezone_source"] = "query_override"
        elif context.owner_calendar_timezone:
            mode_status["calendar_timezone_source"] = "owner_context"
        else:
            mode_status["calendar_timezone_source"] = "utc_fallback"
        if RetrievalModeName.SEMANTIC in query.modes:
            mode_status[RetrievalModeName.SEMANTIC.value] = "AVAILABLE:" + self.embedding_provider.provider_id if self.embedding_provider.available else "UNAVAILABLE"
        scores: dict[str, float] = {}
        records: dict[str, SourceRecord] = {}

        # Temporal/date expressions are normalized before candidate retrieval.
        if not query.temporal_start and not query.temporal_end:
            parsed = parse_date_expression(query.query_text, calendar_timezone)
            if parsed:
                query = ContinuityQuery(
                    query_text=query.query_text,
                    modes=query.modes,
                    temporal_start=parsed[0],
                    temporal_end=parsed[1],
                    calendar_timezone=calendar_timezone,
                    source_ids=query.source_ids,
                    message_ids=query.message_ids,
                    conversation_id=query.conversation_id,
                    project_id=query.project_id,
                    entity_ids=query.entity_ids,
                    goal_ids=query.goal_ids,
                    decision_ids=query.decision_ids,
                    include_deleted=query.include_deleted,
                    limit=query.limit,
                )

        if RetrievalModeName.EXACT in query.modes or RetrievalModeName.TEMPORAL in query.modes or query.source_ids or query.message_ids or query.conversation_id:
            for record in self.store.candidate_sources(context.owner_id, query, limit=max(query.limit, 50)):
                records[record.source_id] = record
                scores[record.source_id] = max(scores.get(record.source_id, 0.0), 0.85 if (query.source_ids or query.message_ids or query.temporal_start) else 0.4)

        if RetrievalModeName.LEXICAL in query.modes and query.query_text:
            query_tokens = _token_set(query.query_text)
            for record in self.store.lexical_sources(context.owner_id, query.query_text, include_deleted=query.include_deleted, limit=max(query.limit, 50)):
                if query.project_id and record.project_id != query.project_id:
                    continue
                overlap = len(query_tokens & _token_set(record.content))
                records[record.source_id] = record
                scores[record.source_id] = max(scores.get(record.source_id, 0.0), min(0.75, 0.25 + 0.1 * overlap))

        if RetrievalModeName.RELATIONAL in query.modes and (query.project_id or query.entity_ids or query.goal_ids or query.decision_ids):
            broader = ContinuityQuery(
                query_text=query.query_text,
                modes=(RetrievalModeName.RELATIONAL,),
                temporal_start=query.temporal_start,
                temporal_end=query.temporal_end,
                calendar_timezone=query.calendar_timezone,
                project_id=query.project_id,
                include_deleted=query.include_deleted,
                limit=max(query.limit, 100),
            )
            for record in self.store.candidate_sources(context.owner_id, broader, limit=max(query.limit, 100)):
                signal = 0.0
                if query.project_id and record.project_id == query.project_id:
                    signal += 0.35
                if set(query.entity_ids) & set(record.entity_ids):
                    signal += 0.25
                if set(query.goal_ids) & set(record.goal_ids):
                    signal += 0.25
                if set(query.decision_ids) & set(record.decision_ids):
                    signal += 0.25
                if signal > 0:
                    records[record.source_id] = record
                    scores[record.source_id] = max(scores.get(record.source_id, 0.0), signal)

        if RetrievalModeName.SEMANTIC in query.modes and self.embedding_provider.available and query.query_text:
            semantic_query = ContinuityQuery(
                query_text=query.query_text,
                modes=(RetrievalModeName.SEMANTIC,),
                temporal_start=query.temporal_start,
                temporal_end=query.temporal_end,
                calendar_timezone=query.calendar_timezone,
                project_id=query.project_id,
                include_deleted=query.include_deleted,
                limit=max(query.limit, 200),
            )
            # Staged retrieval: never load the whole archive. Pull bounded candidates through structured indexes first.
            for record in self.store.candidate_sources(context.owner_id, semantic_query, limit=max(query.limit, 200)):
                sim = self.embedding_provider.similarity(query.query_text, record.content)
                if sim >= 0.18:
                    records[record.source_id] = record
                    scores[record.source_id] = max(scores.get(record.source_id, 0.0), sim)

        if RetrievalModeName.CAUSAL in query.modes or _contains_any(query.query_text, ("why", "led to", "what changed", "choose", "chose", "decided")):
            derived = self.store.search_derived(context.owner_id, query, limit=max(query.limit, 40))
            for mem in derived:
                if mem.kind in (DerivedMemoryKind.DECISION, DerivedMemoryKind.EVENT, DerivedMemoryKind.OUTCOME, DerivedMemoryKind.LESSON):
                    source = self.store.get_source(context.owner_id, mem.source_id)
                    if source:
                        records[source.source_id] = source
                        scores[source.source_id] = max(scores.get(source.source_id, 0.0), 0.7)
            mode_status[RetrievalModeName.CAUSAL.value] = "AVAILABLE"

        ordered = sorted(records.values(), key=lambda r: (scores.get(r.source_id, 0.0), r.event_time, -r.sequence), reverse=True)[: query.limit]
        source_ids = [r.source_id for r in ordered]
        derived_records = self.store.derived_for_sources(context.owner_id, source_ids)
        timeline_events = self.store.timeline_for_sources(context.owner_id, source_ids)
        self.store.observe(context.owner_id, "memory.retrieve", {"query_hash": hashlib.sha256(query.query_text.encode()).hexdigest()[:16], "count": len(ordered)})
        return RetrievalResponse(
            status=AdapterOutcome.ALLOW,
            decision=decision,
            query=query,
            source_records=tuple(ordered),
            derived_memories=derived_records,
            timeline_events=timeline_events,
            source_scores={sid: scores.get(sid, 0.0) for sid in source_ids},
            mode_status=mode_status,
            message="governed retrieval completed",
        )

    def answer_historical_query(self, context: MemoryAccessContext, question: str) -> HistoricalAnswer:
        calendar_timezone = context.owner_calendar_timezone or "UTC"
        parsed = parse_date_expression(question, calendar_timezone)
        query = ContinuityQuery(
            query_text=question,
            modes=(RetrievalModeName.EXACT, RetrievalModeName.TEMPORAL, RetrievalModeName.LEXICAL),
            temporal_start=parsed[0] if parsed else None,
            temporal_end=parsed[1] if parsed else None,
            calendar_timezone=calendar_timezone,
            limit=50,
        )
        retrieval = self.retrieve(context, query)
        if retrieval.status != AdapterOutcome.ALLOW:
            return HistoricalAnswer(question, retrieval.status, (), "Memory retrieval is unavailable.", retrieval.message, (), retrieval.mode_status)
        ordered = tuple(sorted(retrieval.source_records, key=lambda r: (r.event_time, r.sequence)))
        if not ordered:
            return HistoricalAnswer(question, AdapterOutcome.ALLOW, (), "No source-backed conversation records matched the requested time.", "No inference was made because no source record matched.", (), retrieval.mode_status)
        user_messages = [r.content for r in ordered if r.role.lower() == "user"]
        summary_basis = user_messages or [r.content for r in ordered]
        summary = "Source-backed summary: " + _redact_content(" ".join(summary_basis), 500)
        inference = "This answer is based on exact source messages, not generated summaries alone."
        return HistoricalAnswer(question, AdapterOutcome.ALLOW, ordered, summary, inference, tuple(r.source_id for r in ordered), retrieval.mode_status)

    def derive_from_conversation(self, context: MemoryAccessContext, conversation_id: str, *, fail_after: int | None = None) -> tuple[DerivedMemoryRecord, ...]:
        decision = self.adapter.govern(context, operation="derive", purpose="structured memory extraction", scope={"conversation_id": conversation_id})
        if decision.outcome != AdapterOutcome.ALLOW:
            return ()
        sources = self.store.list_sources_for_conversation(context.owner_id, conversation_id)
        derived: list[DerivedMemoryRecord] = []
        for idx, source in enumerate(sources, start=1):
            derived.extend(self._derive_from_source(source))
            if fail_after is not None and idx >= fail_after:
                raise RuntimeError("simulated derivation crash after source persistence")
        return tuple(derived)

    def _derive_from_source(self, source: SourceRecord) -> tuple[DerivedMemoryRecord, ...]:
        text = source.content.strip()
        low = text.lower()
        records: list[DerivedMemoryRecord] = []
        # Keep extraction conservative and source-backed.
        if _contains_any(low, ("long-term", "long term", "persistent", "continuity", "memory", "architecture", "vision")) and "zorq" in low:
            records.append(self.store_derived_memory_from_source(source, DerivedMemoryKind.PROJECT_FACT, "ZORQ continuity/architecture vision discussed: " + _redact_content(text, 180), "project-fact:zorq-continuity", source.project_id or "project-zorq", source.entity_ids or ("entity-zorq",), source.goal_ids or ("goal-continuity",), source.decision_ids or ("decision-architecture",)))
        m = re.search(r"\bi prefer\s+(.+?)(?:[.!?]|$)", low)
        if m:
            thing = _safe_id(m.group(1), prefix="pref")
            records.append(self.store_derived_memory_from_source(source, DerivedMemoryKind.PREFERENCE, "User preferred " + m.group(1).strip(), "preference:" + thing, source.project_id, source.entity_ids, source.goal_ids, source.decision_ids))
        m = re.search(r"\bi use\s+(.+?)(?:[.!?]|$)", low)
        if m:
            thing = _safe_id(m.group(1), prefix="use")
            records.append(self.store_derived_memory_from_source(source, DerivedMemoryKind.PERSONAL_FACT, "User used " + m.group(1).strip(), "use:" + thing, source.project_id, source.entity_ids, source.goal_ids, source.decision_ids))
        m = re.search(r"\b(?:i stopped using|i no longer use|i don't use|i do not use)\s+(.+?)(?:[.!?]|$)", low)
        if m:
            thing = _safe_id(m.group(1), prefix="use")
            claim_key = "use:" + thing
            previous = self.store.find_current_derived_by_claim(source.owner_id, claim_key)
            supersedes_id = previous[0].memory_id if previous else None
            records.append(self.store_derived_memory_from_source(source, DerivedMemoryKind.PERSONAL_FACT, "User no longer used " + m.group(1).strip(), claim_key, source.project_id, source.entity_ids, source.goal_ids, source.decision_ids, supersedes_id=supersedes_id))
        if _contains_any(low, ("we decided", "we chose", "decision", "chose to", "choose to")):
            records.append(self.store_derived_memory_from_source(source, DerivedMemoryKind.DECISION, "Decision evidence: " + _redact_content(text, 180), "decision:" + _safe_id(text[:60], prefix="decision"), source.project_id, source.entity_ids, source.goal_ids, source.decision_ids or ("decision-architecture",)))
        return tuple(records)

    def store_derived_memory_from_source(
        self,
        source: SourceRecord,
        kind: DerivedMemoryKind,
        derived_text: str,
        claim_key: str,
        project_id: str | None,
        entity_ids: Sequence[str],
        goal_ids: Sequence[str],
        decision_ids: Sequence[str],
        *,
        supersedes_id: str | None = None,
        conflict_group_id: str | None = None,
        lifecycle_state: LifecycleState = LifecycleState.CURRENT,
    ) -> DerivedMemoryRecord:
        previous = self.store.find_current_derived_by_claim(source.owner_id, claim_key)
        if previous and supersedes_id is None and all(mem.text != derived_text for mem in previous):
            # Preserve both and mark conflict unless the caller supplied supersession.
            conflict_group_id = conflict_group_id or _stable_id(source.owner_id, claim_key, "conflict", prefix="conflict")
            self.store.mark_conflicted(source.owner_id, [mem.memory_id for mem in previous], conflict_group_id)
            lifecycle_state = LifecycleState.CONFLICTED
        memory = DerivedMemoryRecord(
            memory_id=_stable_id(source.owner_id, source.source_id, kind.value, claim_key, derived_text, prefix="mem"),
            owner_id=source.owner_id,
            kind=kind,
            text=derived_text,
            claim_key=claim_key,
            source_id=source.source_id,
            conversation_id=source.conversation_id,
            message_id=source.message_id,
            event_time=source.event_time,
            created_at=utc_now(),
            valid_from=source.event_time,
            valid_until=None,
            lifecycle_state=lifecycle_state,
            privacy_class=source.privacy_class,
            confidence=0.72,
            provenance={"source_id": source.source_id, "conversation_id": source.conversation_id, "message_id": source.message_id, "derivation": "deterministic-phase3b-extractor-v1"},
            project_id=project_id,
            entity_ids=tuple(entity_ids),
            goal_ids=tuple(goal_ids),
            decision_ids=tuple(decision_ids),
            supersedes_id=supersedes_id,
            conflict_group_id=conflict_group_id,
        )
        return self.store.store_derived_memory(memory)

    def answer_current_or_historical_state(self, context: MemoryAccessContext, query_text: str, claim_key: str) -> HistoricalAnswer:
        memories = self.store.find_current_derived_by_claim(context.owner_id, claim_key)
        all_query = ContinuityQuery(query_text=query_text, modes=(RetrievalModeName.EXACT, RetrievalModeName.SEMANTIC), calendar_timezone=context.owner_calendar_timezone, limit=20)
        retrieval = self.retrieve(context, all_query)
        if "back then" in query_text.lower() or "historical" in query_text.lower():
            relevant = [m for m in retrieval.derived_memories if m.claim_key == claim_key]
        else:
            relevant = list(memories)
        source_ids = tuple(m.source_id for m in relevant)
        sources = tuple(r for r in retrieval.source_records if r.source_id in source_ids)
        if not relevant:
            return HistoricalAnswer(query_text, retrieval.status, (), "No source-backed current state is available.", "Evidence is insufficient.", (), retrieval.mode_status)
        summary = "; ".join(f"{m.lifecycle_state.value}: {m.text}" for m in relevant)
        return HistoricalAnswer(query_text, retrieval.status, sources, summary, "Derived memories preserve source provenance and temporal lifecycle state.", source_ids, retrieval.mode_status)

    def activate_contextual_memory(
        self,
        context: MemoryAccessContext,
        *,
        current_message: str,
        conversation_id: str,
        created_at: datetime,
        project_id: str | None = None,
        branch_id: str | None = None,
        trigger: Any = None,
        limit: int = 12,
    ) -> ActivationRuntimeResult:
        _ensure_utc(created_at, "created_at")
        frame = self._build_context_frame(context, current_message, conversation_id, created_at, project_id, branch_id)
        trigger_value = trigger or MemoryActivationTrigger.SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION
        request = MemoryActivationRequest(
            activation_id=_stable_id(context.owner_id, conversation_id, current_message, _iso(created_at), prefix="activation"),
            owner_id=context.owner_id,
            principal_id=context.principal_id,
            created_at=created_at,
            conversation_id=conversation_id,
            current_context=frame,
            purpose=context.purpose or "contextual memory activation",
            task_scope={"project_id": project_id, "conversation_id": conversation_id},
            requested_memory_scope={"staged": True, "limit": limit, "project_id": project_id},
            privacy_class=PrivacyClass.PERSONAL,
            provider_trust_class=context.provider_trust_class,
            trigger=trigger_value,
            temporal_context=TemporalExtent(event_time=created_at, created_at=created_at),
        )
        query = ContinuityQuery(
            query_text=current_message,
            modes=(RetrievalModeName.RELATIONAL, RetrievalModeName.SEMANTIC, RetrievalModeName.LEXICAL, RetrievalModeName.CAUSAL),
            project_id=project_id,
            entity_ids=frame.entity_ids,
            goal_ids=frame.goal_ids,
            decision_ids=frame.decision_ids,
            calendar_timezone=context.owner_calendar_timezone,
            limit=limit * 4,
        )
        retrieval = self.retrieve(context, query)
        if retrieval.status != AdapterOutcome.ALLOW:
            empty_selection = MemoryContextSelection(
                selection_id=_stable_id(request.activation_id, "empty", prefix="selection"),
                activation_id=request.activation_id,
                owner_id=context.owner_id,
                created_at=created_at,
                decisions=(),
                explanation="Memory retrieval unavailable; continued without contextual memory.",
            )
            return ActivationRuntimeResult(retrieval.status, request, retrieval, empty_selection, (), (), (), (), (), (), {}, retrieval.message)
        activation_decision = self.adapter.govern(context, operation="activate", purpose=request.purpose, scope={"activation_id": request.activation_id})
        candidates: list[MemoryActivationCandidate] = []
        decisions: list[MemoryActivationDecision] = []
        for source in retrieval.source_records[:limit]:
            candidate, score, signal_count = self._candidate_from_source(request, source, current_message, created_at, retrieval.source_scores.get(source.source_id, 0.0))
            candidates.append(candidate)
            if activation_decision.outcome != AdapterOutcome.ALLOW:
                state = MemoryActivationDecisionState.BLOCKED_BY_POLICY
                policy = MemoryActivationPolicyDecision.BLOCK
                decisions.append(
                    MemoryActivationDecision(
                        decision_id=_stable_id(candidate.candidate_id, "decision", prefix="actdec"),
                        activation_id=request.activation_id,
                        owner_id=context.owner_id,
                        candidate=candidate,
                        created_at=created_at,
                        decision_state=state,
                        policy_decision=policy,
                        governance_status=activation_decision.governance_status,
                        reason=activation_decision.reason,
                    )
                )
                continue
            if candidate.sensitivity in (PrivacyClass.SECRET, PrivacyClass.SYSTEM_SECURITY, PrivacyClass.REGULATED):
                decisions.append(
                    MemoryActivationDecision(
                        decision_id=_stable_id(candidate.candidate_id, "privacy-block", prefix="actdec"),
                        activation_id=request.activation_id,
                        owner_id=context.owner_id,
                        candidate=candidate,
                        created_at=created_at,
                        decision_state=MemoryActivationDecisionState.BLOCKED_BY_PRIVACY,
                        policy_decision=MemoryActivationPolicyDecision.BLOCK,
                        governance_status=GovernanceStatus.ALLOW,
                        reason="sensitive memory relevance alone does not justify activation or surfacing",
                    )
                )
            elif candidate.sensitivity == PrivacyClass.SENSITIVE:
                if candidate.relevance in (MemoryRelevanceLevel.HIGH, MemoryRelevanceLevel.MEDIUM) and signal_count >= 2:
                    decisions.append(
                        MemoryActivationDecision(
                            decision_id=_stable_id(candidate.candidate_id, "redact", prefix="actdec"),
                            activation_id=request.activation_id,
                            owner_id=context.owner_id,
                            candidate=candidate,
                            created_at=created_at,
                            decision_state=MemoryActivationDecisionState.ACTIVATED,
                            policy_decision=MemoryActivationPolicyDecision.REDACT,
                            governance_status=GovernanceStatus.ALLOW,
                            reason="sensitive memory is relevant but details must be redacted",
                            activated_for_internal_reasoning=True,
                            user_visible_mention_allowed=False,
                            explanation="A related sensitive memory exists, but details are redacted.",
                        )
                    )
            elif candidate.relevance == MemoryRelevanceLevel.HIGH and signal_count >= 2:
                decisions.append(
                    MemoryActivationDecision(
                        decision_id=_stable_id(candidate.candidate_id, "show", prefix="actdec"),
                        activation_id=request.activation_id,
                        owner_id=context.owner_id,
                        candidate=candidate,
                        created_at=created_at,
                        decision_state=MemoryActivationDecisionState.ACTIVATED,
                        policy_decision=MemoryActivationPolicyDecision.SHOW,
                        governance_status=GovernanceStatus.ALLOW,
                        reason="multi-dimensional relevance passed governance and temporal checks",
                        activated_for_internal_reasoning=True,
                        user_visible_mention_allowed=True,
                        explanation=self.activation_explanation(source),
                    )
                )
            elif candidate.relevance == MemoryRelevanceLevel.MEDIUM and signal_count >= 2:
                decisions.append(
                    MemoryActivationDecision(
                        decision_id=_stable_id(candidate.candidate_id, "internal", prefix="actdec"),
                        activation_id=request.activation_id,
                        owner_id=context.owner_id,
                        candidate=candidate,
                        created_at=created_at,
                        decision_state=MemoryActivationDecisionState.ACTIVATED,
                        policy_decision=MemoryActivationPolicyDecision.USE_INTERNAL_ONLY,
                        governance_status=GovernanceStatus.ALLOW,
                        reason="relevant but not enough support for user-visible mention",
                        activated_for_internal_reasoning=True,
                        user_visible_mention_allowed=False,
                        explanation=self.activation_explanation(source),
                    )
                )
            else:
                decisions.append(
                    MemoryActivationDecision(
                        decision_id=_stable_id(candidate.candidate_id, "not-relevant", prefix="actdec"),
                        activation_id=request.activation_id,
                        owner_id=context.owner_id,
                        candidate=candidate,
                        created_at=created_at,
                        decision_state=MemoryActivationDecisionState.NOT_RELEVANT,
                        policy_decision=MemoryActivationPolicyDecision.BLOCK,
                        governance_status=GovernanceStatus.NOT_APPLICABLE,
                        reason="insufficient multi-dimensional relevance; no automatic activation",
                    )
                )
        selection, _fw = self.firewall.apply(context, request=request, decisions=decisions)
        activated = selection.selected_candidate_ids
        relevant = tuple(d.candidate.candidate_id for d in decisions if d.decision_state in (MemoryActivationDecisionState.ACTIVATED, MemoryActivationDecisionState.RELEVANT, MemoryActivationDecisionState.CONFLICTED, MemoryActivationDecisionState.STALE))
        explanation = self.explain_selection(selection)
        self.store.observe(context.owner_id, "memory.activation", {"activation_id": request.activation_id, "retrieved": len(retrieval.source_records), "activated": len(activated)})
        return ActivationRuntimeResult(
            AdapterOutcome.ALLOW,
            request,
            retrieval,
            selection,
            tuple(candidates),
            tuple(r.source_id for r in retrieval.source_records),
            relevant,
            activated,
            selection.internal_context_candidate_ids,
            selection.user_visible_candidate_ids,
            selection.reasoning_context,
            explanation,
        )

    def _build_context_frame(self, context: MemoryAccessContext, current_message: str, conversation_id: str, created_at: datetime, project_id: str | None, branch_id: str | None) -> CurrentContextFrame:
        low = current_message.lower()
        entity_ids: list[str] = []
        goal_ids: list[str] = []
        decision_ids: list[str] = []
        project_ids: list[str] = []
        if project_id:
            project_ids.append(project_id)
        if "zorq" in low:
            entity_ids.append("entity-zorq")
            if "project-zorq" not in project_ids:
                project_ids.append("project-zorq")
        if _contains_any(low, ("memory", "continuity", "coherent", "long-term", "history", "remember")):
            goal_ids.append("goal-continuity")
        if _contains_any(low, ("architecture", "redesign", "core", "decision", "choose", "chose")):
            decision_ids.append("decision-architecture")
        return CurrentContextFrame(
            context_id=_stable_id(context.owner_id, conversation_id, current_message, _iso(created_at), prefix="ctx"),
            owner_id=context.owner_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            created_at=created_at,
            current_topic=_redact_content(current_message, 120),
            active_question=current_message if "?" in current_message else "",
            entity_ids=tuple(dict.fromkeys(entity_ids)),
            project_ids=tuple(dict.fromkeys(project_ids)),
            goal_ids=tuple(dict.fromkeys(goal_ids)),
            decision_ids=tuple(dict.fromkeys(decision_ids)),
            current_assumptions=("Phase 2.6 Action Plane remains separate from memory",),
            active_conversation_branch=branch_id or "main",
            temporal_context=TemporalExtent(event_time=created_at, created_at=created_at),
        )

    def _candidate_from_source(self, request: MemoryActivationRequest, source: SourceRecord, current_message: str, created_at: datetime, retrieval_score: float) -> tuple[MemoryActivationCandidate, float, int]:
        signals: list[MemoryRelevanceSignal] = []
        score = 0.0
        signal_count = 0
        current_tokens = _token_set(current_message)
        source_tokens = _token_set(source.content)
        semantic = self.embedding_provider.similarity(current_message, source.content)
        if semantic >= 0.25 or retrieval_score >= 0.25:
            score += max(semantic, retrieval_score) * 0.4
            signal_count += 1
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.SEMANTIC_SIMILARITY, MemoryRelevanceLevel.HIGH if semantic >= 0.45 else MemoryRelevanceLevel.MEDIUM, f"Semantic/concept relation score {semantic:.2f}"))
        if source.project_id and source.project_id in request.current_context.project_ids:
            score += 0.35
            signal_count += 1
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.PROJECT_OVERLAP, MemoryRelevanceLevel.HIGH, "same project"))
        if set(source.entity_ids) & set(request.current_context.entity_ids) or ("zorq" in current_tokens and "zorq" in source_tokens):
            score += 0.25
            signal_count += 1
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.ENTITY_OVERLAP, MemoryRelevanceLevel.HIGH, "same entity"))
        if set(source.goal_ids) & set(request.current_context.goal_ids) or ({"memory", "continuity", "persistent"} & current_tokens and {"memory", "continuity", "persistent"} & source_tokens):
            score += 0.2
            signal_count += 1
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.GOAL_OVERLAP, MemoryRelevanceLevel.MEDIUM, "goal overlap"))
        if set(source.decision_ids) & set(request.current_context.decision_ids) or ({"architecture", "redesign", "core"} & current_tokens and {"architecture", "vision", "core"} & source_tokens):
            score += 0.2
            signal_count += 1
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.DECISION_OVERLAP, MemoryRelevanceLevel.MEDIUM, "decision/architecture overlap"))
        if _contains_any(current_message, ("why", "what led", "redesign", "architecture")) and _contains_any(source.content, ("vision", "because", "decided", "architecture", "long-term")):
            score += 0.15
            signal_count += 1
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.CAUSAL_RELATIONSHIP, MemoryRelevanceLevel.MEDIUM, "historical source may explain current architecture discussion"))
        if source.event_time < created_at - timedelta(days=30):
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.TEMPORAL_RELEVANCE, MemoryRelevanceLevel.MEDIUM, "historical source predates current context"))
        if _contains_any(current_message, ("earlier", "previous", "last time", "back then")):
            score += 0.1
            signal_count += 1
            signals.append(self._signal(request, source.source_id, MemoryRelevanceSignalType.EXPLICIT_PRIOR_REFERENCE, MemoryRelevanceLevel.HIGH, "current message explicitly references prior context"))
        derived = self.store.derived_for_sources(source.owner_id, (source.source_id,))
        validity = MemoryValidity.HISTORICALLY_VALID
        state = LifecycleState.HISTORICAL if source.event_time.date() < created_at.date() else LifecycleState.CURRENT
        conflict_state = LifecycleState.HISTORICAL
        if any(d.lifecycle_state == LifecycleState.SUPERSEDED for d in derived):
            validity = MemoryValidity.SUPERSEDED
            state = LifecycleState.SUPERSEDED
        if any(d.lifecycle_state == LifecycleState.CONFLICTED for d in derived):
            validity = MemoryValidity.CONFLICTED
            conflict_state = LifecycleState.CONFLICTED
        if score >= 0.72 and signal_count >= 2:
            level = MemoryRelevanceLevel.HIGH
        elif score >= 0.38 and signal_count >= 2:
            level = MemoryRelevanceLevel.MEDIUM
        elif score > 0:
            level = MemoryRelevanceLevel.LOW
        else:
            level = MemoryRelevanceLevel.NONE
        provenance = Provenance(
            provenance_id=_stable_id(source.source_id, request.activation_id, prefix="prov"),
            owner_id=source.owner_id,
            source_type=SourceType.CONVERSATION_MESSAGE,
            source_id=source.source_id,
            created_at=request.created_at,
            observed_at=source.event_time,
            conversation_id=source.conversation_id,
            message_id=source.message_id,
            confidence=min(1.0, max(0.0, score)),
            verification_state=VerificationState.UNVERIFIED,
        )
        candidate = MemoryActivationCandidate(
            candidate_id=_stable_id(request.activation_id, source.source_id, prefix="candidate"),
            activation_id=request.activation_id,
            owner_id=source.owner_id,
            source_id=source.source_id,
            memory_id=derived[0].memory_id if derived else None,
            created_at=request.created_at,
            relevance=level,
            relevance_reasons=tuple(sig.explanation for sig in signals if sig.relevance != MemoryRelevanceLevel.NONE),
            semantic_relation=f"local concept score {semantic:.2f}",
            temporal_relation="historical" if state != LifecycleState.CURRENT else "current",
            provenance=provenance,
            entity_relation_ids=tuple(set(source.entity_ids) & set(request.current_context.entity_ids)) or (("entity-zorq",) if "zorq" in current_tokens and "zorq" in source_tokens else ()),
            project_relation_ids=(source.project_id,) if source.project_id and source.project_id in request.current_context.project_ids else (),
            goal_relation_ids=tuple(set(source.goal_ids) & set(request.current_context.goal_ids)),
            decision_relation_ids=tuple(set(source.decision_ids) & set(request.current_context.decision_ids)),
            historical_current_validity=validity,
            historical_current_state=state,
            conflict_state=conflict_state,
            sensitivity=source.privacy_class,
            relevance_signals=tuple(signals),
            temporal_extent=TemporalExtent(event_time=source.event_time, created_at=request.created_at, observed_at=source.event_time, ingested_at=source.ingested_at, valid_from=source.event_time),
            confidence=min(1.0, max(0.0, score)),
            governance_status=GovernanceStatus.ALLOW,
        )
        return candidate, score, signal_count

    def _signal(self, request: MemoryActivationRequest, source_id: str, signal_type: MemoryRelevanceSignalType, relevance: MemoryRelevanceLevel, explanation: str) -> MemoryRelevanceSignal:
        return MemoryRelevanceSignal(
            signal_id=_stable_id(request.activation_id, source_id, signal_type.value, explanation, prefix="signal"),
            owner_id=request.owner_id,
            created_at=request.created_at,
            signal_type=signal_type,
            relevance=relevance,
            explanation=explanation,
            evidence_refs=(source_id,),
            confidence={MemoryRelevanceLevel.HIGH: 0.85, MemoryRelevanceLevel.MEDIUM: 0.6, MemoryRelevanceLevel.LOW: 0.3, MemoryRelevanceLevel.NONE: 0.0, MemoryRelevanceLevel.UNCERTAIN: 0.2}[relevance],
        )

    def activation_explanation(self, source: SourceRecord) -> str:
        month = source.event_time.strftime("%B %Y")
        if source.project_id:
            return f"It was related to {source.project_id} context from {month}."
        return f"It was related to a source-backed conversation from {month}."

    def explain_selection(self, selection: MemoryContextSelection) -> str:
        if not selection.selected_candidate_ids:
            return "No sufficiently supported, policy-permitted historical memory was activated."
        visible = set(selection.user_visible_candidate_ids)
        for decision in selection.decisions:
            if decision.candidate.candidate_id in visible:
                return decision.explanation or "A source-backed historical memory was materially relevant."
        return "A governed historical memory was used internally without exposing restricted details."

    def delete_memory(self, context: MemoryAccessContext, *, scope_type: str, include_backups: bool = False, **scope: Any) -> DeletionReport:
        deletion_id = _stable_id(context.owner_id, scope_type, _json(scope), utc_now().isoformat(), prefix="delete")
        normalized_scope = dict(scope)
        if isinstance(normalized_scope.get("temporal_start"), datetime):
            normalized_scope["temporal_start"] = _iso(normalized_scope["temporal_start"])
        if isinstance(normalized_scope.get("temporal_end"), datetime):
            normalized_scope["temporal_end"] = _iso(normalized_scope["temporal_end"])
        return self.adapter.delete(context, deletion_id=deletion_id, scope_type=scope_type, scope=normalized_scope, include_backups=include_backups)

    def export_owner_memory(self, context: MemoryAccessContext) -> Mapping[str, Any] | None:
        decision, data = self.adapter.export(context)
        if decision.outcome != AdapterOutcome.ALLOW:
            return None
        return data

    def memory_cannot_authorize_action(self, remembered_instruction: str) -> bool:
        """Return False for all remembered instructions by design.

        The method exists as an executable guardrail for tests and callers: memory
        may inform reasoning/proposals, but it is not action authority.
        """
        _ = remembered_instruction
        return False


__all__ = [
    "AdapterDecision",
    "AdapterOutcome",
    "ActivationRuntimeResult",
    "ContinuityQuery",
    "DeletionReport",
    "DeletionRuntimeStatus",
    "DerivedMemoryKind",
    "DerivedMemoryRecord",
    "DocumentedMemoryOSAdapter",
    "HistoricalAnswer",
    "LocalConceptEmbeddingProvider",
    "MemoryAccessContext",
    "MemoryCapturePolicyEngine",
    "MemoryFirewall",
    "PersonalContinuityEngine",
    "PersonalContinuityStore",
    "RetrievalModeName",
    "RetrievalResponse",
    "SourceRecord",
    "TimelineEventRecord",
]
