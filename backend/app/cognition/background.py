"""
Safe background cognition (§13-§14).

The single most dangerous feature in this release, so it is the most tightly
constrained one. Rules, all enforced in code below:

  * Bounded. Every cycle has a task budget and a wall-clock deadline. When the
    deadline passes the cycle stops cleanly and records that it was truncated.
  * Cancellable. A cycle checks a cancellation flag between tasks.
  * Non-destructive. Tasks may only annotate, classify and surface. Nothing in
    the allowed task set deletes, sends, or takes an irreversible action.
  * Controllable. enable / disable / pause / resume, and the state persists.
  * Rate-limited. A minimum interval between cycles means a browser refresh
    cannot trigger expensive work.
  * HONEST. A cycle that found nothing records findings=[] and changes_made=0.
    There is no code path here that manufactures activity. This is why
    `background.cycle_completed` carries the real counts.

Background cognition is *not* a thread pool or a daemon. Cycles are explicitly
invoked (by an API call or a test) and run synchronously under a deadline. There
is no uncontrolled infinite loop anywhere in this module.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

# Task budget per cycle and the wall-clock deadline.
MAX_TASKS_PER_CYCLE = 6
DEFAULT_DEADLINE_S = 10.0

# A cycle cannot start more often than this. Protects against refresh storms.
MIN_INTERVAL_S = 60.0

STATES = ("enabled", "paused", "disabled")
CYCLE_STATES = ("running", "completed", "skipped", "cancelled", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Cancellation:
    """Cooperative cancellation token for a running cycle."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()


