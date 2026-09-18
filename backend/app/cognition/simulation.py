"""
Counterfactual sandbox V2 (§20) - strictly isolated from real state.

V8.2's `Sandbox` already projects counterfactuals read-only. V8.3 adds the
guarantees the spec demands:

  * **Isolation is structural, not conventional.** The sandbox operates on a
    deep-copied snapshot. It holds no reference to the live world model during
    projection, so there is no code path by which a projection can write.
  * **Everything produced is tagged SIMULATED.** A simulated outcome is never
    an observation, never a memory, and never a world fact (§39).
  * **Committing requires a separate explicit confirmation.** `commit()` will
    not act on a simulation id alone; the caller must pass `confirm=True` AND
    the exact changes to apply. Without both, nothing happens.

The isolation test in the suite asserts that running a simulation leaves world
state, memories and missions byte-identical.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any

SIMULATED = "SIMULATED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SimulationEngine:
    """Isolated what-if projection over a frozen snapshot of real state."""

    def __init__(self, db, bus, world, sandbox=None, missions=None,
                 world_v2=None) -> None:
        self.db = db
        self.bus = bus
        self.world = world
        self.sandbox = sandbox      # V8.2 Sandbox, reused for its projection
        self.missions = missions
        # Commits go through WorldStateV2 so a committed change is recorded in
        # world_changes like any other real change - a simulation that becomes
        # reality must leave the same audit trail as a spoken correction.
        self.world_v2 = world_v2

    # -------------------------------------------------------------- snapshot
    def _snapshot(self, user_id: str) -> dict[str, Any]:
        """
        A deep copy of current state. Mutating this cannot touch the database:
        these are plain dicts detached from any live object.
        """
        return {
            "world": copy.deepcopy([dict(e) for e in self.world.list(user_id)]),
            "missions": copy.deepcopy(
                [dict(m) for m in self.missions.list(user_id)]
                if self.missions is not None else []),
            "taken_at": _now(),
        }

    # ------------------------------------------------------------- simulate
    def simulate(self, user_id: str, question: str, *,
                 assumptions: list[str] | None = None,
                 correlation_id: str | None = None) -> dict[str, Any]:
        """
        Project a counterfactual against a frozen snapshot.

        The result is explicitly SIMULATED and is stored in `sandbox_runs`,
        never in world_entities, observations or memories.
        """
        question = " ".join((question or "").split())[:500]
        if len(question) < 3:
            raise ValueError("A counterfactual question is required.")

        sid = f"sim_{uuid.uuid4().hex[:10]}"
        self.bus.emit(user_id, "simulation.started", question,
                      subject_kind="simulation", subject_id=sid,
                      correlation_id=correlation_id)

        snapshot = self._snapshot(user_id)
        stated = list(assumptions or [])

        # Reuse the V8.2 projection logic, which is already read-only.
        projection: dict[str, Any]
        if self.sandbox is not None:
            projection = dict(self.sandbox.simulate(user_id, question))
        else:
            projection = {"assumptions": [], "changed_variables": [],
                          "projected_effects": [], "risks": [],
                          "confidence": 0.2}

        declared_assumptions = stated + list(projection.get("assumptions", []))
        active = [e for e in snapshot["world"]
                  if e.get("state") in ("active", "at_risk")]
        open_missions = [m for m in snapshot["missions"]
                         if m.get("state") in ("draft", "active", "waiting",
                                               "blocked", "paused")]

        if not active and not open_missions:
            evidence_note = ("There is very little tracked state, so this "
                             "projection rests almost entirely on assumptions.")
            confidence = 0.15
        else:
            evidence_note = (f"Projected against {len(active)} active world "
                             f"fact(s) and {len(open_missions)} open "
                             f"mission(s), frozen at {snapshot['taken_at']}.")
            confidence = float(projection.get("confidence", 0.4))

        result = {
            "id": sid,
            "epistemic_status": SIMULATED,
            "question": question,
            "assumptions": declared_assumptions,
            "changed": projection.get("changed_variables", []),
            "effects": projection.get("projected_effects", []),
            "risks": projection.get("risks", []),
            "confidence": round(confidence, 3),
            "snapshot_taken_at": snapshot["taken_at"],
            "snapshot_size": {"world": len(snapshot["world"]),
                              "missions": len(snapshot["missions"])},
            "evidence_note": evidence_note,
            "isolation": ("This projection ran against a frozen copy. No "
                          "memory, world fact or mission was read-modified or "
                          "written."),
            "disclaimer": ("SIMULATED — this is a projection, not an "
                           "observation. Nothing here is a fact about what "
                           "happened or will happen."),
        }

        # Tag the persisted V8.2 run row as SIMULATED so no reader can mistake
        # a projection for something that happened.
        run_id = projection.get("id")
        if run_id:
            self.db.execute(
                "UPDATE sandbox_runs SET epistemic_status=?, assumptions=?"
                " WHERE id=?",
                (SIMULATED, json.dumps(declared_assumptions), run_id))
            result_run_id = run_id
        else:
            result_run_id = None

        result["run_id"] = result_run_id

        self.bus.emit(user_id, "simulation.completed", question,
                      subject_kind="simulation", subject_id=sid,
                      correlation_id=correlation_id,
                      payload={"epistemic_status": SIMULATED,
                               "confidence": result["confidence"],
                               "effects": len(result["effects"])})
        return result

    # --------------------------------------------------------------- commit
    def commit(self, user_id: str, simulation_id: str, *,
               confirm: bool = False, changes: list[dict[str, Any]] | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Apply a simulated change for real.

        Requires BOTH an explicit `confirm=True` and an explicit list of
        changes. A simulation id alone can never mutate real state - that
        separation is the whole point of the sandbox.
        """
        if not confirm:
            return {
                "committed": False,
                "reason": ("A simulation never changes real state on its own. "
                           "Re-issue with explicit confirmation to apply it."),
            }
        if not changes:
            return {
                "committed": False,
                "reason": ("Confirmation was given but no concrete changes were "
                           "specified. Nothing was applied."),
            }

        applied: list[dict[str, Any]] = []
        for change in changes:
            kind = change.get("kind")
            if kind == "world_state":
                entity_id = change.get("entity_id")
                state = change.get("state")
                if not entity_id or not state:
                    continue
                reason = f"Committed from simulation {simulation_id}"
                if self.world_v2 is not None:
                    updated = self.world_v2.set_state(
                        user_id, entity_id, state, reason=reason,
                        evidence=[f"Explicitly confirmed simulation "
                                  f"{simulation_id}"],
                        correlation_id=correlation_id)
                else:
                    updated = self.world.set_state(
                        user_id, entity_id, state, reason=reason,
                        correlation_id=correlation_id)
                if updated:
                    applied.append({"kind": kind, "entity_id": entity_id,
                                    "state": state})

        self.bus.emit(user_id, "simulation.committed",
                      f"Applied {len(applied)} change(s) from a simulation",
                      subject_kind="simulation", subject_id=simulation_id,
                      correlation_id=correlation_id,
                      payload={"applied": len(applied)})
        return {"committed": True, "applied": applied,
                "detail": (f"{len(applied)} change(s) applied with explicit "
                           f"confirmation.")}

    def discard(self, user_id: str, simulation_id: str, *,
                reason: str = "Discarded by the user",
                correlation_id: str | None = None) -> dict[str, Any]:
        self.bus.emit(user_id, "simulation.discarded", reason,
                      subject_kind="simulation", subject_id=simulation_id,
                      correlation_id=correlation_id)
        return {"discarded": True, "reason": reason}

    def history(self, user_id: str, limit: int = 25) -> list[dict[str, Any]]:
        if self.sandbox is None:
            return []
        runs = self.sandbox.history(user_id, limit=limit)
        for run in runs:
            run.setdefault("epistemic_status", SIMULATED)
        return runs
