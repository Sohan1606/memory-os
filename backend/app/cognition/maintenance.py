"""
Memory maintenance V2 (§23-§24).

Wraps V8.2's `MemoryHealthEngine` for use from background cognition. Two
additions, both about safety and honesty:

  1. **Review is read-only.** `review()` diagnoses and reports; it never applies
     a remedy. Applying is a separate, explicit call. This is what makes it safe
     to run from a background cycle.
  2. **Reinforcement requires evidence of use, not retrieval.** V8.2 already
     keeps confidence and reputation separate; this module makes the rule
     explicit in code: a memory being *retrieved* is not evidence it was
     correct, useful, or should be strengthened. Only a recorded outcome does
     that.

Remedies remain the non-destructive V8.2 set: MERGE, DOWNGRADE, REVALIDATE,
QUARANTINE, RETIRE. Nothing is ever permanently destroyed automatically.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Remedies that may be applied without asking. All are reversible.
AUTO_SAFE_REMEDIES = ("REVALIDATE",)

# Remedies that always require explicit confirmation.
CONFIRM_REQUIRED = ("MERGE", "RETIRE", "QUARANTINE", "DOWNGRADE")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MaintenanceV2:
    """Non-destructive, background-safe memory upkeep."""

    def __init__(self, db, bus, health, memory) -> None:
        self.db = db
        self.bus = bus
        self.health = health
        self.memory = memory

    # --------------------------------------------------------------- review
    def review(self, user_id: str, *,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Diagnose memory health WITHOUT changing anything.

        Returns findings in the shape background cognition expects. Because it
        applies nothing, `changes` is always 0 - and that is reported honestly
        rather than dressed up as work performed.
        """
        self.bus.emit(user_id, "memory.maintenance_started",
                      "Reviewing memory health",
                      correlation_id=correlation_id)
        report = self.health.report(user_id)
        findings = [
            {
                "kind": "memory_health",
                "subject_id": f["memory_id"],
                "summary": f"{f['issue']} — suggested remedy {f['remedy']}",
                "issue": f["issue"],
                "remedy": f["remedy"],
                "confidence": f["confidence"],
                "evidence": f["evidence"],
                "requires_confirmation": f["remedy"] in CONFIRM_REQUIRED,
            }
            for f in report.get("findings", [])
        ]
        detail = ("Reviewed memory health; nothing needs attention."
                  if not findings else
                  f"{len(findings)} finding(s) recorded for review. "
                  f"No changes were applied.")
        self.bus.emit(user_id, "memory.maintenance_completed", detail,
                      correlation_id=correlation_id,
                      payload={"findings": len(findings),
                               "grade": report.get("grade"),
                               "changes": 0})
        return {
            "findings": findings,
            "changes": 0,
            "grade": report.get("grade"),
            "total_active": report.get("total_active", 0),
            "checked_at": report.get("checked_at", _now()),
            "detail": detail,
        }

    # ------------------------------------------------------------ reinforce
    def reinforce_from_outcome(self, user_id: str, memory_id: str, *,
                               helped: bool, evidence: list[str],
                               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Strengthen or weaken a memory based on a RECORDED OUTCOME.

        Refuses without evidence. Retrieval alone is explicitly not accepted as
        a reason - that would let a frequently-surfaced wrong memory make itself
        look more trustworthy over time.
        """
        if not evidence:
            return {
                "applied": False,
                "reason": ("INSUFFICIENT EVIDENCE — reinforcement requires a "
                           "recorded outcome. Retrieval on its own is not "
                           "evidence that a memory was correct or useful."),
            }
        mem = self.memory.get(memory_id)
        if mem is None:
            return {"applied": False, "reason": "Memory no longer exists."}

        current = float(getattr(mem, "importance", 0.5))
        delta = 0.08 if helped else -0.08
        new_importance = round(max(0.05, min(1.0, current + delta)), 3)
        self.memory.update(memory_id, importance=new_importance,
                           reason=("outcome:helped" if helped
                                   else "outcome:did_not_help"))
        event = "memory.reinforced" if helped else "memory.downgraded"
        self.bus.emit(user_id, event,
                      ("Strengthened by a recorded outcome" if helped
                       else "Weakened by a recorded outcome"),
                      subject_kind="memory", subject_id=memory_id,
                      correlation_id=correlation_id,
                      payload={"importance": new_importance,
                               "evidence": evidence})
        return {"applied": True, "importance": new_importance,
                "evidence": evidence,
                "detail": (f"Importance moved {current:.2f} → "
                           f"{new_importance:.2f} on recorded evidence.")}

    def revalidate(self, user_id: str, memory_id: str, *, reason: str,
                   correlation_id: str | None = None) -> dict[str, Any]:
        """
        Flag a memory for confirmation. Changes no content - the safest remedy,
        and the only one background cognition may apply unattended.
        """
        mem = self.memory.get(memory_id)
        if mem is None:
            return {"applied": False, "reason": "Memory no longer exists."}
        self.bus.emit(user_id, "memory.revalidated", reason,
                      subject_kind="memory", subject_id=memory_id,
                      correlation_id=correlation_id,
                      payload={"reason": reason})
        return {"applied": True, "memory_id": memory_id, "reason": reason,
                "detail": ("Flagged for confirmation next time it is relevant. "
                           "Nothing was changed or deleted.")}

    def remedies(self) -> dict[str, Any]:
        return {
            "non_destructive": True,
            "auto_safe": list(AUTO_SAFE_REMEDIES),
            "confirm_required": list(CONFIRM_REQUIRED),
            "detail": ("All remedies are reversible. Nothing is permanently "
                       "destroyed automatically; retire and quarantine are soft "
                       "status changes that preserve version history."),
        }
