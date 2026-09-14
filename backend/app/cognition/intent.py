"""
Intent and need engine.

Intent is probabilistic and revisable. The system distinguishes what the user
SAID from what they may MEAN and what they may NEED - and treats the latter two
as hypotheses, never facts.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any

STATUSES = ("current", "emerging", "historical", "dormant", "abandoned", "uncertain")

# Need hypotheses. Ordered: the first match with the strongest signal wins.
NEEDS = ("information", "action", "planning", "decision_support", "reassurance",
         "listening", "reflection", "exploration", "second_opinion",
         "delegation", "silence")

_SIGNALS: list[tuple[str, re.Pattern[str], float]] = [
    ("decision_support", re.compile(
        r"\b(should i|which (?:one|option)|better to|or should|pros and cons|"
        r"trade[- ]?offs?|help me (?:decide|choose))\b", re.I), 0.75),
    ("planning", re.compile(
        r"\b(plan|roadmap|schedule|timeline|how do i get|steps to|break (?:this|it) down|"
        r"prioriti[sz]e)\b", re.I), 0.7),
    ("action", re.compile(
        r"\b(can you (?:do|make|create|update|delete|send|run)|please (?:do|create|update)|"
        r"go ahead and|set (?:this|that) up)\b", re.I), 0.72),
    ("second_opinion", re.compile(
        r"\b(what do you think|does that (?:make sense|sound right)|am i wrong|"
        r"sanity check|thoughts\?)", re.I), 0.65),
    ("reassurance", re.compile(
        r"\b(worried|anxious|stressed|overwhelmed|nervous|scared|burn(?:ed|t) out|"
        r"exhausted|can't keep up)\b", re.I), 0.7),
    ("listening", re.compile(
        r"\b(just (?:venting|thinking out loud|need to talk)|no advice|"
        r"don'?t (?:fix|solve)|just listen)\b", re.I), 0.85),
    ("silence", re.compile(
        r"\b(leave me alone|stop (?:talking|asking)|be quiet|not now)\b", re.I), 0.9),
    ("reflection", re.compile(
        r"\b(looking back|in hindsight|what did i learn|how did (?:that|it) go|"
        r"reviewing)\b", re.I), 0.6),
    ("exploration", re.compile(
        r"\b(what if|explore|brainstorm|ideas for|curious about|what are my options)\b",
        re.I), 0.6),
    ("delegation", re.compile(
        r"\b(handle (?:this|it)|take care of|you decide|do it for me|on my behalf)\b",
        re.I), 0.7),
    ("information", re.compile(
        r"^(what|who|when|where|why|how|which|do you|does|is|are|can you tell)\b", re.I),
     0.55),
]

_INTENT_PHRASE = re.compile(
    r"\b(?:i (?:want|need|aim|plan|intend) to|my goal is to|i'm trying to|"
    r"i am trying to|help me)\s+(.{4,90})", re.I)
_CHANGE = re.compile(
    r"\b(?:actually|instead|changed my mind|no longer|scrap that|forget (?:that|it),"
    r"|switching to|pivoting to|not anymore)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[.!?].*$", "", text, flags=re.S)).strip(" ,;:-—")


class IntentEngine:
    """Tracks the user's evolving objective and infers their likely need."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    # ---------------------------------------------------------------- needs
    def detect_need(self, text: str) -> dict[str, Any]:
        """
        Infer the most likely need. Returns a hypothesis with confidence, never
        a claim of fact. Falls back to 'information' at low confidence.
        """
        best: tuple[str, float] | None = None
        matched: list[str] = []
        for need, pattern, weight in _SIGNALS:
            if pattern.search(text):
                matched.append(need)
                if best is None or weight > best[1]:
                    best = (need, weight)
        if best is None:
            return {"need": "information", "confidence": 0.3, "signals": [],
                    "hypothesis": True}
        return {"need": best[0], "confidence": round(best[1], 2),
                "signals": matched, "hypothesis": True}

    # --------------------------------------------------------------- intent
    def record(self, user_id: str, label: str, *, confidence: float = 0.6,
               evidence: str = "", source: str = "rules",
               correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Record an intent supplied by an external understander (e.g. the LLM
        extractor). Deduplicates against existing intents rather than stacking
        near-identical goals, and never deletes prior intents.
        """
        label = _clean(label)[:120]
        if len(label) < 4:
            return None

        existing = self.db.query_one(
            "SELECT * FROM intents WHERE user_id=? AND lower(label)=lower(?)",
            (user_id, label))
        if existing:
            conf = min(0.95, max(float(existing["confidence"]), float(confidence)))
            self.db.execute(
                "UPDATE intents SET status='current', confidence=?, updated_at=?"
                " WHERE id=?", (conf, _now(), existing["id"]))
            return self.get(existing["id"])

        intent_id = f"i_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO intents (id,user_id,label,status,confidence,evidence,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (intent_id, user_id, label, "current",
             max(0.0, min(0.95, float(confidence))), evidence[:300], now, now))
        self.bus.emit(user_id, "intent.detected", f"Working toward: {label}",
                      subject_kind="intent", subject_id=intent_id,
                      correlation_id=correlation_id,
                      payload={"confidence": confidence, "source": source})
        return self.get(intent_id)

    def observe(self, user_id: str, text: str,
                correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Update intent from an utterance.

        An explicit change signal demotes the previous current intent to
        'historical' rather than deleting it - old intent stays true as history.
        """
        stripped = text.strip()
        if not stripped or stripped.endswith("?"):
            return None

        match = _INTENT_PHRASE.search(stripped)
        if match is None:
            return None
        label = _clean(match.group(1))[:120]
        if len(label) < 4:
            return None

        current = self.current(user_id)
        changed = bool(_CHANGE.search(stripped))

        existing = self.db.query_one(
            "SELECT * FROM intents WHERE user_id=? AND lower(label)=lower(?)",
            (user_id, label))
        if existing:
            conf = min(0.95, float(existing["confidence"]) + 0.1)
            self.db.execute(
                "UPDATE intents SET status='current', confidence=?, updated_at=?"
                " WHERE id=?", (conf, _now(), existing["id"]))
            self.bus.emit(user_id, "intent.updated", f"Still working toward: {label}",
                          subject_kind="intent", subject_id=existing["id"],
                          correlation_id=correlation_id, payload={"confidence": conf})
            return self.get(existing["id"])

        if current and changed:
            self.db.execute(
                "UPDATE intents SET status='historical', updated_at=? WHERE id=?",
                (_now(), current["id"]))

        intent_id = f"i_{uuid.uuid4().hex[:12]}"
        now = _now()
        status = "current" if (changed or current is None) else "emerging"
        confidence = 0.65 if changed else 0.5
        self.db.execute(
            "INSERT INTO intents (id,user_id,label,status,confidence,evidence,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (intent_id, user_id, label, status, confidence, stripped[:300], now, now))

        event = "intent.changed" if (current and changed) else "intent.detected"
        summary = (f"Objective changed to: {label}" if (current and changed)
                   else f"Working toward: {label}")
        self.bus.emit(user_id, event, summary, subject_kind="intent",
                      subject_id=intent_id, correlation_id=correlation_id,
                      payload={"status": status, "confidence": confidence,
                               "previous": current["label"] if current else None})
        return self.get(intent_id)

    def set_status(self, user_id: str, intent_id: str, status: str,
                   correlation_id: str | None = None) -> dict[str, Any] | None:
        if status not in STATUSES:
            raise ValueError(f"Unknown intent status: {status!r}")
        row = self.db.query_one("SELECT * FROM intents WHERE id=? AND user_id=?",
                                (intent_id, user_id))
        if row is None:
            return None
        self.db.execute("UPDATE intents SET status=?, updated_at=? WHERE id=?",
                        (status, _now(), intent_id))
        self.bus.emit(user_id, "intent.updated", f"{row['label']} → {status}",
                      subject_kind="intent", subject_id=intent_id,
                      correlation_id=correlation_id, payload={"status": status})
        return self.get(intent_id)

    # ----------------------------------------------------------------- reads
    def get(self, intent_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM intents WHERE id=?", (intent_id,))
        return dict(row) if row else None

    def current(self, user_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM intents WHERE user_id=? AND status='current'"
            " ORDER BY datetime(updated_at) DESC LIMIT 1", (user_id,))
        return dict(row) if row else None

    def list(self, user_id: str) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM intents WHERE user_id=? ORDER BY datetime(updated_at) DESC",
            (user_id,))]

    def trajectory(self, user_id: str) -> list[dict[str, Any]]:
        """Chronological intent evolution - historical → current → emerging."""
        return [dict(r) for r in self.db.query(
            "SELECT * FROM intents WHERE user_id=? ORDER BY datetime(created_at) ASC",
            (user_id,))]

    def conflicts(self, user_id: str) -> list[dict[str, Any]]:
        """
        Competing high-confidence objectives.

        Two simultaneously current/emerging intents above 0.6 confidence compete
        for the same finite resource (the user's time).
        """
        active = [dict(r) for r in self.db.query(
            "SELECT * FROM intents WHERE user_id=? AND status IN ('current','emerging')"
            " AND confidence >= 0.6", (user_id,))]
        out: list[dict[str, Any]] = []
        for i, a in enumerate(active):
            for b in active[i + 1:]:
                out.append({"a": a, "b": b, "resource": "time",
                            "note": "Both objectives are active and compete for time."})
        return out
