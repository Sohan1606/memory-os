"""
V8.2 — Intent Evolution V2 (§10) and Need Detection V2 (§11).

Both remain strictly probabilistic. The hard rules:

  * A question NEVER creates an intent as fact. Asking "should I use Postgres?"
    does not mean the user intends to use Postgres.
  * Intent is confirmed only by an explicit statement of purpose.
  * Every intent change records: previous intent, new intent, what changed it,
    confidence, and uncertainty.
  * Needs are hypotheses. Where correctness is later observable, it is recorded
    and used to report accuracy honestly — otherwise INSUFFICIENT EVIDENCE.

This module works alongside the V8.1 IntentEngine (which keeps handling the
`intents` table) and adds the transition ledger plus stronger state handling.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

# Full intent lifecycle.
STATUSES = ("current", "emerging", "historical", "dormant", "abandoned",
            "resumed", "conflicting", "uncertain")

# Statuses that count as "the user is actively pursuing this".
ACTIVE = ("current", "emerging", "resumed")

DORMANT_AFTER_DAYS = 30.0

# Explicit purpose statements. Note every pattern is a STATEMENT form.
_PURPOSE = re.compile(
    r"\b(?:i (?:want|need|aim|plan|intend|am going) to|my goal is to|"
    r"i'm trying to|i am trying to|i'm working on|i am working on|"
    r"help me)\s+(.{4,90})", re.I)

# Explicit abandonment / change signals.
_ABANDON = re.compile(
    r"\b(?:giving up on|dropping|scrap(?:ping)? (?:that|this)|no longer (?:want|need)|"
    r"not doing .* anymore|abandoned?)\b", re.I)
_CHANGE = re.compile(
    r"\b(?:actually|instead|changed my mind|switching to|pivoting to|"
    r"forget (?:that|it))\b", re.I)
_RESUME = re.compile(
    r"\b(?:back to|picking up|resuming|returning to|let's continue)\b", re.I)

# Question detection: anything interrogative cannot assert intent.
_QUESTION = re.compile(
    r"^\s*(?:what|who|when|where|why|how|which|is|are|do|does|did|can|could|"
    r"should|would|will|shall|may|might)\b", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_question(text: str) -> bool:
    stripped = text.strip()
    return stripped.endswith("?") or bool(_QUESTION.match(stripped))


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ",
                  re.sub(r"[.!?].*$", "", text, flags=re.S)).strip(" ,;:-—")


class IntentEvolution:
    """Records and explains how the user's objective changes over time."""

    def __init__(self, db, bus, intent_engine) -> None:
        self.db = db
        self.bus = bus
        self.engine = intent_engine

    # ------------------------------------------------------------ transitions
    def record_transition(self, user_id: str, intent_id: str, *,
                          to_status: str, from_status: str | None = None,
                          previous_intent_id: str | None = None,
                          confidence: float = 0.5, uncertainty: float = 0.5,
                          changed_because: str = "",
                          evidence: list[str] | None = None,
                          correlation_id: str | None = None) -> dict[str, Any]:
        if to_status not in STATUSES:
            raise ValueError(f"Unknown intent status: {to_status!r}")
        tid = f"it_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO intent_transitions (id,user_id,intent_id,from_status,"
            "to_status,previous_intent_id,confidence,uncertainty,changed_because,"
            "evidence,correlation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, user_id, intent_id, from_status, to_status, previous_intent_id,
             confidence, uncertainty, changed_because[:300],
             json.dumps(evidence or [], default=str), correlation_id, _now()))
        self.bus.emit(
            user_id,
            "intent.changed" if from_status and from_status != to_status
            else "intent.updated",
            changed_because[:160] or f"Intent → {to_status}",
            subject_kind="intent", subject_id=intent_id,
            correlation_id=correlation_id,
            payload={"from_status": from_status, "to_status": to_status,
                     "previous_intent_id": previous_intent_id,
                     "confidence": round(confidence, 3),
                     "uncertainty": round(uncertainty, 3),
                     "changed_because": changed_because[:200]})
        return self.get_transition(tid)  # type: ignore[return-value]

    def get_transition(self, transition_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM intent_transitions WHERE id=?",
                                (transition_id,))
        if row is None:
            return None
        d = dict(row)
        try:
            d["evidence"] = json.loads(d.get("evidence") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["evidence"] = []
        return d

    def transitions(self, user_id: str, intent_id: str | None = None,
                    limit: int = 50) -> list[dict[str, Any]]:
        if intent_id:
            rows = self.db.query(
                "SELECT id FROM intent_transitions WHERE user_id=? AND intent_id=?"
                " ORDER BY id DESC LIMIT ?", (user_id, intent_id, limit))
        else:
            rows = self.db.query(
                "SELECT id FROM intent_transitions WHERE user_id=?"
                " ORDER BY id DESC LIMIT ?", (user_id, limit))
        return [t for t in (self.get_transition(r["id"]) for r in rows) if t]

    # -------------------------------------------------------------- observing
    def observe(self, user_id: str, text: str,
                correlation_id: str | None = None) -> dict[str, Any]:
        """
        Interpret one utterance for intent, honestly.

        Returns a report containing the previous intent, the current intent,
        why it changed (if it did), confidence and uncertainty.
        """
        previous = self.engine.current(user_id)
        report: dict[str, Any] = {
            "previous_intent": previous, "current_intent": previous,
            "changed": False, "changed_because": None,
            "confidence": float(previous["confidence"]) if previous else 0.0,
            "uncertainty": 1.0 - (float(previous["confidence"]) if previous else 0.0),
            "is_question": _is_question(text),
            "note": None,
        }

        # --- a question never asserts intent ---
        if _is_question(text):
            report["note"] = (
                "This was a question, so no intent was asserted from it. "
                "Questions can refine an existing intent but never create one "
                "as fact.")
            return report

        # --- explicit abandonment ---
        if previous and _ABANDON.search(text):
            self.engine.set_status(user_id, previous["id"], "abandoned",
                                   correlation_id=correlation_id)
            self.record_transition(
                user_id, previous["id"], from_status=previous["status"],
                to_status="abandoned", confidence=0.8, uncertainty=0.2,
                changed_because="You said you were dropping this objective.",
                evidence=[text[:200]], correlation_id=correlation_id)
            report.update({"current_intent": None, "changed": True,
                           "changed_because":
                               "You said you were dropping this objective.",
                           "confidence": 0.8, "uncertainty": 0.2})
            return report

        match = _PURPOSE.search(text)
        if match is None:
            # No new objective stated. But the user may be explicitly RESUMING
            # one they had set aside ("back to the Postgres migration"), which
            # is a real signal we should not throw away.
            resumed = self._try_resume(user_id, text,
                                       correlation_id=correlation_id)
            if resumed is not None:
                report.update(resumed)
                return report

            # Nothing asserted. An existing intent may still go dormant.
            self._decay(user_id, correlation_id=correlation_id)
            report["note"] = ("No explicit statement of purpose in this message; "
                              "intent is unchanged.")
            return report

        label = _clean(match.group(1))[:120]
        if len(label) < 4:
            return report

        changed_signal = bool(_CHANGE.search(text))
        resumed_signal = bool(_RESUME.search(text))

        existing = self.db.query_one(
            "SELECT * FROM intents WHERE user_id=? AND lower(label)=lower(?)",
            (user_id, label))

        if existing:
            from_status = existing["status"]
            to_status = "resumed" if from_status in (
                "historical", "dormant", "abandoned") else "current"
            confidence = min(0.95, float(existing["confidence"]) + 0.1)
            self.db.execute(
                "UPDATE intents SET status=?, confidence=?, updated_at=?"
                " WHERE id=?", (to_status, confidence, _now(), existing["id"]))
            because = ("You returned to an objective you had set aside."
                       if to_status == "resumed"
                       else "You restated this objective, so I am more confident.")
            self.record_transition(
                user_id, existing["id"], from_status=from_status,
                to_status=to_status, confidence=confidence,
                uncertainty=round(1.0 - confidence, 3), changed_because=because,
                evidence=[text[:200]], correlation_id=correlation_id)
            current = self.engine.get(existing["id"])
            report.update({"current_intent": current,
                           "changed": from_status != to_status,
                           "changed_because": because, "confidence": confidence,
                           "uncertainty": round(1.0 - confidence, 3)})
            return report

        # --- a genuinely new objective ---
        # Confidence stays modest: a single statement is a hypothesis.
        #
        # Without an explicit change signal the new objective is only EMERGING:
        # an unconfirmed second goal must not displace the objective the user
        # actually confirmed, so `previous` stays current until it is either
        # superseded or closed.
        confidence = 0.65 if (changed_signal or resumed_signal) else 0.5
        status = "current" if (changed_signal or previous is None) else "emerging"

        intent_id = f"i_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO intents (id,user_id,label,status,confidence,evidence,"
            "created_at,updated_at,uncertainty,previous_intent_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (intent_id, user_id, label, status, confidence, text[:300], now, now,
             round(1.0 - confidence, 3), previous["id"] if previous else None))

        because = ("You stated a different objective, so the previous one became "
                   "history." if (previous and changed_signal)
                   else "You stated a new objective.")

        if previous and changed_signal:
            self.db.execute(
                "UPDATE intents SET status='historical', updated_at=? WHERE id=?",
                (now, previous["id"]))
            self.record_transition(
                user_id, previous["id"], from_status=previous["status"],
                to_status="historical", confidence=0.7, uncertainty=0.3,
                changed_because="Superseded by a newly stated objective.",
                evidence=[text[:200]], correlation_id=correlation_id)
        elif previous:
            # Two live objectives. The previous one stays CURRENT (it was
            # confirmed; this one has not been), but the competition is
            # recorded so the conflict is visible rather than silently resolved.
            self.record_transition(
                user_id, previous["id"], from_status=previous["status"],
                to_status=previous["status"],
                confidence=float(previous["confidence"]), uncertainty=0.6,
                changed_because=("A second objective appeared without the first "
                                 "being closed; both are now in play."),
                evidence=[text[:200]], correlation_id=correlation_id)

        self.bus.emit(user_id, "intent.detected", f"Working toward: {label}",
                      subject_kind="intent", subject_id=intent_id,
                      correlation_id=correlation_id,
                      payload={"status": status, "confidence": confidence,
                               "hypothesis": True})
        self.record_transition(
            user_id, intent_id, from_status=None, to_status=status,
            previous_intent_id=previous["id"] if previous else None,
            confidence=confidence, uncertainty=round(1.0 - confidence, 3),
            changed_because=because, evidence=[text[:200]],
            correlation_id=correlation_id)

        if status == "emerging":
            # An unconfirmed second objective must NOT be reported as the
            # current one. It is surfaced separately as `emerging_intent` so the
            # competition is visible without overwriting what the user actually
            # confirmed.
            report.update({
                "current_intent": previous,
                "emerging_intent": self.engine.get(intent_id),
                "changed": False,
                "changed_because": None,
                "confidence": float(previous["confidence"]) if previous else 0.0,
                "uncertainty": 0.6,
                "note": ("A second objective appeared while the first is still "
                         "open. I am tracking it as emerging rather than "
                         "assuming it replaced your current goal."),
            })
            return report

        report.update({"current_intent": self.engine.get(intent_id),
                       "emerging_intent": None,
                       "changed": True, "changed_because": because,
                       "confidence": confidence,
                       "uncertainty": round(1.0 - confidence, 3)})
        return report

    def _try_resume(self, user_id: str, text: str, *,
                    correlation_id: str | None = None) -> dict[str, Any] | None:
        """
        Handle "back to X" / "picking up X" where X is an intent we already know.

        We only resume an intent whose label genuinely overlaps the utterance —
        a vague "let's continue" is not enough to pick a goal for the user.
        """
        if not _RESUME.search(text):
            return None

        rows = self.db.query(
            "SELECT * FROM intents WHERE user_id=? AND status IN"
            " ('historical','dormant','abandoned') ORDER BY updated_at DESC",
            (user_id,))
        if not rows:
            return None

        words = set(re.findall(r"[a-z0-9]{4,}", text.lower()))
        best, best_overlap = None, 0
        for row in rows:
            label_words = set(re.findall(r"[a-z0-9]{4,}", row["label"].lower()))
            overlap = len(words & label_words)
            if overlap > best_overlap:
                best, best_overlap = row, overlap

        if best is None or best_overlap == 0:
            return None  # Ambiguous: do not guess which goal they meant.

        confidence = min(0.95, float(best["confidence"]) + 0.1)
        self.db.execute(
            "UPDATE intents SET status='resumed', confidence=?, updated_at=?"
            " WHERE id=?", (confidence, _now(), best["id"]))
        because = "You returned to an objective you had set aside."
        self.record_transition(
            user_id, best["id"], from_status=best["status"], to_status="resumed",
            confidence=confidence, uncertainty=round(1.0 - confidence, 3),
            changed_because=because, evidence=[text[:200]],
            correlation_id=correlation_id)
        return {"current_intent": self.engine.get(best["id"]), "changed": True,
                "changed_because": because, "confidence": confidence,
                "uncertainty": round(1.0 - confidence, 3)}

    def _decay(self, user_id: str, correlation_id: str | None = None) -> None:
        """Active intents nobody has mentioned for a long time go dormant."""
        rows = self.db.query(
            "SELECT id, label, status, confidence FROM intents WHERE user_id=?"
            " AND status IN ('current','emerging','resumed')"
            " AND julianday('now') - julianday(updated_at) > ?",
            (user_id, DORMANT_AFTER_DAYS))
        for row in rows:
            self.db.execute(
                "UPDATE intents SET status='dormant', updated_at=? WHERE id=?",
                (_now(), row["id"]))
            self.record_transition(
                user_id, row["id"], from_status=row["status"], to_status="dormant",
                confidence=float(row["confidence"]), uncertainty=0.5,
                changed_because=(f"Not mentioned for more than "
                                 f"{int(DORMANT_AFTER_DAYS)} days."),
                correlation_id=correlation_id)

    # ------------------------------------------------------------ explanation
    def explain(self, user_id: str, intent_id: str) -> dict[str, Any]:
        """'Why did this intent change?' answered from the transition ledger."""
        intent = self.engine.get(intent_id)
        history = list(reversed(self.transitions(user_id, intent_id)))
        if not history:
            return {"intent": intent, "transitions": [],
                    "explanation": ("INSUFFICIENT EVIDENCE — no recorded "
                                    "transitions for this intent.")}
        lines = [f"{t['created_at']}: "
                 f"{t['from_status'] or 'new'} → {t['to_status']} "
                 f"({t['changed_because']})" for t in history]
        return {"intent": intent, "transitions": history,
                "explanation": "\n".join(lines)}


