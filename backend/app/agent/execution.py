"""
V8.2 — Execution tracing and tool-loop safety for the agent (§4).

This module owns the *mechanics* of running a model-driven turn safely:

  * a structured trace with the canonical stages
        MODEL_CALL, TOOL_DECISION, TOOL_RESULT, MODEL_REVISION, FINAL_RESPONSE
  * maximum tool-loop depth
  * per-turn deadline (timeout) handling
  * duplicate tool-call detection
  * recovery after a tool failure
  * cooperative cancellation

It deliberately contains NO routing rules about which tool to call for which
message: that decision belongs to the model. The only keyword logic left in the
codebase is the clearly-labelled deterministic fallback planner.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Canonical trace stages.
MODEL_CALL = "MODEL_CALL"
TOOL_SURFACE = "TOOL_SURFACE"
TOOL_DECISION = "TOOL_DECISION"
TOOL_RESULT = "TOOL_RESULT"
MODEL_REVISION = "MODEL_REVISION"
FINAL_RESPONSE = "FINAL_RESPONSE"
CONTEXT_BUILD = "CONTEXT_BUILD"
DEGRADED = "PROVIDER_DEGRADED"
CANCELLED = "CANCELLED"
LIMIT_REACHED = "LIMIT_REACHED"
TOOL_FAILED = "TOOL_FAILED"
DUPLICATE_TOOL_CALL = "DUPLICATE_TOOL_CALL"

STAGES = (MODEL_CALL, TOOL_SURFACE, TOOL_DECISION, TOOL_RESULT, MODEL_REVISION,
          FINAL_RESPONSE, CONTEXT_BUILD, DEGRADED, CANCELLED, LIMIT_REACHED,
          TOOL_FAILED, DUPLICATE_TOOL_CALL)

# Bus event emitted per stage, where one exists.
_STAGE_EVENT = {
    MODEL_CALL: "execution.model_call",
    TOOL_SURFACE: "routing.tool_surface",
    TOOL_DECISION: "execution.tool_decision",
    TOOL_RESULT: "execution.tool_result",
    MODEL_REVISION: "execution.model_revision",
    FINAL_RESPONSE: "execution.final_response",
    CANCELLED: "execution.cancelled",
    LIMIT_REACHED: "execution.limit_reached",
}

DEFAULT_MAX_TOOL_DEPTH = 4


def normalize_model_tool_args(args: Any) -> Any:
    """
    Normalise a RAW model-generated tool-call argument structure at the
    actual execution boundary, before LangChain/Pydantic validation.

    V8.5.1 Windows verification: llama3.2:3b correctly selected
    `list_missions` but serialised the JSON null token as the STRING
    "null" ({"open_only": "null"}), which strict schema validation
    rejected and the user saw as TOOL_FAILED. The exact lowercase token
    "null" is the one spelling JSON permits for null — it is unambiguously
    a null representation, never a plausible value.

    Rules (deliberately minimal — this is NOT a coercion layer):
      * the exact string "null" -> None, recursively through dicts/lists;
      * EVERYTHING else is preserved byte-for-byte: "NULL", "None", "nil",
        "banana", "", "true", "false" all pass through unchanged so normal
        strict validation still rejects what it should reject;
      * real booleans, real nulls, numbers and nested structures are
        returned untouched.
    """
    if isinstance(args, dict):
        return {key: normalize_model_tool_args(value)
                for key, value in args.items()}
    if isinstance(args, list):
        return [normalize_model_tool_args(value) for value in args]
    if isinstance(args, str) and args == "null":
        return None
    return args


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Cancellation:
    """A cooperative cancellation token for one turn."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


