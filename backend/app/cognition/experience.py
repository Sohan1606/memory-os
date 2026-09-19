"""First-class, evidence-backed experiences for V8.4.1.

An Experience is a meaningful observed episode, not another name for a message
or a memory. It binds a situation and action to an observed outcome, while the
canonical ObservationLog remains the source of evidence. Every lifecycle change
is append-only in ``experience_transitions`` and on the Cognitive Event Bus.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

LIFECYCLES = ("observed", "enriched", "validated", "active", "archived")
SCOPES = ("user", "task", "project", "domain", "environment", "global")
EVIDENCE_ROLES = ("supporting", "counterexample")

_ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "observed": ("enriched", "archived"),
    "enriched": ("validated", "archived"),
    "validated": ("active", "archived"),
    "active": ("archived",),
    "archived": (),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(value: Any, default: Any) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, json.JSONDecodeError):
        return default


class ExperienceStore:
    """Persistence and auditable lifecycle for meaningful observed episodes."""

    def __init__(self, db, bus, observations, causal) -> None:
        self.db = db
        self.bus = bus
        self.observations = observations
        self.causal = causal

    def create(
        self, user_id: str, situation: str, *, evidence_ids: list[str],
        action: str | None = None, outcome: str | None = None,
        success: bool | None = None, observation: str | None = None,
        context: dict[str, Any] | None = None, intent: str | None = None,
        need: str | None = None, consequences: list[str] | None = None,
        surprise: float | None = None, regret: float | None = None,
        confidence: float = 0.5, scope_kind: str = "user",
        scope_value: str | None = None, pattern_key: str | None = None,
        source: str = "conversation", provenance: dict[str, Any] | None = None,
        thread_id: str | None = None, correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Create an observed episode linked to real canonical observations."""
        situation = (situation or "").strip()
        if not situation:
            raise ValueError("An experience must describe its situation.")
        if scope_kind not in SCOPES:
            raise ValueError(f"Unknown experience scope: {scope_kind!r}")
        if scope_kind not in ("user", "global") and not (scope_value or "").strip():
            raise ValueError(f"Scope {scope_kind!r} requires scope_value.")
        evidence = self._owned_observations(user_id, evidence_ids)
        if not evidence:
            raise ValueError(
                "An experience requires at least one real observation as evidence.")
        if success is not None and not (outcome or "").strip():
            raise ValueError("A success/failure judgement requires an observed outcome.")

        eid = f"exp_{uuid.uuid4().hex[:12]}"
        now = _now()
        self.db.execute(
            "INSERT INTO experiences (id,user_id,thread_id,situation,context,intent,"
            "need,action,observation,outcome,consequences,success,surprise,regret,"
            "confidence,scope_kind,scope_value,pattern_key,source,provenance,"
            "lifecycle,created_at,updated_at) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, user_id, thread_id, situation, json.dumps(context or {}), intent,
             need, action, observation, outcome, json.dumps(consequences or []),
             None if success is None else int(success), surprise, regret,
             max(0.0, min(1.0, float(confidence))), scope_kind, scope_value,
             (pattern_key or "").strip() or None, source,
             json.dumps(provenance or {}), "observed", now, now))
        for obs in evidence:
            self._link_observation(
                user_id, eid, obs["id"], "supporting",
                correlation_id=correlation_id)
        self._record_transition(user_id, eid, None, "observed",
                                "Meaningful episode recorded from observations.",
                                evidence_ids, correlation_id)
        self.bus.emit(
            user_id, "experience.created", situation[:160],
            thread_id=thread_id, subject_kind="experience", subject_id=eid,
            correlation_id=correlation_id,
            payload={"evidence_ids": [o["id"] for o in evidence],
                     "scope": {"kind": scope_kind, "value": scope_value},
                     "success": success})
        return self.get(user_id, eid)  # type: ignore[return-value]

    def enrich(self, user_id: str, experience_id: str, *,
               action: str | None = None, outcome: str | None = None,
               success: bool | None = None, observation: str | None = None,
               context: dict[str, Any] | None = None,
               consequences: list[str] | None = None,
               evidence_ids: list[str] | None = None,
               reason: str = "Episode enriched from new evidence.",
               correlation_id: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, experience_id)
        if item is None:
            raise KeyError(experience_id)
        if item["lifecycle"] == "archived":
            raise ValueError("An archived experience cannot be enriched.")
        if success is not None and not ((outcome or item.get("outcome") or "").strip()):
            raise ValueError("A success/failure judgement requires an observed outcome.")

        evidence = self._owned_observations(user_id, evidence_ids or [])
        for obs in evidence:
            self._link_observation(
                user_id, experience_id, obs["id"], "supporting",
                correlation_id=correlation_id)
        updates = {
            "action": action if action is not None else item.get("action"),
            "outcome": outcome if outcome is not None else item.get("outcome"),
            "success": (None if success is None and item.get("success") is None
                        else int(success) if success is not None
                        else int(bool(item.get("success")))),
            "observation": (observation if observation is not None
                            else item.get("observation")),
            "context": json.dumps(context if context is not None else item["context"]),
            "consequences": json.dumps(consequences if consequences is not None
                                       else item["consequences"]),
        }
        self.db.execute(
            "UPDATE experiences SET action=?,outcome=?,success=?,observation=?,"
            "context=?,consequences=?,updated_at=? WHERE id=? AND user_id=?",
            (updates["action"], updates["outcome"], updates["success"],
             updates["observation"], updates["context"], updates["consequences"],
             _now(), experience_id, user_id))
        current = item["lifecycle"]
        if current == "observed":
            self._transition(user_id, experience_id, "enriched", reason,
                             [o["id"] for o in evidence], correlation_id)
        self.bus.emit(
            user_id, "experience.enriched", reason,
            subject_kind="experience", subject_id=experience_id,
            correlation_id=correlation_id,
            payload={"evidence_ids": [o["id"] for o in evidence]})
        return self.get(user_id, experience_id)  # type: ignore[return-value]

    def validate(self, user_id: str, experience_id: str, *,
                 reason: str = "Episode checked against linked evidence.",
                 correlation_id: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, experience_id)
        if item is None:
            raise KeyError(experience_id)
        if item["lifecycle"] == "observed":
            self._transition(user_id, experience_id, "enriched",
                             "Validation began after structural enrichment.", [],
                             correlation_id)
            item = self.get(user_id, experience_id) or item
        if item["lifecycle"] not in ("enriched", "validated", "active"):
            raise ValueError(f"Cannot validate experience in {item['lifecycle']} lifecycle.")

        direct = [e for e in item["evidence"]
                  if e.get("epistemic_status") == "OBSERVED"
                  and e.get("role") == "supporting"]
        complete = bool(item.get("action") and item.get("outcome"))
        if not direct:
            raise ValueError("Experience validation requires direct OBSERVED evidence.")
        if not complete:
            raise ValueError("Experience validation requires both action and outcome.")
        quality = sum(float(e.get("confidence") or 0.0) for e in direct) / len(direct)
        confidence = min(1.0, 0.25 + 0.55 * quality + 0.1 * min(2, len(direct)))
        self.db.execute(
            "UPDATE experiences SET confidence=?,updated_at=? WHERE id=? AND user_id=?",
            (round(confidence, 4), _now(), experience_id, user_id))
        if item["lifecycle"] == "enriched":
            self._transition(user_id, experience_id, "validated", reason,
                             [e["observation_id"] for e in direct], correlation_id)
        self.bus.emit(
            user_id, "experience.validated", reason,
            subject_kind="experience", subject_id=experience_id,
            correlation_id=correlation_id,
            payload={"direct_evidence": len(direct),
                     "confidence": round(confidence, 4)})
        return self.get(user_id, experience_id)  # type: ignore[return-value]

    def activate(self, user_id: str, experience_id: str, *,
                 reason: str = "Validated episode made available for learning.",
                 correlation_id: str | None = None) -> dict[str, Any]:
        return self.transition(user_id, experience_id, "active", reason=reason,
                               correlation_id=correlation_id)

    def archive(self, user_id: str, experience_id: str, *, reason: str,
                correlation_id: str | None = None) -> dict[str, Any]:
        return self.transition(user_id, experience_id, "archived", reason=reason,
                               correlation_id=correlation_id)

    def transition(self, user_id: str, experience_id: str, lifecycle: str, *,
                   reason: str, evidence_ids: list[str] | None = None,
                   correlation_id: str | None = None) -> dict[str, Any]:
        item = self.get(user_id, experience_id)
        if item is None:
            raise KeyError(experience_id)
        if item["lifecycle"] == lifecycle:
            return item
        self._transition(user_id, experience_id, lifecycle, reason,
                         evidence_ids or [], correlation_id)
        event = {"active": "experience.activated",
                 "archived": "experience.archived"}.get(
                     lifecycle, "experience.enriched")
        self.bus.emit(user_id, event, reason, subject_kind="experience",
                      subject_id=experience_id, correlation_id=correlation_id,
                      payload={"from": item["lifecycle"], "to": lifecycle,
                               "evidence_ids": evidence_ids or []})
        return self.get(user_id, experience_id)  # type: ignore[return-value]

    def _transition(self, user_id: str, experience_id: str, lifecycle: str,
                    reason: str, evidence_ids: list[str],
                    correlation_id: str | None) -> None:
        if lifecycle not in LIFECYCLES:
            raise ValueError(f"Unknown experience lifecycle: {lifecycle!r}")
        row = self.db.query_one(
            "SELECT lifecycle FROM experiences WHERE id=? AND user_id=?",
            (experience_id, user_id))
        if row is None:
            raise KeyError(experience_id)
        previous = str(row["lifecycle"])
        if lifecycle == previous:
            return
        if lifecycle not in _ALLOWED_TRANSITIONS.get(previous, ()):
            raise ValueError(f"Invalid experience transition: {previous} -> {lifecycle}.")
        self.db.execute(
            "UPDATE experiences SET lifecycle=?,updated_at=? WHERE id=? AND user_id=?",
            (lifecycle, _now(), experience_id, user_id))
        self._record_transition(user_id, experience_id, previous, lifecycle,
                                reason, evidence_ids, correlation_id)

    def _record_transition(self, user_id: str, experience_id: str,
                           previous: str | None, lifecycle: str, reason: str,
                           evidence_ids: list[str], correlation_id: str | None) -> None:
        self.db.execute(
            "INSERT INTO experience_transitions (id,experience_id,user_id,"
            "previous_lifecycle,lifecycle,reason,evidence,correlation_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (f"ext_{uuid.uuid4().hex[:12]}", experience_id, user_id, previous,
             lifecycle, reason[:500], json.dumps(evidence_ids), correlation_id,
             _now()))

    def link_evidence(self, user_id: str, experience_id: str,
                      observation_id: str, *, role: str = "supporting") -> dict[str, Any]:
        if self.get(user_id, experience_id) is None:
            raise KeyError(experience_id)
        self._owned_observations(user_id, [observation_id])
        self._link_observation(user_id, experience_id, observation_id, role)
        return self.get(user_id, experience_id)  # type: ignore[return-value]

    def _link_observation(self, user_id: str, experience_id: str,
                          observation_id: str, role: str, *,
                          correlation_id: str | None = None) -> None:
        if role not in EVIDENCE_ROLES:
            raise ValueError(f"Unknown experience evidence role: {role!r}")
        existing = self.db.query_one(
            "SELECT 1 FROM experience_evidence WHERE experience_id=? AND user_id=?"
            " AND observation_id=? AND role=?",
            (experience_id, user_id, observation_id, role))
        self.db.execute(
            "INSERT OR IGNORE INTO experience_evidence (experience_id,user_id,"
            "observation_id,role,created_at) VALUES (?,?,?,?,?)",
            (experience_id, user_id, observation_id, role, _now()))
        if existing is None:
            self.causal.link(
                user_id, "observation", observation_id, "experience", experience_id,
                relation=("informed" if role == "supporting" else "contradicted"),
                weight=1.0, correlation_id=correlation_id)

    def _owned_observations(self, user_id: str,
                            evidence_ids: list[str]) -> list[dict[str, Any]]:
        ids = list(dict.fromkeys(str(i) for i in evidence_ids if str(i).strip()))
        evidence: list[dict[str, Any]] = []
        for oid in ids:
            row = self.db.query_one(
                "SELECT * FROM observations WHERE id=? AND user_id=?", (oid, user_id))
            if row is None:
                raise ValueError(
                    f"Observation {oid!r} does not exist in this user namespace.")
            evidence.append(dict(row))
        return evidence

    def get(self, user_id: str, experience_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM experiences WHERE id=? AND user_id=?",
            (experience_id, user_id))
        return self._row(row) if row else None

    def list(self, user_id: str, *, lifecycle: str | None = None,
             pattern_key: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM experiences WHERE user_id=?"
        params: list[Any] = [user_id]
        if lifecycle:
            sql += " AND lifecycle=?"
            params.append(lifecycle)
        if pattern_key:
            sql += " AND pattern_key=?"
            params.append(pattern_key)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        params.append(max(1, min(500, int(limit))))
        return [self._row(r) for r in self.db.query(sql, params)]

    def provenance(self, user_id: str, experience_id: str) -> dict[str, Any]:
        item = self.get(user_id, experience_id)
        if item is None:
            raise KeyError(experience_id)
        transitions = [dict(r) for r in self.db.query(
            "SELECT * FROM experience_transitions WHERE experience_id=? AND user_id=?"
            " ORDER BY created_at, rowid", (experience_id, user_id))]
        for transition in transitions:
            transition["evidence"] = _loads(transition.get("evidence"), [])
        return {"experience": item, "evidence": item["evidence"],
                "transitions": transitions, "provenance": item["provenance"]}

    def _row(self, row) -> dict[str, Any]:
        item = dict(row)
        item["context"] = _loads(item.get("context"), {})
        item["consequences"] = _loads(item.get("consequences"), [])
        item["provenance"] = _loads(item.get("provenance"), {})
        item["success"] = (None if item.get("success") is None
                           else bool(item["success"]))
        evidence_rows = self.db.query(
            "SELECT ee.observation_id,ee.role,o.source,o.origin,o.content,"
            "o.epistemic_status,o.confidence,o.observed_at,o.provenance "
            "FROM experience_evidence ee JOIN observations o "
            "ON o.id=ee.observation_id AND o.user_id=ee.user_id "
            "WHERE ee.experience_id=? AND ee.user_id=? ORDER BY ee.created_at",
            (item["id"], item["user_id"]))
        evidence = []
        for erow in evidence_rows:
            entry = dict(erow)
            entry["provenance"] = _loads(entry.get("provenance"), {})
            evidence.append(entry)
        item["evidence"] = evidence
        item["evidence_count"] = len(evidence)
        return item
