"""
V8.2 — Cognitive Context Builder.

One canonical assembly stage that runs BEFORE model execution and produces a
bounded, ranked, explained context bundle.

Rules from §5 of the brief:
  * Never dump the database into the model. Everything is capped.
  * Every included item carries: source, relevance, confidence, and a plain
    reason for inclusion.
  * Items the system is not permitted to read are reported as NOT CONNECTED
    rather than omitted silently.
  * Assembly failures degrade — they never break the turn.

The builder reads from subsystems that already exist (memory, world, intent,
predictions, decisions, policy, continuity). It owns no state of its own, which
keeps panels as views over the same cognitive state rather than a parallel store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)

# Hard caps. Latency and prompt size are correctness concerns, not nice-to-haves.
MAX_MEMORIES = 6
MAX_WORLD = 6
MAX_GOALS = 4
MAX_COMMITMENTS = 4
MAX_DECISIONS = 3
MAX_PREDICTIONS = 3
MAX_EPISODIC = 6
MAX_PREFERENCES = 4
MAX_TOTAL_CHARS = 4000


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_days(iso: Any) -> float | None:
    try:
        dt = datetime.fromisoformat(str(iso))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (_now() - dt).total_seconds() / 86_400)
    except (TypeError, ValueError):
        return None


@dataclass
class ContextItem:
    """One piece of context, with provenance and a reason it was included."""

    kind: str                  # memory | world | goal | commitment | intent | ...
    id: str | None
    content: str
    source: str
    relevance: float           # 0..1, from real retrieval/selection evidence
    confidence: float | None   # None when the subsystem records no confidence
    reason: str
    permission: str = "OWNED"  # OWNED | NOT CONNECTED
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "id": self.id, "content": self.content,
                "source": self.source, "relevance": round(self.relevance, 4),
                "confidence": (round(self.confidence, 3)
                               if self.confidence is not None else None),
                "reason": self.reason, "permission": self.permission,
                **({"extra": self.extra} if self.extra else {})}


@dataclass
class ContextBundle:
    """The assembled, bounded context for one turn."""

    correlation_id: str | None
    items: list[ContextItem] = field(default_factory=list)
    sections: dict[str, list[ContextItem]] = field(default_factory=dict)
    unavailable: list[dict[str, str]] = field(default_factory=list)
    truncated: bool = False
    degraded: bool = False
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- rendering
    def to_prompt(self) -> str:
        """
        Render the bundle as a compact prompt block.

        Only sections that actually have content appear. Empty context yields an
        explicit statement of emptiness rather than a misleading blank.
        """
        if not self.items:
            return "No relevant prior context was found for this message."

        titles = {
            "memory": "Relevant long-term memories",
            "episodic": "Recent conversation",
            "goal": "Active goals",
            "commitment": "Open commitments",
            "world": "Current world state",
            "intent": "Current intent hypothesis",
            "preference": "Known preferences",
            "decision": "Recent decisions",
            "prediction": "Open predictions",
            "outcome": "Relevant prior outcomes",
            "capability": "System state",
        }
        out: list[str] = []
        for key, title in titles.items():
            section = self.sections.get(key) or []
            if not section:
                continue
            out.append(f"{title}:")
            for item in section:
                conf = (f", confidence {item.confidence:.2f}"
                        if item.confidence is not None else "")
                ident = f", id={item.id}" if item.id else ""
                out.append(f"- {item.content} (relevance {item.relevance:.2f}"
                           f"{conf}{ident})")
            out.append("")
        if self.unavailable:
            names = ", ".join(u["source"] for u in self.unavailable)
            out.append(f"Not available to me: {names}.")
        return "\n".join(out).strip()

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "item_count": len(self.items),
            "items": [i.as_dict() for i in self.items],
            "sections": {k: [i.as_dict() for i in v]
                         for k, v in self.sections.items() if v},
            "unavailable": self.unavailable,
            "truncated": self.truncated,
            "degraded": self.degraded,
            "notes": self.notes,
            "prompt_chars": len(self.to_prompt()),
        }


class ContextBuilder:
    """
    Assembles the single canonical context bundle for a turn.

    It is given the cognition root so it can read from the same subsystems the
    Observatory reads from — there is no second copy of any state here.
    """

    def __init__(self, cognition) -> None:
        self.cog = cognition

    # ------------------------------------------------------------------ build
    def build(self, user_id: str, message: str, *,
              retrieved: list[dict[str, Any]] | None = None,
              thread_id: str | None = None,
              correlation_id: str | None = None) -> ContextBundle:
        """
        Assemble context. Each section is guarded independently so one failing
        subsystem degrades that section only.
        """
        bundle = ContextBundle(correlation_id=correlation_id)
        sections: dict[str, list[ContextItem]] = {}

        for key, fn in (
            ("memory", lambda: self._memories(user_id, retrieved)),
            ("episodic", lambda: self._episodic(user_id, thread_id)),
            ("goal", lambda: self._world_of_kind(user_id, "goal", MAX_GOALS)),
            ("commitment", lambda: self._world_of_kind(user_id, "commitment",
                                                       MAX_COMMITMENTS)),
            ("world", lambda: self._world_other(user_id)),
            ("intent", lambda: self._intent(user_id)),
            ("preference", lambda: self._preferences(user_id)),
            ("decision", lambda: self._decisions(user_id)),
            ("prediction", lambda: self._predictions(user_id)),
            ("capability", lambda: self._capability_state()),
        ):
            try:
                sections[key] = fn() or []
            except Exception as exc:  # a broken section must not break the turn
                log.info("Context section %s unavailable: %s", key, exc)
                bundle.degraded = True
                bundle.notes.append(f"Section '{key}' could not be assembled.")
                sections[key] = []

        # Permission-aware external sources (§19). Honest UNCONNECTED, never faked.
        bundle.unavailable = self._external_sources()

        # Flatten with a global cap, highest relevance first, but always keep at
        # least the top item of each non-empty section so context stays balanced.
        ordered: list[ContextItem] = []
        for items in sections.values():
            if items:
                ordered.append(items[0])
        rest = [i for items in sections.values() for i in items[1:]]
        rest.sort(key=lambda i: i.relevance, reverse=True)

        kept: list[ContextItem] = []
        total = 0
        for item in ordered + rest:
            cost = len(item.content) + 40
            if total + cost > MAX_TOTAL_CHARS:
                bundle.truncated = True
                continue
            kept.append(item)
            total += cost

        kept_ids = {id(i) for i in kept}
        bundle.items = kept
        bundle.sections = {k: [i for i in v if id(i) in kept_ids]
                           for k, v in sections.items()}
        if bundle.truncated:
            bundle.notes.append(
                f"Context was capped at {MAX_TOTAL_CHARS} characters; "
                "lowest-relevance items were dropped.")
        return bundle

    # --------------------------------------------------------------- sections
    def _memories(self, user_id: str,
                  retrieved: list[dict[str, Any]] | None) -> list[ContextItem]:
        """
        Long-term memories. Prefers the candidates already retrieved this turn so
        we do not run retrieval twice, and honours arbitration/quarantine state.
        """
        candidates = retrieved or []
        items: list[ContextItem] = []
        for c in candidates[:MAX_MEMORIES]:
            rep = self.cog.reputation.get(user_id, c["id"])
            if rep.get("lifecycle") in ("retired", "quarantined"):
                continue
            reasons = c.get("reasons") or []
            why = ("; ".join(reasons[:2]) if reasons
                   else f"Retrieved with relevance {float(c.get('score', 0)):.2f}")
            items.append(ContextItem(
                kind="memory", id=c["id"], content=c["content"],
                source=str(c.get("source") or "conversation"),
                relevance=float(c.get("score", 0.0) or 0.0),
                confidence=float(c.get("confidence", 0.7) or 0.7),
                reason=f"{why}. Reputation: {rep.get('reputation')}.",
                extra={"reputation": rep.get("reputation"),
                       "lifecycle": rep.get("lifecycle"),
                       "category": c.get("category")}))
        return items

    def _episodic(self, user_id: str, thread_id: str | None) -> list[ContextItem]:
        """Recent turns in this thread — continuity within the conversation."""
        if not thread_id:
            return []
        rows = self.cog.db.query(
            "SELECT role, content, created_at FROM messages WHERE user_id=?"
            " AND thread_id=? ORDER BY id DESC LIMIT ?",
            (user_id, thread_id, MAX_EPISODIC))
        items: list[ContextItem] = []
        # Most recent turn is the most relevant; decay gently down the list.
        for pos, r in enumerate(rows):
            items.append(ContextItem(
                kind="episodic", id=None,
                content=f"{r['role']}: {str(r['content'])[:240]}",
                source="conversation",
                relevance=round(max(0.2, 0.9 - 0.12 * pos), 4),
                confidence=None,
                reason=f"Turn {pos + 1} back in this thread."))
        return items

    def _world_of_kind(self, user_id: str, kind: str, cap: int) -> list[ContextItem]:
        entities = [e for e in self.cog.world.list(user_id, kind=kind)
                    if e["state"] not in ("completed", "abandoned")]
        items: list[ContextItem] = []
        for e in entities[:cap]:
            urgency = 0.9 if e["state"] == "at_risk" else 0.6
            items.append(ContextItem(
                kind="goal" if kind == "goal" else "commitment", id=e["id"],
                content=f"{e['label']} [{e['state']}]",
                source=str(e.get("source") or "conversation"),
                relevance=urgency, confidence=float(e.get("confidence") or 0.6),
                reason=(f"Active {kind} in the world model, state '{e['state']}'."),
                extra={"state": e["state"], "due_at": e.get("due_at")}))
        return items

    def _world_other(self, user_id: str) -> list[ContextItem]:
        skip = {"goal", "commitment"}
        entities = [e for e in self.cog.world.list(user_id)
                    if e["kind"] not in skip
                    and e["state"] not in ("completed", "abandoned")]
        items: list[ContextItem] = []
        for e in entities[:MAX_WORLD]:
            items.append(ContextItem(
                kind="world", id=e["id"],
                content=f"{e['kind']}: {e['label']} [{e['state']}]",
                source=str(e.get("source") or "conversation"),
                relevance=0.75 if e["state"] == "at_risk" else 0.5,
                confidence=float(e.get("confidence") or 0.6),
                reason=f"Tracked {e['kind']} currently '{e['state']}'.",
                extra={"kind": e["kind"], "state": e["state"]}))
        return items

    def _intent(self, user_id: str) -> list[ContextItem]:
        current = self.cog.intent.current(user_id)
        if not current:
            return []
        return [ContextItem(
            kind="intent", id=current["id"],
            content=f"Appears to be working toward: {current['label']}",
            source="intent-engine",
            relevance=0.8, confidence=float(current.get("confidence") or 0.5),
            reason=("Current intent hypothesis — probabilistic, not confirmed "
                    "fact."))]

    def _preferences(self, user_id: str) -> list[ContextItem]:
        """Behavioural policy learned about this user (how to respond)."""
        items: list[ContextItem] = []
        for pol in self.cog.policy.list(user_id)[:MAX_PREFERENCES]:
            if pol.get("confidence", 0) < 0.35:
                continue
            items.append(ContextItem(
                kind="preference", id=pol["key"],
                content=f"{pol['key'].replace('_', ' ')}: {pol['value']}",
                source="policy-engine", relevance=0.7,
                confidence=float(pol.get("confidence") or 0.5),
                reason=(f"Learned from {pol.get('evidence_count', 0)} observation(s): "
                        f"{pol.get('rationale') or 'no rationale recorded'}.")))
        return items

    def _decisions(self, user_id: str) -> list[ContextItem]:
        rows = self.cog.db.query(
            "SELECT id, summary, chosen, status, created_at FROM decisions"
            " WHERE user_id=? ORDER BY datetime(created_at) DESC LIMIT ?",
            (user_id, MAX_DECISIONS))
        items: list[ContextItem] = []
        for r in rows:
            age = _age_days(r["created_at"])
            if age is not None and age > 60:
                continue
            items.append(ContextItem(
                kind="decision", id=r["id"],
                content=f"{r['summary']} → chose {r['chosen']}",
                source="decision-log", relevance=0.55, confidence=None,
                reason=f"Recent decision, status '{r['status']}'."))
        return items

    def _predictions(self, user_id: str) -> list[ContextItem]:
        items: list[ContextItem] = []
        for p in self.cog.predictions.list(user_id, status="open")[:MAX_PREDICTIONS]:
            items.append(ContextItem(
                kind="prediction", id=p["id"], content=p["statement"],
                source="prediction-engine", relevance=0.5,
                confidence=float(p.get("confidence") or 0.5),
                reason="Open prediction awaiting an observable outcome."))
        return items

    def _capability_state(self) -> list[ContextItem]:
        """A one-line truthful statement of what the system can do right now."""
        try:
            decision = self.cog.router.route("memory_retrieval")
        except Exception:
            return []
        return [ContextItem(
            kind="capability", id=None,
            content=(f"Execution mode for memory work: {decision.mode}"
                     f"{' (degraded)' if decision.degraded else ''}."),
            source="capability-router", relevance=0.3, confidence=None,
            reason=decision.reason)]

    def _external_sources(self) -> list[dict[str, str]]:
        """
        Context fabric placeholders (§19).

        These are declared so the architecture is permission-aware and ready,
        but they return NOT CONNECTED — never fabricated results.
        """
        return [
            {"source": "calendar", "state": "NOT CONNECTED",
             "detail": "No calendar connector is configured."},
            {"source": "email", "state": "NOT CONNECTED",
             "detail": "No email connector is configured."},
            {"source": "files", "state": "NOT CONNECTED",
             "detail": "Only files explicitly uploaded to /api/perceive are read."},
        ]