@dataclass
class ExecutionTrace:
    """
    An ordered, persistable record of how one turn actually executed.

    The trace is the single source of truth for the activity trail shown in the
    UI: nothing is added to it that did not happen.
    """

    correlation_id: str
    user_id: str
    thread_id: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    deadline_s: float | None = None
    max_tool_depth: int = DEFAULT_MAX_TOOL_DEPTH
    cancellation: Cancellation | None = None
    _seen_tool_calls: set[str] = field(default_factory=set)

    # ------------------------------------------------------------------ steps
    def add(self, stage: str, detail: str = "",
            **payload: Any) -> dict[str, Any]:
        """Append one step. Unknown stages are rejected so the trace can't drift."""
        if stage not in STAGES:
            raise ValueError(f"Unknown execution stage: {stage!r}")
        step = {"type": stage, "step": len(self.steps) + 1, "detail": detail,
                "at": _now(), **payload}
        self.steps.append(step)
        return step

    # ------------------------------------------------------------ constraints
    @property
    def elapsed_s(self) -> float:
        return time.monotonic() - self.started_at

    def timed_out(self) -> bool:
        return (self.deadline_s is not None
                and self.elapsed_s >= self.deadline_s)

    def cancelled(self) -> bool:
        return bool(self.cancellation and self.cancellation.cancelled)

    def tool_rounds(self) -> int:
        return sum(1 for s in self.steps if s["type"] == TOOL_DECISION)

    def depth_exceeded(self) -> bool:
        return self.tool_rounds() >= self.max_tool_depth

    def remaining_s(self) -> float | None:
        if self.deadline_s is None:
            return None
        return max(0.0, self.deadline_s - self.elapsed_s)

    def should_stop(self) -> tuple[bool, str | None]:
        """
        Whether the loop must stop now, and why.

        Checked before every model call and before every tool execution.
        """
        if self.cancelled():
            return True, "cancelled by the caller"
        if self.timed_out():
            return True, (f"turn exceeded its {self.deadline_s:.0f}s budget")
        if self.depth_exceeded():
            return True, (f"reached the maximum of {self.max_tool_depth} tool "
                          "rounds")
        return False, None

    # ------------------------------------------------------ duplicate detection
    @staticmethod
    def call_signature(name: str, args: Any) -> str:
        try:
            body = json.dumps(args, sort_keys=True, default=str)
        except (TypeError, ValueError):
            body = str(args)
        return f"{name}:{body}"

    def is_duplicate(self, name: str, args: Any) -> bool:
        """True when this exact tool call was already made in this turn."""
        return self.call_signature(name, args) in self._seen_tool_calls

    def remember_call(self, name: str, args: Any) -> None:
        self._seen_tool_calls.add(self.call_signature(name, args))

    # -------------------------------------------------------------- rendering
    def activity(self) -> list[dict[str, Any]]:
        """The trace in the shape the existing API/UI already consumes."""
        return list(self.steps)

    def summary(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "steps": len(self.steps),
            "model_calls": sum(1 for s in self.steps if s["type"] == MODEL_CALL),
            "tool_rounds": self.tool_rounds(),
            "tool_results": sum(1 for s in self.steps if s["type"] == TOOL_RESULT),
            "revisions": sum(1 for s in self.steps if s["type"] == MODEL_REVISION),
            "tool_failures": sum(1 for s in self.steps if s["type"] == TOOL_FAILED),
            "duplicates_blocked": sum(1 for s in self.steps
                                      if s["type"] == DUPLICATE_TOOL_CALL),
            "elapsed_s": round(self.elapsed_s, 3),
            "cancelled": self.cancelled(),
            "timed_out": self.timed_out(),
            "depth_limited": self.depth_exceeded(),
        }


