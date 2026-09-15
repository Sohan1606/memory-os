"""
V8.2 — Trust calibration and recovery, per capability (§17).

The key rule: there is no single global trust score. Reliability is tracked per
(capability, task_class) pair, so a failing tool call damages trust in that tool
class only — never in unrelated capabilities such as retrieval or extraction.

Recovery is deliberately cautious: after failures, trust returns only through
demonstrated successes, and a run of consecutive failures suppresses the score
even if the long-run ratio still looks acceptable.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Capability classes the system genuinely has.
CAPABILITIES = (
    "memory_retrieval", "memory_write", "tool_execution", "structured_extraction",
    "model_generation", "prediction", "world_tracking", "action",
)

# Below this many observations we publish no score at all.
MIN_EVIDENCE = 3
# This many consecutive failures suppresses the capability regardless of ratio.
SUPPRESS_AFTER_CONSECUTIVE = 3

LABEL_RELIABLE = "RELIABLE"
LABEL_MIXED = "MIXED"
LABEL_UNRELIABLE = "UNRELIABLE"
LABEL_SUPPRESSED = "SUPPRESSED"
LABEL_UNKNOWN = "INSUFFICIENT EVIDENCE"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CapabilityTrust:
    """Evidence-based reliability, isolated per capability and task class."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def _ensure(self, user_id: str, capability: str, task_class: str) -> None:
        if self.db.query_one(
                "SELECT capability FROM capability_trust WHERE user_id=?"
                " AND capability=? AND task_class=?",
                (user_id, capability, task_class)) is None:
            self.db.execute(
                "INSERT INTO capability_trust (user_id,capability,task_class,"
                "updated_at) VALUES (?,?,?,?)",
                (user_id, capability, task_class, _now()))

    # ------------------------------------------------------------- recording
    def record(self, user_id: str, capability: str, success: bool, *,
               task_class: str = "general", detail: str = "",
               correlation_id: str | None = None) -> dict[str, Any]:
        """Record one real observation of a capability succeeding or failing."""
        if capability not in CAPABILITIES:
            raise ValueError(f"Unknown capability: {capability!r}")
        self._ensure(user_id, capability, task_class)
        before = self.score(user_id, capability, task_class=task_class)

        now = _now()
        if success:
            self.db.execute(
                "UPDATE capability_trust SET successes=successes+1,"
                " consecutive_failures=0, last_success_at=?, updated_at=?"
                " WHERE user_id=? AND capability=? AND task_class=?",
                (now, now, user_id, capability, task_class))
        else:
            self.db.execute(
                "UPDATE capability_trust SET failures=failures+1,"
                " consecutive_failures=consecutive_failures+1, last_failure_at=?,"
                " updated_at=? WHERE user_id=? AND capability=? AND task_class=?",
                (now, now, user_id, capability, task_class))

        after = self.score(user_id, capability, task_class=task_class)

        if after["label"] != before["label"]:
            self.bus.emit(
                user_id, "trust.capability_changed",
                f"{capability} reliability: {before['label']} → {after['label']}",
                subject_kind="capability", subject_id=capability,
                correlation_id=correlation_id,
                payload={"capability": capability, "task_class": task_class,
                         "from": before["label"], "to": after["label"],
                         "detail": detail[:200]})
            if (before["label"] in (LABEL_UNRELIABLE, LABEL_SUPPRESSED)
                    and after["label"] in (LABEL_RELIABLE, LABEL_MIXED)):
                self.bus.emit(
                    user_id, "trust.recovered",
                    f"{capability} is working again ({after['label']})",
                    subject_kind="capability", subject_id=capability,
                    correlation_id=correlation_id,
                    payload={"capability": capability,
                             "task_class": task_class})
        return after

    # ---------------------------------------------------------------- scoring
    def score(self, user_id: str, capability: str, *,
              task_class: str = "general") -> dict[str, Any]:
        """
        Laplace-smoothed reliability with an explicit evidence floor.

        Under MIN_EVIDENCE observations we refuse to publish a number: one
        success is not evidence of reliability.
        """
        row = self.db.query_one(
            "SELECT * FROM capability_trust WHERE user_id=? AND capability=?"
            " AND task_class=?", (user_id, capability, task_class))
        s = int(row["successes"]) if row else 0
        f = int(row["failures"]) if row else 0
        consecutive = int(row["consecutive_failures"]) if row else 0
        total = s + f

        base = {
            "capability": capability, "task_class": task_class,
            "successes": s, "failures": f, "total": total,
            "consecutive_failures": consecutive,
            "last_success_at": row["last_success_at"] if row else None,
            "last_failure_at": row["last_failure_at"] if row else None,
        }

        if consecutive >= SUPPRESS_AFTER_CONSECUTIVE:
            return {**base, "reliability": None, "label": LABEL_SUPPRESSED,
                    "detail": (f"{consecutive} consecutive failures — this "
                               "capability is suppressed until it demonstrably "
                               "works again.")}
        if total < MIN_EVIDENCE:
            return {**base, "reliability": None, "label": LABEL_UNKNOWN,
                    "detail": (f"Only {total} observation(s); at least "
                               f"{MIN_EVIDENCE} are needed before reporting "
                               "reliability.")}

        reliability = (s + 1) / (total + 2)
        if reliability >= 0.85:
            label = LABEL_RELIABLE
        elif reliability >= 0.6:
            label = LABEL_MIXED
        else:
            label = LABEL_UNRELIABLE
        return {**base, "reliability": round(reliability, 3), "label": label,
                "detail": f"{s}/{total} observations succeeded."}

    def all(self, user_id: str) -> list[dict[str, Any]]:
        """
        Every capability, including ones with no evidence.

        Task classes that have accumulated their own evidence are listed
        separately so a failure in one class stays visible as such.
        """
        out = [self.score(user_id, c) for c in CAPABILITIES]
        for row in self.db.query(
                "SELECT capability, task_class FROM capability_trust"
                " WHERE user_id=? AND task_class!='general'", (user_id,)):
            if row["capability"] in CAPABILITIES:
                out.append(self.score(user_id, row["capability"],
                                      task_class=row["task_class"]))
        return out

    def usable(self, user_id: str, capability: str, *,
               task_class: str = "general") -> bool:
        """
        Whether a capability should be relied on automatically right now.

        Unknown reliability is permitted (we have to try something to learn),
        but a suppressed or demonstrably unreliable capability is not.
        """
        return self.score(user_id, capability,
                          task_class=task_class)["label"] not in (
            LABEL_UNRELIABLE, LABEL_SUPPRESSED)

    def explain(self, user_id: str, capability: str, *,
                task_class: str = "general") -> str:
        s = self.score(user_id, capability, task_class=task_class)
        if s["label"] == LABEL_UNKNOWN:
            return (f"I have not used '{capability}' enough to judge it "
                    f"({s['total']} observation(s)).")
        if s["label"] == LABEL_SUPPRESSED:
            return (f"'{capability}' failed {s['consecutive_failures']} times in a "
                    "row, so I am not relying on it until it works again.")
        return (f"'{capability}' is {s['label']} — {s['successes']} success(es) "
                f"and {s['failures']} failure(s) recorded.")
