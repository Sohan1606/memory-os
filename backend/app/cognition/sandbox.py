"""
Counterfactual sandbox.

Simulation branches are computed from real current state but are NEVER written
back to it. The only table a sandbox run touches is `sandbox_runs`, which is a
record of the question asked - not a mutation of the world.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

_DELAY = re.compile(r"\b(delay|postpone|push back|later|slip)\b", re.I)
_DROP = re.compile(r"\b(drop|remove|cancel|abandon|stop working on|skip)\b", re.I)
_FOCUS = re.compile(r"\b(focus (?:only )?on|prioriti[sz]e|just do)\b", re.I)
_SWITCH = re.compile(r"\b(instead of|switch to|other option|alternative)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Sandbox:
    """Safe what-if projection over a read-only snapshot of world state."""

    def __init__(self, db, bus, world) -> None:
        self.db = db
        self.bus = bus
        self.world = world

    def simulate(self, user_id: str, question: str) -> dict[str, Any]:
        """
        Project a counterfactual.

        Returns assumptions, changed variables, projected effects, confidence and
        risks - all clearly marked SIMULATION ONLY.
        """
        # Read-only snapshot. We copy so no downstream mutation can leak.
        entities = [dict(e) for e in self.world.list(user_id)]
        active = [e for e in entities if e["state"] in ("active", "at_risk")]
        at_risk = [e for e in entities if e["state"] == "at_risk"]

        kind = "general"
        if _DELAY.search(question):
            kind = "delay"
        elif _DROP.search(question):
            kind = "drop"
        elif _FOCUS.search(question):
            kind = "focus"
        elif _SWITCH.search(question):
            kind = "switch"

        assumptions: list[str] = [
            "Current world state is the starting point.",
            f"{len(active)} active item(s) are in scope.",
            "No external changes occur during the projection window.",
        ]
        changed: list[str] = []
        effects: list[str] = []
        risks: list[str] = []

        if not active:
            confidence = 0.2
            effects.append("There is not enough tracked state to project a meaningful "
                           "difference yet.")
            risks.append("Projection is based on very little evidence.")
        elif kind == "delay":
            changed.append("Timeline extended for the affected item.")
            effects.append(f"Near-term load across {len(active)} active item(s) drops.")
            effects.append("Any dependent item inherits the delay.")
            risks.append("Downstream commitments may slip in turn.")
            confidence = 0.45
        elif kind == "drop":
            changed.append("One item removed from the active set.")
            effects.append(f"Remaining active items: {max(0, len(active) - 1)}.")
            effects.append("Attention concentrates on what is left.")
            risks.append("Work already invested in the dropped item is lost.")
            confidence = 0.5
        elif kind == "focus":
            changed.append("All but one item moved to dormant.")
            effects.append("Throughput on the focused item increases.")
            effects.append(f"{max(0, len(active) - 1)} item(s) stop progressing.")
            risks.append("Deprioritised commitments may become at-risk.")
            confidence = 0.45
        elif kind == "switch":
            changed.append("Alternative option selected instead of the current one.")
            effects.append("Expected outcome changes; prior work may not transfer.")
            risks.append("Switching cost is not yet measurable from recorded evidence.")
            confidence = 0.35
        else:
            changed.append("No specific variable identified in the question.")
            effects.append(f"Baseline: {len(active)} active, {len(at_risk)} at risk.")
            confidence = 0.3

        if at_risk:
            risks.append(f"{len(at_risk)} item(s) are already flagged at risk.")

        run_id = f"sb_{uuid.uuid4().hex[:12]}"
        projection = {"kind": kind, "changed": changed, "effects": effects,
                      "risks": risks, "baseline": {"active": len(active),
                                                   "at_risk": len(at_risk)}}
        self.db.execute(
            "INSERT INTO sandbox_runs (id,user_id,question,assumptions,projection,"
            "confidence,created_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, user_id, question, json.dumps(assumptions),
             json.dumps(projection), confidence, _now()))

        return {
            "id": run_id, "simulation": True,
            "banner": "SIMULATION ONLY — NO REAL CHANGES",
            "question": question, "kind": kind, "assumptions": assumptions,
            "changed_variables": changed, "projected_effects": effects,
            "risks": risks, "confidence": round(confidence, 2),
            "baseline": projection["baseline"],
            "note": "Projected from current tracked state; not a guarantee.",
        }

    def history(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        out = []
        for r in self.db.query(
                "SELECT * FROM sandbox_runs WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, limit)):
            d = dict(r)
            for field in ("assumptions", "projection"):
                try:
                    d[field] = json.loads(d[field]) if d[field] else None
                except json.JSONDecodeError:
                    d[field] = None
            d["simulation"] = True
            out.append(d)
        return out
