"""
Continuous world model (§7-§9).

This EXTENDS `world.WorldModel` rather than replacing it. V8.2's WorldModel
persists entities correctly but has two gaps found in the V8.3 audit:

  1. `world_changes` exists in the schema with no writer - the world had a
     present but no past.
  2. Nothing models staleness or reconciles conflicting claims; a repeated
     mention simply overwrote state.

`WorldStateV2` wraps the existing model and adds:

  * change provenance  - every mutation appends to world_changes with the
                         previous value, the new value, why, and the evidence.
  * staleness          - per fact-class expectations. Stale is NOT false: a
                         stale fact keeps its value and gains a caveat.
  * reconciliation     - keep / supersede / merge / flag / downgrade / ignore,
                         scored on the same principles as V8.2 arbitration.

Nothing here deletes world state. Reconciliation downgrades and supersedes;
removal stays a user action.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Any

from .world import KINDS, STATES

# ---------------------------------------------------------------- staleness
# How long a *class* of fact typically stays true. These are expectations, not
# expiry dates: crossing the horizon marks something for re-confirmation, never
# marks it false. Chosen conservatively - we would rather ask than assume.
FRESHNESS_HORIZON_DAYS: dict[str, float] = {
    "person": 180.0,       # relationships change slowly
    "constraint": 120.0,   # constraints are fairly durable
    "resource": 90.0,
    "goal": 90.0,
    "project": 45.0,       # project state moves fast
    "commitment": 21.0,    # deadlines and promises go stale quickly
    "risk": 14.0,          # a risk not revisited in two weeks is unreliable
}
DEFAULT_HORIZON_DAYS = 60.0

FRESHNESS_CLASSES = ("FRESH", "AGEING", "STALE", "UNKNOWN")

# Reconciliation verdicts (§8).
VERDICTS = ("keep", "supersede", "merge", "flag", "downgrade", "ignore")

# Similarity above which two labels are considered the same subject.
MERGE_SIMILARITY = 0.86
# Similarity band in which two claims are about the same thing but differ.
CONFLICT_SIMILARITY = 0.55


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso() -> str:
    return _now().isoformat(timespec="seconds")


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


class WorldStateV2:
    """Change-tracked, staleness-aware, reconciling world state."""

    def __init__(self, db, bus, world, observations=None) -> None:
        self.db = db
        self.bus = bus
        self.world = world
        self.observations = observations

    # ----------------------------------------------------- change provenance
    def record_change(self, user_id: str, entity_id: str, change: str, *,
                      previous_state: str | None = None,
                      new_state: str | None = None,
                      source: str = "conversation",
                      confidence: float | None = None,
                      evidence: list[str] | None = None,
                      correlation_id: str | None = None) -> dict[str, Any]:
        """
        Append one change record. This is the writer the V8.2 schema was
        missing - from V8.3 onward the world model has a reconstructable past.
        """
        cid = f"wc_{uuid.uuid4().hex[:12]}"
        self.db.execute(
            "INSERT INTO world_changes (id,user_id,entity_id,change,"
            "previous_state,new_state,source,confidence,evidence,"
            "correlation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (cid, user_id, entity_id, change, previous_state, new_state, source,
             confidence, json.dumps(evidence or []), correlation_id, _iso()))
        self.bus.emit(user_id, "world.change_recorded", change,
                      subject_kind="world", subject_id=entity_id,
                      correlation_id=correlation_id,
                      payload={"change": change, "previous": previous_state,
                               "new": new_state, "source": source})
        return {"id": cid, "entity_id": entity_id, "change": change}

    def changes(self, user_id: str, *, entity_id: str | None = None,
                limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM world_changes WHERE user_id=?"
        params: list[Any] = [user_id]
        if entity_id:
            sql += " AND entity_id=?"
            params.append(entity_id)
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(int(limit))
        out = []
        for row in self.db.query(sql, params):
            item = dict(row)
            try:
                item["evidence"] = json.loads(item.get("evidence") or "[]")
            except (TypeError, ValueError):
                item["evidence"] = []
            out.append(item)
        return out

    # --------------------------------------------------- tracked mutation API
    def upsert(self, user_id: str, kind: str, label: str, *,
               state: str = "active", detail: str | None = None,
               confidence: float = 0.6, due_at: str | None = None,
               source: str = "conversation", evidence: list[str] | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        """
        Upsert through the V8.2 model, then record what actually changed.
        """
        before = self.db.query_one(
            "SELECT * FROM world_entities WHERE user_id=? AND kind=?"
            " AND lower(label)=lower(?)", (user_id, kind, label.strip()[:120]))
        entity = self.world.upsert(
            user_id, kind, label, state=state, detail=detail,
            confidence=confidence, due_at=due_at, source=source,
            correlation_id=correlation_id)

        if before is None:
            self.record_change(user_id, entity["id"], "created",
                               previous_state=None, new_state=entity["state"],
                               source=source, confidence=entity["confidence"],
                               evidence=evidence, correlation_id=correlation_id)
        elif before["state"] != entity["state"]:
            self.record_change(user_id, entity["id"], "state_changed",
                               previous_state=before["state"],
                               new_state=entity["state"], source=source,
                               confidence=entity["confidence"], evidence=evidence,
                               correlation_id=correlation_id)
        else:
            self.record_change(user_id, entity["id"], "reconfirmed",
                               previous_state=before["state"],
                               new_state=entity["state"], source=source,
                               confidence=entity["confidence"], evidence=evidence,
                               correlation_id=correlation_id)

        # Any real mention is a confirmation that the fact is still current.
        self.confirm(user_id, entity["id"], source=source,
                     correlation_id=correlation_id, emit=False)
        return self.get(user_id, entity["id"]) or entity

    def set_state(self, user_id: str, entity_id: str, state: str, *,
                  reason: str | None = None, evidence: list[str] | None = None,
                  correlation_id: str | None = None) -> dict[str, Any] | None:
        before = self.world.get(entity_id)
        entity = self.world.set_state(user_id, entity_id, state, reason=reason,
                                      correlation_id=correlation_id)
        if entity is None:
            return None
        self.record_change(user_id, entity_id, "state_changed",
                           previous_state=before["state"] if before else None,
                           new_state=state, source="conversation",
                           confidence=entity.get("confidence"),
                           evidence=evidence or ([reason] if reason else []),
                           correlation_id=correlation_id)
        self.confirm(user_id, entity_id, correlation_id=correlation_id, emit=False)
        return self.get(user_id, entity_id)

    # -------------------------------------------------------------- freshness
    def confirm(self, user_id: str, entity_id: str, *,
                source: str = "conversation",
                correlation_id: str | None = None,
                emit: bool = True) -> dict[str, Any] | None:
        """Mark a fact as confirmed-current right now."""
        row = self.db.query_one(
            "SELECT * FROM world_entities WHERE id=? AND user_id=?",
            (entity_id, user_id))
        if row is None:
            return None
        self.db.execute(
            "UPDATE world_entities SET last_confirmed_at=?, stale=0,"
            " freshness_class='FRESH', freshness_reason=? WHERE id=?",
            (_iso(), f"Confirmed via {source}.", entity_id))
        if emit:
            self.bus.emit(user_id, "world.confirmed", row["label"],
                          subject_kind="world", subject_id=entity_id,
                          correlation_id=correlation_id,
                          payload={"source": source})
        return self.get(user_id, entity_id)

    def assess_freshness(self, entity: dict[str, Any]) -> dict[str, Any]:
        """
        Classify how current one fact is. Stale never means false - it means
        'this was true when last confirmed and should be re-checked'.
        """
        horizon = FRESHNESS_HORIZON_DAYS.get(entity["kind"], DEFAULT_HORIZON_DAYS)
        anchor = (_parse(entity.get("last_confirmed_at"))
                  or _parse(entity.get("updated_at"))
                  or _parse(entity.get("created_at")))
        if anchor is None:
            return {"freshness_class": "UNKNOWN", "stale": False,
                    "age_days": None, "horizon_days": horizon,
                    "reason": "No timestamp available for this fact."}
        age = (_now() - anchor).total_seconds() / 86400.0
        if age >= horizon:
            klass, stale = "STALE", True
            reason = (f"Last confirmed {age:.0f} days ago; {entity['kind']} facts "
                      f"are expected to be re-checked every {horizon:.0f} days. "
                      f"Still treated as the best available value, not as false.")
        elif age >= horizon * 0.66:
            klass, stale = "AGEING", False
            reason = (f"Last confirmed {age:.0f} days ago, approaching the "
                      f"{horizon:.0f}-day re-check horizon for {entity['kind']}.")
        else:
            klass, stale = "FRESH", False
            reason = f"Confirmed {age:.0f} days ago."
        return {"freshness_class": klass, "stale": stale,
                "age_days": round(age, 1), "horizon_days": horizon,
                "reason": reason}

    def refresh_staleness(self, user_id: str, *,
                          correlation_id: str | None = None) -> dict[str, Any]:
        """
        Re-classify every fact. Safe to run in a background cycle: it only
        writes freshness metadata, never values or states.
        """
        entities = self.world.list(user_id)
        newly_stale: list[dict[str, Any]] = []
        counts = {k: 0 for k in FRESHNESS_CLASSES}
        for entity in entities:
            verdict = self.assess_freshness(entity)
            counts[verdict["freshness_class"]] += 1
            was_stale = bool(entity.get("stale"))
            self.db.execute(
                "UPDATE world_entities SET freshness_class=?, stale=?,"
                " freshness_reason=? WHERE id=?",
                (verdict["freshness_class"], 1 if verdict["stale"] else 0,
                 verdict["reason"], entity["id"]))
            if verdict["stale"] and not was_stale:
                newly_stale.append({"id": entity["id"], "label": entity["label"],
                                    "kind": entity["kind"],
                                    "reason": verdict["reason"]})
                self.bus.emit(user_id, "world.stale", entity["label"],
                              subject_kind="world", subject_id=entity["id"],
                              correlation_id=correlation_id,
                              payload={"reason": verdict["reason"],
                                       "age_days": verdict["age_days"]})
        return {"assessed": len(entities), "counts": counts,
                "newly_stale": newly_stale,
                "detail": (f"{len(entities)} fact(s) assessed; "
                           f"{counts['STALE']} stale, {counts['AGEING']} ageing.")}

    def stale_facts(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM world_entities WHERE user_id=? AND stale=1"
            " ORDER BY datetime(updated_at) DESC", (user_id,))
        return [dict(r) for r in rows]

    # --------------------------------------------------------- reconciliation
    def reconcile(self, user_id: str, kind: str, label: str, *,
                  state: str = "active", detail: str | None = None,
                  confidence: float = 0.6, source: str = "conversation",
                  explicit_correction: bool = False,
                  evidence: list[str] | None = None,
                  correlation_id: str | None = None) -> dict[str, Any]:
        """
        Decide what an incoming claim means against what is already known.

        Verdicts, in the spirit of V8.2 arbitration:
          keep       - existing information wins; incoming is weaker
          supersede  - incoming replaces the previous value (history retained)
          merge      - same subject, compatible detail; enrich in place
          flag       - genuine conflict the system should not silently resolve
          downgrade  - conflicting but weak; lower confidence and ask later
          ignore     - nothing meaningful to add
        """
        if kind not in KINDS:
            raise ValueError(f"Unknown world entity kind: {kind!r}")
        label = " ".join((label or "").split())[:120]
        if len(label) < 3:
            raise ValueError("World entity label is too short to be meaningful.")

        existing = self.world.list(user_id, kind=kind)
        match: dict[str, Any] | None = None
        similarity = 0.0
        for candidate in existing:
            score = _similar(candidate["label"], label)
            if score > similarity:
                match, similarity = candidate, score

        # Nothing comparable: this is simply new information.
        if match is None or similarity < CONFLICT_SIMILARITY:
            entity = self.upsert(user_id, kind, label, state=state, detail=detail,
                                 confidence=confidence, source=source,
                                 evidence=evidence, correlation_id=correlation_id)
            return self._verdict("keep", entity, similarity,
                                 "No comparable existing fact; recorded as new.",
                                 user_id, correlation_id, evidence)

        fresh = self.assess_freshness(match)
        same_subject = similarity >= MERGE_SIMILARITY
        state_differs = match["state"] != state
        existing_conf = float(match["confidence"])

        # An explicit correction from the user always wins (§8).
        if explicit_correction:
            entity = self._supersede(user_id, match, label, state, detail,
                                     max(confidence, existing_conf),
                                     "User explicitly corrected this.",
                                     evidence, correlation_id)
            return self._verdict("supersede", entity, similarity,
                                 "Explicit user correction overrides the "
                                 "previous value.", user_id, correlation_id,
                                 evidence)

        # Same subject, no conflict: enrich rather than duplicate.
        if same_subject and not state_differs:
            entity = self.upsert(user_id, kind, match["label"], state=state,
                                 detail=detail or match.get("detail"),
                                 confidence=max(confidence, existing_conf),
                                 source=source, evidence=evidence,
                                 correlation_id=correlation_id)
            self.bus.emit(user_id, "world.merged", match["label"],
                          subject_kind="world", subject_id=match["id"],
                          correlation_id=correlation_id,
                          payload={"similarity": round(similarity, 3)})
            return self._verdict("merge", entity, similarity,
                                 "Same subject with compatible detail; merged.",
                                 user_id, correlation_id, evidence)

        # Conflicting state. Who wins depends on evidence strength and age.
        if state_differs:
            incoming_strength = confidence + (0.15 if evidence else 0.0)
            existing_strength = existing_conf
            if fresh["stale"]:
                # Stale existing information yields to fresh evidence, but the
                # old value is preserved in history, never erased.
                existing_strength -= 0.2
            if incoming_strength >= existing_strength + 0.1:
                entity = self._supersede(
                    user_id, match, match["label"], state, detail,
                    confidence,
                    (f"Newer evidence (confidence {confidence:.2f}) outweighed "
                     f"the previous value (confidence {existing_conf:.2f}"
                     + (", which was stale" if fresh["stale"] else "") + ")."),
                    evidence, correlation_id)
                return self._verdict("supersede", entity, similarity,
                                     "Fresher, better-evidenced claim replaced "
                                     "the previous state.", user_id,
                                     correlation_id, evidence)
            if abs(incoming_strength - existing_strength) < 0.1:
                # Too close to call - this is exactly what flagging is for.
                self.bus.emit(
                    user_id, "world.flagged",
                    f"Conflicting information about: {match['label']}",
                    subject_kind="world", subject_id=match["id"],
                    correlation_id=correlation_id,
                    payload={"existing_state": match["state"],
                             "incoming_state": state,
                             "similarity": round(similarity, 3)})
                self.record_change(
                    user_id, match["id"], "conflict_flagged",
                    previous_state=match["state"], new_state=state,
                    source=source, confidence=confidence, evidence=evidence,
                    correlation_id=correlation_id)
                return self._verdict(
                    "flag", self.get(user_id, match["id"]), similarity,
                    (f"'{match['label']}' is recorded as {match['state']} but "
                     f"was just described as {state}, with comparable evidence "
                     f"on both sides. Not resolved automatically."),
                    user_id, correlation_id, evidence)
            # Incoming is weaker: keep the value, lower our certainty in it.
            new_conf = round(max(0.1, existing_conf - 0.1), 3)
            self.db.execute(
                "UPDATE world_entities SET confidence=?, updated_at=? WHERE id=?",
                (new_conf, _iso(), match["id"]))
            self.bus.emit(user_id, "world.downgraded", match["label"],
                          subject_kind="world", subject_id=match["id"],
                          correlation_id=correlation_id,
                          payload={"confidence": new_conf,
                                   "reason": "Weaker conflicting claim observed."})
            self.record_change(user_id, match["id"], "confidence_downgraded",
                               previous_state=match["state"], new_state=match["state"],
                               source=source, confidence=new_conf,
                               evidence=evidence, correlation_id=correlation_id)
            return self._verdict("downgrade", self.get(user_id, match["id"]),
                                 similarity,
                                 "Conflicting but weaker claim; kept the "
                                 "existing value with reduced confidence.",
                                 user_id, correlation_id, evidence)

        return self._verdict("ignore", self.get(user_id, match["id"]), similarity,
                             "Nothing materially new in this claim.",
                             user_id, correlation_id, evidence)

    def _supersede(self, user_id: str, match: dict[str, Any], label: str,
                   state: str, detail: str | None, confidence: float,
                   reason: str, evidence: list[str] | None,
                   correlation_id: str | None) -> dict[str, Any]:
        previous = match["state"]
        self.db.execute(
            "UPDATE world_entities SET state=?, detail=COALESCE(?, detail),"
            " confidence=?, updated_at=?, last_confirmed_at=?, stale=0,"
            " freshness_class='FRESH', freshness_reason=? WHERE id=?",
            (state, detail, max(0.0, min(0.97, confidence)), _iso(), _iso(),
             "Superseded by newer information.", match["id"]))
        self.bus.emit(user_id, "world.superseded", f"{label} → {state}",
                      subject_kind="world", subject_id=match["id"],
                      correlation_id=correlation_id,
                      payload={"previous": previous, "new": state,
                               "reason": reason})
        self.record_change(user_id, match["id"], "superseded",
                           previous_state=previous, new_state=state,
                           source="reconciliation", confidence=confidence,
                           evidence=evidence, correlation_id=correlation_id)
        return self.get(user_id, match["id"]) or dict(match)

    def _verdict(self, verdict: str, entity: dict[str, Any] | None,
                 similarity: float, explanation: str, user_id: str,
                 correlation_id: str | None,
                 evidence: list[str] | None) -> dict[str, Any]:
        if verdict not in VERDICTS:
            raise ValueError(f"Unknown reconciliation verdict: {verdict!r}")
        if verdict in ("supersede", "merge", "downgrade", "flag"):
            self.bus.emit(user_id, "world.reconciled", explanation[:140],
                          subject_kind="world",
                          subject_id=(entity or {}).get("id"),
                          correlation_id=correlation_id,
                          payload={"verdict": verdict,
                                   "similarity": round(similarity, 3)})
        if self.observations is not None and entity is not None:
            self.observations.record(
                user_id, f"World reconciliation: {explanation}",
                source="world", origin=entity["id"],
                epistemic_status="INFERRED", confidence=0.6,
                subject_kind="world", subject_id=entity["id"],
                provenance={"verdict": verdict,
                            "similarity": round(similarity, 3),
                            "evidence": evidence or []},
                correlation_id=correlation_id)
        return {"verdict": verdict, "entity": entity,
                "similarity": round(similarity, 3), "explanation": explanation}

    # ---------------------------------------------------------------- reading
    def get(self, user_id: str, entity_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM world_entities WHERE id=? AND user_id=?",
            (entity_id, user_id))
        if row is None:
            return None
        entity = dict(row)
        entity["freshness"] = self.assess_freshness(entity)
        return entity

    def snapshot(self, user_id: str) -> dict[str, Any]:
        """Current world state with freshness attached to every fact."""
        entities = []
        for row in self.world.list(user_id):
            item = dict(row)
            item["freshness"] = self.assess_freshness(item)
            entities.append(item)
        stale = [e for e in entities if e["freshness"]["stale"]]
        by_kind: dict[str, int] = {}
        for entity in entities:
            by_kind[entity["kind"]] = by_kind.get(entity["kind"], 0) + 1
        return {
            "entities": entities,
            "count": len(entities),
            "by_kind": by_kind,
            "stale_count": len(stale),
            "detail": ("World model is empty." if not entities else
                       f"{len(entities)} tracked fact(s), {len(stale)} stale."),
        }
