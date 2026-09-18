"""
V8.2 — Object permanence (§21).

When the user inspects an object in the UI, "that memory" / "that decision" /
"that prediction" in the next message should resolve to the object they are
actually looking at.

Safety rules:
  * Resolution is by STABLE ID, never by matching display text.
  * Focus is scoped to (user, session) and expires, so a stale panel from an
    hour ago cannot silently capture a new reference.
  * Resolution is only attempted for genuinely referential phrasing. "Remember
    that I like Python" must not be hijacked by a focused object.
  * If nothing is focused, the resolver says so instead of guessing.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

# Object kinds a reference can resolve to.
# v8.3.1: focus is no longer only "what is open in the inspector". The
# conversation sets it too, so "pause that" can resolve to a real object. The
# cognitive object types are therefore focusable alongside the V8.2 UI kinds.
KINDS = ("memory", "decision", "prediction", "world", "intent", "policy",
         "arbitration", "influence", "continuity",
         "mission", "goal", "project", "person", "simulation", "observation")

# Natural phrasings that refer to a currently-inspected object.
_REFERENCE = re.compile(
    r"\b(?:that|this|the)\s+(memory|decision|prediction|person|goal|project|"
    r"commitment|intent|policy|preference|entity|item|one|mission|objective|"
    r"simulation|observation)\b", re.I)

# Bare deictic references ("why did you use that?", "explain this").
_BARE = re.compile(
    r"\b(?:why (?:did|do) you (?:use|choose|pick|believe|decide) (?:that|this|it)|"
    r"what (?:changed|happened) (?:with|to) (?:that|this|it)|"
    r"explain (?:that|this|it)|tell me (?:more )?about (?:that|this|it)|"
    r"forget (?:that|this|it)|undo (?:that|this|it)|"
    r"(?:pause|resume|stop|continue|complete|finish|drop|abandon|"
    r"deprioritise|deprioritize)\s+(?:that|this|it)|"
    r"(?:that|this|it) is (?:wrong|outdated|no longer true))\b", re.I)

# Word → object kind.
_WORD_KIND = {
    "memory": "memory", "decision": "decision", "prediction": "prediction",
    "person": "world", "goal": "world", "project": "world",
    "commitment": "world", "entity": "world", "item": "world",
    "intent": "intent", "policy": "policy", "preference": "policy",
    # v8.3.1 cognitive objects.
    "mission": "mission", "objective": "mission",
    "simulation": "simulation", "observation": "observation",
}

FOCUS_TTL_MINUTES = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat(timespec="seconds")


class FocusTracker:
    """Tracks what the user is currently inspecting, and resolves references."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    # ------------------------------------------------------------------ write
    def set_focus(self, user_id: str, subject_kind: str, subject_id: str, *,
                  session_id: str = "default", label: str | None = None,
                  correlation_id: str | None = None) -> dict[str, Any]:
        """Record that the user opened a specific object in the UI."""
        if subject_kind not in KINDS:
            raise ValueError(f"Unknown focus kind: {subject_kind!r}")
        now = _iso()
        self.db.execute(
            "INSERT INTO focus_state (user_id,session_id,subject_kind,subject_id,"
            "label,updated_at) VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(user_id,session_id,subject_kind) DO UPDATE SET"
            " subject_id=excluded.subject_id, label=excluded.label,"
            " updated_at=excluded.updated_at",
            (user_id, session_id, subject_kind, subject_id, label, now))
        self.bus.emit(user_id, "focus.changed",
                      f"Inspecting {subject_kind} {subject_id}",
                      subject_kind=subject_kind, subject_id=subject_id,
                      correlation_id=correlation_id,
                      payload={"session_id": session_id, "label": label})
        return {"subject_kind": subject_kind, "subject_id": subject_id,
                "session_id": session_id, "label": label, "updated_at": now}

    def clear(self, user_id: str, *, session_id: str = "default",
              subject_kind: str | None = None) -> int:
        if subject_kind:
            cur = self.db.execute(
                "DELETE FROM focus_state WHERE user_id=? AND session_id=?"
                " AND subject_kind=?", (user_id, session_id, subject_kind))
        else:
            cur = self.db.execute(
                "DELETE FROM focus_state WHERE user_id=? AND session_id=?",
                (user_id, session_id))
        return cur.rowcount or 0

    # ------------------------------------------------------------------- read
    def current(self, user_id: str, *,
                session_id: str = "default") -> list[dict[str, Any]]:
        """Live (unexpired) focus entries, newest first."""
        cutoff = (_now() - timedelta(minutes=FOCUS_TTL_MINUTES)).isoformat(
            timespec="seconds")
        rows = self.db.query(
            "SELECT * FROM focus_state WHERE user_id=? AND session_id=?"
            " AND updated_at >= ? ORDER BY updated_at DESC",
            (user_id, session_id, cutoff))
        return [dict(r) for r in rows]

    # --------------------------------------------------------------- resolve
    def resolve(self, user_id: str, text: str, *, session_id: str = "default",
                correlation_id: str | None = None) -> dict[str, Any]:
        """
        Resolve a conversational reference to a focused object.

        Returns {resolved, subject_kind, subject_id, reason}. `resolved` is
        False whenever the reference is absent, ambiguous, or nothing is
        focused — the caller must never assume a fallback.
        """
        typed = _REFERENCE.search(text)
        bare = _BARE.search(text)
        if not typed and not bare:
            return {"resolved": False, "reason": (
                "No referential phrase such as 'that memory' was used.")}

        focused = self.current(user_id, session_id=session_id)
        if not focused:
            return {"resolved": False, "reason": (
                "You referred to something specific, but nothing is currently "
                "open in the inspector, so I cannot tell which object you mean.")}

        wanted_kind: str | None = None
        if typed:
            word = typed.group(1).lower()
            wanted_kind = _WORD_KIND.get(word)

        if wanted_kind:
            match = next((f for f in focused
                          if f["subject_kind"] == wanted_kind), None)
            if match is None:
                return {"resolved": False, "reason": (
                    f"You referred to a {wanted_kind}, but the object you have "
                    f"open is a {focused[0]['subject_kind']}.")}
        else:
            # Bare reference: only safe when exactly one thing is in focus.
            if len(focused) > 1:
                kinds = ", ".join(f["subject_kind"] for f in focused)
                return {"resolved": False, "reason": (
                    f"Ambiguous reference — you have several objects open "
                    f"({kinds}). Say which one you mean.")}
            match = focused[0]

        self.bus.emit(user_id, "focus.resolved",
                      f"'that' resolved to {match['subject_kind']} "
                      f"{match['subject_id']}",
                      subject_kind=match["subject_kind"],
                      subject_id=match["subject_id"],
                      correlation_id=correlation_id,
                      payload={"phrase": (typed or bare).group(0)})
        return {"resolved": True, "subject_kind": match["subject_kind"],
                "subject_id": match["subject_id"], "label": match.get("label"),
                "phrase": (typed or bare).group(0),
                "reason": (f"You have {match['subject_kind']} "
                           f"{match['subject_id']} open in the inspector.")}