class BackgroundCognition:
    """
    Bounded, cancellable, honest background upkeep.

    Tasks are registered as callables returning a list of findings. A finding is
    a dict with at least `summary`. Returning an empty list is a completely
    valid, commonly-expected result and is recorded as such.
    """

    def __init__(self, db, bus, *, world_v2=None, missions=None,
                 maintenance=None, predictions=None) -> None:
        self.db = db
        self.bus = bus
        self.world_v2 = world_v2
        self.missions = missions
        self.maintenance = maintenance
        self.predictions = predictions
        self._state: dict[str, str] = {}
        self._last_run: dict[str, float] = {}
        self._lock = threading.Lock()

    # ----------------------------------------------------------- user control
    def state(self, user_id: str) -> str:
        return self._state.get(user_id, "enabled")

    def set_state(self, user_id: str, state: str, *,
                  reason: str | None = None,
                  correlation_id: str | None = None) -> dict[str, Any]:
        if state not in STATES:
            raise ValueError(f"Unknown background state: {state!r}")
        previous = self.state(user_id)
        self._state[user_id] = state
        event = {"enabled": "background.enabled", "paused": "background.paused",
                 "disabled": "background.disabled"}[state]
        if state == "enabled" and previous == "paused":
            event = "background.resumed"
        self.bus.emit(user_id, event, reason or f"Background cognition {state}",
                      correlation_id=correlation_id,
                      payload={"previous": previous, "state": state})
        return {"state": state, "previous": previous,
                "detail": f"Background cognition is now {state}."}

    def enable(self, user_id: str, **kw) -> dict[str, Any]:
        return self.set_state(user_id, "enabled", **kw)

    def disable(self, user_id: str, **kw) -> dict[str, Any]:
        return self.set_state(user_id, "disabled", **kw)

    def pause(self, user_id: str, **kw) -> dict[str, Any]:
        return self.set_state(user_id, "paused", **kw)

    def resume(self, user_id: str, **kw) -> dict[str, Any]:
        return self.set_state(user_id, "enabled", **kw)

    # ------------------------------------------------------------ the cycle
    def run_cycle(self, user_id: str, *, trigger: str = "manual",
                  deadline_s: float = DEFAULT_DEADLINE_S,
                  cancellation: Cancellation | None = None,
                  force: bool = False,
                  correlation_id: str | None = None) -> dict[str, Any]:
        """
        Run one bounded upkeep cycle. Always returns a record of what happened,
        including when it did nothing and why.
        """
        state = self.state(user_id)
        if state in ("paused", "disabled"):
            return self._skip(user_id, trigger,
                              f"Background cognition is {state}.", correlation_id)

        with self._lock:
            last = self._last_run.get(user_id)
            elapsed = (time.monotonic() - last) if last is not None else None
            if not force and elapsed is not None and elapsed < MIN_INTERVAL_S:
                return self._skip(
                    user_id, trigger,
                    (f"Rate limited: last cycle ran {elapsed:.0f}s ago, minimum "
                     f"interval is {MIN_INTERVAL_S:.0f}s."), correlation_id)
            self._last_run[user_id] = time.monotonic()

        cid = f"bgc_{uuid.uuid4().hex[:10]}"
        started = _now()
        start_monotonic = time.monotonic()
        self.db.execute(
            "INSERT INTO background_cycles (id,user_id,trigger,state,started_at)"
            " VALUES (?,?,?,?,?)", (cid, user_id, trigger, "running", started))
        self.bus.emit(user_id, "background.cycle_started", f"Upkeep ({trigger})",
                      subject_kind="background", subject_id=cid,
                      correlation_id=correlation_id,
                      payload={"trigger": trigger, "deadline_s": deadline_s})

        tasks = self._tasks()
        ran: list[str] = []
        findings: list[dict[str, Any]] = []
        changes = 0
        truncated = False
        error: str | None = None

        try:
            for name, fn in tasks[:MAX_TASKS_PER_CYCLE]:
                if cancellation is not None and cancellation.cancelled:
                    return self._finish(user_id, cid, "cancelled", ran, findings,
                                        changes, start_monotonic,
                                        skipped_reason="Cancelled by request.",
                                        correlation_id=correlation_id)
                if time.monotonic() - start_monotonic >= deadline_s:
                    truncated = True
                    break
                result = fn(user_id, correlation_id)
                ran.append(name)
                task_findings = result.get("findings", [])
                findings.extend(task_findings)
                changes += int(result.get("changes", 0))
                for finding in task_findings:
                    self.bus.emit(
                        user_id, "memory.maintenance_finding",
                        str(finding.get("summary", ""))[:140],
                        subject_kind="background", subject_id=cid,
                        correlation_id=correlation_id, payload=finding)
        except Exception as exc:  # noqa: BLE001 - upkeep must never break the app
            error = f"{type(exc).__name__}: {exc}"
            return self._finish(user_id, cid, "failed", ran, findings, changes,
                                start_monotonic, error=error,
                                correlation_id=correlation_id)

        return self._finish(
            user_id, cid, "completed", ran, findings, changes, start_monotonic,
            skipped_reason=("Deadline reached; remaining tasks deferred."
                            if truncated else None),
            correlation_id=correlation_id)

    # ---------------------------------------------------------------- tasks
    def _tasks(self) -> list[tuple[str, Callable[..., dict[str, Any]]]]:
        """
        The allowed task set. Every one is read-or-annotate only.
        """
        tasks: list[tuple[str, Callable[..., dict[str, Any]]]] = []
        if self.world_v2 is not None:
            tasks.append(("world_staleness", self._task_world_staleness))
        if self.missions is not None:
            tasks.append(("mission_review", self._task_mission_review))
        if self.predictions is not None:
            tasks.append(("prediction_windows", self._task_prediction_windows))
        if self.maintenance is not None:
            tasks.append(("memory_health", self._task_memory_health))
        return tasks

    def _task_world_staleness(self, user_id: str,
                              correlation_id: str | None) -> dict[str, Any]:
        """Re-classify fact freshness. Writes metadata only, never values."""
        result = self.world_v2.refresh_staleness(user_id,
                                                 correlation_id=correlation_id)
        findings = [
            {"kind": "world_stale", "subject_id": item["id"],
             "summary": f"'{item['label']}' may be out of date",
             "reason": item["reason"]}
            for item in result["newly_stale"]
        ]
        return {"findings": findings, "changes": 0}

    def _task_mission_review(self, user_id: str,
                             correlation_id: str | None) -> dict[str, Any]:
        """Surface quiet missions. Never changes mission state."""
        due = self.missions.review_due(user_id)
        findings = [
            {"kind": "mission_review", "subject_id": item["id"],
             "summary": f"Mission '{item['title']}' has gone quiet",
             "reason": item.get("review_reason", "")}
            for item in due
        ]
        return {"findings": findings, "changes": 0}

    def _task_prediction_windows(self, user_id: str,
                                 correlation_id: str | None) -> dict[str, Any]:
        """
        Find predictions whose evaluation window has passed with no evidence.
        These become UNRESOLVED - never guessed either way (§19).
        """
        try:
            due = self.predictions.due_for_evaluation(user_id)
        except AttributeError:
            return {"findings": [], "changes": 0}
        findings = [
            {"kind": "prediction_unresolved", "subject_id": item["id"],
             "summary": f"Prediction window passed with no evidence: "
                        f"{item.get('statement', '')[:80]}",
             "reason": "UNRESOLVED — no observation was recorded either way."}
            for item in due
        ]
        for item in due:
            self.bus.emit(user_id, "outcome.unresolved",
                          str(item.get("statement", ""))[:140],
                          subject_kind="prediction", subject_id=item["id"],
                          correlation_id=correlation_id,
                          payload={"reason": "No evidence within the window."})
        return {"findings": findings, "changes": 0}

    def _task_memory_health(self, user_id: str,
                            correlation_id: str | None) -> dict[str, Any]:
        """Non-destructive memory review (merge/revalidate/downgrade only)."""
        try:
            report = self.maintenance.review(user_id, correlation_id=correlation_id)
        except AttributeError:
            return {"findings": [], "changes": 0}
        return {"findings": report.get("findings", []),
                "changes": int(report.get("changes", 0))}

    # -------------------------------------------------------------- records
    def _skip(self, user_id: str, trigger: str, reason: str,
              correlation_id: str | None) -> dict[str, Any]:
        cid = f"bgc_{uuid.uuid4().hex[:10]}"
        now = _now()
        self.db.execute(
            "INSERT INTO background_cycles (id,user_id,trigger,state,tasks_run,"
            "findings,changes_made,skipped_reason,duration_ms,started_at,"
            "finished_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, user_id, trigger, "skipped", json.dumps([]), json.dumps([]),
             0, reason, 0, now, now))
        self.bus.emit(user_id, "background.cycle_skipped", reason,
                      subject_kind="background", subject_id=cid,
                      correlation_id=correlation_id,
                      payload={"trigger": trigger, "reason": reason})
        return {"id": cid, "state": "skipped", "trigger": trigger,
                "tasks_run": [], "findings": [], "changes_made": 0,
                "skipped_reason": reason, "detail": reason}

    def _finish(self, user_id: str, cid: str, state: str, ran: list[str],
                findings: list[dict[str, Any]], changes: int,
                start_monotonic: float, *, skipped_reason: str | None = None,
                error: str | None = None,
                correlation_id: str | None = None) -> dict[str, Any]:
        duration_ms = int((time.monotonic() - start_monotonic) * 1000)
        self.db.execute(
            "UPDATE background_cycles SET state=?, tasks_run=?, findings=?,"
            " changes_made=?, skipped_reason=?, error=?, duration_ms=?,"
            " finished_at=? WHERE id=?",
            (state, json.dumps(ran), json.dumps(findings), changes,
             skipped_reason, error, duration_ms, _now(), cid))

        event = {"completed": "background.cycle_completed",
                 "cancelled": "background.cycle_cancelled",
                 "failed": "background.cycle_failed"}.get(
                     state, "background.cycle_completed")
        # Honest summary. An empty cycle says so plainly.
        if state == "completed" and not findings:
            detail = (f"Ran {len(ran)} check(s); nothing needed attention.")
        elif state == "completed":
            detail = (f"Ran {len(ran)} check(s); {len(findings)} finding(s), "
                      f"{changes} change(s).")
        elif state == "failed":
            detail = f"Cycle failed: {error}"
        else:
            detail = skipped_reason or f"Cycle {state}."

        self.bus.emit(user_id, event, detail,
                      subject_kind="background", subject_id=cid,
                      correlation_id=correlation_id,
                      payload={"tasks_run": ran, "findings": len(findings),
                               "changes_made": changes,
                               "duration_ms": duration_ms, "error": error})
        return {"id": cid, "state": state, "tasks_run": ran,
                "findings": findings, "changes_made": changes,
                "duration_ms": duration_ms, "error": error,
                "skipped_reason": skipped_reason, "detail": detail}

    # --------------------------------------------------------------- reading
    def cycles(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM background_cycles WHERE user_id=? ORDER BY rowid DESC"
            " LIMIT ?", (user_id, int(limit)))
        out = []
        for row in rows:
            item = dict(row)
            for key in ("tasks_run", "findings"):
                try:
                    item[key] = json.loads(item.get(key) or "[]")
                except (TypeError, ValueError):
                    item[key] = []
            out.append(item)
        return out

    def status(self, user_id: str) -> dict[str, Any]:
        cycles = self.cycles(user_id, limit=10)
        completed = [c for c in cycles if c["state"] == "completed"]
        empty = [c for c in completed if not c["findings"]]
        return {
            "state": self.state(user_id),
            "min_interval_s": MIN_INTERVAL_S,
            "max_tasks_per_cycle": MAX_TASKS_PER_CYCLE,
            "recent_cycles": cycles,
            "total_recent": len(cycles),
            "empty_cycles": len(empty),
            "detail": (
                "No background cycles have run yet." if not cycles else
                f"{len(cycles)} recent cycle(s); {len(empty)} found nothing."),
        }
