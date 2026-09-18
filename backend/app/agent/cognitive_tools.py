"""
Cognitive tools — the conversational surface of the V8.3 subsystems (§2).

V8.3 built missions, world state, predictions, attention, continuity, history
and simulation, then exposed them only over HTTP and in the Observatory. The
model driving a conversation could not see any of them, so asked "what missions
am I working on?" it could only search memory and improvise. That is the gap
this module closes.

Every tool here calls the REAL subsystem. There are no canned responses, no
placeholder data and no second implementation of anything: each function is a
thin, honest adapter over `runtime.cognition.*`.

Three rules shape every return value:

  1. **Absence is stated, never filled in.** No missions returns
     "NO_MISSIONS_RECORDED", not an empty-sounding paragraph the model can
     embroider. A mission with no next step returns
     `next_step_status: "NO_NEXT_STEP_RECORDED"` (§5).
  2. **Epistemic status travels with the data.** Anything the model receives is
     labelled RECORDED / OBSERVED / INFERRED / PREDICTED / SIMULATED so it
     cannot quietly restate a projection as a fact (§10).
  3. **Identity is by id.** Tools accept and return stable ids, and mutating
     tools resolve "that" through the focus tracker rather than matching
     display text (§7/§8).

Returns are JSON strings because that is what the tool-calling contract expects;
the shapes are kept small so a 3B local model can actually use them.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

log = logging.getLogger(__name__)

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticUndefined

# Sentinels the model is instructed to repeat verbatim rather than paraphrase.
NO_MISSIONS = "NO_MISSIONS_RECORDED"
NO_NEXT_STEP = "NO_NEXT_STEP_RECORDED"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS_REFERENCE"


def _j(payload: Any) -> str:
    return json.dumps(payload, default=str, indent=1)


def _mission_view(mission: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """
    Compact mission projection for a model prompt.

    `next_step_status` is the important field: it forces the distinction
    between a recorded next step and the absence of one, so the model cannot
    silently invent a plan (§5).
    """
    steps = mission.get("steps", [])
    pending = [s for s in steps if s["state"] in ("pending", "in_progress")]
    done = [s for s in steps if s["state"] == "done"]

    recorded_next = mission.get("next_step")
    if recorded_next:
        next_status = "RECORDED"
        next_value: str | None = recorded_next
    elif pending:
        next_status = "RECORDED"
        next_value = pending[0]["summary"]
    else:
        next_status = NO_NEXT_STEP
        next_value = None

    view: dict[str, Any] = {
        "mission_id": mission["id"],
        "title": mission["title"],
        "state": mission["state"],
        "progress": mission.get("progress", 0.0),
        "next_step": next_value,
        "next_step_status": next_status,
        "epistemic_status": "RECORDED",
    }
    if next_status == NO_NEXT_STEP:
        view["next_step_guidance"] = (
            "No next step is recorded for this mission. Say so plainly. You may "
            "propose one, but you must label it as a proposal, not as an "
            "existing fact about the mission.")

    blockers: list[str] = []
    if mission.get("blocked_reason"):
        blockers.append(mission["blocked_reason"])
    if mission.get("waiting_on"):
        blockers.append(f"waiting on {mission['waiting_on']}")
    view["blockers"] = blockers or None
    if not blockers:
        view["blockers_status"] = "NO_BLOCKERS_RECORDED"

    if full:
        view["description"] = mission.get("description")
        view["scope"] = mission.get("scope")
        view["constraints"] = mission.get("constraints") or None
        view["success_criteria"] = mission.get("success_criteria") or None
        view["steps_done"] = [s["summary"] for s in done]
        view["steps_pending"] = [s["summary"] for s in pending]
        view["last_activity_at"] = mission.get("last_activity_at")
    return view


def build_cognitive_tools(
    cognition: Any, user_id: str, *, thread_id: str | None = None,
    correlation_id: str | None = None,
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
) -> list[StructuredTool]:
    """
    Build the cognitive toolset for one user. `user_id` is bound server-side so
    a model can never reach another namespace.
    """
    session = thread_id or "default"

    def emit(kind: str, payload: dict[str, Any]) -> None:
        if on_event:
            on_event(kind, payload)

    def _focus(subject_kind: str, subject_id: str, label: str) -> None:
        """Remember what the conversation is about, so 'that' can resolve (§8)."""
        try:
            cognition.focus.set_focus(user_id, subject_kind, subject_id,
                                      label=label, session_id=session,
                                      correlation_id=correlation_id)
        except Exception as exc:  # an aid, never a reason to fail a turn
            log.warning("Could not set conversational focus on %s %s: %s",
                        subject_kind, subject_id, exc)

    def _resolve_mission(mission_id: str | None) -> tuple[dict[str, Any] | None, str | None]:
        """
        Resolve a mission by explicit id, else by conversational focus.

        Returns (mission, error). Never guesses between candidates.
        """
        if mission_id:
            mission = cognition.missions.get(user_id, mission_id)
            if mission is None:
                return None, f"{NOT_FOUND}: no mission with id {mission_id}."
            return mission, None

        focused = [f for f in cognition.focus.current(user_id, session_id=session)
                   if f["subject_kind"] == "mission"]
        if not focused:
            open_missions = cognition.missions.list(user_id, open_only=True)
            if len(open_missions) == 1:
                return open_missions[0], None
            if not open_missions:
                return None, (f"{NO_MISSIONS}: there are no missions on record, "
                              f"so there is nothing to act on.")
            titles = ", ".join(f"{m['title']} ({m['id']})" for m in open_missions[:5])
            return None, (f"{AMBIGUOUS}: several missions are open and none is "
                          f"in focus. Ask which one is meant. Candidates: {titles}")
        mission = cognition.missions.get(user_id, focused[0]["subject_id"])
        if mission is None:
            return None, f"{NOT_FOUND}: the focused mission no longer exists."
        return mission, None

    # ===================================================== MISSION (§2/§7)
    def list_missions(open_only: bool | None = True) -> str:
        """List the user's missions."""
        # A small model frequently emits `"open_only": null` for an argument it
        # has no opinion about. That is a legitimate "unspecified", not an
        # error, so it normalises to the documented default instead of raising
        # a ValidationError the user would see as TOOL_FAILED.
        open_only = True if open_only is None else bool(open_only)
        missions = cognition.missions.list(user_id, open_only=open_only)
        emit("LIST_MISSIONS", {"count": len(missions), "open_only": open_only})
        if not missions:
            return _j({"status": NO_MISSIONS,
                       "detail": ("No missions are recorded. Do not invent one. "
                                  "If the user wants to track an objective, "
                                  "offer to create a mission.")})
        if len(missions) == 1:
            _focus("mission", missions[0]["id"], missions[0]["title"])
        return _j({"status": "OK", "count": len(missions),
                   "missions": [_mission_view(m) for m in missions]})

    def get_mission(mission_id: str = "") -> str:
        """Get the full state of one mission, including steps and blockers."""
        mission, error = _resolve_mission(mission_id or None)
        if error:
            emit("GET_MISSION", {"error": error})
            return _j({"status": error.split(":")[0], "detail": error})
        _focus("mission", mission["id"], mission["title"])
        emit("GET_MISSION", {"mission_id": mission["id"]})
        return _j({"status": "OK", "mission": _mission_view(mission, full=True)})

    def create_mission(title: str, description: str = "",
                       success_criteria: str = "") -> str:
        """Create a new long-running mission. Only for explicit requests."""
        criteria = [c.strip() for c in success_criteria.split(";") if c.strip()]
        try:
            # A mission the user asked for starts active, not as a draft.
            mission = cognition.missions.create(
                user_id, title, description=description or None,
                success_criteria=criteria, source="conversation",
                state="active",
                evidence=["Created from an explicit request in conversation"],
                correlation_id=correlation_id)
        except ValueError as exc:
            return _j({"status": "INVALID", "detail": str(exc)})
        _focus("mission", mission["id"], mission["title"])
        emit("CREATE_MISSION", {"mission_id": mission["id"], "title": title})
        return _j({"status": "CREATED", "mission": _mission_view(mission, full=True)})

    def update_mission_state(state: str, reason: str, mission_id: str = "",
                             blocked_reason: str = "",
                             waiting_on: str = "") -> str:
        """Change a mission's state (active, paused, blocked, completed...)."""
        mission, error = _resolve_mission(mission_id or None)
        if error:
            emit("UPDATE_MISSION", {"error": error})
            return _j({"status": error.split(":")[0], "detail": error})
        try:
            updated = cognition.missions.set_state(
                user_id, mission["id"], state, reason=reason,
                blocked_reason=blocked_reason or None,
                waiting_on=waiting_on or None,
                evidence=[f"User request in conversation: {reason}"],
                correlation_id=correlation_id)
        except ValueError as exc:
            return _j({"status": "INVALID_STATE", "detail": str(exc)})
        if updated is None:
            return _j({"status": NOT_FOUND, "detail": "Mission disappeared."})
        _focus("mission", updated["id"], updated["title"])
        emit("UPDATE_MISSION", {"mission_id": updated["id"], "state": state})
        return _j({"status": "UPDATED", "previous_state": mission["state"],
                   "mission": _mission_view(updated, full=True)})

    # --------------------------------------------- v8.3.1.1 action tools (§2)
    # Real-model verification showed that a 3B model reliably reaches for a
    # verb it recognises ("resume") but struggles to assemble the three correct
    # arguments for the generic update_mission_state, and then narrates an
    # unknown state. One tool per intention removes that failure mode: the
    # model only has to pick the verb, and the tool supplies the object (from
    # focus), the target state and the reason.
    #
    # These are NOT a second implementation. Every one of them funnels into
    # _transition, which calls the canonical MissionRegistry.set_state, so
    # events, evidence, history and progress behave exactly as before.
    TERMINAL = ("completed", "failed", "abandoned")

    def _transition(action: str, target: str, mission_id: str,
                    reason: str, *, valid_from: tuple[str, ...] | None,
                    already_msg: str) -> str:
        """
        Shared body for the action tools.

        Truthfulness rules enforced here rather than in each tool:
          - a no-op is reported as a no-op, never as a transition;
          - a terminal mission is never silently resurrected;
          - an invalid transition explains itself instead of guessing.
        """
        event = f"{action.upper()}_MISSION"
        mission, error = _resolve_mission(mission_id or None)
        if error:
            emit(event, {"error": error})
            return _j({"status": error.split(":")[0], "detail": error})

        current = mission["state"]
        view = _mission_view(mission, full=True)

        # Already in the target state: real information, not a transition.
        if current == target:
            emit(event, {"mission_id": mission["id"], "noop": True})
            _focus("mission", mission["id"], mission["title"])
            return _j({"status": "NO_CHANGE", "reason_code": already_msg,
                       "previous_state": current, "state": current,
                       "mission_id": mission["id"], "title": mission["title"],
                       "next_step": view["next_step"],
                       "next_step_status": view["next_step_status"],
                       "epistemic_status": "RECORDED",
                       "detail": (f"This mission is already {current}. Nothing "
                                  f"was changed. Report the recorded state.")})

        # Terminal missions are finished history, not something to reopen here.
        if current in TERMINAL and target not in TERMINAL:
            emit(event, {"mission_id": mission["id"], "terminal": current})
            return _j({"status": "TERMINAL_STATE", "state": current,
                       "mission_id": mission["id"], "title": mission["title"],
                       "epistemic_status": "RECORDED",
                       "detail": (f"This mission is {current}, which is a final "
                                  f"state. It cannot be {action}d. Say so "
                                  f"plainly; do not describe it as active. If "
                                  f"the user wants to carry on with this work, "
                                  f"offer to create a new mission.")})

        if valid_from is not None and current not in valid_from:
            emit(event, {"mission_id": mission["id"], "invalid_from": current})
            return _j({"status": "INVALID_TRANSITION",
                       "state": current, "mission_id": mission["id"],
                       "title": mission["title"],
                       "epistemic_status": "RECORDED",
                       "detail": (f"This mission is {current}; {action} applies "
                                  f"to a mission that is "
                                  f"{' or '.join(valid_from)}. Report the "
                                  f"recorded state rather than assuming.")})

        try:
            updated = cognition.missions.set_state(
                user_id, mission["id"], target, reason=reason,
                evidence=[f"User asked to {action} this mission in conversation"],
                correlation_id=correlation_id)
        except ValueError as exc:
            return _j({"status": "INVALID_STATE", "detail": str(exc)})
        if updated is None:
            return _j({"status": NOT_FOUND, "detail": "Mission disappeared."})

        _focus("mission", updated["id"], updated["title"])
        emit(event, {"mission_id": updated["id"],
                     "previous_state": current, "state": updated["state"]})
        after = _mission_view(updated, full=True)
        return _j({"status": "UPDATED", "previous_state": current,
                   "state": updated["state"], "mission_id": updated["id"],
                   "title": updated["title"],
                   "next_step": after["next_step"],
                   "next_step_status": after["next_step_status"],
                   "blockers": after["blockers"],
                   "epistemic_status": "RECORDED",
                   "detail": (f"Recorded: '{updated['title']}' went from "
                              f"{current} to {updated['state']}. Answer from "
                              f"this confirmed state.")})

    def pause_mission(mission_id: str = "", reason: str = "") -> str:
        """Pause the mission under discussion."""
        return _transition(
            "pause", "paused", mission_id,
            reason or "User asked to pause this mission",
            valid_from=("active", "waiting", "blocked", "draft"),
            already_msg="ALREADY_PAUSED")

    def resume_mission(mission_id: str = "", reason: str = "") -> str:
        """Resume the paused mission under discussion."""
        return _transition(
            "resume", "active", mission_id,
            reason or "User asked to resume this mission",
            valid_from=("paused",), already_msg="ALREADY_ACTIVE")

    def complete_mission(mission_id: str = "", reason: str = "") -> str:
        """Mark the mission under discussion as complete."""
        return _transition(
            "complete", "completed", mission_id,
            reason or "User said this mission is complete",
            valid_from=None, already_msg="ALREADY_COMPLETED")

    def abandon_mission(mission_id: str = "", reason: str = "") -> str:
        """Abandon the mission under discussion."""
        return _transition(
            "abandon", "abandoned", mission_id,
            reason or "User asked to abandon this mission",
            valid_from=None, already_msg="ALREADY_ABANDONED")

    def add_mission_step(summary: str, mission_id: str = "") -> str:
        """Record a concrete next step on a mission."""
        mission, error = _resolve_mission(mission_id or None)
        if error:
            return _j({"status": error.split(":")[0], "detail": error})
        result = cognition.missions.add_step(
            user_id, mission["id"], summary, correlation_id=correlation_id)
        emit("ADD_MISSION_STEP", {"mission_id": mission["id"],
                                  "added": result.get("added")})
        if not result.get("added"):
            return _j({"status": "NOT_ADDED", "detail": result.get("reason")})
        return _j({"status": "ADDED", "step": result["step"]["summary"],
                   "mission": _mission_view(
                       cognition.missions.get(user_id, mission["id"]) or mission)})

    def complete_mission_step(step_summary: str, mission_id: str = "") -> str:
        """Mark a mission step done, identified by its text."""
        mission, error = _resolve_mission(mission_id or None)
        if error:
            return _j({"status": error.split(":")[0], "detail": error})
        pending = [s for s in mission.get("steps", [])
                   if s["state"] in ("pending", "in_progress")]
        if not pending:
            return _j({"status": "NO_PENDING_STEPS",
                       "detail": "This mission has no open steps recorded."})
        needle = step_summary.lower().strip()
        matches = [s for s in pending if needle and needle in s["summary"].lower()]
        if not matches and len(pending) == 1:
            matches = pending
        if not matches:
            return _j({"status": AMBIGUOUS,
                       "detail": "Which step? Open steps: "
                                 + "; ".join(s["summary"] for s in pending)})
        if len(matches) > 1:
            return _j({"status": AMBIGUOUS,
                       "detail": "Several steps match: "
                                 + "; ".join(s["summary"] for s in matches)})
        updated = cognition.missions.complete_step(
            user_id, matches[0]["id"], evidence=["User reported completion"],
            correlation_id=correlation_id)
        emit("COMPLETE_MISSION_STEP", {"mission_id": mission["id"]})
        return _j({"status": "COMPLETED", "step": matches[0]["summary"],
                   "mission": _mission_view(updated or mission, full=True)})

    def get_mission_blockers(mission_id: str = "") -> str:
        """What is blocking progress — recorded blockers only."""
        missions = ([m] if mission_id
                    else cognition.missions.list(user_id, open_only=True))
        if mission_id:
            found = cognition.missions.get(user_id, mission_id)
            if found is None:
                return _j({"status": NOT_FOUND})
            missions = [found]
        if not missions:
            return _j({"status": NO_MISSIONS})
        blocked = []
        for mission in missions:
            if mission["state"] in ("blocked", "waiting") or mission.get("blocked_reason"):
                blocked.append({"mission_id": mission["id"],
                                "title": mission["title"],
                                "state": mission["state"],
                                "blocked_reason": mission.get("blocked_reason"),
                                "waiting_on": mission.get("waiting_on")})
        emit("MISSION_BLOCKERS", {"count": len(blocked)})
        if not blocked:
            return _j({"status": "NO_BLOCKERS_RECORDED",
                       "detail": ("No mission is recorded as blocked or "
                                  "waiting. Do not speculate about blockers.")})
        return _j({"status": "OK", "blocked": blocked})

    # ===================================================== WORLD (§2/§20)
    def get_world_state(kind: str = "") -> str:
        """
        Current tracked world state: projects, people, risks, constraints.
        Use for "what am I working on" rather than searching memory.
        """
        snapshot = cognition.world_v2.snapshot(user_id)
        entities = snapshot["entities"]
        if kind:
            entities = [e for e in entities if e["kind"] == kind.lower()]
        emit("GET_WORLD_STATE", {"count": len(entities), "kind": kind or "all"})
        if not entities:
            return _j({"status": "NO_WORLD_STATE_RECORDED",
                       "detail": f"Nothing is tracked{' of kind ' + kind if kind else ''}."})
        return _j({"status": "OK", "count": len(entities),
                   "entities": [{
                       "entity_id": e["id"], "kind": e["kind"],
                       "label": e["label"], "state": e["state"],
                       "confidence": round(float(e["confidence"]), 2),
                       "freshness": e["freshness"]["freshness_class"],
                       "stale_note": (e["freshness"]["reason"]
                                      if e["freshness"]["stale"] else None),
                       "epistemic_status": "OBSERVED",
                   } for e in entities[:12]]})

    def get_world_changes(entity_id: str = "") -> str:
        """What changed recently in tracked state, with real before/after."""
        changes = cognition.world_v2.changes(user_id,
                                             entity_id=entity_id or None,
                                             limit=15)
        emit("GET_WORLD_CHANGES", {"count": len(changes)})
        if not changes:
            return _j({"status": "NO_CHANGES_RECORDED",
                       "detail": "No world changes are recorded."})
        return _j({"status": "OK",
                   "changes": [{
                       "change": c["change"], "from": c["previous_state"],
                       "to": c["new_state"], "when": c["created_at"],
                       "evidence": c.get("evidence") or None,
                       "epistemic_status": "OBSERVED",
                   } for c in changes]})

    # ================================================ CONTINUITY (§11)
    def get_current_focus() -> str:
        """
        What the user is working on right now: missions, goals, commitments and
        open threads combined. Use for "what am I working on / what's pending".
        """
        missions = cognition.missions.list(user_id, open_only=True)
        goals = cognition.world.list(user_id, kind="goal", state="active")
        commitments = cognition.world.list(user_id, kind="commitment",
                                           state="active")
        projects = cognition.world.list(user_id, kind="project", state="active")
        try:
            threads = cognition.continuity.open_items(user_id)
        except Exception:
            threads = []
        emit("GET_CURRENT_FOCUS", {"missions": len(missions),
                                   "goals": len(goals),
                                   "projects": len(projects)})
        if not (missions or goals or commitments or projects or threads):
            return _j({"status": "NOTHING_RECORDED",
                       "detail": ("No missions, goals, projects, commitments or "
                                  "open threads are recorded. Say so honestly "
                                  "rather than guessing what they might be.")})
        return _j({
            "status": "OK",
            "missions": [_mission_view(m) for m in missions],
            "goals": [g["label"] for g in goals[:5]],
            "projects": [p["label"] for p in projects[:5]],
            "commitments": [c["label"] for c in commitments[:5]],
            "open_threads": [t.get("summary") for t in threads[:5]],
            "note": ("These are RECORDED objects. Missions, goals, projects and "
                     "commitments are different things — do not merge them."),
        })

    # ================================================ PREDICTIONS (§2)
    def get_predictions() -> str:
        """Open predictions and their evidence. Predictions are not facts."""
        try:
            open_predictions = cognition.predictions.list(user_id, status="open")
        except Exception:
            open_predictions = []
        emit("GET_PREDICTIONS", {"count": len(open_predictions)})
        if not open_predictions:
            return _j({"status": "NO_PREDICTIONS_RECORDED"})
        return _j({"status": "OK",
                   "predictions": [{
                       "prediction_id": p["id"], "statement": p["statement"],
                       "confidence": p["confidence"], "state": p["status"],
                       "evidence": p.get("evidence") or None,
                       "epistemic_status": "PREDICTED",
                   } for p in open_predictions[:8]],
                   "note": "These are predictions, not observed facts."})

    # ================================================ ATTENTION (§13/§14)
    def get_attention_state() -> str:
        """
        What the system has been tracking or deliberately not raising, plus any
        real findings from background upkeep.
        """
        suppressions = cognition.attention_v2.suppressions(user_id, limit=5)
        policy = cognition.attention_v2.silence_policy(user_id)
        cycles = cognition.background.cycles(user_id, limit=3)
        findings = [f for c in cycles for f in (c.get("findings") or [])]
        emit("GET_ATTENTION_STATE", {"suppressions": len(suppressions),
                                     "findings": len(findings)})
        return _j({
            "status": "OK",
            "background_findings": findings or None,
            "background_note": ("No background finding is outstanding."
                                if not findings else
                                "These came from real background cycles."),
            "not_raised": [{"topic": s["topic"],
                            "why": s.get("suppressed_because")}
                           for s in suppressions] or None,
            "silence_policy": policy["verdict"],
            "note": ("Only report findings that are listed here. Never invent "
                     "background activity."),
        })

    # ================================================== HISTORY (§2)
    def get_historical_state(when: str) -> str:
        """
        What was known at a past moment. `when` is an ISO-8601 timestamp.
        Returns HISTORY NOT AVAILABLE outside recorded history.
        """
        result = cognition.timemachine.world_at(user_id, when)
        emit("GET_HISTORICAL_STATE", {"when": when,
                                      "available": result.get("available")})
        if not result.get("available"):
            return _j({"status": "HISTORY_NOT_AVAILABLE",
                       "detail": result.get("reason"),
                       "guidance": ("Say the history is not available for that "
                                    "point. Do not describe current state as "
                                    "though it were the past.")})
        return _j({"status": "OK", "as_of": result["as_of"],
                   "entities": [{"label": e["label"],
                                 "state_at_time": e["state_at_time"]}
                                for e in result["entities"][:10]],
                   "unknown": [u["label"] for u in result["unknown"][:5]] or None,
                   "epistemic_status": "OBSERVED"})

    # =============================================== SIMULATION (§2)
    def simulate_scenario(question: str) -> str:
        """Explore a what-if. Changes nothing; results are SIMULATED."""
        result = cognition.simulation.simulate(
            user_id, question, correlation_id=correlation_id)
        emit("SIMULATE_SCENARIO", {"simulation_id": result["id"]})
        return _j({"status": "OK", "epistemic_status": "SIMULATED",
                   "effects": result["effects"] or None,
                   "risks": result["risks"] or None,
                   "assumptions": result["assumptions"],
                   "confidence": result["confidence"],
                   "note": ("SIMULATED — a projection, not a fact, and nothing "
                            "was changed. Present it as a what-if.")})

    # ============================================== EXPLANATION (§9)
    def explain(question_kind: str = "why") -> str:
        """
        Evidence for the current topic. `question_kind`: why | why_now |
        what_changed.
        """
        focused = cognition.focus.current(user_id, session_id=session)
        kind = (question_kind or "why").lower().replace(" ", "_")
        emit("EXPLAIN", {"kind": kind, "focused": len(focused)})

        if kind == "what_changed":
            changes = cognition.world_v2.changes(user_id, limit=10)
            mission_history: list[dict[str, Any]] = []
            for item in focused:
                if item["subject_kind"] == "mission":
                    mission_history = cognition.missions.history(
                        user_id, item["subject_id"], limit=10)
            if not changes and not mission_history:
                return _j({"status": "NO_CHANGES_RECORDED",
                           "detail": "Nothing has changed on record."})
            return _j({"status": "OK",
                       "world_changes": [{
                           "change": c["change"], "from": c["previous_state"],
                           "to": c["new_state"], "when": c["created_at"]}
                           for c in changes[:6]] or None,
                       "mission_history": [{
                           "change": h["change"], "from": h["previous_state"],
                           "to": h["new_state"], "reason": h["reason"],
                           "when": h["created_at"]}
                           for h in mission_history[:6]] or None,
                       "epistemic_status": "OBSERVED"})

        if not focused:
            return _j({"status": "NO_SUBJECT_IN_FOCUS",
                       "detail": ("Nothing specific is in focus. Ask what the "
                                  "user means, or explain from the evidence "
                                  "already in context.")})

        evidence: dict[str, Any] = {"status": "OK", "subjects": []}
        for item in focused:
            entry: dict[str, Any] = {"kind": item["subject_kind"],
                                     "id": item["subject_id"],
                                     "label": item.get("label")}
            if item["subject_kind"] == "mission":
                mission = cognition.missions.get(user_id, item["subject_id"])
                if mission:
                    entry["mission"] = _mission_view(mission, full=True)
                    entry["history"] = [{
                        "change": h["change"], "reason": h["reason"],
                        "when": h["created_at"]}
                        for h in cognition.missions.history(
                            user_id, mission["id"], limit=5)]
            obs = cognition.observations.evidence_for(
                user_id, item["subject_kind"], item["subject_id"])
            entry["evidence_count"] = obs["total"]
            entry["observations"] = [o["content"] for o in
                                     obs["observations"][:4]] or None
            entry["verdict"] = obs["verdict"]
            evidence["subjects"].append(entry)
        evidence["note"] = ("Explain from this recorded evidence only. If the "
                            "evidence is thin, say so.")
        return _j(evidence)

    # ------------------------------------------------------------- schemas
    #
    # Null tolerance (V8.3.1.2)
    # -------------------------
    # A small local model routinely emits an explicit JSON `null` for an
    # optional argument it has no value for, e.g. {"open_only": null} or
    # {"mission_id": null}. Pydantic rejects null for a non-Optional field, and
    # the user sees TOOL_FAILED with a ValidationError for what was actually a
    # reasonable call.
    #
    # `null` means "unspecified", so every optional argument accepts None and
    # is coerced back to its documented default before the tool body runs. The
    # model is never forced to supply a value it does not have, and explicit
    # true/false (or a real id string) still behave exactly as before.
    class _NullTolerant(BaseModel):
        """Base schema that turns an explicit null into the field default."""

        @model_validator(mode="before")
        @classmethod
        def _nulls_to_defaults(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                return data
            cleaned = {}
            for key, value in data.items():
                if value is None and key in cls.model_fields:
                    default = cls.model_fields[key].default
                    if default is not PydanticUndefined:
                        # Drop it so the field default applies.
                        continue
                cleaned[key] = value
            return cleaned

    class ListMissionsArgs(_NullTolerant):
        open_only: bool | None = Field(
            default=True,
            description="Optional. True for only open missions, false for all "
                        "of them. Omit it (or send null) for the default of "
                        "open missions only.")

    class MissionIdArgs(_NullTolerant):
        mission_id: str = Field(default="",
                                description="Mission id. Leave empty to use the "
                                            "mission currently being discussed.")

    class CreateMissionArgs(_NullTolerant):
        title: str = Field(description="Short title of the objective.")
        description: str = Field(default="", description="Optional detail.")
        success_criteria: str = Field(
            default="", description="Optional semicolon-separated criteria.")

    class UpdateMissionStateArgs(_NullTolerant):
        state: str = Field(description="One of: active, paused, blocked, "
                                       "waiting, completed, failed, abandoned.")
        reason: str = Field(description="Why the state is changing.")
        mission_id: str = Field(default="", description="Leave empty for the "
                                                        "mission under discussion.")
        blocked_reason: str = Field(default="", description="If blocked, why.")
        waiting_on: str = Field(default="", description="If waiting, on what.")

    class MissionActionArgs(_NullTolerant):
        """Args for the action tools. Both fields are optional so a small model
        can call `resume_mission()` bare and let focus supply the object."""

        mission_id: str = Field(
            default="", description="Omit to use the focused mission.")
        reason: str = Field(
            default="", description="Optional reason the user gave.")

    class AddStepArgs(_NullTolerant):
        summary: str = Field(description="The concrete next step.")
        mission_id: str = Field(default="")

    class CompleteStepArgs(_NullTolerant):
        step_summary: str = Field(description="Text identifying the step.")
        mission_id: str = Field(default="")

    class WorldArgs(_NullTolerant):
        kind: str = Field(default="", description="Optional filter: project, "
                                                  "goal, person, risk, "
                                                  "commitment, constraint.")

    class EntityArgs(_NullTolerant):
        entity_id: str = Field(default="", description="Optional entity id.")

    class NoArgs(BaseModel):
        pass

    class HistoryArgs(_NullTolerant):
        when: str = Field(description="ISO-8601 timestamp of the past moment.")

    class SimulateArgs(_NullTolerant):
        question: str = Field(description="The what-if question.")

    class ExplainArgs(_NullTolerant):
        question_kind: str = Field(default="why",
                                   description="why | why_now | what_changed")

    return [
        StructuredTool.from_function(
            func=list_missions, name="list_missions",
            description="List the user's long-running missions (objectives that "
                        "span conversations). Use this for questions like 'what "
                        "am I working on' or 'what missions do I have'. Missions "
                        "are NOT memories.",
            args_schema=ListMissionsArgs),
        StructuredTool.from_function(
            func=get_mission, name="get_mission",
            description="READ one mission: state, progress, steps, next step, "
                        "blockers. For questions ABOUT a mission. Read-only. "
                        "Do NOT call it before a lifecycle action — a focused "
                        "mission's id and state are already in your context.",
            args_schema=MissionIdArgs),
        StructuredTool.from_function(
            func=create_mission, name="create_mission",
            description="Create a mission. ONLY when the user explicitly asks to "
                        "track or work toward an objective. Never create one "
                        "from a question such as 'what should I do about X'.",
            args_schema=CreateMissionArgs),
        # Action tools first: for a plain pause/resume/complete/abandon these
        # are the correct choice, and listing them ahead of the generic tool
        # helps a small model pick the simpler option.
        StructuredTool.from_function(
            func=pause_mission, name="pause_mission",
            description="PAUSE an active mission (-> paused). The ONLY tool for "
                        "'pause it', 'put it on hold', 'stop that for now'. "
                        "If a mission is in focus call it with NO ARGUMENTS; "
                        "never call get_mission or get_world_state first.",
            args_schema=MissionActionArgs),
        StructuredTool.from_function(
            func=resume_mission, name="resume_mission",
            description="RESUME a paused mission (paused -> active). The ONLY "
                        "tool for 'resume it', 'continue it', 'start it "
                        "again', 'carry on with it', 'unpause it'. If a "
                        "mission is in focus call it with NO ARGUMENTS; never "
                        "call get_mission or get_world_state first.",
            args_schema=MissionActionArgs),
        StructuredTool.from_function(
            func=complete_mission, name="complete_mission",
            description="COMPLETE the whole mission. ONLY when the user "
                        "explicitly says it is finished ('complete it', 'mark "
                        "it done'). If in focus, call with NO ARGUMENTS. NOT "
                        "for one step (use complete_mission_step). Never "
                        "infer completion from progress.",
            args_schema=MissionActionArgs),
        StructuredTool.from_function(
            func=abandon_mission, name="abandon_mission",
            description="ABANDON the whole mission permanently. ONLY on an "
                        "explicit 'abandon it' / 'drop it'. If in focus, call "
                        "with NO ARGUMENTS.",
            args_schema=MissionActionArgs),
        StructuredTool.from_function(
            func=update_mission_state, name="update_mission_state",
            description="Set a mission to 'blocked' or 'waiting' only, or "
                        "record a blocker reason. Do NOT use it for pause, "
                        "resume, complete or abandon — those have their own "
                        "tools.",
            args_schema=UpdateMissionStateArgs),
        StructuredTool.from_function(
            func=add_mission_step, name="add_mission_step",
            description="Record a concrete next step on a mission.",
            args_schema=AddStepArgs),
        StructuredTool.from_function(
            func=complete_mission_step, name="complete_mission_step",
            description="Mark ONE STEP INSIDE a mission done. NOT a lifecycle "
                        "tool: it cannot complete, resume or pause the "
                        "mission itself.",
            args_schema=CompleteStepArgs),
        StructuredTool.from_function(
            func=get_mission_blockers, name="get_mission_blockers",
            description="What is blocking progress. Use for 'what's blocking me'.",
            args_schema=MissionIdArgs),
        StructuredTool.from_function(
            func=get_world_state, name="get_world_state",
            description="READ world facts: projects, people, risks, "
                        "constraints, goals. Prefer over memory search for "
                        "'what projects am I working on'. UNRELATED to "
                        "mission lifecycle — never call it to resume, pause, "
                        "complete or abandon a mission.",
            args_schema=WorldArgs),
        StructuredTool.from_function(
            func=get_world_changes, name="get_world_changes",
            description="Recent recorded changes to tracked state, with real "
                        "before and after values. Use for 'what changed'.",
            args_schema=EntityArgs),
        StructuredTool.from_function(
            func=get_current_focus, name="get_current_focus",
            description="Combined view of missions, goals, projects, "
                        "commitments and open threads. Use for 'what am I "
                        "working on', 'what's pending', 'where did we leave off'.",
            args_schema=NoArgs),
        StructuredTool.from_function(
            func=get_predictions, name="get_predictions",
            description="Open predictions with their evidence. Predictions are "
                        "expectations, not facts.",
            args_schema=NoArgs),
        StructuredTool.from_function(
            func=get_attention_state, name="get_attention_state",
            description="Real findings from background upkeep and things the "
                        "system chose not to raise. Use for 'anything I should "
                        "know'.",
            args_schema=NoArgs),
        StructuredTool.from_function(
            func=get_historical_state, name="get_historical_state",
            description="What was known at a past moment. Use for 'what did you "
                        "know last week'. Returns HISTORY_NOT_AVAILABLE if the "
                        "period predates recorded history.",
            args_schema=HistoryArgs),
        StructuredTool.from_function(
            func=simulate_scenario, name="simulate_scenario",
            description="Explore a what-if without changing anything. Use for "
                        "'what if I delay this'. Results are SIMULATED.",
            args_schema=SimulateArgs),
        StructuredTool.from_function(
            func=explain, name="explain",
            description="Recorded evidence behind the current topic. Use for "
                        "'why?', 'why now?' and 'what changed?'.",
            args_schema=ExplainArgs),
    ]