# =============================================================== need detection
NEEDS = ("information", "action", "planning", "decision_support", "reassurance",
         "listening", "reflection", "exploration", "second_opinion",
         "delegation", "silence")

# Signals, each with the evidence weight it genuinely carries.
_SIGNALS: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("planning", re.compile(
        r"\b(plan|roadmap|schedule|timeline|steps?|how (?:do|should) i (?:start|approach)|"
        r"break (?:this|it) down)\b", re.I), 0.7),
    ("decision_support", re.compile(
        r"\b(should i|which (?:one|should)|trade-?offs?|pros and cons|"
        r"decide between|better option|a or b)\b", re.I), 0.75),
    ("action", re.compile(
        r"\b(can you (?:do|make|create|update|delete|send|run|save)|"
        r"please (?:do|create|update|save|remember)|go ahead and|"
        r"set (?:this|that) up)\b", re.I), 0.72),
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
        r"\b(leave me alone|stop (?:talking|asking)|be quiet|not now)\b", re.I),
     0.9),
    ("reflection", re.compile(
        r"\b(looking back|in hindsight|what did i learn|how did (?:that|it) go|"
        r"reviewing)\b", re.I), 0.6),
    ("exploration", re.compile(
        r"\b(what if|explore|brainstorm|ideas for|curious about|"
        r"what are my options)\b", re.I), 0.6),
    ("delegation", re.compile(
        r"\b(handle (?:this|it)|take care of|you decide|do it for me|"
        r"on my behalf)\b", re.I), 0.7),
    ("information", re.compile(
        r"^(what|who|when|where|why|how|which|do you|does|is|are|can you tell)\b",
        re.I), 0.55),
)