class TraceRecorder:
    """
    Persists execution traces and mirrors the important stages onto the
    canonical cognitive event bus.

    Writing to the bus keeps the Observatory a *view* of one event stream
    instead of a second store.
    """

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def new_trace(self, user_id: str, *, correlation_id: str | None = None,
                  thread_id: str | None = None, deadline_s: float | None = None,
                  max_tool_depth: int = DEFAULT_MAX_TOOL_DEPTH,
                  cancellation: Cancellation | None = None) -> ExecutionTrace:
        return ExecutionTrace(
            correlation_id=correlation_id or f"turn_{uuid.uuid4().hex[:12]}",
            user_id=user_id, thread_id=thread_id, deadline_s=deadline_s,
            max_tool_depth=max_tool_depth, cancellation=cancellation)

    def record(self, trace: ExecutionTrace, stage: str, detail: str = "",
               **payload: Any) -> dict[str, Any]:
        """Add a step, persist it, and emit the matching bus event."""
        step = trace.add(stage, detail, **payload)
        try:
            self.db.execute(
                "INSERT INTO execution_traces (user_id,correlation_id,thread_id,"
                "step,stage,detail,payload,created_at) VALUES (?,?,?,?,?,?,?,?)",
                (trace.user_id, trace.correlation_id, trace.thread_id,
                 step["step"], stage, detail[:400],
                 json.dumps(payload, default=str), step["at"]))
        except Exception:  # observability must never break a conversation
            pass

        event = _STAGE_EVENT.get(stage)
        if event:
            try:
                self.bus.emit(trace.user_id, event, detail[:200] or stage,
                              thread_id=trace.thread_id,
                              subject_kind="execution",
                              subject_id=trace.correlation_id,
                              correlation_id=trace.correlation_id,
                              payload={k: v for k, v in payload.items()
                                       if k != "raw"})
            except Exception:
                pass
        return step

    # ------------------------------------------------------------------ reads
    def for_correlation(self, correlation_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM execution_traces WHERE correlation_id=?"
            " ORDER BY step ASC", (correlation_id,))
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"] or "{}")
            except json.JSONDecodeError:
                d["payload"] = {}
            out.append(d)
        return out

    def recent(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT DISTINCT correlation_id, MAX(id) AS last_id FROM"
            " execution_traces WHERE user_id=? GROUP BY correlation_id"
            " ORDER BY last_id DESC LIMIT ?", (user_id, limit))
        return [{"correlation_id": r["correlation_id"],
                 "steps": self.for_correlation(r["correlation_id"])}
                for r in rows]


def run_tool_safely(trace: ExecutionTrace, recorder: TraceRecorder | None,
                    tool, args: dict[str, Any]) -> dict[str, Any]:
    """
    Invoke one real tool with duplicate detection and failure recovery.

    Returns {ok, content, duplicate, error}. A failure is never raised into the
    agent loop: the model receives the error text and gets a chance to recover.
    """
    name = getattr(tool, "name", str(tool))
    # V8.5.1: the args dict is RAW model output. Normalise the stringified
    # JSON null token ("null" -> None) HERE — the actual execution boundary —
    # before LangChain hands it to strict schema validation. Nothing else is
    # coerced; see normalize_model_tool_args.
    args = normalize_model_tool_args(args)

    def emit(stage: str, detail: str, **payload: Any) -> None:
        if recorder is not None:
            recorder.record(trace, stage, detail, **payload)
        else:
            trace.add(stage, detail, **payload)

    if trace.is_duplicate(name, args):
        message = (f"DUPLICATE_CALL: '{name}' was already called with these exact "
                   "arguments in this turn. Use the previous result instead of "
                   "repeating it.")
        emit(DUPLICATE_TOOL_CALL, f"Blocked a repeat call to {name}", tool=name)
        return {"ok": False, "content": message, "duplicate": True, "error": None}

    trace.remember_call(name, args)
    try:
        content = tool.invoke(args)
        emit(TOOL_RESULT, f"{name} returned a result", tool=name,
             chars=len(str(content)))
        return {"ok": True, "content": str(content), "duplicate": False,
                "error": None}
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"[:300]
        emit(TOOL_FAILED, f"{name} failed: {detail}", tool=name, error=detail)
        # Hand the failure back to the model as a tool result so it can recover
        # rather than aborting the turn.
        return {"ok": False,
                "content": (f"TOOL_ERROR: {name} failed ({detail}). Do not retry "
                            "the same call; answer with what you already have or "
                            "say what you could not do."),
                "duplicate": False, "error": detail}
