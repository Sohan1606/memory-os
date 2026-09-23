"""Truthful V9/V9.0.1 Cognitive Surface Protocol.

Live activity is persisted on the canonical Cognitive EventBus.  The polling
API and the completed-turn projection read the same events; there is no second
trace, process-global stage cache, or decorative timer.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

STAGES = frozenset({
    "LISTENING", "UNDERSTANDING", "CHECKING_PERSONAL_CONTEXT",
    "CHECKING_MEMORY", "CHECKING_WORLD_STATE",
    "VERIFYING_EXTERNAL_INFORMATION", "IDENTIFYING_UNKNOWNS",
    "EVALUATING_CONSEQUENCES", "FORMING_RESPONSE", "WAITING_FOR_USER",
    "RECORDING_OUTCOME", "UPDATING_MODEL",
    "AUDITING_PERSONAL_MODEL", "CHECKING_FOR_STALE_STATE",
    "COMPARING_PERSONAL_STATE", "CHECKING_CONTRADICTIONS",
    "CHECKING_OPEN_UNKNOWNS", "COMPARING_PREDICTION_TO_OUTCOME",
    "ANALYZING_MODEL_ERROR", "EVALUATING_MAINTENANCE_OPTIONS",
    "WAITING_FOR_CONFIRMATION", "APPLYING_VERIFIED_UPDATE", "RE_AUDITING_MODEL",
})
STATUSES = frozenset({"ACTIVE", "COMPLETED", "DEGRADED", "FAILED"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(value: Any) -> dict[str, Any]:
    try:
        return json.loads(value) if value else {}
    except (TypeError, ValueError):
        return {}


class SurfaceLifecycle:
    """Append and reconstruct user/correlation-scoped live surface activity."""

    def __init__(self, bus) -> None:
        self.bus = bus
        self.db = bus.db

    def start_turn(self, user_id: str, thread_id: str, correlation_id: str) -> dict[str, Any]:
        self.bus.emit(
            user_id, "surface.turn_started", "Started an observable cognitive turn.",
            thread_id=thread_id, subject_kind="conversation", subject_id=thread_id,
            correlation_id=correlation_id,
            payload={"thread_id": thread_id, "status": "ACTIVE"})
        self.transition(user_id, thread_id, correlation_id, "LISTENING", "COMPLETED",
                        source_kind="conversation", source_id=thread_id)
        return self.snapshot(user_id, correlation_id) or {}

    def transition(self, user_id: str, thread_id: str, correlation_id: str,
                   stage: str, status: str, *, source_kind: str = "correlation",
                   source_id: str | None = None, detail: str | None = None) -> dict[str, Any]:
        if stage not in STAGES:
            raise ValueError(f"Unknown cognitive surface stage: {stage}")
        if status not in STATUSES:
            raise ValueError(f"Unknown cognitive surface status: {status}")
        payload = {"stage": stage, "status": status,
                   "source": {"kind": source_kind,
                              "id": source_id or correlation_id}}
        if detail:
            payload["detail"] = detail[:300]
        event = self.bus.emit(
            user_id, "surface.activity", f"{stage}: {status}",
            thread_id=thread_id, subject_kind=source_kind,
            subject_id=source_id or correlation_id,
            correlation_id=correlation_id, payload=payload)
        return {**payload, "timestamp": event.created_at, "event_id": event.id}

    def snapshot(self, user_id: str, correlation_id: str) -> dict[str, Any] | None:
        # Both predicates are mandatory. A guessed correlation id reveals
        # neither another user's activity nor whether the turn exists.
        rows = self.db.query(
            "SELECT * FROM cognitive_events WHERE user_id=? AND correlation_id=?"
            " AND type IN ('surface.turn_started','surface.activity','surface.selected')"
            " ORDER BY id ASC", (user_id, correlation_id))
        started = next((r for r in rows if r["type"] == "surface.turn_started"), None)
        if started is None:
            return None
        history: list[dict[str, Any]] = []
        latest: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in rows:
            if row["type"] != "surface.activity":
                continue
            payload = _loads(row["payload"])
            stage = payload.get("stage")
            status = payload.get("status")
            if stage not in STAGES or status not in STATUSES:
                continue
            activity = {"stage": stage, "status": status,
                        "source": payload.get("source") or {
                            "kind": row["subject_kind"] or "correlation",
                            "id": row["subject_id"] or correlation_id},
                        "timestamp": row["created_at"], "event_id": row["id"]}
            if payload.get("detail"):
                activity["detail"] = payload["detail"]
            history.append(activity)
            if stage not in latest:
                order.append(stage)
            latest[stage] = activity
        activities = [latest[stage] for stage in order]
        current = activities[-1]["stage"] if activities else "LISTENING"
        failed = any(a["status"] == "FAILED" for a in activities)
        waiting = latest.get("WAITING_FOR_USER", {}).get("status") == "ACTIVE"
        return {
            "schema_version": "9.0.1",
            "conversation": {"thread_id": started["subject_id"],
                             "correlation_id": correlation_id},
            "status": "FAILED" if failed and not waiting else (
                "COMPLETED" if waiting else "ACTIVE"),
            "cognitive_stage": current,
            "activities": activities,
            "history": history,
            "updated_at": history[-1]["timestamp"] if history else started["created_at"],
        }


class CognitiveSurface:
    """Completed-turn projection enriched from the live EventBus lifecycle."""

    def __init__(self, bus, runtime, lifecycle: SurfaceLifecycle | None = None) -> None:
        self.bus = bus
        self.runtime = runtime
        self.lifecycle = lifecycle or SurfaceLifecycle(bus)

    def build(self, user_id: str, *, thread_id: str, correlation_id: str,
              meaning: dict[str, Any] | None, trace: dict[str, Any] | None,
              agent_result: dict[str, Any]) -> dict[str, Any]:
        timestamp = _now()
        refs = [{"kind": "cognitive_object", "id": obj["id"], "type": obj["type"]}
                for obj in (meaning or {}).get("created_objects", [])]
        live = self.lifecycle.snapshot(user_id, correlation_id) or {
            "activities": [], "history": [], "cognitive_stage": "WAITING_FOR_USER"}

        recalled = agent_result.get("recalled") or []
        retrieval = (trace or {}).get("retrieval") or {}
        uncertainties: list[dict[str, Any]] = []
        semantic = (meaning or {}).get("semantic") or {}
        if semantic.get("ambiguous"):
            uncertainties.append({"kind": "AMBIGUOUS_MEANING",
                                  "detail": semantic.get("ambiguity_reason"),
                                  "source_ref": (meaning or {}).get("compilation_id")})
        if retrieval.get("degraded"):
            uncertainties.append({"kind": "DEGRADED",
                                  "detail": "Memory retrieval reported degraded operation.",
                                  "source_ref": correlation_id})
        if not recalled and any(c.get("type") == "QUESTION"
                                for c in semantic.get("candidates", [])):
            uncertainties.append({"kind": "INSUFFICIENT_EVIDENCE",
                                  "detail": "No relevant long-term memory was returned for this turn.",
                                  "source_ref": correlation_id})

        dependencies = self.runtime.dependency_status()
        provider = dependencies.get("model_provider", {})
        system_state = "DEGRADED" if any(
            d["state"] in ("DEGRADED", "FAILED") for d in dependencies.values()) else "ACTIVE"
        if provider.get("state") == "NOT_CONFIGURED":
            system_state = "DEGRADED"

        evidence = [{"kind": "memory", "id": r.get("memory", {}).get("id"),
                     "content": r.get("memory", {}).get("content"),
                     "confidence": r.get("score"), "source": "memory_retrieval"}
                    for r in recalled if r.get("memory")]
        insights: list[dict[str, Any]] = []
        if trace and trace.get("need"):
            insights.append({"kind": "NEED_HYPOTHESIS", "value": trace["need"].get("need"),
                             "confidence": trace["need"].get("confidence"),
                             "source_ref": correlation_id})
        if trace and trace.get("intent"):
            insights.append({"kind": "CURRENT_INTENT", "value": trace["intent"].get("label"),
                             "confidence": trace["intent"].get("confidence"),
                             "source_ref": trace["intent"].get("id")})

        surface = {
            "schema_version": "9.0.1",
            "conversation": {"thread_id": thread_id, "correlation_id": correlation_id},
            "system_state": {"status": system_state,
                             "provider": provider.get("state", "UNKNOWN"),
                             "timestamp": timestamp},
            "cognitive_stage": live.get("cognitive_stage", "WAITING_FOR_USER"),
            "activities": live.get("activities", []),
            "activity_history": live.get("history", []),
            "active_objects": refs, "relevant_evidence": evidence,
            "visible_insights": insights, "uncertainties": uncertainties,
            "next_interaction": {
                "kind": "CLARIFICATION" if semantic.get("ambiguous") else "USER_INPUT",
                "prompt": semantic.get("ambiguity_reason") if semantic.get("ambiguous") else None},
            "timestamp": timestamp,
        }
        self.bus.emit(
            user_id, "surface.selected", "Selected a backend-driven cognitive surface.",
            thread_id=thread_id, subject_kind="conversation", subject_id=thread_id,
            correlation_id=correlation_id,
            payload={"stage": surface["cognitive_stage"],
                     "activities": [a["stage"] for a in surface["activities"]],
                     "active_object_ids": [r["id"] for r in refs]})
        self.bus.emit(
            user_id, "cognitive_response.generated", "Generated a cognitive response.",
            thread_id=thread_id, subject_kind="conversation", subject_id=thread_id,
            correlation_id=correlation_id,
            payload={"provider": agent_result.get("provider"),
                     "surface_stage": surface["cognitive_stage"]})
        return surface