class NeedDetector:
    """
    Classifies what the user likely needs. Always a hypothesis.

    V8.2 additions over V8.1: conversation-context awareness, persisted
    hypotheses, and measurable historical accuracy where feedback exists.
    """

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    def detect(self, user_id: str, text: str, *,
               recent_needs: list[str] | None = None,
               correlation_id: str | None = None,
               persist: bool = True) -> dict[str, Any]:
        """
        Infer the most likely need from signals plus recent conversational shape.
        """
        matched: list[tuple[str, float]] = []
        for need, pattern, weight in _SIGNALS:
            if pattern.search(text):
                matched.append((need, weight))

        if not matched:
            hypothesis = {"need": "information", "confidence": 0.3,
                          "signals": [], "hypothesis": True,
                          "source": "rules",
                          "reason": ("No strong signal; defaulting to "
                                     "'information' at low confidence.")}
        else:
            matched.sort(key=lambda pair: pair[1], reverse=True)
            need, weight = matched[0]

            # Context: a need repeated across recent turns is more credible.
            recent = recent_needs or []
            if recent and need == recent[0]:
                weight = min(0.95, weight + 0.08)
                note = " Reinforced: the previous turn showed the same need."
            elif recent and need in recent:
                weight = min(0.95, weight + 0.04)
                note = " Seen earlier in this conversation."
            else:
                note = ""

            hypothesis = {
                "need": need, "confidence": round(weight, 2),
                "signals": [m[0] for m in matched], "hypothesis": True,
                "source": "rules",
                "reason": (f"Matched the '{need}' signal in your wording.{note}")}

        if persist:
            hid = f"nh_{uuid.uuid4().hex[:12]}"
            self.db.execute(
                "INSERT INTO need_hypotheses (id,user_id,need,confidence,signals,"
                "source,utterance,was_correct,correlation_id,created_at)"
                " VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                (hid, user_id, hypothesis["need"], hypothesis["confidence"],
                 json.dumps(hypothesis["signals"]), hypothesis["source"],
                 text[:300], correlation_id, _now()))
            hypothesis["id"] = hid

        self.bus.emit(user_id, "need.detected",
                      f"Need looks like: {hypothesis['need']}",
                      subject_kind="need", subject_id=hypothesis["need"],
                      correlation_id=correlation_id, payload=hypothesis)
        return hypothesis

    def recent(self, user_id: str, limit: int = 5) -> list[str]:
        return [r["need"] for r in self.db.query(
            "SELECT need FROM need_hypotheses WHERE user_id=? ORDER BY id DESC"
            " LIMIT ?", (user_id, limit))]

    def evaluate(self, user_id: str, hypothesis_id: str, correct: bool,
                 correlation_id: str | None = None) -> dict[str, Any] | None:
        """Record observed feedback on whether a need hypothesis was right."""
        row = self.db.query_one(
            "SELECT * FROM need_hypotheses WHERE id=? AND user_id=?",
            (hypothesis_id, user_id))
        if row is None:
            return None
        self.db.execute(
            "UPDATE need_hypotheses SET was_correct=? WHERE id=?",
            (1 if correct else 0, hypothesis_id))
        self.bus.emit(user_id, "need.evaluated",
                      f"Need '{row['need']}' was "
                      f"{'right' if correct else 'wrong'}",
                      subject_kind="need", subject_id=row["need"],
                      correlation_id=correlation_id,
                      payload={"hypothesis_id": hypothesis_id,
                               "correct": correct})
        return {"id": hypothesis_id, "need": row["need"], "correct": correct}

    def accuracy(self, user_id: str) -> dict[str, Any]:
        """Historical correctness — only where feedback was actually recorded."""
        rows = self.db.query(
            "SELECT need, was_correct FROM need_hypotheses WHERE user_id=?"
            " AND was_correct IS NOT NULL", (user_id,))
        if not rows:
            return {"evaluated": 0, "accuracy": None,
                    "detail": "INSUFFICIENT EVIDENCE — no need hypothesis has "
                              "been confirmed or corrected yet."}
        correct = sum(1 for r in rows if int(r["was_correct"]) == 1)
        by_need: dict[str, dict[str, int]] = {}
        for r in rows:
            entry = by_need.setdefault(r["need"], {"correct": 0, "total": 0})
            entry["total"] += 1
            entry["correct"] += int(r["was_correct"])
        return {"evaluated": len(rows), "correct": correct,
                "accuracy": round(correct / len(rows), 3),
                "by_need": by_need,
                "detail": f"{correct}/{len(rows)} need hypotheses were confirmed."}
