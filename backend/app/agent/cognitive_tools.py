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
from typing import Any, Callable, Literal

log = logging.getLogger(__name__)

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticUndefined

# Sentinels the model is instructed to repeat verbatim rather than paraphrase.
NO_MISSIONS = "NO_MISSIONS_RECORDED"
NO_NEXT_STEP = "NO_NEXT_STEP_RECORDED"
NOT_FOUND = "NOT_FOUND"
AMBIGUOUS = "AMBIGUOUS_REFERENCE"

# Canonical actions exposed to the model. KnowledgeStore.correct() remains the
# mutation authority and deliberately retains its compatibility aliases for API
# callers and older persisted integrations.
CorrectionAction = Literal[
    "retire", "weaken", "outdated", "contradict", "rescope"]
CORRECTION_ACTIONS = ("retire", "weaken", "outdated", "contradict", "rescope")

ExplanationIntent = Literal[
    "why",
    "why_not",
    "why_now",
    "what_changed",
    "what_evidence",
    "what_alternatives",
    "what_caused_change",
]
EXPLANATION_INTENTS = (
    "why",
    "why_not",
    "why_now",
    "what_changed",
    "what_evidence",
    "what_alternatives",
    "what_caused_change",
)


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

    def _resolve_knowledge(item_id: str | None,
                           kind: str | None = None) -> tuple[dict[str, Any] | None, str | None]:
        """Resolve a Skill/Principle by id or stable conversational focus."""
        if item_id:
            item = cognition.knowledge.get(user_id, item_id)
            if item is None or (kind and item["kind"] != kind):
                return None, f"{NOT_FOUND}: no matching learned object {item_id}."
            return item, None
        focused = [f for f in cognition.focus.current(user_id, session_id=session)
                   if f["subject_kind"] in ("skill", "principle")
                   and (not kind or f["subject_kind"] == kind)]
        if len(focused) != 1:
            return None, (f"{AMBIGUOUS}: no single Skill or Principle is in "
                          "focus. List them, then ask which one the user means.")
        item = cognition.knowledge.get(user_id, focused[0]["subject_id"])
        if item is None:
            return None, f"{NOT_FOUND}: the focused learned object no longer exists."
        return item, None

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

    # =================================== EXPERIENCE / SKILL / PRINCIPLE (V8.4.1)
    def list_learned(kind: str = "all") -> str:
        """List evidence-backed Skills and/or Principles."""
        requested = (kind or "all").lower().rstrip("s")
        if requested not in ("all", "skill", "principle"):
            return _j({"status": "INVALID", "detail":
                       "kind must be all, skill, or principle"})
        items = cognition.knowledge.list(
            user_id, kind=None if requested == "all" else requested, limit=30)
        emit("LIST_LEARNED", {"kind": requested, "count": len(items)})
        if not items:
            return _j({"status": "NO_LEARNED_KNOWLEDGE_RECORDED",
                       "detail": "No matching Skills or Principles are recorded."})
        if len(items) == 1:
            _focus(items[0]["kind"], items[0]["id"], items[0]["name"])
        return _j({"status": "OK", "items": [{
            "id": i["id"], "kind": i["kind"], "name": i["name"],
            "statement": i["statement"], "lifecycle": i["lifecycle"],
            "confidence": i["confidence"],
            "reputation": i["reputation"]["reputation"],
            "scope": {"kind": i["scope_kind"], "value": i["scope_value"]},
            "supporting_evidence": i["supporting_evidence_count"],
            "counterexamples": i["counterexample_count"],
        } for i in items]})

    def list_experiences() -> str:
        """List meaningful observed episodes available to learning."""
        items = cognition.experiences.list(user_id, limit=30)
        emit("LIST_EXPERIENCES", {"count": len(items)})
        if not items:
            return _j({"status": "NO_EXPERIENCES_RECORDED"})
        return _j({"status": "OK", "experiences": [{
            "id": e["id"], "situation": e["situation"],
            "action": e["action"], "outcome": e["outcome"],
            "success": e["success"], "lifecycle": e["lifecycle"],
            "evidence_count": e["evidence_count"],
            "scope": {"kind": e["scope_kind"], "value": e["scope_value"]},
        } for e in items]})

    def inspect_learned(item_id: str = "") -> str:
        """Explain how a focused Skill/Principle was learned and how it performs."""
        item, error = _resolve_knowledge(item_id or None)
        if error:
            return _j({"status": error.split(":")[0], "detail": error})
        _focus(item["kind"], item["id"], item["name"])
        report = cognition.knowledge.explain(user_id, item["id"])
        emit("INSPECT_LEARNED", {"item_id": item["id"], "kind": item["kind"]})
        return _j({"status": "OK", "item": {
            "id": item["id"], "kind": item["kind"], "name": item["name"],
            "statement": item["statement"], "lifecycle": item["lifecycle"],
            "confidence": item["confidence"],
            "reputation": item["reputation"],
            "scope": {"kind": item["scope_kind"], "value": item["scope_value"]},
            "procedure": item["procedure"],
            "expected_outcome": item["expected_outcome"],
        }, "summary": report["summary"],
            "supporting_evidence": report["supporting_evidence"],
            "counterexamples": report["counterexamples"],
            "validation_history": report["validation_history"],
            "lifecycle_history": report["lifecycle_history"],
            "note": report["note"]})

    def correct_learned(action: CorrectionAction, reason: str,
                        item_id: str = "", scope_kind: str = "",
                        scope_value: str = "") -> str:
        """Apply one canonical correction to focused learned knowledge."""
        normalized_action = str(action).lower().strip()
        item, error = _resolve_knowledge(item_id or None)
        if error:
            return _j({"status": error.split(":")[0], "detail": error})
        previous_lifecycle = str(item["lifecycle"])
        before = (previous_lifecycle, float(item["confidence"]),
                  item["scope_kind"], item.get("scope_value"),
                  item["reputation"].get("evidence_contradictions", 0))
        try:
            # KnowledgeStore.correct() is the sole mutation path. The tool
            # translates no lifecycle state and fabricates no successful result.
            updated = cognition.knowledge.correct(
                user_id, item["id"], action=normalized_action, reason=reason,
                scope_kind=scope_kind or None, scope_value=scope_value or None,
                evidence=["Explicit user correction in conversation"],
                correlation_id=correlation_id)
        except ValueError as exc:
            return _j({"status": "INVALID", "action": normalized_action,
                       "previous_lifecycle": previous_lifecycle,
                       "resulting_lifecycle": previous_lifecycle,
                       "detail": str(exc)})
        after = (str(updated["lifecycle"]), float(updated["confidence"]),
                 updated["scope_kind"], updated.get("scope_value"),
                 updated["reputation"].get("evidence_contradictions", 0))
        status = "UPDATED" if after != before else "NO_CHANGE"
        _focus(updated["kind"], updated["id"], updated["name"])
        emit("CORRECT_LEARNED", {
            "item_id": updated["id"], "action": normalized_action,
            "status": status, "previous_lifecycle": previous_lifecycle,
            "resulting_lifecycle": updated["lifecycle"]})
        detail = (
            "The correction changed canonical learned knowledge; future retrieval "
            "will respect the resulting lifecycle and scope."
            if status == "UPDATED" else
            "No canonical lifecycle, confidence, reputation, or scope changed; "
            "do not describe this as a newly completed correction.")
        return _j({"status": status, "action": normalized_action,
                   "id": updated["id"], "kind": updated["kind"],
                   "name": updated["name"],
                   "previous_lifecycle": previous_lifecycle,
                   "resulting_lifecycle": updated["lifecycle"],
                   # Keep the established key for compatible consumers.
                   "lifecycle": updated["lifecycle"],
                   "scope": {"kind": updated["scope_kind"],
                             "value": updated["scope_value"]},
                   "detail": detail})

    # ============================================== EXPLANATION (§9)
    def explain_cognition(
        intent: str = "why",
        subject_id: str = "",
        subject_kind: str = "",
        question: str = "",
        **kwargs: Any,
    ) -> str:
        """
        Explain WHY / WHY_NOT / WHY_NOW / WHAT_CHANGED / WHAT_EVIDENCE / WHAT_ALTERNATIVES / WHAT_CAUSED_CHANGE
        using canonical recorded decision, event, arbitration, and causal records.
        """
        # Handle alias question_kind if passed in kwargs
        query_intent = kwargs.get("question_kind") or intent or "why"
        normalized_intent = str(query_intent).lower().replace(" ", "_").strip()
        if normalized_intent not in EXPLANATION_INTENTS:
            normalized_intent = "why"

        focused = cognition.focus.current(user_id, session_id=session)
        skind = (subject_kind or "").strip().lower() or None
        sid = (subject_id or "").strip() or None
        q = (question or "").strip() or None

        emit("EXPLAIN", {
            "intent": normalized_intent,
            "subject_kind": skind,
            "subject_id": sid,
            "focused": len(focused),
        })

        if normalized_intent == "what_changed" and not skind and not sid and not focused:
            changes = cognition.world_v2.changes(user_id, limit=10)
            if not changes:
                return _j({"status": "NO_CHANGES_RECORDED", "detail": "Nothing has changed on record."})
            return _j({
                "status": "OK",
                "query_intent": "what_changed",
                "explanation_type": "WORLD_CHANGE",
                "world_changes": [{
                    "change": c["change"], "from": c["previous_state"],
                    "to": c["new_state"], "when": c["created_at"]}
                    for c in changes[:6]],
                "epistemic_status": "OBSERVED",
            })

        if not skind or not sid:
            if not focused:
                return _j({
                    "status": "NO_SUBJECT_IN_FOCUS",
                    "detail": ("Nothing specific is in focus. Ask what the "
                               "user means, or explain from the evidence "
                               "already in context."),
                })
            distinct_subjects = {(f["subject_kind"], f["subject_id"]) for f in focused}
            if len(distinct_subjects) == 1:
                skind = skind or focused[0]["subject_kind"]
                sid = sid or focused[0]["subject_id"]
            elif not skind and not sid and normalized_intent not in ("what_changed", "why_now"):
                return _j({
                    "status": "AMBIGUOUS_REFERENCE",
                    "detail": "Multiple subjects are in focus. Specify which one to explain.",
                    "focused_subjects": [
                        {"kind": f["subject_kind"], "id": f["subject_id"], "label": f.get("label")}
                        for f in focused
                    ],
                })

        exp_model = None
        if hasattr(cognition, "explanation_engine"):
            try:
                exp_model = cognition.explanation_engine.explain(
                    user_id,
                    subject_kind=skind,
                    subject_id=sid,
                    query_intent=normalized_intent,
                    question=q,
                    correlation_id=correlation_id,
                    persist=True,
                )
            except Exception as exc:  # defensive
                log.warning("ExplanationEngine.explain failed: %s", exc)

        if exp_model:
            # Check if subject was not found or insufficient evidence
            if exp_model.get("user_id") == "-" and "INSUFFICIENT EVIDENCE" in exp_model.get("summary", ""):
                return _j({
                    "status": "INSUFFICIENT_EVIDENCE",
                    "detail": exp_model.get("summary"),
                    "explanation_type": exp_model.get("explanation_type"),
                    "query_intent": normalized_intent,
                })

            state_now = exp_model.get("state_now")
            if not state_now and exp_model.get("subject", {}).get("current_state"):
                state_now = exp_model["subject"]["current_state"]

            state_then = exp_model.get("state_then")
            if not state_then and exp_model.get("subject", {}).get("historical_state"):
                state_then = exp_model["subject"]["historical_state"]

            change_reason = (
                exp_model.get("change_reason")
                or (exp_model.get("correction", {}).get("reason") if exp_model.get("correction") else None)
            )

            result_payload = {
                "status": "OK",
                "explanation_id": exp_model.get("id"),
                "explanation_type": exp_model.get("explanation_type"),
                "query_intent": exp_model.get("query_intent") or normalized_intent,
                "subject": exp_model.get("subject"),
                "summary": exp_model.get("summary"),
                "decisive_factors": exp_model.get("decisive_factors"),
                "supporting_evidence": exp_model.get("supporting_evidence"),
                "counter_evidence": exp_model.get("counter_evidence"),
                "alternatives": exp_model.get("alternatives"),
                "causality": exp_model.get("causality"),
                "correction": exp_model.get("correction"),
                "timeline": exp_model.get("timeline"),
                "state_now": state_now,
                "state_then": state_then,
                "change_reason": change_reason,
                "epistemic_status": "OBSERVED",
                "confidence": exp_model.get("confidence"),
                "note": "Explain from this recorded evidence only. Never invent internal reasoning.",
            }
            return _j(result_payload)

        return _j({
            "status": "INSUFFICIENT_EVIDENCE",
            "detail": "No explanation engine available.",
        })

    def explain(
        question_kind: str = "why",
        subject_kind: str | None = None,
        subject_id: str | None = None,
        question: str | None = None,
        **kwargs: Any,
    ) -> str:
        """
        Legacy V8.3.1 explanation tool adapter.
        Delegates to ExplanationEngine to generate the canonical explanation graph,
        then adapts it into the V8.3.1 response contract (with `subjects` and `mission_history`).
        """
        intent = kwargs.get("intent") or question_kind or "why"
        normalized_intent = str(intent).lower().replace(" ", "_").strip()
        if normalized_intent not in EXPLANATION_INTENTS:
            normalized_intent = "why"

        focused = cognition.focus.current(user_id, session_id=session)
        skind = (subject_kind or "").strip().lower() or None
        sid = (subject_id or "").strip() or None
        q = (question or "").strip() or None

        emit("EXPLAIN", {
            "kind": normalized_intent,
            "intent": normalized_intent,
            "subject_kind": skind,
            "subject_id": sid,
            "focused": len(focused),
        })

        # 1. Legacy "what_changed" handling
        if normalized_intent == "what_changed":
            changes = cognition.world_v2.changes(user_id, limit=10) if hasattr(cognition, "world_v2") else []
            mission_history: list[dict[str, Any]] = []
            for item in focused:
                if item.get("subject_kind") == "mission" and hasattr(cognition, "missions"):
                    raw_hist = cognition.missions.history(user_id, item["subject_id"], limit=10)
                    for h in raw_hist:
                        mission_history.append({
                            "change": h.get("change", ""),
                            "from": h.get("previous_state", ""),
                            "to": h.get("new_state", ""),
                            "reason": h.get("reason", ""),
                            "when": h.get("created_at", ""),
                        })
            if not changes and not mission_history:
                return _j({
                    "status": "NO_CHANGES_RECORDED",
                    "detail": "Nothing has changed on record.",
                })
            return _j({
                "status": "OK",
                "world_changes": [{
                    "change": c["change"],
                    "from": c["previous_state"],
                    "to": c["new_state"],
                    "when": c["created_at"],
                } for c in changes[:6]] if changes else None,
                "mission_history": mission_history or None,
                "epistemic_status": "OBSERVED",
            })

        # 2. Focus / Target resolution for general / why queries
        if not skind or not sid:
            if not focused:
                return _j({
                    "status": "NO_SUBJECT_IN_FOCUS",
                    "detail": ("Nothing specific is in focus. Ask what the "
                               "user means, or explain from the evidence "
                               "already in context."),
                })
            distinct_subjects = {(f["subject_kind"], f["subject_id"]) for f in focused}
            if len(distinct_subjects) == 1:
                skind = skind or focused[0]["subject_kind"]
                sid = sid or focused[0]["subject_id"]
            elif not skind and not sid and normalized_intent not in ("what_changed", "why_now"):
                return _j({
                    "status": "AMBIGUOUS_REFERENCE",
                    "detail": "Multiple subjects are in focus. Specify which one to explain.",
                    "focused_subjects": [
                        {"kind": f["subject_kind"], "id": f["subject_id"], "label": f.get("label")}
                        for f in focused
                    ],
                })

        # 3. Canonical Explanation Generation via ExplanationEngine
        exp_model = None
        if hasattr(cognition, "explanation_engine"):
            try:
                exp_model = cognition.explanation_engine.explain(
                    user_id,
                    subject_kind=skind,
                    subject_id=sid,
                    query_intent=normalized_intent,
                    question=q,
                    correlation_id=correlation_id,
                    persist=True,
                )
            except Exception as exc:
                log.warning("ExplanationEngine.explain failed: %s", exc)

        if exp_model and exp_model.get("user_id") == "-" and "INSUFFICIENT EVIDENCE" in exp_model.get("summary", ""):
            return _j({
                "status": "INSUFFICIENT_EVIDENCE",
                "detail": exp_model.get("summary"),
                "explanation_type": exp_model.get("explanation_type"),
                "query_intent": normalized_intent,
            })

        # 4. Adapt canonical result into legacy envelope
        evidence: dict[str, Any] = {"status": "OK", "subjects": []}
        subjects_to_inspect = focused if focused else ([{"subject_kind": skind, "subject_id": sid, "label": sid}] if skind and sid else [])

        for item in subjects_to_inspect:
            ikind = item.get("subject_kind")
            iid = item.get("subject_id")
            ilabel = item.get("label") or iid
            entry: dict[str, Any] = {"kind": ikind, "id": iid, "label": ilabel}

            if ikind == "mission" and hasattr(cognition, "missions"):
                mission = cognition.missions.get(user_id, iid)
                if mission:
                    entry["mission"] = _mission_view(mission, full=True)
                    raw_h = cognition.missions.history(user_id, mission["id"], limit=5)
                    entry["history"] = [{
                        "change": h.get("change", ""),
                        "from": h.get("previous_state", ""),
                        "to": h.get("new_state", ""),
                        "reason": h.get("reason", ""),
                        "when": h.get("created_at", ""),
                    } for h in raw_h]
            elif ikind in ("skill", "principle") and hasattr(cognition, "knowledge"):
                learned = cognition.knowledge.explain(user_id, iid)
                entry["learned"] = {
                    "summary": learned.get("summary"),
                    "supporting_evidence": learned.get("supporting_evidence"),
                    "counterexamples": learned.get("counterexamples"),
                    "validation_history": learned.get("validation_history"),
                    "lifecycle_history": learned.get("lifecycle_history"),
                    "note": learned.get("note"),
                }
            elif ikind == "experience" and hasattr(cognition, "experiences"):
                entry["experience"] = cognition.experiences.provenance(user_id, iid)

            if hasattr(cognition, "observations") and ikind and iid:
                obs = cognition.observations.evidence_for(user_id, ikind, iid)
                entry["evidence_count"] = obs.get("total", 0)
                entry["observations"] = [o.get("content") for o in obs.get("observations", [])[:4]] or None
                entry["verdict"] = obs.get("verdict")

            evidence["subjects"].append(entry)

        evidence["note"] = "Explain from this recorded evidence only. If the evidence is thin, say so."
        evidence["epistemic_status"] = "OBSERVED"

        # Merge canonical V8.4.2 properties onto legacy envelope
        if exp_model:
            evidence["explanation_id"] = exp_model.get("id")
            evidence["explanation_type"] = exp_model.get("explanation_type")
            evidence["query_intent"] = exp_model.get("query_intent") or normalized_intent
            evidence["subject"] = exp_model.get("subject")
            evidence["summary"] = exp_model.get("summary")
            evidence["decisive_factors"] = exp_model.get("decisive_factors")
            evidence["supporting_evidence"] = exp_model.get("supporting_evidence")
            evidence["counter_evidence"] = exp_model.get("counter_evidence")
            evidence["alternatives"] = exp_model.get("alternatives")
            evidence["causality"] = exp_model.get("causality")
            evidence["correction"] = exp_model.get("correction")
            evidence["timeline"] = exp_model.get("timeline")
            evidence["state_now"] = exp_model.get("state_now")
            evidence["state_then"] = exp_model.get("state_then")
            evidence["change_reason"] = exp_model.get("change_reason")

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
        intent: ExplanationIntent = Field(
            default="why",
            description=(
                "The specific explanation query intent: "
                "'why' = why an action/decision/memory/skill was used or chosen; "
                "'why_not' = why an alternative or retired skill was NOT used; "
                "'why_now' = what temporal event or trigger initiated this turn; "
                "'what_changed' = what state transitions, retirements, or modifications occurred; "
                "'what_evidence' = what canonical supporting observations exist; "
                "'what_alternatives' = what candidates were evaluated and why each was rejected; "
                "'what_caused_change' = what upstream causal factors drove this state."
            ),
        )
        subject_id: str = Field(
            default="",
            description=(
                "Stable subject id. Omit (or leave empty/null) when an object "
                "(Skill, Principle, Memory, Mission, Decision) is already focused: "
                "the tool automatically resolves the focused object. Never invent an id."
            ),
        )
        subject_kind: str = Field(
            default="",
            description=(
                "Optional subject kind: skill, principle, memory, mission, decision, "
                "routing, intervention, attention, prediction, world, intent, autonomy, "
                "policy. Omit when an object is in focus."
            ),
        )
        question: str = Field(
            default="",
            description="Optional natural language question for conversational context.",
        )

        @model_validator(mode="before")
        @classmethod
        def _normalize_intent_and_nulls(cls, data: Any) -> Any:
            if not isinstance(data, dict):
                return data
            cleaned = {}
            for key, value in data.items():
                if value is None and key in cls.model_fields:
                    default = cls.model_fields[key].default
                    if default is not PydanticUndefined:
                        continue
                cleaned[key] = value
            # Support question_kind as alias for intent
            if "question_kind" in cleaned and ("intent" not in cleaned or not cleaned.get("intent")):
                cleaned["intent"] = cleaned.pop("question_kind")
            if "intent" in cleaned and isinstance(cleaned["intent"], str):
                normalized = cleaned["intent"].lower().replace(" ", "_").strip()
                if normalized in EXPLANATION_INTENTS:
                    cleaned["intent"] = normalized
            return cleaned

    class LearnedListArgs(_NullTolerant):
        kind: str = Field(default="all", description="all | skill | principle")

    class LearnedIdArgs(_NullTolerant):
        item_id: str = Field(default="", description=(
            "Stable Skill/Principle id. Omit to use the focused learned object."))

    class CorrectLearnedArgs(_NullTolerant):
        action: CorrectionAction = Field(description=(
            "Choose exactly one correction operation. retire = forget completely "
            "or stop using it, excluding it from future retrieval; weaken = it "
            "may still help but should be trusted/recommended less; outdated = "
            "it is no longer current because circumstances changed; contradict = "
            "the user says it is false or invalid; rescope = it remains valid "
            "only in a narrower scope and requires scope_kind (and scope_value "
            "except for user/global)."))
        reason: str = Field(description=(
            "The user's stated correction, recorded in the audit trail."))
        item_id: str = Field(default="", description=(
            "Stable Skill/Principle id. Omit it (or send null/empty) when a "
            "Skill or Principle is focused: the tool resolves that exact focused "
            "object. Never invent or ask the user for an id already in focus."))
        scope_kind: str = Field(default="", description=(
            "Only for action=rescope: user | task | project | domain | "
            "environment | global."))
        scope_value: str = Field(default="", description=(
            "Only for action=rescope; required for task/project/domain/environment."))

    def _invalid_correction_args(_error: Exception) -> str:
        # Literal validation normally prevents a bad model call. If a provider
        # nevertheless emits unsupported arguments, expose INVALID as a tool
        # payload rather than a ValidationError/TOOL_FAILED or superficial success.
        return _j({
            "status": "INVALID", "action": None,
            "detail": ("Unsupported correction arguments. action must be exactly "
                       + ", ".join(CORRECTION_ACTIONS) + ".")})

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
            func=list_learned, name="list_learned",
            description="List actual learned Skills and Principles with lifecycle, "
                        "confidence, reputation, scope and evidence counts. Use "
                        "for 'what skills have you learned?' or 'what principles "
                        "do you have?'.",
            args_schema=LearnedListArgs),
        StructuredTool.from_function(
            func=list_experiences, name="list_experiences",
            description="List evidence-backed Experiences the system can learn "
                        "from. Use for 'what experiences support that?' or 'what "
                        "have you learned from?'.",
            args_schema=NoArgs),
        StructuredTool.from_function(
            func=inspect_learned, name="inspect_learned",
            description=(
                "Inspect the current recorded state, statement, procedure, scope, "
                "confidence, reputation and validation history of a focused Skill or Principle. "
                "Use to see what the object currently is and its static evidence. "
                "DO NOT use for 'why did you use that skill', 'why not', 'why now', or 'what changed' — "
                "use explain_cognition for all WHY / WHY NOT / WHY NOW / WHAT CHANGED questions."
            ),
            args_schema=LearnedIdArgs),
        StructuredTool.from_function(
            func=explain_cognition, name="explain_cognition",
            description=(
                "Explain WHY, WHY_NOT, WHY_NOW, WHAT_CHANGED, WHAT_EVIDENCE, WHAT_ALTERNATIVES, or WHAT_CAUSED_CHANGE "
                "for any cognitive decision, memory recall, skill/principle usage or retirement, arbitration, routing, or intervention "
                "using recorded decision, event, arbitration, and causal records. "
                "When a Skill, Memory, Mission, or Decision is focused, omit subject_id and subject_kind to explain that focused subject. "
                "Unlike inspect_learned (which only reads static state), explain_cognition explains the causal reasons, decisive factors, "
                "counterfactual alternatives, and historical transitions."
            ),
            args_schema=ExplainArgs),
        StructuredTool.from_function(
            func=explain, name="explain",
            description=(
                "Explain WHY, WHY_NOT, WHY_NOW, WHAT_CHANGED, WHAT_EVIDENCE, WHAT_ALTERNATIVES, or WHAT_CAUSED_CHANGE "
                "using recorded decision, event, arbitration, and causal records."
            ),
            args_schema=ExplainArgs),
        StructuredTool.from_function(
            func=correct_learned, name="correct_learned",
            description=(
                "Apply an explicit user correction to one Skill or Principle. "
                "Choose action=retire when the user says forget it or stop using "
                "it; retirement removes it from future retrieval. Choose weaken "
                "when it may still help but deserves less reliance, outdated when "
                "changed circumstances made it no longer current, contradict when "
                "the user says it is false/invalid, and rescope when it remains "
                "valid only in a narrower context. For rescope supply scope_kind "
                "and any required scope_value. When a Skill or Principle is "
                "focused, OMIT item_id: this tool resolves the exact focused "
                "object, so never invent or request a database id. Return status "
                "must be honored: UPDATED changed canonical state; INVALID or "
                "NO_CHANGE must not be described as a successful new change."),
            args_schema=CorrectLearnedArgs,
            handle_validation_error=_invalid_correction_args),
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
    ]
