"""
Self model and continuity.

The system must know its own limits and be able to say "I don't have enough
reliable information to do that safely" instead of hallucinating a capability.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _age(iso: str | None) -> float | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (TypeError, ValueError):
        return None


class SelfModel:
    """Honest introspection over what this deployment can actually do."""

    def __init__(self, runtime) -> None:
        self._rt = runtime

    def capabilities(self) -> dict[str, Any]:
        rt = self._rt
        provider = rt.provider.status()
        vectors = rt.vectors

        def cap(name: str, active: bool, detail: str,
                degraded: bool = False) -> dict[str, Any]:
            state = "ACTIVE" if active else ("DEGRADED" if degraded else "NOT CONFIGURED")
            return {"name": name, "state": state, "detail": detail}

        return {
            "conversation": cap("conversation", True,
                                "Text conversation is always available."),
            "model_tool_calling": cap(
                "model_tool_calling", provider.supports_tool_calling,
                provider.detail, degraded=not provider.supports_tool_calling),
            "semantic_memory": cap(
                "semantic_memory", vectors.mode == "semantic",
                f"Vector retrieval mode: {vectors.mode}.",
                degraded=vectors.mode != "semantic"),
            "persistent_memory": cap("persistent_memory", True,
                                     "SQLite + Chroma persist across restarts."),
            "voice_server": cap(
                "voice_server", rt.transcriber.mode == "whisper",
                rt.transcriber.detail),
            "langmem": cap("langmem", rt.langmem.status().active,
                           rt.langmem.status().detail),
            "model_assisted_extraction": cap(
                "model_assisted_extraction",
                rt.cognition.extractor.available,
                rt.cognition.extractor.status()["detail"],
                degraded=not rt.cognition.extractor.available),
            "external_context": cap(
                "external_context", False,
                "No external connectors are configured. Calendar, email and files"
                " are NOT CONNECTED."),
            "external_actions": cap(
                "external_actions", False,
                "The system cannot take actions outside its own memory store."),
        }

    def limitations(self) -> list[str]:
        """Plain-language limits, derived from live state rather than hardcoded."""
        out: list[str] = []
        rt = self._rt
        if not rt.provider.status().supports_tool_calling:
            out.append(
                "No tool-calling model is configured, so I answer from a deterministic"
                " planner rather than generating free-form language.")
        if rt.vectors.mode != "semantic":
            out.append(
                "Semantic retrieval is unavailable; I am matching on keywords, which is"
                " less precise.")
        if not rt.langmem.status().active:
            if rt.cognition.extractor.available:
                out.append(
                    "LangMem is not installed. Understanding is extracted by the"
                    " local language model and validated against a strict schema.")
            else:
                out.append(
                    "LangMem is not active and no language model is configured;"
                    " memory extraction uses my deterministic policy engine.")
        if rt.transcriber.mode != "whisper":
            out.append("Server-side transcription is not configured; voice uses your browser.")
        out.append("I have no access to your calendar, email or files.")
        out.append("I cannot take actions outside my own memory store.")
        return out

    def can(self, capability: str) -> dict[str, Any]:
        """Answer 'can you do X?' honestly."""
        caps = self.capabilities()
        entry = caps.get(capability)
        if entry is None:
            return {"capability": capability, "able": False,
                    "reason": "I don't have that capability at all."}
        able = entry["state"] == "ACTIVE"
        return {"capability": capability, "able": able, "state": entry["state"],
                "reason": entry["detail"]}

    def health_summary(self) -> dict[str, Any]:
        caps = self.capabilities()
        active = [k for k, v in caps.items() if v["state"] == "ACTIVE"]
        degraded = [k for k, v in caps.items() if v["state"] == "DEGRADED"]
        missing = [k for k, v in caps.items() if v["state"] == "NOT CONFIGURED"]
        return {"active": active, "degraded": degraded, "not_configured": missing,
                "capabilities": caps, "limitations": self.limitations()}


class Continuity:
    """Reconstructs useful state when the user returns after time away."""

    def __init__(self, db, bus, world, intent, predictions) -> None:
        self.db = db
        self.bus = bus
        self.world = world
        self.intent = intent
        self.predictions = predictions

    def resume(self, user_id: str) -> dict[str, Any]:
        """
        Build a returning-user briefing from real state.

        Never greets the user as a stranger when there is history, and never
        invents history when there is none.
        """
        last = self.db.query_one(
            "SELECT created_at FROM cognitive_events WHERE user_id=?"
            " ORDER BY id DESC LIMIT 1", (user_id,))
        away_seconds = _age(last["created_at"]) if last else None

        current_intent = self.intent.current(user_id)
        open_commitments = [e for e in self.world.list(user_id, kind="commitment")
                            if e["state"] not in ("completed", "abandoned")]
        at_risk = self.world.list(user_id, state="at_risk")
        open_predictions = self.predictions.list(user_id, status="open")

        # Memories not touched in a long time are flagged as possibly stale.
        stale = self.db.query(
            "SELECT id, content, updated_at FROM memories WHERE user_id=?"
            " AND status='active' AND julianday('now') - julianday(updated_at) > 45"
            " ORDER BY updated_at ASC LIMIT 5", (user_id,))

        first_time = last is None
        lines: list[str] = []
        if first_time:
            lines.append("This is the first thing we've talked about.")
        else:
            if current_intent:
                lines.append(f"You were working toward: {current_intent['label']}.")
            if open_commitments:
                lines.append(f"{len(open_commitments)} commitment(s) are still open.")
            if at_risk:
                lines.append(f"{len(at_risk)} item(s) are flagged at risk.")
            if open_predictions:
                lines.append(f"{len(open_predictions)} prediction(s) are awaiting an outcome.")
            if stale:
                lines.append(f"{len(stale)} memory(ies) haven't been confirmed in a while.")
            if not lines:
                lines.append("Nothing has changed since we last spoke.")

        return {
            "first_time": first_time,
            "away_seconds": int(away_seconds) if away_seconds else None,
            "current_intent": current_intent,
            "open_commitments": open_commitments,
            "at_risk": at_risk,
            "open_predictions": open_predictions,
            "stale_memories": [dict(r) for r in stale],
            "briefing": " ".join(lines),
        }


class RecoveryManager:
    """Detect → diagnose → retry/fallback → verify → continue."""

    def __init__(self, bus) -> None:
        self.bus = bus

    def attempt(self, user_id: str, operation: str, fn, *, fallback=None,
                correlation_id: str | None = None) -> dict[str, Any]:
        """
        Run `fn`, and on failure diagnose and try `fallback`.

        Always reports truthfully which path produced the result - a fallback
        result is never presented as a full success.
        """
        try:
            return {"ok": True, "degraded": False, "result": fn(),
                    "path": "primary"}
        except Exception as exc:
            self.bus.emit(user_id, "recovery.started",
                          f"{operation} failed: {type(exc).__name__}",
                          subject_kind="recovery", subject_id=operation,
                          correlation_id=correlation_id,
                          payload={"error": str(exc)[:300]})
            if fallback is None:
                return {"ok": False, "degraded": True, "result": None,
                        "path": "none", "error": str(exc)[:300],
                        "message": f"I couldn't complete {operation}, so I haven't "
                                   "changed anything."}
            try:
                result = fallback()
                self.bus.emit(user_id, "recovery.completed",
                              f"{operation} recovered via fallback",
                              subject_kind="recovery", subject_id=operation,
                              correlation_id=correlation_id)
                return {"ok": True, "degraded": True, "result": result,
                        "path": "fallback",
                        "message": f"{operation} completed in a degraded mode."}
            except Exception as exc2:
                return {"ok": False, "degraded": True, "result": None,
                        "path": "failed", "error": str(exc2)[:300],
                        "message": f"I couldn't complete {operation}, so I haven't "
                                   "changed anything."}
