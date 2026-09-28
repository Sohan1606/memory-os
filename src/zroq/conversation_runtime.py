"""ZORQ Phase 3C text-first conversational runtime.

This module connects the Phase 3B personal-continuity engine, contextual memory
activation, response cursor contracts, checkpoints, branches, and a
provider-neutral streaming interface into a bounded text conversational runtime.

It intentionally does not implement voice, STT, TTS, microphone input, browser
or GUI automation, background daemon behavior, production MEMORY//OS, or any
Action Plane execution path. Conversation state is never action authority.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Protocol, Sequence

from .domain_contracts import (
    ContractValidationError,
    ConversationCheckpoint,
    ConversationState,
    GenerationState,
    InteractionCommandType,
    InteractionControlCommand,
    InteractionTarget,
    PrivacyClass,
    ResponseCursor,
    ResumePolicy,
    RetentionOverride,
    SpeechState,
)
from .personal_continuity import (
    AdapterOutcome,
    ActivationRuntimeResult,
    ContinuityQuery,
    DeletionReport,
    DocumentedMemoryOSAdapter,
    HistoricalAnswer,
    MemoryAccessContext,
    PersonalContinuityEngine,
    PersonalContinuityStore,
    RetrievalModeName,
    RetrievalResponse,
    utc_now,
)

PHASE3C_SCHEMA_VERSION = "zorq.phase3c.conversation-runtime.v1"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ensure_utc(dt: datetime | None, field_name: str) -> None:
    if dt is not None and dt.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware UTC")
    if dt is not None and dt.utcoffset() is not None and dt.utcoffset().total_seconds() != 0:
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
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _json(data: Any) -> str:
    def convert(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {str(k): convert(v) for k, v in value.items()}
        if isinstance(value, (tuple, list)):
            return [convert(v) for v in value]
        if isinstance(value, set):
            return sorted(convert(v) for v in value)
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, datetime):
            return _iso(value)
        return value
    return json.dumps(convert(data), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


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


def _safe_branch(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value.strip().lower())[:80].strip("-._:")
    return value or "branch"


def _redact(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


# ---------------------------------------------------------------------------
# Streaming provider contract
# ---------------------------------------------------------------------------


class ResponseStreamEventType(str, Enum):
    RESPONSE_STARTED = "RESPONSE_STARTED"
    TEXT_DELTA = "TEXT_DELTA"
    MEMORY_REFERENCE = "MEMORY_REFERENCE"
    EVIDENCE_REFERENCE = "EVIDENCE_REFERENCE"
    RESPONSE_PAUSED = "RESPONSE_PAUSED"
    RESPONSE_INTERRUPTED = "RESPONSE_INTERRUPTED"
    RESPONSE_COMPLETED = "RESPONSE_COMPLETED"
    RESPONSE_FAILED = "RESPONSE_FAILED"
    RESPONSE_CANCELED = "RESPONSE_CANCELED"


@dataclass(frozen=True)
class ResponseStreamEvent:
    event_type: ResponseStreamEventType
    response_id: str
    owner_id: str
    conversation_id: str
    branch_id: str
    created_at: datetime
    text_delta: str = ""
    memory_id: str | None = None
    evidence_id: str | None = None
    message: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _ensure_utc(self.created_at, "created_at")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True)
class ConversationProviderRequest:
    owner_id: str
    conversation_id: str
    branch_id: str
    response_id: str
    user_text: str
    recent_messages: tuple[Mapping[str, Any], ...]
    memory_context: Mapping[str, Any]
    evidence_source_ids: tuple[str, ...]
    generated_prefix: str = ""
    resume_from_position: int = 0
    runtime_context: Mapping[str, Any] = field(default_factory=dict)
    generation_config: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "recent_messages", tuple(self.recent_messages))
        object.__setattr__(self, "memory_context", MappingProxyType(dict(self.memory_context)))
        object.__setattr__(self, "evidence_source_ids", tuple(self.evidence_source_ids))
        object.__setattr__(self, "runtime_context", MappingProxyType(dict(self.runtime_context)))
        object.__setattr__(self, "generation_config", MappingProxyType(dict(self.generation_config)))


class CancellationSignal:
    def __init__(self) -> None:
        self.canceled = False
        self.paused = False
        self.reason = ""

    def cancel(self, reason: str) -> None:
        self.canceled = True
        self.reason = reason

    def pause(self, reason: str) -> None:
        self.paused = True
        self.reason = reason


class ConversationModelProvider(Protocol):
    provider_id: str
    is_test_provider: bool

    def stream_response(self, request: ConversationProviderRequest, cancellation_signal: CancellationSignal) -> Iterable[ResponseStreamEvent]: ...


class DeterministicConversationProvider:
    """Deterministic test provider; not an intelligence model.

    Phase 3C.1 extends this provider with optional synchronization hooks for
    adversarial streaming tests. The hooks are test controls only: they can block
    before deltas/completion, intentionally ignore cooperative cancellation, and
    emit delayed reference events so the runtime can prove stale-event handling.
    """

    provider_id = "deterministic-test-conversation-provider"
    is_test_provider = True

    def __init__(
        self,
        script: Sequence[str] | None = None,
        *,
        fail: bool = False,
        block_before_delta: Mapping[int, tuple[Any, Any]] | None = None,
        block_before_completion: tuple[Any, Any] | None = None,
        ignore_cancellation: bool = False,
        late_events: Sequence[Mapping[str, Any]] = (),
        block_timeout_seconds: float = 5.0,
    ) -> None:
        self.script = tuple(script or ("I ", "can ", "continue ", "from ", "context."))
        self.fail = fail
        self.block_before_delta = dict(block_before_delta or {})
        self.block_before_completion = block_before_completion
        self.ignore_cancellation = ignore_cancellation
        self.late_events = tuple(dict(item) for item in late_events)
        self.block_timeout_seconds = block_timeout_seconds
        self.requests: list[ConversationProviderRequest] = []

    def _block(self, pair: tuple[Any, Any] | None) -> None:
        if not pair:
            return
        ready, release = pair
        if hasattr(ready, "set"):
            ready.set()
        if hasattr(release, "wait"):
            release.wait(self.block_timeout_seconds)

    def _cooperative_control_event(self, request: ConversationProviderRequest, cancellation_signal: CancellationSignal) -> ResponseStreamEvent | None:
        if self.ignore_cancellation:
            return None
        if cancellation_signal.canceled:
            return ResponseStreamEvent(ResponseStreamEventType.RESPONSE_CANCELED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), message=cancellation_signal.reason)
        if cancellation_signal.paused:
            return ResponseStreamEvent(ResponseStreamEventType.RESPONSE_PAUSED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), message=cancellation_signal.reason)
        return None

    def stream_response(self, request: ConversationProviderRequest, cancellation_signal: CancellationSignal) -> Iterable[ResponseStreamEvent]:
        self.requests.append(request)
        now = utc_now()
        yield ResponseStreamEvent(ResponseStreamEventType.RESPONSE_STARTED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, now, metadata={"provider_id": self.provider_id, "test_provider": True})
        if self.fail:
            yield ResponseStreamEvent(ResponseStreamEventType.RESPONSE_FAILED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), message="deterministic provider failure")
            return
        for item in request.memory_context.get("items", ())[:4]:
            control_event = self._cooperative_control_event(request, cancellation_signal)
            if control_event is not None:
                yield control_event
                return
            source_id = str(item.get("source_id", ""))
            memory_id = item.get("memory_id") or source_id
            if source_id or memory_id:
                yield ResponseStreamEvent(ResponseStreamEventType.MEMORY_REFERENCE, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), memory_id=str(memory_id), evidence_id=source_id or None, metadata={"source_id": source_id})
        full_script = "".join(self.script)
        if request.generated_prefix and full_script.startswith(request.generated_prefix):
            start = len(request.generated_prefix)
            chunks = []
            cursor = 0
            for part in self.script:
                next_cursor = cursor + len(part)
                if next_cursor > start:
                    chunks.append(part[max(0, start - cursor):])
                cursor = next_cursor
        elif request.generated_prefix:
            chunks = (" Continuing from the interrupted prefix.",)
        else:
            chunks = self.script
        for idx, chunk in enumerate(chunks):
            self._block(self.block_before_delta.get(idx))
            control_event = self._cooperative_control_event(request, cancellation_signal)
            if control_event is not None:
                yield control_event
                return
            if chunk:
                yield ResponseStreamEvent(ResponseStreamEventType.TEXT_DELTA, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), text_delta=chunk)
        for late in self.late_events:
            event_type = ResponseStreamEventType(str(late.get("event_type", ResponseStreamEventType.MEMORY_REFERENCE.value)))
            yield ResponseStreamEvent(
                event_type,
                request.response_id,
                request.owner_id,
                request.conversation_id,
                request.branch_id,
                utc_now(),
                text_delta=str(late.get("text_delta", "")),
                memory_id=late.get("memory_id"),
                evidence_id=late.get("evidence_id"),
                message=str(late.get("message", "")),
                metadata=late.get("metadata", {}),
            )
        self._block(self.block_before_completion)
        control_event = self._cooperative_control_event(request, cancellation_signal)
        if control_event is not None:
            yield control_event
            return
        yield ResponseStreamEvent(ResponseStreamEventType.RESPONSE_COMPLETED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now())


class UnavailableConversationProvider:
    provider_id = "unavailable-conversation-provider"
    is_test_provider = False

    def stream_response(self, request: ConversationProviderRequest, cancellation_signal: CancellationSignal) -> Iterable[ResponseStreamEvent]:
        yield ResponseStreamEvent(ResponseStreamEventType.RESPONSE_STARTED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), metadata={"provider_id": self.provider_id})
        yield ResponseStreamEvent(ResponseStreamEventType.RESPONSE_FAILED, request.response_id, request.owner_id, request.conversation_id, request.branch_id, utc_now(), message="conversation model provider unavailable")


# ---------------------------------------------------------------------------
# Runtime result records
# ---------------------------------------------------------------------------


class RuntimeCommandKind(str, Enum):
    NONE = "NONE"
    STOP = "STOP"
    PAUSE = "PAUSE"
    CONTINUE = "CONTINUE"
    RESUME = "RESUME"
    CANCEL = "CANCEL"
    REPEAT = "REPEAT"
    GO_BACK = "GO_BACK"
    SKIP = "SKIP"
    CHANGE_TOPIC = "CHANGE_TOPIC"
    REMEMBER_THIS = "REMEMBER_THIS"
    DO_NOT_REMEMBER = "DO_NOT_REMEMBER"
    FORGET_THIS = "FORGET_THIS"
    FORGET_CONVERSATION = "FORGET_CONVERSATION"
    EXPLICIT_RECALL = "EXPLICIT_RECALL"
    SHOW_MEMORY = "SHOW_MEMORY"
    CHECKPOINT = "CHECKPOINT"


class ResponseControlState(str, Enum):
    ACTIVE = "ACTIVE"
    STOP_REQUESTED = "STOP_REQUESTED"
    PAUSE_REQUESTED = "PAUSE_REQUESTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    RESUME_REQUESTED = "RESUME_REQUESTED"
    TERMINAL = "TERMINAL"


_CONTROL_PRECEDENCE: Mapping[ResponseControlState, int] = MappingProxyType(
    {
        ResponseControlState.ACTIVE: 0,
        ResponseControlState.RESUME_REQUESTED: 0,
        ResponseControlState.TERMINAL: 0,
        ResponseControlState.PAUSE_REQUESTED: 100,
        ResponseControlState.STOP_REQUESTED: 200,
        ResponseControlState.CANCEL_REQUESTED: 300,
    }
)


@dataclass(frozen=True)
class ConversationRuntimeResult:
    conversation_id: str
    owner_id: str
    branch_id: str
    state: ConversationState
    command: RuntimeCommandKind
    response_id: str | None = None
    response_text: str = ""
    events: tuple[ResponseStreamEvent, ...] = ()
    memory_activation: ActivationRuntimeResult | None = None
    historical_answer: HistoricalAnswer | None = None
    retrieval: RetrievalResponse | None = None
    deletion: DeletionReport | None = None
    checkpoint: ConversationCheckpoint | None = None
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))


@dataclass
class _ActiveStream:
    response_id: str
    request: ConversationProviderRequest
    iterator: Iterable[ResponseStreamEvent]
    cancellation: CancellationSignal
    control_epoch: int
    consuming: bool = False


# ---------------------------------------------------------------------------
# Runtime persistence
# ---------------------------------------------------------------------------


class ConversationRuntimeStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_runtime (
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    current_branch_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    active_topic TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    next_sequence INTEGER NOT NULL,
                    last_response_id TEXT,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(owner_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_branches (
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    parent_branch_id TEXT,
                    topic TEXT NOT NULL,
                    start_sequence INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    lifecycle_state TEXT NOT NULL,
                    PRIMARY KEY(owner_id, conversation_id, branch_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_responses (
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    branch_id TEXT NOT NULL,
                    response_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    generation_state TEXT NOT NULL,
                    speech_state TEXT NOT NULL,
                    generated_text TEXT NOT NULL,
                    text_position INTEGER NOT NULL,
                    semantic_position TEXT NOT NULL,
                    resume_policy TEXT NOT NULL,
                    referenced_memory_ids_json TEXT NOT NULL,
                    referenced_evidence_ids_json TEXT NOT NULL,
                    generation_config_json TEXT NOT NULL,
                    source_message_ids_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    resumable INTEGER NOT NULL DEFAULT 1,
                    control_state TEXT NOT NULL DEFAULT 'ACTIVE',
                    control_epoch INTEGER NOT NULL DEFAULT 0,
                    control_updated_at TEXT,
                    control_reason TEXT NOT NULL DEFAULT '',
                    control_requested_by TEXT NOT NULL DEFAULT '',
                    terminal_state TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(owner_id, response_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_checkpoints (
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    checkpoint_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(owner_id, conversation_id, checkpoint_id)
                );
                CREATE TABLE IF NOT EXISTS conversation_runtime_events (
                    event_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    branch_id TEXT,
                    response_id TEXT,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                """
            )
            self._ensure_response_control_columns(conn)

    def _ensure_response_control_columns(self, conn: sqlite3.Connection) -> None:
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(conversation_responses)").fetchall()}
        migrations = {
            "control_state": "ALTER TABLE conversation_responses ADD COLUMN control_state TEXT NOT NULL DEFAULT 'ACTIVE'",
            "control_epoch": "ALTER TABLE conversation_responses ADD COLUMN control_epoch INTEGER NOT NULL DEFAULT 0",
            "control_updated_at": "ALTER TABLE conversation_responses ADD COLUMN control_updated_at TEXT",
            "control_reason": "ALTER TABLE conversation_responses ADD COLUMN control_reason TEXT NOT NULL DEFAULT ''",
            "control_requested_by": "ALTER TABLE conversation_responses ADD COLUMN control_requested_by TEXT NOT NULL DEFAULT ''",
            "terminal_state": "ALTER TABLE conversation_responses ADD COLUMN terminal_state TEXT NOT NULL DEFAULT ''",
        }
        for name, sql in migrations.items():
            if name not in existing:
                conn.execute(sql)

    def event(self, owner_id: str, conversation_id: str, event_type: str, *, branch_id: str | None = None, response_id: str | None = None, metadata: Mapping[str, Any] | None = None) -> None:
        metadata = {k: v for k, v in dict(metadata or {}).items() if k not in {"content", "raw_content", "secret"}}
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversation_runtime_events(event_id, owner_id, conversation_id, branch_id, response_id, event_type, created_at, metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                (_stable_id(owner_id, conversation_id, event_type, utc_now().isoformat(), prefix="crev"), owner_id, conversation_id, branch_id, response_id, event_type, _iso(utc_now()), _json(metadata)),
            )

    def start(self, owner_id: str, conversation_id: str, branch_id: str, topic: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT owner_id FROM conversation_runtime WHERE conversation_id=? AND owner_id!=?", (conversation_id, owner_id)).fetchone()
            if row:
                conn.execute("ROLLBACK")
                raise PermissionError("conversation_id belongs to a different owner")
            conn.execute(
                "INSERT OR IGNORE INTO conversation_runtime(owner_id, conversation_id, current_branch_id, state, active_topic, started_at, updated_at, next_sequence, last_response_id) VALUES(?,?,?,?,?,?,?,?,?)",
                (owner_id, conversation_id, branch_id, ConversationState.IDLE.value, topic, _iso(now), _iso(now), 1, None),
            )
            conn.execute(
                "INSERT OR IGNORE INTO conversation_branches(owner_id, conversation_id, branch_id, parent_branch_id, topic, start_sequence, created_at, lifecycle_state) VALUES(?,?,?,?,?,?,?,?)",
                (owner_id, conversation_id, branch_id, None, topic, 1, _iso(now), "CURRENT"),
            )
            conn.execute("COMMIT")
        self.event(owner_id, conversation_id, "conversation_started", branch_id=branch_id, metadata={"topic": topic})

    def conversation(self, owner_id: str, conversation_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM conversation_runtime WHERE owner_id=? AND conversation_id=?", (owner_id, conversation_id)).fetchone()

    def require_conversation(self, owner_id: str, conversation_id: str) -> sqlite3.Row:
        row = self.conversation(owner_id, conversation_id)
        if not row:
            with self._connect() as conn:
                cross = conn.execute("SELECT owner_id FROM conversation_runtime WHERE conversation_id=?", (conversation_id,)).fetchone()
            if cross:
                raise PermissionError("conversation belongs to a different owner")
            raise KeyError("conversation not found")
        return row

    def set_state(self, owner_id: str, conversation_id: str, state: ConversationState, *, last_response_id: str | None = None) -> None:
        with self._connect() as conn:
            if last_response_id is None:
                conn.execute("UPDATE conversation_runtime SET state=?, updated_at=? WHERE owner_id=? AND conversation_id=?", (state.value, _iso(utc_now()), owner_id, conversation_id))
            else:
                conn.execute("UPDATE conversation_runtime SET state=?, updated_at=?, last_response_id=? WHERE owner_id=? AND conversation_id=?", (state.value, _iso(utc_now()), last_response_id, owner_id, conversation_id))

    def next_sequence(self, owner_id: str, conversation_id: str) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT next_sequence FROM conversation_runtime WHERE owner_id=? AND conversation_id=?", (owner_id, conversation_id)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError("conversation not found")
            seq = int(row["next_sequence"])
            conn.execute("UPDATE conversation_runtime SET next_sequence=?, updated_at=? WHERE owner_id=? AND conversation_id=?", (seq + 1, _iso(utc_now()), owner_id, conversation_id))
            conn.execute("COMMIT")
        return seq

    def current_branch(self, owner_id: str, conversation_id: str) -> str:
        return str(self.require_conversation(owner_id, conversation_id)["current_branch_id"])

    def create_branch(self, owner_id: str, conversation_id: str, topic: str, parent_branch_id: str | None = None) -> str:
        parent = parent_branch_id or self.current_branch(owner_id, conversation_id)
        branch_id = _stable_id(owner_id, conversation_id, parent, topic, utc_now().isoformat(), prefix="branch")
        start_seq = int(self.require_conversation(owner_id, conversation_id)["next_sequence"])
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO conversation_branches(owner_id, conversation_id, branch_id, parent_branch_id, topic, start_sequence, created_at, lifecycle_state) VALUES(?,?,?,?,?,?,?,?)",
                (owner_id, conversation_id, branch_id, parent, topic, start_seq, _iso(now), "CURRENT"),
            )
            conn.execute("UPDATE conversation_runtime SET current_branch_id=?, active_topic=?, updated_at=? WHERE owner_id=? AND conversation_id=?", (branch_id, topic, _iso(now), owner_id, conversation_id))
        self.event(owner_id, conversation_id, "branch_created", branch_id=branch_id, metadata={"parent_branch_id": parent, "topic": topic})
        return branch_id

    def switch_branch(self, owner_id: str, conversation_id: str, branch_id: str) -> None:
        with self._connect() as conn:
            row = conn.execute("SELECT topic FROM conversation_branches WHERE owner_id=? AND conversation_id=? AND branch_id=?", (owner_id, conversation_id, branch_id)).fetchone()
            if not row:
                raise KeyError("branch not found")
            conn.execute("UPDATE conversation_runtime SET current_branch_id=?, active_topic=?, updated_at=? WHERE owner_id=? AND conversation_id=?", (branch_id, row["topic"], _iso(utc_now()), owner_id, conversation_id))
        self.event(owner_id, conversation_id, "branch_switched", branch_id=branch_id)

    def latest_response(self, owner_id: str, conversation_id: str) -> sqlite3.Row | None:
        conv = self.require_conversation(owner_id, conversation_id)
        rid = conv["last_response_id"]
        if not rid:
            return None
        with self._connect() as conn:
            return conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, rid)).fetchone()

    def response(self, owner_id: str, response_id: str) -> sqlite3.Row | None:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()

    def upsert_response(self, owner_id: str, conversation_id: str, branch_id: str, response_id: str, provider_id: str, state: GenerationState, speech_state: SpeechState, text: str, referenced_memory_ids: Sequence[str], referenced_evidence_ids: Sequence[str], generation_config: Mapping[str, Any], source_message_ids: Sequence[str], *, resumable: bool = True, resume_policy: ResumePolicy = ResumePolicy.SUMMARIZE_THEN_RESUME) -> ResponseCursor:
        now = utc_now()
        created = now
        existing = self.response(owner_id, response_id)
        if existing:
            created = _from_iso(existing["created_at"]) or now
        cursor = ResponseCursor(
            cursor_id=_stable_id(owner_id, response_id, "cursor", prefix="cursor"),
            response_id=response_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            owner_id=owner_id,
            created_at=created,
            updated_at=now,
            generation_state=state,
            speech_state=speech_state,
            text_position=len(text),
            semantic_position=f"char:{len(text)}",
            last_spoken_boundary="text-only:no-audio-boundary",
            resume_policy=resume_policy if resumable else ResumePolicy.DO_NOT_RESUME,
            referenced_memory_ids=tuple(referenced_memory_ids),
            referenced_evidence_ids=tuple(referenced_evidence_ids),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_responses(owner_id, conversation_id, branch_id, response_id, provider_id, generation_state, speech_state,
                    generated_text, text_position, semantic_position, resume_policy, referenced_memory_ids_json, referenced_evidence_ids_json,
                    generation_config_json, source_message_ids_json, created_at, updated_at, resumable)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(owner_id, response_id) DO UPDATE SET
                    generation_state=excluded.generation_state,
                    speech_state=excluded.speech_state,
                    generated_text=excluded.generated_text,
                    text_position=excluded.text_position,
                    semantic_position=excluded.semantic_position,
                    resume_policy=excluded.resume_policy,
                    referenced_memory_ids_json=excluded.referenced_memory_ids_json,
                    referenced_evidence_ids_json=excluded.referenced_evidence_ids_json,
                    generation_config_json=excluded.generation_config_json,
                    source_message_ids_json=excluded.source_message_ids_json,
                    updated_at=excluded.updated_at,
                    resumable=excluded.resumable
                """,
                (owner_id, conversation_id, branch_id, response_id, provider_id, state.value, speech_state.value, text, len(text), f"char:{len(text)}", cursor.resume_policy.value, _json(referenced_memory_ids), _json(referenced_evidence_ids), _json(generation_config), _json(source_message_ids), _iso(created), _iso(now), 1 if resumable else 0),
            )
            conn.execute("UPDATE conversation_runtime SET last_response_id=?, updated_at=? WHERE owner_id=? AND conversation_id=?", (response_id, _iso(now), owner_id, conversation_id))
        return cursor

    def cursor_from_response_row(self, row: sqlite3.Row) -> ResponseCursor:
        created = _from_iso(row["created_at"]) or utc_now()
        updated = _from_iso(row["updated_at"]) or created
        return ResponseCursor(
            cursor_id=_stable_id(row["owner_id"], row["response_id"], "cursor", prefix="cursor"),
            response_id=row["response_id"],
            conversation_id=row["conversation_id"],
            branch_id=row["branch_id"],
            owner_id=row["owner_id"],
            created_at=created,
            updated_at=updated,
            generation_state=GenerationState(row["generation_state"]),
            speech_state=SpeechState(row["speech_state"]),
            text_position=int(row["text_position"]),
            semantic_position=row["semantic_position"],
            last_spoken_boundary="text-only:no-audio-boundary",
            interruption_reason=row["control_reason"] or "",
            resume_policy=ResumePolicy(row["resume_policy"]),
            referenced_memory_ids=tuple(_loads(row["referenced_memory_ids_json"], [])),
            referenced_evidence_ids=tuple(_loads(row["referenced_evidence_ids_json"], [])),
        )

    def begin_stream_control(self, owner_id: str, response_id: str) -> int:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError("response not found")
            if row["control_state"] == ResponseControlState.CANCEL_REQUESTED.value:
                conn.execute("ROLLBACK")
                raise ContractValidationError("canceled response cannot begin a new stream")
            epoch = int(row["control_epoch"])
            conn.execute(
                "UPDATE conversation_responses SET control_state=?, control_updated_at=?, terminal_state='', generation_state=?, speech_state=?, updated_at=? WHERE owner_id=? AND response_id=?",
                (ResponseControlState.ACTIVE.value, _iso(now), GenerationState.GENERATING.value, SpeechState.SPEAKING.value, _iso(now), owner_id, response_id),
            )
            conn.execute("COMMIT")
        return epoch

    def response_is_current_for_stream(self, owner_id: str, response_id: str, expected_epoch: int) -> bool:
        row = self.response(owner_id, response_id)
        return bool(row and int(row["control_epoch"]) == expected_epoch and row["control_state"] == ResponseControlState.ACTIVE.value)

    def apply_response_control(
        self,
        owner_id: str,
        conversation_id: str,
        response_id: str,
        control_state: ResponseControlState,
        generation_state: GenerationState,
        speech_state: SpeechState,
        conversation_state: ConversationState,
        *,
        reason: str,
        requested_by: str,
        resumable: bool,
        resume_policy: ResumePolicy,
    ) -> tuple[sqlite3.Row, bool]:
        """Atomically persist an authoritative control decision.

        Returns `(row_after, changed)`. Lower-precedence or duplicate decisions are
        ignored rather than allowed to overwrite a stronger terminal decision.
        """
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError("response not found")
            current_control = ResponseControlState(row["control_state"])
            current_terminal = row["terminal_state"] or ""
            if current_control == ResponseControlState.TERMINAL and current_terminal in (GenerationState.COMPLETED.value, GenerationState.CANCELED.value):
                conn.execute("COMMIT")
                return row, False
            incoming_prec = _CONTROL_PRECEDENCE[control_state]
            current_prec = _CONTROL_PRECEDENCE[current_control]
            if incoming_prec < current_prec:
                conn.execute("COMMIT")
                return row, False
            if incoming_prec == current_prec and current_control == control_state and row["generation_state"] == generation_state.value:
                conn.execute("COMMIT")
                return row, False
            epoch = int(row["control_epoch"]) + 1
            terminal_state = generation_state.value
            conn.execute(
                """
                UPDATE conversation_responses
                SET control_state=?, control_epoch=?, control_updated_at=?, control_reason=?, control_requested_by=?, terminal_state=?,
                    generation_state=?, speech_state=?, text_position=length(generated_text), semantic_position=?, resume_policy=?,
                    resumable=?, updated_at=?
                WHERE owner_id=? AND response_id=?
                """,
                (
                    control_state.value,
                    epoch,
                    _iso(now),
                    reason,
                    requested_by,
                    terminal_state,
                    generation_state.value,
                    speech_state.value,
                    f"char:{len(row['generated_text'])}",
                    resume_policy.value if resumable else ResumePolicy.DO_NOT_RESUME.value,
                    1 if resumable else 0,
                    _iso(now),
                    owner_id,
                    response_id,
                ),
            )
            conn.execute(
                "UPDATE conversation_runtime SET state=?, updated_at=?, last_response_id=? WHERE owner_id=? AND conversation_id=?",
                (conversation_state.value, _iso(now), response_id, owner_id, conversation_id),
            )
            row_after = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            conn.execute("COMMIT")
        return row_after, True

    def request_resume_control(self, owner_id: str, conversation_id: str, response_id: str, *, reason: str, requested_by: str) -> tuple[sqlite3.Row, bool]:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            if not row:
                conn.execute("ROLLBACK")
                raise KeyError("response not found")
            generation = GenerationState(row["generation_state"])
            control = ResponseControlState(row["control_state"])
            if not int(row["resumable"]) or generation == GenerationState.CANCELED or control == ResponseControlState.CANCEL_REQUESTED:
                conn.execute("COMMIT")
                return row, False
            if generation == GenerationState.COMPLETED or (control == ResponseControlState.TERMINAL and row["terminal_state"] == GenerationState.COMPLETED.value):
                conn.execute("COMMIT")
                return row, False
            epoch = int(row["control_epoch"]) + 1
            conn.execute(
                """
                UPDATE conversation_responses
                SET control_state=?, control_epoch=?, control_updated_at=?, control_reason=?, control_requested_by=?, terminal_state='',
                    generation_state=?, speech_state=?, resume_policy=?, resumable=1, updated_at=?
                WHERE owner_id=? AND response_id=?
                """,
                (ResponseControlState.RESUME_REQUESTED.value, epoch, _iso(now), reason, requested_by, GenerationState.RESUMING.value, SpeechState.RESUMING.value, ResumePolicy.SUMMARIZE_THEN_RESUME.value, _iso(now), owner_id, response_id),
            )
            conn.execute("UPDATE conversation_runtime SET state=?, updated_at=?, last_response_id=? WHERE owner_id=? AND conversation_id=?", (ConversationState.RESUMING.value, _iso(now), response_id, owner_id, conversation_id))
            row_after = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            conn.execute("COMMIT")
        return row_after, True

    def try_persist_stream_delta(
        self,
        owner_id: str,
        conversation_id: str,
        branch_id: str,
        response_id: str,
        expected_epoch: int,
        provider_id: str,
        text: str,
        referenced_memory_ids: Sequence[str],
        referenced_evidence_ids: Sequence[str],
        generation_config: Mapping[str, Any],
        source_message_ids: Sequence[str] = (),
    ) -> bool:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            if not row or int(row["control_epoch"]) != expected_epoch or row["control_state"] != ResponseControlState.ACTIVE.value:
                conn.execute("COMMIT")
                return False
            conn.execute(
                """
                UPDATE conversation_responses
                SET provider_id=?, generation_state=?, speech_state=?, generated_text=?, text_position=?, semantic_position=?,
                    referenced_memory_ids_json=?, referenced_evidence_ids_json=?, generation_config_json=?, source_message_ids_json=?, updated_at=?
                WHERE owner_id=? AND response_id=? AND control_epoch=? AND control_state=?
                """,
                (provider_id, GenerationState.GENERATING.value, SpeechState.SPEAKING.value, text, len(text), f"char:{len(text)}", _json(referenced_memory_ids), _json(referenced_evidence_ids), _json(generation_config), _json(source_message_ids), _iso(now), owner_id, response_id, expected_epoch, ResponseControlState.ACTIVE.value),
            )
            changed = conn.total_changes > 0
            if changed:
                conn.execute("UPDATE conversation_runtime SET state=?, updated_at=?, last_response_id=? WHERE owner_id=? AND conversation_id=?", (ConversationState.SPEAKING.value, _iso(now), response_id, owner_id, conversation_id))
            conn.execute("COMMIT")
        return changed

    def try_update_stream_references(
        self,
        owner_id: str,
        response_id: str,
        expected_epoch: int,
        memory_ids: Sequence[str],
        evidence_ids: Sequence[str],
    ) -> bool:
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            if not row or int(row["control_epoch"]) != expected_epoch or row["control_state"] != ResponseControlState.ACTIVE.value:
                conn.execute("COMMIT")
                return False
            conn.execute(
                "UPDATE conversation_responses SET referenced_memory_ids_json=?, referenced_evidence_ids_json=?, updated_at=? WHERE owner_id=? AND response_id=? AND control_epoch=? AND control_state=?",
                (_json(memory_ids), _json(evidence_ids), _iso(now), owner_id, response_id, expected_epoch, ResponseControlState.ACTIVE.value),
            )
            changed = conn.total_changes > 0
            conn.execute("COMMIT")
        return changed

    def try_finalize_stream(
        self,
        owner_id: str,
        conversation_id: str,
        branch_id: str,
        response_id: str,
        expected_epoch: int,
        provider_id: str,
        final_state: GenerationState,
        speech_state: SpeechState,
        text: str,
        referenced_memory_ids: Sequence[str],
        referenced_evidence_ids: Sequence[str],
        generation_config: Mapping[str, Any],
        source_message_ids: Sequence[str] = (),
        *,
        resumable: bool,
        resume_policy: ResumePolicy,
    ) -> tuple[bool, sqlite3.Row | None]:
        now = utc_now()
        conversation_state = {
            GenerationState.COMPLETED: ConversationState.COMPLETED,
            GenerationState.PAUSED: ConversationState.PAUSED,
            GenerationState.INTERRUPTED: ConversationState.INTERRUPTED,
            GenerationState.CANCELED: ConversationState.CANCELED,
        }.get(final_state, ConversationState.CANCELED)
        terminal_control = ResponseControlState.TERMINAL if final_state == GenerationState.COMPLETED else ResponseControlState.TERMINAL
        terminal_state = final_state.value
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            if not row or int(row["control_epoch"]) != expected_epoch or row["control_state"] != ResponseControlState.ACTIVE.value:
                conn.execute("COMMIT")
                return False, row
            conn.execute(
                """
                UPDATE conversation_responses
                SET provider_id=?, generation_state=?, speech_state=?, generated_text=?, text_position=?, semantic_position=?, resume_policy=?,
                    referenced_memory_ids_json=?, referenced_evidence_ids_json=?, generation_config_json=?, source_message_ids_json=?,
                    updated_at=?, resumable=?, control_state=?, control_updated_at=?, terminal_state=?
                WHERE owner_id=? AND response_id=? AND control_epoch=? AND control_state=?
                """,
                (
                    provider_id,
                    final_state.value,
                    speech_state.value,
                    text,
                    len(text),
                    f"char:{len(text)}",
                    resume_policy.value if resumable else ResumePolicy.DO_NOT_RESUME.value,
                    _json(referenced_memory_ids),
                    _json(referenced_evidence_ids),
                    _json(generation_config),
                    _json(source_message_ids),
                    _iso(now),
                    1 if resumable else 0,
                    terminal_control.value,
                    _iso(now),
                    terminal_state,
                    owner_id,
                    response_id,
                    expected_epoch,
                    ResponseControlState.ACTIVE.value,
                ),
            )
            changed = conn.total_changes > 0
            if changed:
                conn.execute(
                    "UPDATE conversation_runtime SET state=?, updated_at=?, last_response_id=? WHERE owner_id=? AND conversation_id=?",
                    (conversation_state.value, _iso(now), response_id, owner_id, conversation_id),
                )
            row_after = conn.execute("SELECT * FROM conversation_responses WHERE owner_id=? AND response_id=?", (owner_id, response_id)).fetchone()
            conn.execute("COMMIT")
        return changed, row_after

    def checkpoint(self, checkpoint: ConversationCheckpoint) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO conversation_checkpoints(owner_id, conversation_id, checkpoint_id, created_at, checkpoint_json, active) VALUES(?,?,?,?,?,1)",
                (checkpoint.owner_id, checkpoint.conversation_id, checkpoint.checkpoint_id, _iso(checkpoint.created_at), _json(checkpoint.to_dict()),),
            )
        self.event(checkpoint.owner_id, checkpoint.conversation_id, "checkpoint_created", branch_id=checkpoint.branch_id, response_id=checkpoint.response_cursor.response_id if checkpoint.response_cursor else None, metadata={"checkpoint_id": checkpoint.checkpoint_id})

    def latest_checkpoint(self, owner_id: str, conversation_id: str) -> ConversationCheckpoint | None:
        with self._connect() as conn:
            row = conn.execute("SELECT checkpoint_json FROM conversation_checkpoints WHERE owner_id=? AND conversation_id=? AND active=1 ORDER BY created_at DESC LIMIT 1", (owner_id, conversation_id)).fetchone()
        if not row:
            return None
        return ConversationCheckpoint.from_dict(_loads(row["checkpoint_json"], {}))

    def scrub_conversation_content(self, owner_id: str, conversation_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE conversation_responses SET generated_text='', text_position=0, semantic_position='deleted', resumable=0 WHERE owner_id=? AND conversation_id=?", (owner_id, conversation_id))
            conn.execute("UPDATE conversation_runtime SET deleted=1, state=?, updated_at=? WHERE owner_id=? AND conversation_id=?", (ConversationState.CANCELED.value, _iso(utc_now()), owner_id, conversation_id))
        self.event(owner_id, conversation_id, "memory_command", metadata={"command": "forget_conversation", "runtime_cache_scrubbed": True})


# ---------------------------------------------------------------------------
# Conversation runtime
# ---------------------------------------------------------------------------


class ConversationRuntime:
    def __init__(self, engine: PersonalContinuityEngine, provider: ConversationModelProvider | None = None, *, db_path: str | Path | None = None) -> None:
        self.engine = engine
        self.store = ConversationRuntimeStore(db_path or engine.store.db_path)
        self.provider = provider or UnavailableConversationProvider()
        self._active_streams: dict[tuple[str, str], _ActiveStream] = {}
        self._do_not_remember_next: set[tuple[str, str]] = set()
        self._last_memory_explanation: dict[tuple[str, str], str] = {}

    @property
    def action_plane_available_from_conversation(self) -> bool:
        return False

    def start_conversation(self, context: MemoryAccessContext, *, conversation_id: str, title: str = "", topic: str = "", branch_id: str = "branch-main") -> ConversationRuntimeResult:
        self.engine.start_conversation(context, conversation_id=conversation_id, started_at=utc_now(), title=title, topic=topic, participant="user", branch_id=branch_id, privacy_class=PrivacyClass.PERSONAL)
        self.store.start(context.owner_id, conversation_id, branch_id, topic or title or "main")
        self._checkpoint(context, conversation_id, reason="conversation_started")
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, RuntimeCommandKind.NONE, message="conversation started")

    def reopen_conversation(self, context: MemoryAccessContext, conversation_id: str) -> ConversationRuntimeResult:
        row = self.store.require_conversation(context.owner_id, conversation_id)
        cp = self.store.latest_checkpoint(context.owner_id, conversation_id)
        latest = self.store.latest_response(context.owner_id, conversation_id)
        message = "conversation reopened"
        if latest and latest["generation_state"] == GenerationState.GENERATING.value and (context.owner_id, conversation_id) not in self._active_streams:
            message = "conversation reopened with recoverable generating response; no in-memory producer is assumed"
        return ConversationRuntimeResult(conversation_id, context.owner_id, row["current_branch_id"], ConversationState(row["state"]), RuntimeCommandKind.NONE, response_id=latest["response_id"] if latest else None, response_text=latest["generated_text"] if latest else "", checkpoint=cp, message=message)

    def receive_user_message(self, context: MemoryAccessContext, conversation_id: str, text: str, *, auto_stream: bool = True, max_deltas: int | None = None) -> ConversationRuntimeResult:
        row = self.store.require_conversation(context.owner_id, conversation_id)
        if int(row["deleted"]):
            return ConversationRuntimeResult(conversation_id, context.owner_id, row["current_branch_id"], ConversationState.CANCELED, RuntimeCommandKind.NONE, message="conversation was deleted/tombstoned")
        branch_id = str(row["current_branch_id"])
        command = self._classify_command(text, row)
        if command in {RuntimeCommandKind.STOP, RuntimeCommandKind.PAUSE, RuntimeCommandKind.CANCEL, RuntimeCommandKind.SKIP, RuntimeCommandKind.CONTINUE, RuntimeCommandKind.RESUME}:
            return self._handle_generation_control(context, conversation_id, branch_id, command, text, persist_resume_question=command == RuntimeCommandKind.RESUME)
        if command == RuntimeCommandKind.REPEAT:
            return self._repeat_last(context, conversation_id, branch_id)
        if command == RuntimeCommandKind.GO_BACK:
            return self._go_back(context, conversation_id)
        if command == RuntimeCommandKind.CHANGE_TOPIC:
            topic = self._topic_from_text(text)
            self.store.create_branch(context.owner_id, conversation_id, topic, parent_branch_id=branch_id)
            cp = self._checkpoint(context, conversation_id, reason="change_topic")
            return ConversationRuntimeResult(conversation_id, context.owner_id, self.store.current_branch(context.owner_id, conversation_id), ConversationState.IDLE, command, checkpoint=cp, message="topic changed")
        if command in {RuntimeCommandKind.REMEMBER_THIS, RuntimeCommandKind.DO_NOT_REMEMBER, RuntimeCommandKind.FORGET_THIS, RuntimeCommandKind.FORGET_CONVERSATION, RuntimeCommandKind.EXPLICIT_RECALL, RuntimeCommandKind.SHOW_MEMORY, RuntimeCommandKind.CHECKPOINT}:
            return self._handle_memory_or_runtime_command(context, conversation_id, branch_id, command, text)
        return self._normal_turn(context, conversation_id, branch_id, text, auto_stream=auto_stream, max_deltas=max_deltas)

    def _classify_command(self, text: str, row: sqlite3.Row) -> RuntimeCommandKind:
        low = text.strip().lower()
        active = ConversationState(row["state"]) in (ConversationState.THINKING, ConversationState.SPEAKING, ConversationState.PAUSED, ConversationState.INTERRUPTED, ConversationState.RESUMING)
        if active and low in {"stop", "stop.", "halt"}:
            return RuntimeCommandKind.STOP
        if active and low in {"pause", "pause."}:
            return RuntimeCommandKind.PAUSE
        if low in {"continue", "resume"}:
            return RuntimeCommandKind.CONTINUE
        if "what were you saying" in low or "resume where" in low:
            return RuntimeCommandKind.RESUME
        if active and low in {"cancel", "cancel."}:
            return RuntimeCommandKind.CANCEL
        if active and low in {"skip", "skip."}:
            return RuntimeCommandKind.SKIP
        if low in {"repeat that", "repeat", "say that again"}:
            return RuntimeCommandKind.REPEAT
        if low in {"go back", "back"}:
            return RuntimeCommandKind.GO_BACK
        if "change topic" in low or "new topic" in low:
            return RuntimeCommandKind.CHANGE_TOPIC
        if low.startswith("remember this"):
            return RuntimeCommandKind.REMEMBER_THIS
        if low.startswith("don't remember this") or low.startswith("do not remember this"):
            return RuntimeCommandKind.DO_NOT_REMEMBER
        if low.startswith("forget this conversation"):
            return RuntimeCommandKind.FORGET_CONVERSATION
        if low.startswith("forget this"):
            return RuntimeCommandKind.FORGET_THIS
        if low.startswith("show me what you remember") or low.startswith("what do you remember"):
            return RuntimeCommandKind.SHOW_MEMORY
        if low.startswith("checkpoint") or low == "create checkpoint":
            return RuntimeCommandKind.CHECKPOINT
        if re.search(r"\bwhat did we (?:discuss|talk about)\b", low) and re.search(r"\b\d{4}\b", low):
            return RuntimeCommandKind.EXPLICIT_RECALL
        return RuntimeCommandKind.NONE

    def _topic_from_text(self, text: str) -> str:
        low = text.strip()
        for marker in ("to", ":", "about"):
            if marker in low:
                tail = low.split(marker, 1)[1].strip(" .")
                if tail:
                    return _redact(tail, 80)
        return "new-topic"

    def _record_user(self, context: MemoryAccessContext, conversation_id: str, branch_id: str, text: str, *, explicit_remember: bool = False, explicit_do_not_remember: bool = False) -> str | None:
        if (context.owner_id, conversation_id) in self._do_not_remember_next:
            explicit_do_not_remember = True
            self._do_not_remember_next.remove((context.owner_id, conversation_id))
        seq = self.store.next_sequence(context.owner_id, conversation_id)
        decision, source, _inserted = self.engine.record_message(
            context,
            conversation_id=conversation_id,
            role="user",
            content=text,
            sequence=seq,
            event_time=utc_now(),
            message_id=f"user-{seq}",
            privacy_class=PrivacyClass.PERSONAL,
            branch_id=branch_id,
            explicit_remember=explicit_remember,
            explicit_do_not_remember=explicit_do_not_remember,
        )
        self.store.event(context.owner_id, conversation_id, "message_received", branch_id=branch_id, metadata={"role": "user", "governance": decision.outcome.value if hasattr(decision, "outcome") else str(decision), "retained": source is not None})
        return source.source_id if source else None

    def _record_assistant(
        self,
        context: MemoryAccessContext,
        conversation_id: str,
        branch_id: str,
        response_id: str,
        text: str,
        state: str,
        *,
        stable_source_id: str | None = None,
    ) -> str | None:
        if not text:
            return None
        seq = self.store.next_sequence(context.owner_id, conversation_id)
        _decision, source, _inserted = self.engine.record_message(
            context,
            conversation_id=conversation_id,
            role="assistant",
            content=text,
            sequence=seq,
            event_time=utc_now(),
            message_id=f"assistant-{response_id}-{state}-{seq}",
            source_id=(
                stable_source_id
                or _stable_id(
                    context.owner_id,
                    conversation_id,
                    response_id,
                    state,
                    seq,
                    prefix="src",
                )
            ),
            privacy_class=PrivacyClass.PERSONAL,
            branch_id=branch_id,
        )
        return source.source_id if source else None

    def _assistant_sources_for_response(
        self,
        context: MemoryAccessContext,
        conversation_id: str,
        response_id: str,
    ) -> tuple[Any, ...]:
        prefix = f"assistant-{response_id}-"
        return tuple(
            source
            for source in self.engine.store.list_sources_for_conversation(
                context.owner_id,
                conversation_id,
            )
            if source.role == "assistant"
            and source.message_id.startswith(prefix)
        )

    def _record_assistant_if_no_terminal_source(
        self,
        context: MemoryAccessContext,
        conversation_id: str,
        branch_id: str,
        response_id: str,
        text: str,
        state: str,
    ) -> str | None:
        if not text:
            return None

        existing = self._assistant_sources_for_response(
            context,
            conversation_id,
            response_id,
        )

        non_continuation = [
            source
            for source in existing
            if "continued_completed" not in source.message_id
        ]

        if non_continuation:
            return non_continuation[0].source_id

        # STOP/CANCEL/SKIP races must converge on one persisted terminal
        # assistant source. The memory store already treats source_id as an
        # idempotency key, so concurrent writers cannot create duplicates.
        terminal_source_id = _stable_id(
            context.owner_id,
            conversation_id,
            response_id,
            "terminal-assistant-source",
            prefix="src",
        )

        return self._record_assistant(
            context,
            conversation_id,
            branch_id,
            response_id,
            text,
            state,
            stable_source_id=terminal_source_id,
        )

    def _terminal_event_for_row(self, row: sqlite3.Row) -> ResponseStreamEventType:
        state = GenerationState(row["generation_state"])
        if state == GenerationState.COMPLETED:
            return ResponseStreamEventType.RESPONSE_COMPLETED
        if state == GenerationState.INTERRUPTED:
            return ResponseStreamEventType.RESPONSE_INTERRUPTED
        if state == GenerationState.PAUSED:
            return ResponseStreamEventType.RESPONSE_PAUSED
        if state == GenerationState.CANCELED:
            return ResponseStreamEventType.RESPONSE_CANCELED
        return ResponseStreamEventType.RESPONSE_FAILED

    def _conversation_state_for_generation(self, generation_state: GenerationState) -> ConversationState:
        return {
            GenerationState.COMPLETED: ConversationState.COMPLETED,
            GenerationState.INTERRUPTED: ConversationState.INTERRUPTED,
            GenerationState.PAUSED: ConversationState.PAUSED,
            GenerationState.CANCELED: ConversationState.CANCELED,
            GenerationState.RESUMING: ConversationState.RESUMING,
            GenerationState.GENERATING: ConversationState.SPEAKING,
        }.get(generation_state, ConversationState.IDLE)

    def _authoritative_response_result(
        self,
        context: MemoryAccessContext,
        conversation_id: str,
        branch_id: str,
        response_id: str,
        command: RuntimeCommandKind,
        prior_events: Sequence[ResponseStreamEvent] = (),
        *,
        reason: str,
        row: sqlite3.Row | None = None,
    ) -> ConversationRuntimeResult:
        row = row or self.store.response(context.owner_id, response_id)
        if not row:
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, command, response_id, message=reason)
        conversation_row = self.store.require_conversation(context.owner_id, conversation_id)
        state = ConversationState(conversation_row["state"])
        events = list(prior_events)
        terminal_type = self._terminal_event_for_row(row)
        if not events or events[-1].event_type != terminal_type:
            events.append(ResponseStreamEvent(terminal_type, response_id, context.owner_id, conversation_id, branch_id, utc_now(), message=reason, metadata={"authoritative_control_state": row["control_state"], "control_epoch": int(row["control_epoch"])}))
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, state, command, response_id, row["generated_text"], tuple(events), message=reason)

    def _recent_messages(self, context: MemoryAccessContext, conversation_id: str, limit: int = 8) -> tuple[Mapping[str, Any], ...]:
        rows = self.engine.store.list_sources_for_conversation(context.owner_id, conversation_id)
        recent = rows[-limit:]
        return tuple({"source_id": r.source_id, "role": r.role, "content": _redact(r.content, 240), "sequence": r.sequence, "branch_id": r.branch_id} for r in recent if r.content)

    def _provider_context_frame(self, context: MemoryAccessContext, conversation_id: str, branch_id: str, *, current_user_message: str, recent_messages: Sequence[Mapping[str, Any]], activation: ActivationRuntimeResult | None, response_state: str, generated_prefix: str = "") -> Mapping[str, Any]:
        row = self.store.require_conversation(context.owner_id, conversation_id)
        current_context = activation.request.current_context if activation else None
        timeline_ids = tuple(getattr(event, "timeline_id", "") for event in (activation.retrieval.timeline_events if activation else ()))
        return MappingProxyType(
            {
                "schema_version": PHASE3C_SCHEMA_VERSION,
                "conversation_id": conversation_id,
                "branch_id": branch_id,
                "owner_id": context.owner_id,
                "active_topic": row["active_topic"],
                "conversation_state": row["state"],
                "response_state": response_state,
                "current_user_message": _redact(current_user_message, 600),
                "generated_prefix_chars": len(generated_prefix),
                "latest_messages": tuple(recent_messages),
                "unresolved_questions": (),
                "activated_candidate_ids": activation.activated_candidate_ids if activation else (),
                "retrieved_source_ids": activation.retrieved_source_ids if activation else (),
                "timeline_event_ids": tuple(item for item in timeline_ids if item),
                "project_ids": tuple(current_context.project_ids) if current_context else (),
                "entity_ids": tuple(current_context.entity_ids) if current_context else (),
                "goal_ids": tuple(current_context.goal_ids) if current_context else (),
                "decision_ids": tuple(current_context.decision_ids) if current_context else (),
                "bounded_staged_retrieval": {"limit": 12, "retrieved_count": len(activation.retrieved_source_ids) if activation else 0},
                "memory_firewall": {"provider_egress": context.egress_policy.value, "injection": "minimized_reasoning_context_only"},
            }
        )

    def _normal_turn(self, context: MemoryAccessContext, conversation_id: str, branch_id: str, text: str, *, auto_stream: bool, max_deltas: int | None) -> ConversationRuntimeResult:
        self.store.set_state(context.owner_id, conversation_id, ConversationState.THINKING)
        activation = self.engine.activate_contextual_memory(context, current_message=text, conversation_id=conversation_id, created_at=utc_now(), branch_id=branch_id)
        self.store.event(context.owner_id, conversation_id, "memory_activation", branch_id=branch_id, metadata={"activated": len(activation.activated_candidate_ids), "retrieved": len(activation.retrieved_source_ids)})
        self._last_memory_explanation[(context.owner_id, conversation_id)] = activation.explanation
        self._record_user(context, conversation_id, branch_id, text)
        recent_messages = self._recent_messages(context, conversation_id)
        response_id = _stable_id(context.owner_id, conversation_id, branch_id, text, utc_now().isoformat(), prefix="response")
        request = ConversationProviderRequest(
            owner_id=context.owner_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            response_id=response_id,
            user_text=text,
            recent_messages=recent_messages,
            memory_context=activation.minimized_reasoning_context,
            evidence_source_ids=activation.retrieved_source_ids,
            runtime_context=self._provider_context_frame(context, conversation_id, branch_id, current_user_message=text, recent_messages=recent_messages, activation=activation, response_state=GenerationState.NOT_STARTED.value),
            generation_config={"strategy": "text-first", "provider": self.provider.provider_id, "test_provider": getattr(self.provider, "is_test_provider", False)},
        )
        return self._start_stream(context, conversation_id, branch_id, request, activation=activation, auto_stream=auto_stream, max_deltas=max_deltas)

    def _start_stream(self, context: MemoryAccessContext, conversation_id: str, branch_id: str, request: ConversationProviderRequest, *, activation: ActivationRuntimeResult | None = None, auto_stream: bool = True, max_deltas: int | None = None) -> ConversationRuntimeResult:
        signal = CancellationSignal()
        try:
            iterator = self.provider.stream_response(request, signal)
        except Exception as exc:
            failed = ResponseStreamEvent(ResponseStreamEventType.RESPONSE_FAILED, request.response_id, context.owner_id, conversation_id, branch_id, utc_now(), message=f"provider exception: {type(exc).__name__}")
            self.store.upsert_response(context.owner_id, conversation_id, branch_id, request.response_id, self.provider.provider_id, GenerationState.CANCELED, SpeechState.CANCELED, request.generated_prefix, (), request.evidence_source_ids, request.generation_config, (), resumable=False, resume_policy=ResumePolicy.DO_NOT_RESUME)
            self.store.apply_response_control(context.owner_id, conversation_id, request.response_id, ResponseControlState.CANCEL_REQUESTED, GenerationState.CANCELED, SpeechState.CANCELED, ConversationState.CANCELED, reason=failed.message, requested_by=context.principal_id, resumable=False, resume_policy=ResumePolicy.DO_NOT_RESUME)
            self.store.event(context.owner_id, conversation_id, "conversation_error", branch_id=branch_id, response_id=request.response_id, metadata={"reason": failed.message})
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.CANCELED, RuntimeCommandKind.NONE, request.response_id, request.generated_prefix, (failed,), activation, message="provider failed before streaming")
        self.store.upsert_response(context.owner_id, conversation_id, branch_id, request.response_id, self.provider.provider_id, GenerationState.GENERATING, SpeechState.SPEAKING, request.generated_prefix, (), request.evidence_source_ids, request.generation_config, ())
        control_epoch = self.store.begin_stream_control(context.owner_id, request.response_id)
        self._active_streams[(context.owner_id, conversation_id)] = _ActiveStream(request.response_id, request, iterator, signal, control_epoch)
        self.store.set_state(context.owner_id, conversation_id, ConversationState.SPEAKING, last_response_id=request.response_id)
        self.store.event(context.owner_id, conversation_id, "response_started", branch_id=branch_id, response_id=request.response_id, metadata={"provider_id": self.provider.provider_id, "test_provider": getattr(self.provider, "is_test_provider", False), "control_epoch": control_epoch})
        if auto_stream or max_deltas is not None:
            return self.continue_stream(context, conversation_id, max_deltas=max_deltas, activation=activation)
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.SPEAKING, RuntimeCommandKind.NONE, response_id=request.response_id, memory_activation=activation, message="response streaming started")

    def continue_stream(self, context: MemoryAccessContext, conversation_id: str, *, max_deltas: int | None = None, activation: ActivationRuntimeResult | None = None) -> ConversationRuntimeResult:
        active = self._active_streams.get((context.owner_id, conversation_id))
        row = self.store.require_conversation(context.owner_id, conversation_id)
        branch_id = str(row["current_branch_id"])
        if not active:
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState(row["state"]), RuntimeCommandKind.CONTINUE, message="no active stream")
        if active.consuming:
            current = self.store.response(context.owner_id, active.response_id)
            return ConversationRuntimeResult(conversation_id, context.owner_id, active.request.branch_id, ConversationState.SPEAKING, RuntimeCommandKind.CONTINUE, active.response_id, current["generated_text"] if current else "", message="response stream is already being consumed")
        active.consuming = True
        branch_id = active.request.branch_id
        response_row = self.store.response(context.owner_id, active.response_id)
        text = response_row["generated_text"] if response_row else active.request.generated_prefix
        events: list[ResponseStreamEvent] = []
        delta_count = 0
        stale_reason = ""

        def stale_result(reason: str) -> ConversationRuntimeResult:
            active.consuming = False
            self._active_streams.pop((context.owner_id, conversation_id), None)
            latest = self.store.response(context.owner_id, active.response_id)
            return self._authoritative_response_result(context, conversation_id, branch_id, active.response_id, RuntimeCommandKind.NONE, events, reason=reason, row=latest)

        try:
            for provider_event in active.iterator:
                if not self.store.response_is_current_for_stream(context.owner_id, active.response_id, active.control_epoch):
                    stale_reason = "stream control epoch became stale before accepting provider event"
                    break
                if provider_event.event_type == ResponseStreamEventType.RESPONSE_STARTED:
                    events.append(provider_event)
                    continue
                if provider_event.event_type == ResponseStreamEventType.TEXT_DELTA:
                    candidate_text = text + provider_event.text_delta
                    current = self.store.response(context.owner_id, active.response_id)
                    mem_ids = tuple(_loads(current["referenced_memory_ids_json"], []) if current else ())
                    ev_ids = tuple(_loads(current["referenced_evidence_ids_json"], []) if current else active.request.evidence_source_ids)
                    ok = self.store.try_persist_stream_delta(context.owner_id, conversation_id, branch_id, active.response_id, active.control_epoch, self.provider.provider_id, candidate_text, mem_ids, ev_ids, active.request.generation_config, ())
                    if not ok:
                        stale_reason = "late TEXT_DELTA rejected by response control epoch"
                        break
                    text = candidate_text
                    events.append(provider_event)
                    delta_count += 1
                    if max_deltas is not None and delta_count >= max_deltas:
                        break
                    continue
                if provider_event.event_type in (ResponseStreamEventType.MEMORY_REFERENCE, ResponseStreamEventType.EVIDENCE_REFERENCE):
                    current = self.store.response(context.owner_id, active.response_id)
                    mem_ids = set(_loads(current["referenced_memory_ids_json"], []) if current else [])
                    ev_ids = set(_loads(current["referenced_evidence_ids_json"], []) if current else [])
                    if provider_event.memory_id:
                        mem_ids.add(provider_event.memory_id)
                    if provider_event.evidence_id:
                        ev_ids.add(provider_event.evidence_id)
                    ok = self.store.try_update_stream_references(context.owner_id, active.response_id, active.control_epoch, tuple(mem_ids), tuple(ev_ids))
                    if not ok:
                        stale_reason = f"late {provider_event.event_type.value} rejected by response control epoch"
                        break
                    events.append(provider_event)
                    continue

                final_state: GenerationState | None = None
                speech_state = SpeechState.SPEAKING
                if provider_event.event_type == ResponseStreamEventType.RESPONSE_COMPLETED:
                    final_state = GenerationState.COMPLETED
                    speech_state = SpeechState.COMPLETED
                elif provider_event.event_type in (ResponseStreamEventType.RESPONSE_FAILED, ResponseStreamEventType.RESPONSE_CANCELED):
                    final_state = GenerationState.CANCELED
                    speech_state = SpeechState.CANCELED
                elif provider_event.event_type == ResponseStreamEventType.RESPONSE_PAUSED:
                    final_state = GenerationState.PAUSED
                    speech_state = SpeechState.PAUSED
                elif provider_event.event_type == ResponseStreamEventType.RESPONSE_INTERRUPTED:
                    final_state = GenerationState.INTERRUPTED
                    speech_state = SpeechState.INTERRUPTED
                if final_state is None:
                    continue
                current = self.store.response(context.owner_id, active.response_id)
                mem_ids = tuple(_loads(current["referenced_memory_ids_json"], []) if current else ())
                ev_ids = tuple(_loads(current["referenced_evidence_ids_json"], []) if current else active.request.evidence_source_ids)
                resumable = final_state in (GenerationState.INTERRUPTED, GenerationState.PAUSED)
                resume_policy = ResumePolicy.SUMMARIZE_THEN_RESUME if resumable else ResumePolicy.DO_NOT_RESUME
                ok, finalized = self.store.try_finalize_stream(
                    context.owner_id,
                    conversation_id,
                    branch_id,
                    active.response_id,
                    active.control_epoch,
                    self.provider.provider_id,
                    final_state,
                    speech_state,
                    text,
                    mem_ids,
                    ev_ids,
                    active.request.generation_config,
                    (),
                    resumable=resumable,
                    resume_policy=resume_policy,
                )
                if not ok:
                    stale_reason = f"late {provider_event.event_type.value} rejected by response control epoch"
                    break
                events.append(provider_event)
                cursor = self.store.cursor_from_response_row(finalized)
                if final_state == GenerationState.COMPLETED:
                    persisted_text = text
                    persisted_state = "completed"
                    if active.request.generated_prefix and text.startswith(active.request.generated_prefix):
                        # Resume stores the already-persisted interrupted prefix separately;
                        # only the continuation delta is retained as the new source record.
                        persisted_text = text[len(active.request.generated_prefix):]
                        persisted_state = "continued_completed"
                    source_id = self._record_assistant(context, conversation_id, branch_id, active.response_id, persisted_text, persisted_state)
                    self.store.event(context.owner_id, conversation_id, "response_completed", branch_id=branch_id, response_id=active.response_id, metadata={"source_id": source_id, "chars": len(text), "persisted_chars": len(persisted_text), "control_epoch": active.control_epoch})
                    self._checkpoint(context, conversation_id, reason="response_completed", cursor=cursor)
                elif final_state == GenerationState.PAUSED:
                    self.store.event(context.owner_id, conversation_id, "response_paused", branch_id=branch_id, response_id=active.response_id, metadata={"chars": len(text), "control_epoch": active.control_epoch})
                elif final_state == GenerationState.INTERRUPTED:
                    source_id = self._record_assistant_if_no_terminal_source(context, conversation_id, branch_id, active.response_id, text, "interrupted")
                    self.store.event(context.owner_id, conversation_id, "response_interrupted", branch_id=branch_id, response_id=active.response_id, metadata={"source_id": source_id, "chars": len(text), "control_epoch": active.control_epoch})
                    self._checkpoint(context, conversation_id, reason="response_interrupted", cursor=cursor)
                else:
                    self.store.event(context.owner_id, conversation_id, "conversation_error" if provider_event.event_type == ResponseStreamEventType.RESPONSE_FAILED else "response_canceled", branch_id=branch_id, response_id=active.response_id, metadata={"control_epoch": active.control_epoch})
                active.consuming = False
                self._active_streams.pop((context.owner_id, conversation_id), None)
                return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState(self.store.require_conversation(context.owner_id, conversation_id)["state"]), RuntimeCommandKind.NONE, active.response_id, text, tuple(events), activation, message="response stream finalized")
        except Exception as exc:  # provider failure is recorded truthfully if the stream is still authoritative.
            failed_event = ResponseStreamEvent(ResponseStreamEventType.RESPONSE_FAILED, active.response_id, context.owner_id, conversation_id, branch_id, utc_now(), message=f"provider exception: {type(exc).__name__}")
            ok, finalized = self.store.try_finalize_stream(
                context.owner_id,
                conversation_id,
                branch_id,
                active.response_id,
                active.control_epoch,
                self.provider.provider_id,
                GenerationState.CANCELED,
                SpeechState.CANCELED,
                text,
                (),
                active.request.evidence_source_ids,
                active.request.generation_config,
                (),
                resumable=False,
                resume_policy=ResumePolicy.DO_NOT_RESUME,
            )
            if not ok:
                stale_reason = "provider exception finalization rejected by response control epoch"
            else:
                events.append(failed_event)
                self.store.event(context.owner_id, conversation_id, "conversation_error", branch_id=branch_id, response_id=active.response_id, metadata={"reason": failed_event.message, "control_epoch": active.control_epoch})
                active.consuming = False
                self._active_streams.pop((context.owner_id, conversation_id), None)
                return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.CANCELED, RuntimeCommandKind.NONE, active.response_id, text, tuple(events), activation, message="provider failed during streaming")
        if stale_reason:
            return stale_result(stale_reason)
        if not self.store.response_is_current_for_stream(context.owner_id, active.response_id, active.control_epoch):
            return stale_result("stream control epoch became stale before partial return")
        self.store.set_state(context.owner_id, conversation_id, ConversationState.SPEAKING, last_response_id=active.response_id)
        active.consuming = False
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.SPEAKING, RuntimeCommandKind.NONE, active.response_id, text, tuple(events), activation, message="response stream still active")

    def _latest_response_for_control(self, context: MemoryAccessContext, conversation_id: str, response_id: str | None = None) -> sqlite3.Row | None:
        self.store.require_conversation(context.owner_id, conversation_id)
        if response_id:
            row = self.store.response(context.owner_id, response_id)
            if row and row["conversation_id"] != conversation_id:
                raise PermissionError("response belongs to a different conversation")
            return row
        return self.store.latest_response(context.owner_id, conversation_id)

    def _apply_control_request(
        self,
        context: MemoryAccessContext,
        conversation_id: str,
        command: RuntimeCommandKind,
        *,
        response_id: str | None = None,
        reason: str,
    ) -> ConversationRuntimeResult:
        latest = self._latest_response_for_control(context, conversation_id, response_id)
        branch_id = self.store.current_branch(context.owner_id, conversation_id)
        if not latest:
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, command, message="no response to control")
        branch_id = latest["branch_id"]
        active = self._active_streams.get((context.owner_id, conversation_id))
        if active and active.response_id == latest["response_id"]:
            if command == RuntimeCommandKind.PAUSE:
                active.cancellation.pause(reason)
            else:
                active.cancellation.cancel(reason)
        target = {
            RuntimeCommandKind.STOP: (ResponseControlState.STOP_REQUESTED, GenerationState.INTERRUPTED, SpeechState.INTERRUPTED, ConversationState.INTERRUPTED, True, ResumePolicy.SUMMARIZE_THEN_RESUME, "response_interrupted", "interrupted"),
            RuntimeCommandKind.PAUSE: (ResponseControlState.PAUSE_REQUESTED, GenerationState.PAUSED, SpeechState.PAUSED, ConversationState.PAUSED, True, ResumePolicy.SUMMARIZE_THEN_RESUME, "response_paused", "paused"),
            RuntimeCommandKind.CANCEL: (ResponseControlState.CANCEL_REQUESTED, GenerationState.CANCELED, SpeechState.CANCELED, ConversationState.CANCELED, False, ResumePolicy.DO_NOT_RESUME, "response_canceled", "canceled"),
            RuntimeCommandKind.SKIP: (ResponseControlState.CANCEL_REQUESTED, GenerationState.CANCELED, SpeechState.CANCELED, ConversationState.WAITING, False, ResumePolicy.DO_NOT_RESUME, "response_skipped", "skipped"),
        }[command]
        control_state, generation_state, speech_state, conversation_state, resumable, resume_policy, event_name, source_state = target
        row_after, changed = self.store.apply_response_control(
            context.owner_id,
            conversation_id,
            latest["response_id"],
            control_state,
            generation_state,
            speech_state,
            conversation_state,
            reason=reason,
            requested_by=context.principal_id,
            resumable=resumable,
            resume_policy=resume_policy,
        )
        if changed and active and active.response_id == latest["response_id"] and not active.consuming:
            self._active_streams.pop((context.owner_id, conversation_id), None)
        source_id = None
        if changed and command in (RuntimeCommandKind.STOP, RuntimeCommandKind.CANCEL, RuntimeCommandKind.SKIP):
            source_id = self._record_assistant_if_no_terminal_source(context, conversation_id, branch_id, latest["response_id"], row_after["generated_text"], source_state)
        cursor = self.store.cursor_from_response_row(row_after)
        if changed:
            self.store.event(context.owner_id, conversation_id, event_name, branch_id=branch_id, response_id=latest["response_id"], metadata={"source_id": source_id, "chars": len(row_after["generated_text"]), "control_epoch": int(row_after["control_epoch"]), "control_state": row_after["control_state"]})
            self._checkpoint(context, conversation_id, reason=event_name, cursor=cursor)
        return self._authoritative_response_result(context, conversation_id, branch_id, latest["response_id"], command, reason=("response control applied" if changed else "response control ignored by precedence/current terminal state"), row=row_after)

    def interrupt_response(self, context: MemoryAccessContext, conversation_id: str, *, response_id: str | None = None, reason: str = "out-of-band response interrupt") -> ConversationRuntimeResult:
        return self._apply_control_request(context, conversation_id, RuntimeCommandKind.STOP, response_id=response_id, reason=reason)

    def pause_response(self, context: MemoryAccessContext, conversation_id: str, *, response_id: str | None = None, reason: str = "out-of-band response pause") -> ConversationRuntimeResult:
        return self._apply_control_request(context, conversation_id, RuntimeCommandKind.PAUSE, response_id=response_id, reason=reason)

    def cancel_response(self, context: MemoryAccessContext, conversation_id: str, *, response_id: str | None = None, reason: str = "out-of-band response cancel") -> ConversationRuntimeResult:
        return self._apply_control_request(context, conversation_id, RuntimeCommandKind.CANCEL, response_id=response_id, reason=reason)

    def skip_response(self, context: MemoryAccessContext, conversation_id: str, *, response_id: str | None = None, reason: str = "out-of-band response skip") -> ConversationRuntimeResult:
        return self._apply_control_request(context, conversation_id, RuntimeCommandKind.SKIP, response_id=response_id, reason=reason)

    def resume_response(self, context: MemoryAccessContext, conversation_id: str, *, response_id: str | None = None, reason: str = "out-of-band response resume") -> ConversationRuntimeResult:
        latest = self._latest_response_for_control(context, conversation_id, response_id)
        branch_id = self.store.current_branch(context.owner_id, conversation_id)
        if not latest:
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, RuntimeCommandKind.RESUME, message="nothing resumable")
        active = self._active_streams.get((context.owner_id, conversation_id))
        if active and active.response_id == latest["response_id"]:
            if self.store.response_is_current_for_stream(context.owner_id, active.response_id, active.control_epoch):
                return self.continue_stream(context, conversation_id)
            if active.consuming:
                return self._authoritative_response_result(context, conversation_id, latest["branch_id"], latest["response_id"], RuntimeCommandKind.RESUME, reason="resume deferred until stale producer observes control", row=latest)
            self._active_streams.pop((context.owner_id, conversation_id), None)
        if not int(latest["resumable"]):
            return ConversationRuntimeResult(conversation_id, context.owner_id, latest["branch_id"], ConversationState.IDLE, RuntimeCommandKind.RESUME, latest["response_id"], latest["generated_text"], message="nothing resumable")
        return self._resume_from_row(context, conversation_id, latest, reason=reason)

    def continue_response(self, context: MemoryAccessContext, conversation_id: str, *, response_id: str | None = None, reason: str = "out-of-band response continue") -> ConversationRuntimeResult:
        return self.resume_response(context, conversation_id, response_id=response_id, reason=reason)

    def _handle_generation_control(self, context: MemoryAccessContext, conversation_id: str, branch_id: str, command: RuntimeCommandKind, text: str, *, persist_resume_question: bool = False) -> ConversationRuntimeResult:
        if persist_resume_question:
            self._record_user(context, conversation_id, branch_id, text)
        if command == RuntimeCommandKind.STOP:
            return self.interrupt_response(context, conversation_id, reason="text STOP command")
        if command == RuntimeCommandKind.PAUSE:
            return self.pause_response(context, conversation_id, reason="text PAUSE command")
        if command == RuntimeCommandKind.CANCEL:
            return self.cancel_response(context, conversation_id, reason="text CANCEL command")
        if command == RuntimeCommandKind.SKIP:
            return self.skip_response(context, conversation_id, reason="text SKIP command")
        if command in (RuntimeCommandKind.CONTINUE, RuntimeCommandKind.RESUME):
            return self.resume_response(context, conversation_id, reason="text resume/continue command")
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, command)

    def _resume_from_row(self, context: MemoryAccessContext, conversation_id: str, row: sqlite3.Row, *, reason: str = "resume interrupted response") -> ConversationRuntimeResult:
        branch_id = row["branch_id"]
        prefix = row["generated_text"]
        if row["resume_policy"] == ResumePolicy.DO_NOT_RESUME.value or not int(row["resumable"]):
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.CANCELED, RuntimeCommandKind.RESUME, row["response_id"], prefix, message="response is not resumable")
        resumed_row, changed = self.store.request_resume_control(context.owner_id, conversation_id, row["response_id"], reason=reason, requested_by=context.principal_id)
        if not changed:
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, self._conversation_state_for_generation(GenerationState(resumed_row["generation_state"])), RuntimeCommandKind.RESUME, row["response_id"], resumed_row["generated_text"], message="response is not resumable or is already terminal")
        row = resumed_row
        prefix = row["generated_text"]
        recent_messages = self._recent_messages(context, conversation_id)
        request = ConversationProviderRequest(
            owner_id=context.owner_id,
            conversation_id=conversation_id,
            branch_id=branch_id,
            response_id=row["response_id"],
            user_text="resume interrupted response",
            recent_messages=recent_messages,
            memory_context={},
            evidence_source_ids=tuple(_loads(row["referenced_evidence_ids_json"], [])),
            generated_prefix=prefix,
            resume_from_position=int(row["text_position"]),
            runtime_context=self._provider_context_frame(context, conversation_id, branch_id, current_user_message="resume interrupted response", recent_messages=recent_messages, activation=None, response_state=GenerationState.RESUMING.value, generated_prefix=prefix),
            generation_config={**_loads(row["generation_config_json"], {}), "resume_strategy": "prefix-preserved-provider-continuation"},
        )
        self.store.set_state(context.owner_id, conversation_id, ConversationState.RESUMING, last_response_id=row["response_id"])
        self.store.event(context.owner_id, conversation_id, "response_resumed", branch_id=branch_id, response_id=row["response_id"], metadata={"prefix_chars": len(prefix), "strategy": "prefix-preserved-provider-continuation"})
        base = self._start_stream(context, conversation_id, branch_id, request, auto_stream=True)
        return ConversationRuntimeResult(
            base.conversation_id,
            base.owner_id,
            base.branch_id,
            base.state,
            RuntimeCommandKind.RESUME,
            base.response_id,
            base.response_text,
            base.events,
            base.memory_activation,
            base.historical_answer,
            base.retrieval,
            base.deletion,
            base.checkpoint,
            base.message,
        )

    def _repeat_last(self, context: MemoryAccessContext, conversation_id: str, branch_id: str) -> ConversationRuntimeResult:
        latest = self.store.latest_response(context.owner_id, conversation_id)
        if not latest:
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, RuntimeCommandKind.REPEAT, message="nothing to repeat")
        text = "Repeating: " + latest["generated_text"]
        response_id = _stable_id(context.owner_id, conversation_id, "repeat", utc_now().isoformat(), prefix="response")
        source_id = self._record_assistant(context, conversation_id, branch_id, response_id, text, "repeat")
        cursor = self.store.upsert_response(context.owner_id, conversation_id, branch_id, response_id, "runtime-repeat", GenerationState.COMPLETED, SpeechState.COMPLETED, text, _loads(latest["referenced_memory_ids_json"], []), _loads(latest["referenced_evidence_ids_json"], []), {"strategy": "repeat_prior_response"}, (source_id,) if source_id else (), resumable=False)
        self.store.set_state(context.owner_id, conversation_id, ConversationState.COMPLETED, last_response_id=response_id)
        self._checkpoint(context, conversation_id, reason="repeat", cursor=cursor)
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.COMPLETED, RuntimeCommandKind.REPEAT, response_id, text, message="repeated prior response")

    def _go_back(self, context: MemoryAccessContext, conversation_id: str) -> ConversationRuntimeResult:
        cp = self.store.latest_checkpoint(context.owner_id, conversation_id)
        if not cp:
            return ConversationRuntimeResult(conversation_id, context.owner_id, self.store.current_branch(context.owner_id, conversation_id), ConversationState.IDLE, RuntimeCommandKind.GO_BACK, message="no checkpoint available")
        if cp.branch_id:
            self.store.switch_branch(context.owner_id, conversation_id, cp.branch_id)
        return ConversationRuntimeResult(conversation_id, context.owner_id, cp.branch_id or self.store.current_branch(context.owner_id, conversation_id), ConversationState.IDLE, RuntimeCommandKind.GO_BACK, checkpoint=cp, message="returned to checkpoint branch/topic")

    def _handle_memory_or_runtime_command(self, context: MemoryAccessContext, conversation_id: str, branch_id: str, command: RuntimeCommandKind, text: str) -> ConversationRuntimeResult:
        if command == RuntimeCommandKind.REMEMBER_THIS:
            self._record_user(context, conversation_id, branch_id, text, explicit_remember=True)
            self.store.event(context.owner_id, conversation_id, "memory_command", branch_id=branch_id, metadata={"command": command.value})
            return self._runtime_answer(context, conversation_id, branch_id, command, "I'll retain this according to your memory policy.")
        if command == RuntimeCommandKind.DO_NOT_REMEMBER:
            self._do_not_remember_next.add((context.owner_id, conversation_id))
            self.store.event(context.owner_id, conversation_id, "memory_command", branch_id=branch_id, metadata={"command": command.value})
            return self._runtime_answer(context, conversation_id, branch_id, command, "I won't retain the next message unless policy or later confirmation changes that.")
        if command == RuntimeCommandKind.FORGET_THIS:
            sources = self.engine.store.list_sources_for_conversation(context.owner_id, conversation_id)
            last = sources[-1] if sources else None
            deletion = self.engine.delete_memory(context, scope_type="message", source_ids=(last.source_id,) if last else ())
            self.store.event(context.owner_id, conversation_id, "memory_command", branch_id=branch_id, metadata={"command": command.value, "status": deletion.status.value})
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, command, deletion=deletion, message="forget-this processed")
        if command == RuntimeCommandKind.FORGET_CONVERSATION:
            deletion = self.engine.delete_memory(context, scope_type="conversation", conversation_id=conversation_id)
            self.store.scrub_conversation_content(context.owner_id, conversation_id)
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.CANCELED, command, deletion=deletion, message="conversation forgotten/tombstoned")
        if command == RuntimeCommandKind.EXPLICIT_RECALL:
            self._record_user(context, conversation_id, branch_id, text)
            answer = self.engine.answer_historical_query(context, text)
            response_text = answer.summary + " " + answer.inference
            result = self._runtime_answer(context, conversation_id, branch_id, command, response_text, evidence_ids=answer.evidence_source_ids)
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, result.state, command, result.response_id, result.response_text, result.events, historical_answer=answer, message="explicit historical recall")
        if command == RuntimeCommandKind.SHOW_MEMORY:
            self._record_user(context, conversation_id, branch_id, text)
            query_text = text.split("about", 1)[1].strip() if "about" in text.lower() else text
            retrieval = self.engine.retrieve(context, ContinuityQuery(query_text=query_text, modes=(RetrievalModeName.LEXICAL, RetrievalModeName.SEMANTIC, RetrievalModeName.RELATIONAL), limit=8))
            if retrieval.source_records:
                body = "I found source-backed memories: " + "; ".join(f"{r.source_id}: {_redact(r.content, 120)}" for r in retrieval.source_records[:3])
            else:
                body = "I don't have source-backed memory for that."
            result = self._runtime_answer(context, conversation_id, branch_id, command, body, evidence_ids=tuple(r.source_id for r in retrieval.source_records))
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, result.state, command, result.response_id, result.response_text, result.events, retrieval=retrieval, message="show memory")
        if command == RuntimeCommandKind.CHECKPOINT:
            cp = self._checkpoint(context, conversation_id, reason="explicit_checkpoint")
            return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, command, checkpoint=cp, message="checkpoint created")
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.IDLE, command)

    def _runtime_answer(self, context: MemoryAccessContext, conversation_id: str, branch_id: str, command: RuntimeCommandKind, text: str, *, evidence_ids: Sequence[str] = ()) -> ConversationRuntimeResult:
        response_id = _stable_id(context.owner_id, conversation_id, branch_id, command.value, text, utc_now().isoformat(), prefix="response")
        source_id = self._record_assistant(context, conversation_id, branch_id, response_id, text, command.value.lower())
        cursor = self.store.upsert_response(context.owner_id, conversation_id, branch_id, response_id, "runtime-command", GenerationState.COMPLETED, SpeechState.COMPLETED, text, (), evidence_ids, {"strategy": "runtime-command"}, (source_id,) if source_id else (), resumable=False)
        self.store.set_state(context.owner_id, conversation_id, ConversationState.COMPLETED, last_response_id=response_id)
        cp = self._checkpoint(context, conversation_id, reason="runtime_command", cursor=cursor)
        event = ResponseStreamEvent(ResponseStreamEventType.RESPONSE_COMPLETED, response_id, context.owner_id, conversation_id, branch_id, utc_now(), message="runtime command completed")
        return ConversationRuntimeResult(conversation_id, context.owner_id, branch_id, ConversationState.COMPLETED, command, response_id, text, (event,), checkpoint=cp)

    def _checkpoint(self, context: MemoryAccessContext, conversation_id: str, *, reason: str, cursor: ResponseCursor | None = None, historical_action_state: Mapping[str, Any] | None = None, referenced_session_ids: Sequence[str] = (), referenced_confirmation_ids: Sequence[str] = (), referenced_grant_ids: Sequence[str] = (), referenced_lease_ids: Sequence[str] = (), referenced_security_epoch: int | None = None) -> ConversationCheckpoint:
        row = self.store.require_conversation(context.owner_id, conversation_id)
        topic = str(row["active_topic"])
        checkpoint = ConversationCheckpoint(
            checkpoint_id=_stable_id(context.owner_id, conversation_id, reason, utc_now().isoformat(), prefix="checkpoint"),
            conversation_id=conversation_id,
            owner_id=context.owner_id,
            created_at=utc_now(),
            branch_id=row["current_branch_id"],
            topic=topic,
            unresolved_questions=(),
            referenced_memory_ids=cursor.referenced_memory_ids if cursor else (),
            response_cursor=cursor,
            historical_action_state=dict(historical_action_state or {}),
            referenced_session_ids=tuple(referenced_session_ids),
            referenced_confirmation_ids=tuple(referenced_confirmation_ids),
            referenced_grant_ids=tuple(referenced_grant_ids),
            referenced_lease_ids=tuple(referenced_lease_ids),
            referenced_security_epoch=referenced_security_epoch,
        )
        self.store.checkpoint(checkpoint)
        return checkpoint

    def create_branch(self, context: MemoryAccessContext, conversation_id: str, topic: str) -> str:
        self.store.require_conversation(context.owner_id, conversation_id)
        branch_id = self.store.create_branch(context.owner_id, conversation_id, topic)
        self._checkpoint(context, conversation_id, reason="branch_created")
        return branch_id

    def switch_branch(self, context: MemoryAccessContext, conversation_id: str, branch_id: str) -> None:
        self.store.switch_branch(context.owner_id, conversation_id, branch_id)
        self._checkpoint(context, conversation_id, reason="branch_switched")

    def explain_last_memory_activation(self, context: MemoryAccessContext, conversation_id: str) -> str:
        return self._last_memory_explanation.get((context.owner_id, conversation_id), "No contextual memory activation explanation is available for this conversation.")

    def create_checkpoint_with_historical_references(self, context: MemoryAccessContext, conversation_id: str, *, session_id: str = "session-historical", confirmation_id: str = "confirmation-historical", grant_id: str = "grant-historical", lease_id: str = "lease-historical", security_epoch: int = 0) -> ConversationCheckpoint:
        return self._checkpoint(context, conversation_id, reason="historical_authority_refs", historical_action_state={"note": "historical references only; not executable authority"}, referenced_session_ids=(session_id,), referenced_confirmation_ids=(confirmation_id,), referenced_grant_ids=(grant_id,), referenced_lease_ids=(lease_id,), referenced_security_epoch=security_epoch)


__all__ = [
    "CancellationSignal",
    "ConversationModelProvider",
    "ConversationProviderRequest",
    "ConversationRuntime",
    "ConversationRuntimeResult",
    "ConversationRuntimeStore",
    "DeterministicConversationProvider",
    "ResponseControlState",
    "ResponseStreamEvent",
    "ResponseStreamEventType",
    "RuntimeCommandKind",
    "UnavailableConversationProvider",
]
