"""
V8.2 — Continuity Engine (§9).

Continuity is *selective*. The point is not to remember everything across
conversations, it is to bring back only what still matters, and to be able to
say why it was brought back.

Continuity items are derived from state that already exists (world entities,
intents, predictions, decisions, failures), then persisted in
`continuity_items` so relevance and status survive restarts.

A memory is never surfaced merely for being old. Every surfaced item carries one
of the reasons in REASONS.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

# The only permitted justifications for carrying something forward.
REASONS = {
    "active_project": "continued active project",
    "unfinished_commitment": "unfinished commitment",
    "recent_decision": "recent decision",
    "prior_conversation": "relevant prior conversation",
    "pending_question": "pending question",
    "open_prediction": "awaiting an observable outcome",
    "recent_failure": "recent failure worth revisiting",
    "at_risk": "something flagged at risk",
    "next_step": "next expected step",
}

KINDS = ("goal", "commitment", "project", "decision", "prediction", "question",
         "failure", "task")

# Items untouched for this long stop being surfaced automatically.
DORMANT_AFTER_DAYS = 45.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _age_days(value: Any) -> float | None:
    try:
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86_400)
    except (TypeError, ValueError):
        return None


class ContinuityEngine:
    """Tracks what deserves to carry across conversations, and why."""

    def __init__(self, db, bus, world, intent, predictions) -> None:
        self.db = db
        self.bus = bus
        self.world = world
        self.intent = intent
        self.predictions = predictions

    # ---------------------------------------------------------------- upsert
    def track(self, user_id: str, kind: str, summary: str, *, reason: str,
              subject_kind: str | None = None, subject_id: str | None = None,
              relevance: float = 0.5,
              correlation_id: str | None = None) -> dict[str, Any]:
        """Open (or refresh) a continuity item."""
        if kind not in KINDS:
            raise ValueError(f"Unknown continuity kind: {kind!r}")
        if reason not in REASONS:
            raise ValueError(f"Unknown continuity reason: {reason!r}")

        now = _now()
        existing = None
        if subject_id:
            existing = self.db.query_one(
                "SELECT * FROM continuity_items WHERE user_id=? AND subject_id=?"
                " AND kind=?", (user_id, subject_id, kind))
        if existing is None:
            existing = self.db.query_one(
                "SELECT * FROM continuity_items WHERE user_id=? AND kind=?"
                " AND summary=?", (user_id, kind, summary[:300]))

        if existing:
            self.db.execute(
                "UPDATE continuity_items SET summary=?, reason=?, relevance=?,"
                " status='open', last_seen_at=?, updated_at=? WHERE id=?",
                (summary[:300], reason, max(0.0, min(1.0, relevance)), now, now,
                 existing["id"]))
            return self.get(existing["id"])  # type: ignore[return-value]

        item_id = f"cont_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO continuity_items (id,user_id,kind,subject_kind,subject_id,"
            "summary,reason,relevance,status,last_seen_at,created_at,updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,'open',?,?,?)",
            (item_id, user_id, kind, subject_kind, subject_id, summary[:300],
             reason, max(0.0, min(1.0, relevance)), now, now, now))
        self.bus.emit(user_id, "continuity.item_opened", summary[:160],
                      subject_kind="continuity", subject_id=item_id,
                      correlation_id=correlation_id,
                      payload={"kind": kind, "reason": REASONS[reason],
                               "relevance": relevance})
        return self.get(item_id)  # type: ignore[return-value]

    def close(self, user_id: str, item_id: str, *, note: str = "",
              correlation_id: str | None = None) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM continuity_items WHERE id=? AND user_id=?",
            (item_id, user_id))
        if row is None:
            return None
        self.db.execute(
            "UPDATE continuity_items SET status='closed', updated_at=? WHERE id=?",
            (_now(), item_id))
        self.bus.emit(user_id, "continuity.item_closed",
                      note or f"Closed: {row['summary']}",
                      subject_kind="continuity", subject_id=item_id,
                      correlation_id=correlation_id)
        return self.get(item_id)

    # ------------------------------------------------------------------ reads
    def get(self, item_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM continuity_items WHERE id=?",
                                (item_id,))
        if row is None:
            return None
        d = dict(row)
        d["reason_label"] = REASONS.get(d.get("reason", ""), d.get("reason"))
        d["age_days"] = _age_days(d.get("last_seen_at"))
        return d

    def open_items(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id FROM continuity_items WHERE user_id=? AND status='open'"
            " ORDER BY relevance DESC, datetime(last_seen_at) DESC LIMIT ?",
            (user_id, limit))
        return [i for i in (self.get(r["id"]) for r in rows) if i]

    # ---------------------------------------------------------------- refresh
    def refresh(self, user_id: str,
                correlation_id: str | None = None) -> list[dict[str, Any]]:
        """
        Rebuild continuity items from the live cognitive state.

        Everything here is derived from something the system actually recorded —
        no item is invented to make the briefing look fuller.
        """
        items: list[dict[str, Any]] = []

        for entity in self.world.list(user_id):
            if entity["state"] in ("completed", "abandoned"):
                continue
            if entity["kind"] == "commitment":
                items.append(self.track(
                    user_id, "commitment", entity["label"],
                    reason="unfinished_commitment", subject_kind="world",
                    subject_id=entity["id"], relevance=0.8,
                    correlation_id=correlation_id))
            elif entity["kind"] in ("project", "goal"):
                items.append(self.track(
                    user_id, "project" if entity["kind"] == "project" else "goal",
                    entity["label"],
                    reason="at_risk" if entity["state"] == "at_risk"
                    else "active_project",
                    subject_kind="world", subject_id=entity["id"],
                    relevance=0.85 if entity["state"] == "at_risk" else 0.65,
                    correlation_id=correlation_id))

        for pred in self.predictions.list(user_id, status="open"):
            items.append(self.track(
                user_id, "prediction", pred["statement"],
                reason="open_prediction", subject_kind="prediction",
                subject_id=pred["id"], relevance=0.5,
                correlation_id=correlation_id))

        for row in self.db.query(
                "SELECT id, summary, chosen FROM decisions WHERE user_id=?"
                " AND status='open' ORDER BY datetime(created_at) DESC LIMIT 5",
                (user_id,)):
            items.append(self.track(
                user_id, "decision", f"{row['summary']} → {row['chosen']}",
                reason="recent_decision", subject_kind="decision",
                subject_id=row["id"], relevance=0.55,
                correlation_id=correlation_id))

        # Recent recovery events mean something failed and may need revisiting.
        for event in self.bus.recent(user_id, limit=40,
                                     types=["recovery.started", "action.failed"]):
            age = _age_days(event.created_at)
            if age is not None and age > 3:
                continue
            items.append(self.track(
                user_id, "failure", event.summary, reason="recent_failure",
                subject_kind=event.subject_kind or "recovery",
                subject_id=event.subject_id or str(event.id), relevance=0.6,
                correlation_id=correlation_id))

        self._decay(user_id)
        return items

    def _decay(self, user_id: str) -> None:
        """Stop surfacing items nobody has touched in a long time."""
        for item in self.open_items(user_id, limit=200):
            age = item.get("age_days")
            if age is not None and age > DORMANT_AFTER_DAYS:
                self.db.execute(
                    "UPDATE continuity_items SET status='dormant', updated_at=?"
                    " WHERE id=?", (_now(), item["id"]))

    # --------------------------------------------------------------- relevance
    def relevant_to(self, user_id: str, message: str,
                    limit: int = 5) -> list[dict[str, Any]]:
        """
        Which continuity items matter for THIS message.

        Scores lexical overlap against the item summary, but always keeps
        genuinely urgent items (at-risk / unfinished commitments) in play even
        when the wording does not overlap.
        """
        import re
        tokens = {w for w in re.findall(r"[a-z0-9]{3,}", message.lower())}
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in self.open_items(user_id, limit=100):
            item_tokens = {w for w in
                           re.findall(r"[a-z0-9]{3,}", item["summary"].lower())}
            overlap = (len(tokens & item_tokens) / max(1, len(item_tokens))
                       if item_tokens else 0.0)
            urgent = item["reason"] in ("at_risk", "unfinished_commitment")
            score = overlap * 0.7 + float(item["relevance"]) * 0.3
            if overlap == 0.0 and not urgent:
                continue
            scored.append((score, {**item, "match_score": round(score, 3),
                                   "matched_terms": sorted(tokens & item_tokens)}))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]

    # --------------------------------------------------------------- briefing
    def resume(self, user_id: str,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        A returning-user briefing built entirely from recorded state.

        Extends the V8.1 shape (same keys) with the V8.2 continuity items and
        their reasons, so existing clients keep working.
        """
        last = self.db.query_one(
            "SELECT created_at FROM cognitive_events WHERE user_id=?"
            " ORDER BY id DESC LIMIT 1", (user_id,))
        away_days = _age_days(last["created_at"]) if last else None

        self.refresh(user_id, correlation_id=correlation_id)
        items = self.open_items(user_id, limit=12)

        current_intent = self.intent.current(user_id)
        open_commitments = [e for e in self.world.list(user_id, kind="commitment")
                            if e["state"] not in ("completed", "abandoned")]
        at_risk = self.world.list(user_id, state="at_risk")
        open_predictions = self.predictions.list(user_id, status="open")
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
            for item in items[:3]:
                lines.append(f"{item['summary']} ({item['reason_label']}).")
            if not items and not current_intent:
                lines.append("Nothing is currently open from our earlier conversations.")

        if not first_time:
            self.bus.emit(user_id, "continuity.resumed",
                          f"Resumed with {len(items)} open item(s)",
                          subject_kind="continuity", subject_id=user_id,
                          correlation_id=correlation_id,
                          payload={"items": len(items),
                                   "away_days": round(away_days, 2)
                                   if away_days else None})

        return {
            "first_time": first_time,
            "away_seconds": int(away_days * 86_400) if away_days else None,
            "away_days": round(away_days, 2) if away_days else None,
            "current_intent": current_intent,
            "open_commitments": open_commitments,
            "at_risk": at_risk,
            "open_predictions": open_predictions,
            "stale_memories": [dict(r) for r in stale],
            "continuity_items": items,
            "briefing": (" ".join(lines) if lines
                         else "Nothing has changed since we last spoke."),
        }
