"""
Long-running missions (§10-§12).

A mission is an objective that outlives a single conversation. The V8.2 event
vocabulary already reserved `mission.*` names but nothing implemented them; this
module is the entity behind those events.

Lifecycle:

    draft ──activate──> active ──┬──> waiting ──┐
                        │  ▲     ├──> blocked ──┤
                        │  └─────┴──> paused ───┘
                        ├──> completed
                        ├──> failed
                        └──> abandoned

Design rules:
  * Planning is *bounded*. A mission holds a small set of concrete next steps,
    not an auto-generated tree. Replanning only happens on real evidence.
  * Progress is only ever derived from completed steps or an explicit user
    statement. It is never estimated to make a dashboard look alive.
  * Every state change writes a mission_event with a reason, so mission history
    is fully reconstructable (this is what world state lacked in V8.2).
  * A mission blocked on something the user must do says exactly that, and the
    system does not pretend to be working on it in the background.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

STATES = ("draft", "active", "waiting", "blocked", "paused", "completed",
          "failed", "abandoned")

# States in which a mission is still a live concern for the user.
OPEN_STATES = ("draft", "active", "waiting", "blocked", "paused")
TERMINAL_STATES = ("completed", "failed", "abandoned")

STEP_STATES = ("pending", "in_progress", "done", "skipped", "blocked")
STEP_KINDS = ("task", "decision", "question", "wait", "review")

# A mission is only replanned when there is a real reason to.
REPLAN_REASONS = ("step_completed", "blocked", "unblocked", "user_request",
                  "evidence_changed", "deadline_changed")

# Bounded planning: we never hold more than this many open steps.
MAX_OPEN_STEPS = 7

# After this long with no activity a mission is surfaced for review rather than
# silently forgotten. It is NOT auto-abandoned - only the user abandons things.
REVIEW_AFTER_DAYS = 21


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class MissionRegistry:
    """Persistent missions with honest, fully-evidenced state transitions."""

    def __init__(self, db, bus, observations=None) -> None:
        self.db = db
        self.bus = bus
        self.observations = observations

    # ------------------------------------------------------------- creation
    def create(self, user_id: str, title: str, *, description: str | None = None,
               scope: str | None = None, constraints: list[str] | None = None,
               success_criteria: list[str] | None = None,
               priority: float = 0.5, source: str = "conversation",
               confidence: float = 0.6, evidence: list[str] | None = None,
               state: str = "draft",
               correlation_id: str | None = None) -> dict[str, Any]:
        title = " ".join((title or "").split())[:200]
        if len(title) < 3:
            raise ValueError("A mission needs a meaningful title.")
        if state not in STATES:
            raise ValueError(f"Unknown mission state: {state!r}")

        mid = f"ms_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO missions (id,user_id,title,description,state,priority,"
            "scope,constraints,success_criteria,progress,source,confidence,"
            "evidence,created_at,updated_at,last_activity_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (mid, user_id, title, description, state,
             max(0.0, min(1.0, float(priority))), scope,
             json.dumps(constraints or []), json.dumps(success_criteria or []),
             0.0, source, max(0.0, min(1.0, float(confidence))),
             json.dumps(evidence or []), now, now, now))

        self._log(mid, user_id, "created", None, state,
                  reason="Mission created", evidence=evidence,
                  correlation_id=correlation_id)
        self.bus.emit(user_id, "mission.created", title,
                      subject_kind="mission", subject_id=mid,
                      correlation_id=correlation_id,
                      payload={"state": state, "priority": priority})
        return self.get(user_id, mid)  # type: ignore[return-value]

    # --------------------------------------------------------- state changes
    def set_state(self, user_id: str, mission_id: str, state: str, *,
                  reason: str, evidence: list[str] | None = None,
                  blocked_reason: str | None = None,
                  waiting_on: str | None = None,
                  correlation_id: str | None = None) -> dict[str, Any] | None:
        if state not in STATES:
            raise ValueError(f"Unknown mission state: {state!r}")
        mission = self.get(user_id, mission_id)
        if mission is None:
            return None
        previous = mission["state"]
        if previous == state:
            return mission

        now = _now()
        completed_at = now if state == "completed" else mission.get("completed_at")
        self.db.execute(
            "UPDATE missions SET state=?, blocked_reason=?, waiting_on=?,"
            " updated_at=?, last_activity_at=?, completed_at=? WHERE id=?",
            (state, blocked_reason, waiting_on, now, now, completed_at, mission_id))

        self._log(mission_id, user_id, "state_changed", previous, state,
                  reason=reason, evidence=evidence, correlation_id=correlation_id)

        event = {
            "active": "mission.activated",
            "blocked": "mission.blocked",
            "waiting": "mission.waiting",
            "paused": "mission.paused",
            "completed": "mission.completed",
            "failed": "mission.failed",
            "abandoned": "mission.abandoned",
        }.get(state, "mission.updated")
        if state == "active" and previous in ("blocked", "waiting"):
            event = "mission.unblocked"
        elif state == "active" and previous == "paused":
            event = "mission.resumed"

        self.bus.emit(user_id, event, f"{mission['title']} → {state}",
                      subject_kind="mission", subject_id=mission_id,
                      correlation_id=correlation_id,
                      payload={"previous": previous, "state": state,
                               "reason": reason,
                               "blocked_reason": blocked_reason,
                               "waiting_on": waiting_on})
        if self.observations is not None:
            self.observations.record(
                user_id, f"Mission '{mission['title']}' moved {previous} → {state}: {reason}",
                source="mission", origin=mission_id, epistemic_status="OBSERVED",
                confidence=0.95, subject_kind="mission", subject_id=mission_id,
                correlation_id=correlation_id)
        return self.get(user_id, mission_id)

    # ------------------------------------------------------------ step plans
    def add_step(self, user_id: str, mission_id: str, summary: str, *,
                 kind: str = "task", depends_on: str | None = None,
                 evidence: list[str] | None = None,
                 correlation_id: str | None = None) -> dict[str, Any]:
        if kind not in STEP_KINDS:
            raise ValueError(f"Unknown step kind: {kind!r}")
        mission = self.get(user_id, mission_id)
        if mission is None:
            raise KeyError(mission_id)
        summary = " ".join((summary or "").split())[:300]
        if len(summary) < 3:
            raise ValueError("A step needs a meaningful summary.")

        open_steps = [s for s in mission["steps"] if s["state"] in ("pending", "in_progress")]
        if len(open_steps) >= MAX_OPEN_STEPS:
            return {
                "added": False,
                "reason": (f"Planning is bounded to {MAX_OPEN_STEPS} open steps; "
                           f"close one before adding another."),
            }

        sid = f"mst_{uuid.uuid4().hex[:10]}"
        now = _now()
        self.db.execute(
            "INSERT INTO mission_steps (id,mission_id,user_id,summary,state,kind,"
            "depends_on,evidence,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, mission_id, user_id, summary, "pending", kind, depends_on,
             json.dumps(evidence or []), now, now))
        self._touch(mission_id)
        self.bus.emit(user_id, "mission.step_added", summary,
                      subject_kind="mission", subject_id=mission_id,
                      correlation_id=correlation_id,
                      payload={"step_id": sid, "kind": kind})
        return {"added": True, "step": self._step(sid)}

    def complete_step(self, user_id: str, step_id: str, *,
                      evidence: list[str] | None = None,
                      correlation_id: str | None = None) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM mission_steps WHERE id=? AND user_id=?", (step_id, user_id))
        if row is None:
            return None
        now = _now()
        self.db.execute(
            "UPDATE mission_steps SET state='done', evidence=?, updated_at=?,"
            " completed_at=? WHERE id=?",
            (json.dumps(evidence or []), now, now, step_id))
        mission_id = row["mission_id"]
        self._touch(mission_id)
        progress = self._recompute_progress(user_id, mission_id)
        self.bus.emit(user_id, "mission.step_completed", row["summary"],
                      subject_kind="mission", subject_id=mission_id,
                      correlation_id=correlation_id,
                      payload={"step_id": step_id, "progress": progress})
        self.bus.emit(user_id, "mission.progressed",
                      f"Progress now {int(progress * 100)}%",
                      subject_kind="mission", subject_id=mission_id,
                      correlation_id=correlation_id,
                      payload={"progress": progress})
        self._log(mission_id, user_id, "step_completed", None, None,
                  reason=f"Step completed: {row['summary']}", evidence=evidence,
                  correlation_id=correlation_id)
        return self.get(user_id, mission_id)

    def _recompute_progress(self, user_id: str, mission_id: str) -> float:
        """
        Progress is the fraction of *known* steps that are done. If a mission
        has no steps, progress stays at whatever was explicitly set - we do not
        invent a number.
        """
        rows = self.db.query(
            "SELECT state FROM mission_steps WHERE mission_id=?", (mission_id,))
        counted = [r for r in rows if r["state"] != "skipped"]
        if not counted:
            return float(self.db.query_one(
                "SELECT progress FROM missions WHERE id=?", (mission_id,))["progress"])
        done = sum(1 for r in counted if r["state"] == "done")
        progress = round(done / len(counted), 3)
        self.db.execute("UPDATE missions SET progress=?, updated_at=? WHERE id=?",
                        (progress, _now(), mission_id))
        return progress

    def set_next_step(self, user_id: str, mission_id: str, next_step: str | None,
                      *, correlation_id: str | None = None) -> dict[str, Any] | None:
        mission = self.get(user_id, mission_id)
        if mission is None:
            return None
        self.db.execute("UPDATE missions SET next_step=?, updated_at=? WHERE id=?",
                        (next_step, _now(), mission_id))
        return self.get(user_id, mission_id)

    def replan(self, user_id: str, mission_id: str, *, reason: str,
               steps: list[str] | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Bounded replanning. Only valid for a declared reason - a mission does
        not spontaneously rewrite its own plan.
        """
        if reason not in REPLAN_REASONS:
            return {"replanned": False,
                    "reason": (f"'{reason}' is not a recognised replanning "
                               f"trigger; expected one of {list(REPLAN_REASONS)}.")}
        mission = self.get(user_id, mission_id)
        if mission is None:
            raise KeyError(mission_id)

        added: list[dict[str, Any]] = []
        for summary in (steps or [])[:MAX_OPEN_STEPS]:
            result = self.add_step(user_id, mission_id, summary,
                                   correlation_id=correlation_id)
            if result.get("added"):
                added.append(result["step"])

        self._log(mission_id, user_id, "replanned", None, None, reason=reason,
                  evidence=[s["summary"] for s in added],
                  correlation_id=correlation_id)
        self.bus.emit(user_id, "mission.replanned", reason,
                      subject_kind="mission", subject_id=mission_id,
                      correlation_id=correlation_id,
                      payload={"steps_added": len(added), "reason": reason})
        return {"replanned": True, "steps_added": len(added),
                "steps": added, "mission": self.get(user_id, mission_id)}

    # --------------------------------------------------------------- linking
    def link(self, mission_id: str, subject_kind: str, subject_id: str,
             relation: str = "relates_to") -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO mission_links (mission_id,subject_kind,"
            "subject_id,relation,created_at) VALUES (?,?,?,?,?)",
            (mission_id, subject_kind, subject_id, relation, _now()))

    def links(self, mission_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM mission_links WHERE mission_id=?", (mission_id,))]

    # --------------------------------------------------------------- reading
    def get(self, user_id: str, mission_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM missions WHERE id=? AND user_id=?",
                                (mission_id, user_id))
        if row is None:
            return None
        mission = self._row(row)
        mission["steps"] = [self._step_row(r) for r in self.db.query(
            "SELECT * FROM mission_steps WHERE mission_id=? ORDER BY created_at",
            (mission_id,))]
        mission["links"] = self.links(mission_id)
        return mission

    def list(self, user_id: str, *, state: str | None = None,
             open_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM missions WHERE user_id=?"
        params: list[Any] = [user_id]
        if state:
            sql += " AND state=?"
            params.append(state)
        elif open_only:
            sql += f" AND state IN ({','.join('?' * len(OPEN_STATES))})"
            params.extend(OPEN_STATES)
        sql += " ORDER BY priority DESC, updated_at DESC LIMIT ?"
        params.append(int(limit))
        out = []
        for row in self.db.query(sql, params):
            mission = self._row(row)
            mission["steps"] = [self._step_row(r) for r in self.db.query(
                "SELECT * FROM mission_steps WHERE mission_id=? ORDER BY created_at",
                (row["id"],))]
            out.append(mission)
        return out

    def history(self, user_id: str, mission_id: str,
                limit: int = 100) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM mission_events WHERE mission_id=? AND user_id=?"
            " ORDER BY rowid DESC LIMIT ?", (mission_id, user_id, int(limit)))
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["evidence"] = json.loads(item.get("evidence") or "[]")
            except (TypeError, ValueError):
                item["evidence"] = []
            out.append(item)
        return out

    # ------------------------------------------------------------ continuity
    def resume_brief(self, user_id: str) -> dict[str, Any]:
        """
        What a returning user needs to know about their missions (§11).

        Honest about staleness: a mission nobody has touched for weeks is
        flagged for review, not quietly presented as if it were progressing.
        """
        missions = self.list(user_id, open_only=True)
        now = datetime.now(timezone.utc)
        needs_review: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        waiting: list[dict[str, Any]] = []
        active: list[dict[str, Any]] = []

        for mission in missions:
            last = _parse(mission.get("last_activity_at")) or _parse(mission["created_at"])
            days = (now - last).days if last else 0
            entry = {"id": mission["id"], "title": mission["title"],
                     "state": mission["state"], "progress": mission["progress"],
                     "days_since_activity": days,
                     "next_step": mission.get("next_step")}
            if mission["state"] == "blocked":
                entry["blocked_reason"] = mission.get("blocked_reason")
                blocked.append(entry)
            elif mission["state"] == "waiting":
                entry["waiting_on"] = mission.get("waiting_on")
                waiting.append(entry)
            elif mission["state"] == "active":
                active.append(entry)
            if days >= REVIEW_AFTER_DAYS:
                entry["review_reason"] = (
                    f"No recorded activity for {days} days.")
                needs_review.append(entry)

        if not missions:
            summary = "No open missions."
        else:
            parts = []
            if active:
                parts.append(f"{len(active)} active")
            if blocked:
                parts.append(f"{len(blocked)} blocked")
            if waiting:
                parts.append(f"{len(waiting)} waiting")
            summary = ", ".join(parts) or f"{len(missions)} open"
        return {
            "open": len(missions),
            "active": active,
            "blocked": blocked,
            "waiting": waiting,
            "needs_review": needs_review,
            "summary": summary,
        }

    def review_due(self, user_id: str) -> list[dict[str, Any]]:
        """Missions that have gone quiet. Surfaced, never auto-closed."""
        return self.resume_brief(user_id)["needs_review"]

    # ---------------------------------------------------------------- helpers
    def _touch(self, mission_id: str) -> None:
        now = _now()
        self.db.execute(
            "UPDATE missions SET updated_at=?, last_activity_at=? WHERE id=?",
            (now, now, mission_id))

    def _log(self, mission_id: str, user_id: str, change: str,
             previous: str | None, new: str | None, *, reason: str,
             evidence: list[str] | None = None,
             correlation_id: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO mission_events (id,mission_id,user_id,change,"
            "previous_state,new_state,reason,evidence,correlation_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"mev_{uuid.uuid4().hex[:10]}", mission_id, user_id, change,
             previous, new, reason, json.dumps(evidence or []), correlation_id,
             _now()))

    def _step(self, step_id: str) -> dict[str, Any]:
        return self._step_row(self.db.query_one(
            "SELECT * FROM mission_steps WHERE id=?", (step_id,)))

    @staticmethod
    def _step_row(row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["evidence"] = json.loads(item.get("evidence") or "[]")
        except (TypeError, ValueError):
            item["evidence"] = []
        return item

    @staticmethod
    def _row(row) -> dict[str, Any]:
        item = dict(row)
        for key in ("constraints", "success_criteria", "evidence"):
            try:
                item[key] = json.loads(item.get(key) or "[]")
            except (TypeError, ValueError):
                item[key] = []
        return item
