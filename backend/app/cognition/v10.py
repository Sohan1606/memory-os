"""V10 cognitive self-maintenance over the canonical V9 state.

The services in this module are deliberately boring at authority boundaries:
SQLite is the source of truth, the V9 PersonalStateService owns semantic
objects and versions, EventBus is the only event stream, and the Autonomy
Governor is consulted before a proposal is applied.  Heuristics create
candidates and explicit UNKNOWN results; they never rewrite history.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from ..schemas.semantic import CognitiveObjectCreate, Modality, Provenance
from ..schemas.v10 import (
    ContradictionClass, ContradictionStatus, DebtSeverity, DebtStatus, DebtType,
    ModelErrorClass, ProposalStatus, ProposalType, UnknownStatus,
)


_ACTIVE = {"ACTIVE", "PROPOSED"}
_TERMINAL = {"RETIRED", "SUPERSEDED", "DELETED"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _fingerprint(*parts: Any) -> str:
    raw = "|".join(str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


_PREDICATE_ALIASES = {
    "like": "prefer", "love": "prefer", "enjoy": "prefer", "dislike": "prefer",
    "hate": "prefer", "avoid": "prefer", "prefer": "prefer",
    "want": "desire", "desire": "desire", "seek": "desire", "choose": "desire",
    "value": "value", "care about": "value",
    "depend": "depend", "depends": "depend",
    "support": "support", "supports": "support",
    "require": "require", "requires": "require",
    "relate": "related", "related": "related",
    "save": "spend_or_save", "saved": "spend_or_save", "spend": "spend_or_save",
    "spent": "spend_or_save", "spending": "spend_or_save",
}


def _canonical_predicate(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(value).strip().lower())
    return _PREDICATE_ALIASES.get(normalized, normalized)


def _normalise_atom(value: Any) -> str:
    normalized = re.sub(r"\s+", " ", str(value).strip(" .?!").lower())
    normalized = re.sub(r"^(?:the|this|that|a|an)\s+", "", normalized)
    return normalized


def _negative(text: str) -> bool:
    return bool(re.search(r"\b(?:never|not|no|without|don't|doesn't|dont|cannot|can't|cant|no longer|anymore|avoid)\b", (text or "").lower()))


def _explicit_evolution(value: Any) -> bool:
    """Recognize an explicit change marker, not an object-specific phrase.

    Canonical metadata can state ``evolution=True``. The text fallback only
    recognizes a grammatical change verb and is gated by semantic object type
    at the contradiction boundary; it does not decide which values conflict.
    """
    if isinstance(value, dict):
        if (value.get("metadata") or {}).get("evolution") is True:
            return True
        text = str(value.get("content", ""))
    else:
        text = str(value or "")
    return bool(re.search(r"\b(?:change|changed|changing|shift|shifted|evolve|evolved|evolving|revised|reconsidered)\b",
                         text.lower()))


def _scope(obj: dict[str, Any]) -> str | None:
    metadata = obj.get("metadata") or {}
    return metadata.get("scope") or metadata.get("context") or metadata.get("context_scope")


def _temporal_signature(obj: dict[str, Any]) -> dict[str, Any] | None:
    temporal = obj.get("temporal_scope") or {}
    metadata_temporal = (obj.get("metadata") or {}).get("temporal_scope") or {}
    if temporal.get("kind") in {None, "UNSPECIFIED"}:
        temporal = metadata_temporal
    if not temporal or temporal.get("kind") in {None, "UNSPECIFIED"}:
        return None
    return temporal


def _temporal_relation(left: dict[str, Any], right: dict[str, Any]) -> str | None:
    a = _temporal_signature(left) or {}
    b = _temporal_signature(right) or {}
    if a.get("kind") == "PAST" and b.get("kind") == "FUTURE":
        return "DIFFERENT_TIME"
    if a.get("kind") == "FUTURE" and b.get("kind") == "PAST":
        return "DIFFERENT_TIME"
    if a.get("end") and b.get("start") and str(a["end"]) < str(b["start"]):
        return "DIFFERENT_TIME"
    if b.get("end") and a.get("start") and str(b["end"]) < str(a["start"]):
        return "DIFFERENT_TIME"
    return None


def _proposition(obj: dict[str, Any]) -> dict[str, Any] | None:
    """Build a comparison view from a canonical V9 object.

    This is a comparison projection, not a second semantic store. Explicit
    V9 metadata wins. The conservative fallback only recognizes a small set of
    proposition-shaped utterances; otherwise comparison returns UNKNOWN rather
    than treating token overlap as proposition identity.
    """
    metadata = obj.get("metadata") or {}
    explicit = {
        "subject": metadata.get("subject") or metadata.get("subject_id"),
        "predicate": metadata.get("predicate") or metadata.get("relation"),
        "object": metadata.get("object") or metadata.get("object_value") or metadata.get("target"),
        "polarity": metadata.get("polarity"),
    }
    # Evidence often carries a non-semantic label before the proposition.
    # Remove only a generic prefix, not domain vocabulary.
    content = re.sub(r"\s+", " ", str(obj.get("content", "")).strip()).strip(" .?!")
    content = re.sub(r"^[^:]{1,40}:\s*", "", content)
    lower = content.lower()
    subject = explicit["subject"]
    predicate = explicit["predicate"]
    value = explicit["object"]
    polarity = explicit["polarity"]

    # User-directed propositions. Predicate aliases are a general canonical
    # relation layer; no particular object vocabulary is privileged.
    match = re.match(
        r"^(?:i|user)\s+(?:(don't|do not|never|cannot|can't|avoid)\s+)?"
        r"(care about|like|love|enjoy|dislike|prefer|value|want|desire|seek|need|"
        r"hate|avoid|save|saved|spend|spent|spending|work|choose)\s+(.+)$",
        lower)
    if match:
        neg, pred, value_text = match.groups()
        subject = subject or "USER"
        predicate = predicate or pred
        value = value or value_text
        polarity = polarity or ("NEGATED" if neg or pred in {"dislike", "hate", "avoid"} else "POSITIVE")

    # Subject-first propositions such as "the project depends on X" and
    # "the meeting is not related to X".
    if not predicate:
        match = re.match(
            r"^(?P<subj>.+?)\s+(?P<neg>does not|doesn't|no longer|never|is not|not)\s+"
            r"(?P<pred>depend|depends|support|supports|allow|allows|require|requires|include|includes|relate|relates|related|work)\s+(?P<obj>.+)$", lower)
        if match:
            subject = subject or match.group("subj")
            predicate = match.group("pred")
            value = value or match.group("obj")
            polarity = polarity or "NEGATED"
    if not predicate:
        match = re.match(
            r"^(?P<subj>.+?)\s+(?P<pred>depends|supports|allows|requires|includes|"
            r"relates|related|happened|occurred|is|are|was|were)\s+(?P<obj>.+)$", lower)
        if match:
            subject = subject or match.group("subj")
            predicate = match.group("pred")
            value = value or match.group("obj")
            polarity = polarity or "POSITIVE"

    if not subject or not predicate or not value:
        return None
    modality = str(obj.get("modality") or "ASSERTED").upper()
    if polarity is None:
        polarity = "NEGATED" if modality == "NEGATED" or _negative(content) else "POSITIVE"
    normalized_object = _normalise_atom(value)
    relation = _canonical_predicate(predicate)
    if relation == "depend":
        normalized_object = re.sub(r"^on\s+", "", normalized_object)
    elif relation == "related":
        normalized_object = re.sub(r"^(?:to|with)\s+", "", normalized_object)
    uncertain = modality in {"TENTATIVE", "POSSIBLE", "HYPOTHETICAL", "QUESTIONED"} or bool(
        re.match(r"^(?:maybe|perhaps|i think|i might|it could be)\b", lower))
    return {
        "subject": _normalise_atom(subject),
        "predicate": relation,
        "object": normalized_object,
        "polarity": str(polarity).upper(),
        "modality": modality,
        "uncertain": uncertain,
        "scope": _scope(obj),
    }


def _row_dict(row: Any) -> dict[str, Any]:

    item = dict(row)
    for key in list(item):
        if key.endswith("_json"):
            item[key[:-5]] = _loads(item.pop(key), {})
    for key in ("object_ids", "evidence_refs", "provenance", "missing_evidence",
                "relevant_object_ids", "target_object_ids", "current_state",
                "proposed_state", "dimensions", "finding_refs", "scope_analysis",
                "temporal_analysis", "proposition_analysis"):
        if key in item and isinstance(item[key], str):
            item[key] = _loads(item[key], [] if key.endswith("ids") or key.endswith("refs") else {})
    if "reversible" in item:
        item["reversible"] = bool(item["reversible"])
    return item


class V10ScopedService:
    def __init__(self, db, bus, *, tenant_id: str = "local") -> None:
        self.db = db
        self.bus = bus
        self.tenant_id = tenant_id

    def _tenant(self, user_id: str) -> str:
        """Resolve the tenant from the canonical V8.5 identity namespace."""
        row = self.db.query_one("SELECT tenant_id FROM auth_users WHERE namespace=?", (user_id,))
        return str(row["tenant_id"]) if row else self.tenant_id

    def _payload(self, payload: dict[str, Any] | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        return {**(payload or {}), "tenant_id": self._tenant(user_id) if user_id else self.tenant_id}

    def _emit(self, user_id: str, event_type: str, summary: str, *,
              subject_kind: str, subject_id: str, correlation_id: str | None = None,
              payload: dict[str, Any] | None = None):
        return self.bus.emit(user_id, event_type, summary,
                             subject_kind=subject_kind, subject_id=subject_id,
                             correlation_id=correlation_id,
                             payload=self._payload(payload, user_id=user_id))

    def _canonical_scope(self, alias: str = "") -> str:
        """Scope a legacy V9 row through its canonical identity mapping.

        V9 tables intentionally predate the tenant column. The identity table
        is therefore the authoritative tenant boundary; the legacy fallback
        is only for pre-identity databases being reopened for migration.
        """
        prefix = f"{alias}." if alias else ""
        return (
            f"{prefix}user_id=? AND ("
            "NOT EXISTS (SELECT 1 FROM auth_users WHERE namespace=?) OR "
            "EXISTS (SELECT 1 FROM auth_users WHERE namespace=? AND tenant_id=?))"
        )

    def _canonical_scope_params(self, user_id: str) -> tuple[str, str, str, str]:
        return (user_id, user_id, user_id, self._tenant(user_id))


class CognitiveDebtService(V10ScopedService):
    """Detects unresolved/stale structure and retains its lifecycle history."""

    def __init__(self, db, bus, personal_state, predictions, *, tenant_id: str = "local"):
        super().__init__(db, bus, tenant_id=tenant_id)
        self.personal_state = personal_state
        self.predictions = predictions

    def _object_owned(self, user_id: str, object_id: str) -> bool:
        if self.personal_state.get(user_id, object_id):
            return True
        # Prediction, decision and world ids are canonical V8 objects too, but
        # are not V9 cognitive_objects. Validate them through their owner.
        for table in ("predictions", "decisions", "world_entities"):
            clause = self._canonical_scope("v")
            params = (object_id,) + self._canonical_scope_params(user_id)
            if self.db.query_one(f"SELECT 1 FROM {table} v WHERE v.id=? AND {clause}", params):
                return True
        return False

    def create(self, user_id: str, *, debt_type: str, severity: str,
               object_ids: list[str], evidence_refs: list[dict[str, Any]], reason: str,
               confidence: float, suggested_action: str | None = None,
               provenance: dict[str, Any] | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        if debt_type not in {item.value for item in DebtType}:
            raise ValueError(f"Unsupported cognitive debt type: {debt_type}")
        if severity not in {item.value for item in DebtSeverity}:
            raise ValueError(f"Unsupported cognitive debt severity: {severity}")
        for oid in object_ids:
            if not self._object_owned(user_id, oid):
                raise KeyError("A linked object is not in the verified user scope.")
        fp = _fingerprint(debt_type, *sorted(object_ids), reason)
        existing = self.db.query_one(
            "SELECT * FROM cognitive_debt WHERE user_id=? AND tenant_id=? AND fingerprint=?",
            (user_id, self._tenant(user_id), fp))
        timestamp = now()
        if existing:
            self.db.execute(
                "UPDATE cognitive_debt SET last_checked_at=?, updated_at=?, "
                "evidence_refs_json=?, provenance_json=?, confidence=? "
                "WHERE id=? AND user_id=? AND tenant_id=?",
                (timestamp, timestamp, _json(evidence_refs), _json(provenance or {"source": "SYSTEM_DERIVED"}),
                 max(0.0, min(1.0, confidence)), existing["id"], user_id, self._tenant(user_id)))
            item = self.get(user_id, existing["id"])
            self._emit(user_id, "cognitive_debt.updated", "Rechecked a cognitive debt item.",
                       subject_kind="cognitive_debt", subject_id=existing["id"],
                       correlation_id=correlation_id,
                       payload={"status": item["status"], "debt_type": item["debt_type"]})
            return item  # type: ignore[return-value]
        did = _id("debt")
        self.db.execute(
            "INSERT INTO cognitive_debt (id,user_id,tenant_id,debt_type,severity,status,reason,"
            "suggested_action,object_ids_json,evidence_refs_json,provenance_json,confidence,"
            "fingerprint,correlation_id,detected_at,last_checked_at,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (did, user_id, self._tenant(user_id), debt_type, severity, DebtStatus.OPEN.value, reason,
             suggested_action, _json(object_ids), _json(evidence_refs),
             _json(provenance or {"source": "SYSTEM_DERIVED"}),
             max(0.0, min(1.0, confidence)), fp, correlation_id, timestamp, timestamp,
             timestamp, timestamp))
        item = self.get(user_id, did)
        self._emit(user_id, "cognitive_debt.detected", reason[:180],
                   subject_kind="cognitive_debt", subject_id=did,
                   correlation_id=correlation_id,
                   payload={"debt_type": debt_type, "severity": severity,
                            "object_ids": object_ids, "confidence": confidence})
        return item  # type: ignore[return-value]

    def get(self, user_id: str, debt_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM cognitive_debt WHERE id=? AND user_id=? AND tenant_id=?",
            (debt_id, user_id, self._tenant(user_id)))
        return _row_dict(row) if row else None

    def list(self, user_id: str, *, status: str | None = None,
             limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cognitive_debt WHERE user_id=? AND tenant_id=?"
        params: list[Any] = [user_id, self._tenant(user_id)]
        if status:
            sql += " AND status=?"
            params.append(status.upper())
        sql += " ORDER BY datetime(updated_at) DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [_row_dict(r) for r in self.db.query(sql, params)]

    def detect(self, user_id: str, *, correlation_id: str | None = None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        objects = [o for o in self.personal_state.list(user_id, limit=500)
                   if o.get("status") not in _TERMINAL]
        assumptions = [o for o in objects if o.get("type") == "ASSUMPTION"]
        for assumption in assumptions:
            for evidence in objects:
                # Timestamps are second-granularity in the canonical V9
                # records. Equal timestamps are still ordered by the immutable
                # state-version/event history, so do not discard them here.
                if evidence["id"] == assumption["id"] or evidence.get("created_at", "") < assumption.get("created_at", ""):
                    continue
                if evidence.get("type") not in {"FACT", "OBSERVATION", "CORRECTION", "CLAIM"}:
                    continue
                # Token overlap is not evidence of a changed proposition.
                # Compare the canonical relation projection instead. If either
                # proposition is unavailable or uncertain, leave the finding
                # absent rather than manufacturing stale debt.
                assumption_prop = _proposition(assumption)
                evidence_prop = _proposition(evidence)
                explicit_link = ((evidence.get("metadata") or {}).get("corrects_object_id") == assumption["id"] or
                                 (evidence.get("metadata") or {}).get("supersedes_object_id") == assumption["id"] or
                                 evidence.get("superseded_by") == assumption["id"])
                same_recorded_proposition = bool(assumption_prop and evidence_prop)
                if same_recorded_proposition:
                    same_recorded_proposition = (
                        assumption_prop["subject"] == evidence_prop["subject"] and
                        assumption_prop["predicate"] == evidence_prop["predicate"] and
                        assumption_prop["object"] == evidence_prop["object"] and
                        assumption_prop["polarity"] != evidence_prop["polarity"] and
                        not assumption_prop["uncertain"] and not evidence_prop["uncertain"] and
                        not _temporal_relation(assumption, evidence) and
                        _temporal_signature(assumption) == _temporal_signature(evidence) and
                        _scope(assumption) == _scope(evidence))
                if explicit_link or same_recorded_proposition:
                    found.append(self.create(
                        user_id, debt_type=DebtType.STALE_ASSUMPTION.value,
                        severity=DebtSeverity.MATERIAL.value,
                        object_ids=[assumption["id"], evidence["id"]],
                        evidence_refs=[{"kind": "cognitive_object", "id": evidence["id"],
                                        "source": evidence.get("provenance")}],
                        reason="Later canonical evidence weakens or reverses this assumption; the historical assumption remains unchanged.",
                        confidence=0.96 if explicit_link else 0.9,
                        suggested_action=ProposalType.RETIRE_ASSUMPTION.value,
                        provenance={"source": "SYSTEM_DERIVED", "basis": "canonical_proposition_comparison"},
                        correlation_id=correlation_id))
        # Existing prediction engine remains authoritative. V10 only records
        # missing evaluation as debt; it never marks a prediction wrong.
        prediction_clause = self._canonical_scope("p")
        for row in self.db.query(
                f"SELECT p.* FROM predictions p WHERE {prediction_clause} AND p.status='open' "
                "AND p.expected_evaluation_at IS NOT NULL AND p.expected_evaluation_at<=?",
                (*self._canonical_scope_params(user_id), now())):
            found.append(self.create(
                user_id, debt_type=DebtType.UNVALIDATED_PREDICTION.value,
                severity=DebtSeverity.ROUTINE.value, object_ids=[row["id"]],
                evidence_refs=[{"kind": "prediction", "id": row["id"]}],
                reason="The prediction window has passed without a resolving observation; outcome remains unknown.",
                confidence=0.95, suggested_action=ProposalType.RECORD_OUTCOME.value,
                provenance={"source": "SYSTEM_DERIVED", "basis": "prediction_window"},
                correlation_id=correlation_id))
        world_clause = self._canonical_scope("w")
        for row in self.db.query(
                f"SELECT w.* FROM world_entities w WHERE {world_clause} AND w.state='active' AND w.stale=1",
                self._canonical_scope_params(user_id)):
            found.append(self.create(
                user_id, debt_type=DebtType.STALE_WORLD_DEPENDENCY.value,
                severity=DebtSeverity.MATERIAL.value, object_ids=[row["id"]],
                evidence_refs=[{"kind": "world_entity", "id": row["id"]}],
                reason="An active world dependency is explicitly marked stale by the canonical World Model.",
                confidence=0.9, suggested_action=ProposalType.RECHECK_WORLD_DEPENDENCY.value,
                provenance={"source": "SYSTEM_DERIVED", "basis": "world.stale"},
                correlation_id=correlation_id))

        # Decisions are a canonical V9 table, so these two debt types are
        # evidence-backed. Do not infer commitments, principles, goals, or
        # claims from free text: those categories remain NOT IMPLEMENTED
        # unless a canonical source exposes the required lifecycle evidence.
        decision_clause = self._canonical_scope("d")
        for row in self.db.query(
                f"SELECT d.* FROM decisions d WHERE {decision_clause} AND d.status='open'",
                self._canonical_scope_params(user_id)):
            found.append(self.create(
                user_id, debt_type=DebtType.UNRESOLVED_DECISION.value,
                severity=DebtSeverity.ROUTINE.value, object_ids=[row["id"]],
                evidence_refs=[{"kind": "decision", "id": row["id"], "status": row["status"]}],
                reason="A canonical V9 decision remains open and has not recorded an outcome.",
                confidence=0.98, suggested_action=ProposalType.RECONSTRUCT_DECISION.value,
                provenance={"source": "SYSTEM_DERIVED", "basis": "v9.decision.status=open"},
                correlation_id=correlation_id))
        for row in self.db.query(
                f"SELECT d.* FROM decisions d WHERE {decision_clause} AND d.status='resolved' "
                "AND (d.actual_outcome IS NULL OR trim(d.actual_outcome)='')",
                self._canonical_scope_params(user_id)):
            found.append(self.create(
                user_id, debt_type=DebtType.MISSING_OUTCOME.value,
                severity=DebtSeverity.ROUTINE.value, object_ids=[row["id"]],
                evidence_refs=[{"kind": "decision", "id": row["id"], "status": row["status"]}],
                reason="A canonical V9 decision is marked resolved but has no recorded actual outcome.",
                confidence=0.98, suggested_action=ProposalType.RECORD_OUTCOME.value,
                provenance={"source": "SYSTEM_DERIVED", "basis": "v9.decision.missing_actual_outcome"},
                correlation_id=correlation_id))
        return found

    def transition(self, user_id: str, debt_id: str, status: str, *,
                   reason: str | None = None, until: str | None = None,
                   correlation_id: str | None = None) -> dict[str, Any] | None:
        item = self.get(user_id, debt_id)
        if item is None:
            return None
        status = status.upper()
        valid = {
            DebtStatus.OPEN.value: {DebtStatus.ACKNOWLEDGED.value, DebtStatus.DEFERRED.value},
            DebtStatus.ACKNOWLEDGED.value: {DebtStatus.RESOLVED.value, DebtStatus.DEFERRED.value},
            DebtStatus.DEFERRED.value: {DebtStatus.OPEN.value, DebtStatus.ACKNOWLEDGED.value, DebtStatus.RESOLVED.value},
            DebtStatus.RESOLVED.value: set(),
        }
        if status != item["status"] and status not in valid.get(item["status"], set()):
            raise ValueError(f"Invalid cognitive debt transition {item['status']} -> {status}.")
        ts = now()
        self.db.execute(
            "UPDATE cognitive_debt SET status=?, reason=COALESCE(?,reason), "
            "deferred_until=?, resolved_at=?, updated_at=? WHERE id=? AND user_id=? AND tenant_id=?",
            (status, reason, until if status == DebtStatus.DEFERRED.value else None,
             ts if status == DebtStatus.RESOLVED.value else item.get("resolved_at"), ts,
             debt_id, user_id, self._tenant(user_id)))
        event = "cognitive_debt.resolved" if status == DebtStatus.RESOLVED.value else "cognitive_debt.updated"
        self._emit(user_id, event, f"Cognitive debt is now {status}.",
                   subject_kind="cognitive_debt", subject_id=debt_id,
                   correlation_id=correlation_id, payload={"status": status, "reason": reason})
        return self.get(user_id, debt_id)


class ContradictionEngine(V10ScopedService):
    """Scope-aware comparison; similarity alone never becomes a verdict."""

    COMPARABLE = {"PREFERENCE", "VALUE", "GOAL", "BOUNDARY", "PRINCIPLE",
                  "ASSUMPTION", "BELIEF", "CLAIM", "FACT", "OBSERVATION", "INTENT",
                  "CORRECTION"}

    def __init__(self, db, bus, personal_state, *, tenant_id: str = "local"):
        super().__init__(db, bus, tenant_id=tenant_id)
        self.personal_state = personal_state

    def classify(self, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
        """Compare canonical propositions conservatively.

        A shared token, related topic, or opposite-looking phrase is never
        enough. TRUE_CONTRADICTION requires the same subject, predicate and
        object proposition in overlapping scope with incompatible polarity and
        sufficiently assertive modality.
        """
        if left["id"] == right["id"]:
            return {"classification": ContradictionClass.NOT_A_CONTRADICTION.value,
                    "reason": "The two references identify the same object.", "confidence": 1.0}
        if left.get("superseded_by") == right["id"] or right.get("superseded_by") == left["id"]:
            return {"classification": ContradictionClass.SUPERSESSION.value,
                    "reason": "One canonical object explicitly supersedes the other.", "confidence": 1.0}
        left_meta, right_meta = left.get("metadata") or {}, right.get("metadata") or {}
        correction_targets_pair = (
            (left.get("type") == "CORRECTION" and
             left_meta.get("corrects_object_id") == right.get("id")) or
            (right.get("type") == "CORRECTION" and
             right_meta.get("corrects_object_id") == left.get("id")) or
            left_meta.get("supersedes_object_id") == right.get("id") or
            right_meta.get("supersedes_object_id") == left.get("id"))
        if correction_targets_pair:
            return {"classification": ContradictionClass.SUPERSESSION.value,
                    "reason": "An explicit canonical correction or supersession targets the compared object.",
                    "confidence": 0.99}
        temporal = _temporal_relation(left, right)
        if temporal:
            return {"classification": temporal,
                    "reason": "The recorded temporal scopes do not overlap.", "confidence": 0.94}
        ls, rs = _scope(left), _scope(right)
        if ls and rs and ls != rs:
            return {"classification": ContradictionClass.DIFFERENT_SCOPE.value,
                    "reason": "The objects apply to different recorded contexts.", "confidence": 0.9,
                    "scope_analysis": {"left": ls, "right": rs}}

        evolution_types = {"PREFERENCE", "VALUE", "GOAL", "PRINCIPLE", "BOUNDARY"}
        if (left.get("type") == right.get("type") and
                left.get("type") in evolution_types and
                (_explicit_evolution(left) or _explicit_evolution(right))):
            return {"classification": ContradictionClass.VALUE_EVOLUTION.value,
                    "reason": "A canonical statement explicitly records a change in priorities or preference.",
                    "confidence": 0.96}
        temporary_exception = (
            left_meta.get("temporary_exception") is True or
            right_meta.get("temporary_exception") is True)
        lp, rp = _proposition(left), _proposition(right)
        if lp is None or rp is None:
            return {"classification": ContradictionClass.INSUFFICIENT_CONTEXT.value,
                    "reason": "A subject, predicate, object, or scope could not be established deterministically.",
                    "confidence": 0.2,
                    "proposition_analysis": {"left": lp, "right": rp}}
        if (temporary_exception and
                lp["subject"] == rp["subject"] and
                lp["predicate"] == rp["predicate"] and
                lp["object"] == rp["object"] and
                lp["polarity"] != rp["polarity"]):
            return {"classification": ContradictionClass.TEMPORARY_EXCEPTION.value,
                    "reason": "Canonical metadata marks an otherwise matching polarity change as a bounded exception.",
                    "confidence": 0.9, "proposition_analysis": {"left": lp, "right": rp}}
        if lp["uncertain"] or rp["uncertain"]:
            return {"classification": ContradictionClass.INSUFFICIENT_CONTEXT.value,
                    "reason": "At least one proposition is tentative, hypothetical, or otherwise uncertain.",
                    "confidence": 0.3,
                    "proposition_analysis": {"left": lp, "right": rp}}
        if lp["subject"] != rp["subject"]:
            return {"classification": ContradictionClass.NOT_A_CONTRADICTION.value,
                    "reason": "The propositions concern different subjects.", "confidence": 0.98,
                    "proposition_analysis": {"left": lp, "right": rp}}
        if lp["predicate"] != rp["predicate"]:
            return {"classification": ContradictionClass.NOT_A_CONTRADICTION.value,
                    "reason": "The propositions use different relations; related words do not establish conflict.",
                    "confidence": 0.92, "proposition_analysis": {"left": lp, "right": rp}}
        if lp["object"] != rp["object"]:
            # Different desired/value objects can be competing commitments,
            # but no object vocabulary is treated as inherently opposite.
            if left.get("type") == right.get("type") in {"GOAL", "VALUE"} and lp["predicate"] == rp["predicate"]:
                return {"classification": ContradictionClass.CONTEXTUAL_TRADEOFF.value,
                        "reason": "The same subject has competing canonical goals or values whose context is not recorded.",
                        "confidence": 0.72, "proposition_analysis": {"left": lp, "right": rp}}
            if left.get("type") == right.get("type") == "PREFERENCE":
                return {"classification": ContradictionClass.INSUFFICIENT_CONTEXT.value,
                        "reason": "The same preference relation names different objects; the relevant situation is not recorded.",
                        "confidence": 0.32, "proposition_analysis": {"left": lp, "right": rp}}
            return {"classification": ContradictionClass.NOT_A_CONTRADICTION.value,
                    "reason": "The propositions concern different objects.", "confidence": 0.96,
                    "proposition_analysis": {"left": lp, "right": rp}}
        if lp["polarity"] == rp["polarity"]:
            return {"classification": ContradictionClass.NOT_A_CONTRADICTION.value,
                    "reason": "The canonical propositions have compatible polarity.", "confidence": 0.96,
                    "proposition_analysis": {"left": lp, "right": rp}}
        return {"classification": ContradictionClass.TRUE_CONTRADICTION.value,
                "reason": "The same subject, predicate, object, scope, and time are asserted with incompatible polarity.",
                "confidence": 0.97, "proposition_analysis": {"left": lp, "right": rp}}

    def get(self, user_id: str, record_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM contradiction_records WHERE id=? AND user_id=? AND tenant_id=?",
            (record_id, user_id, self._tenant(user_id)))
        return _row_dict(row) if row else None

    def list(self, user_id: str, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM contradiction_records WHERE user_id=? AND tenant_id=?"
        params: list[Any] = [user_id, self._tenant(user_id)]
        if status:
            sql += " AND status=?"
            params.append(status.upper())
        sql += " ORDER BY datetime(updated_at) DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [_row_dict(r) for r in self.db.query(sql, params)]

    def _record(self, user_id: str, left: dict[str, Any], right: dict[str, Any],
                result: dict[str, Any], *, correlation_id: str | None = None) -> dict[str, Any]:
        left_id, right_id = sorted((left["id"], right["id"]))
        fp = _fingerprint(left_id, right_id)
        timestamp = now()
        existing = self.db.query_one(
            "SELECT * FROM contradiction_records WHERE user_id=? AND tenant_id=? AND fingerprint=?",
            (user_id, self._tenant(user_id), fp))
        refs = [{"kind": "cognitive_object", "id": left["id"], "provenance": left.get("provenance")},
                {"kind": "cognitive_object", "id": right["id"], "provenance": right.get("provenance")}]
        if existing:
            self.db.execute(
                "UPDATE contradiction_records SET classification=?, scope_analysis_json=?, "
                "temporal_analysis_json=?, proposition_analysis_json=?, evidence_refs_json=?, confidence=?, updated_at=? "
                "WHERE id=? AND user_id=? AND tenant_id=?",
                (result["classification"], _json(result.get("scope_analysis", {})),
                 _json({"relation": _temporal_relation(left, right)}), _json(result.get("proposition_analysis", {})),
                 _json(refs), float(result.get("confidence", 0.5)), timestamp,
                 existing["id"], user_id, self._tenant(user_id)))
            record = self.get(user_id, existing["id"])
        else:
            rid = _id("contra")
            self.db.execute(
                "INSERT INTO contradiction_records (id,user_id,tenant_id,left_object_id,right_object_id,"
                "classification,status,scope_analysis_json,temporal_analysis_json,proposition_analysis_json,evidence_refs_json,"
                "provenance_json,confidence,fingerprint,correlation_id,detected_at,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, user_id, self._tenant(user_id), left["id"], right["id"], result["classification"],
                 ContradictionStatus.OPEN.value, _json(result.get("scope_analysis", {})),
                 _json({"relation": _temporal_relation(left, right)}), _json(result.get("proposition_analysis", {})), _json(refs),
                 _json({"source": "SYSTEM_DERIVED", "basis": "scope_aware_comparison"}),
                 float(result.get("confidence", 0.5)), fp, correlation_id, timestamp, timestamp, timestamp))
            record = self.get(user_id, rid)
            self._emit(user_id, "contradiction.detected", "Compared two canonical personal-state objects.",
                       subject_kind="contradiction", subject_id=rid,
                       correlation_id=correlation_id,
                       payload={"left_object_id": left["id"], "right_object_id": right["id"]})
        self._emit(user_id, "contradiction.classified", result["reason"],
                   subject_kind="contradiction", subject_id=record["id"],
                   correlation_id=correlation_id,
                   payload={"classification": result["classification"], "confidence": result.get("confidence")})
        return record  # type: ignore[return-value]

    def audit(self, user_id: str, *, correlation_id: str | None = None) -> list[dict[str, Any]]:
        objects = [o for o in self.personal_state.list(user_id, limit=500)
                   if o.get("status") not in _TERMINAL and o.get("type") in self.COMPARABLE]
        records: list[dict[str, Any]] = []
        for i, left in enumerate(objects):
            for right in objects[i + 1:]:
                result = self.classify(left, right)
                if result["classification"] == ContradictionClass.NOT_A_CONTRADICTION.value:
                    continue
                records.append(self._record(user_id, left, right, result,
                                            correlation_id=correlation_id))
        return records

    def resolve(self, user_id: str, record_id: str, *, status: str = "RESOLVED_FINDING",
                classification: str | None = None, correlation_id: str | None = None) -> dict[str, Any] | None:
        """Resolve/dismiss the finding only; never mutate either V9 object."""
        item = self.get(user_id, record_id)
        if item is None:
            return None
        status = status.upper()
        allowed = {ContradictionStatus.RESOLVED_FINDING.value,
                   ContradictionStatus.DISMISSED.value,
                   ContradictionStatus.SUPERSEDED.value,
                   # Compatibility for clients using the original V10 API.
                   ContradictionStatus.RESOLVED.value}
        if status not in allowed:
            raise ValueError("Contradiction actions can only resolve the finding, dismiss it, or mark it superseded.")
        self.db.execute(
            "UPDATE contradiction_records SET status=?, classification=COALESCE(?,classification), resolved_at=?, updated_at=? "
            "WHERE id=? AND user_id=? AND tenant_id=?",
            (status, classification, now(), now(), record_id, user_id, self._tenant(user_id)))
        payload = {"status": status, "classification": classification,
                   "personal_state_mutated": False}
        # A dismissal is a finding lifecycle closure, not a contradiction
        # resolution. A resolved finding emits both the explicit finding event
        # and the established resolved label; neither claims a V9 mutation.
        self._emit(user_id, "contradiction.finding_resolved",
                   f"Contradiction finding is {status}; personal state was not mutated.",
                   subject_kind="contradiction", subject_id=record_id,
                   correlation_id=correlation_id, payload=payload)
        if status != ContradictionStatus.DISMISSED.value:
            self._emit(user_id, "contradiction.resolved",
                       "Resolved a contradiction finding without changing personal state.",
                       subject_kind="contradiction", subject_id=record_id,
                       correlation_id=correlation_id, payload=payload)
        return self.get(user_id, record_id)


class UnknownService(V10ScopedService):
    """First-class UNKNOWN records backed by canonical QUESTION objects."""

    def __init__(self, db, bus, personal_state, *, tenant_id: str = "local"):
        super().__init__(db, bus, tenant_id=tenant_id)
        self.personal_state = personal_state

    def get(self, user_id: str, unknown_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM unknown_records WHERE id=? AND user_id=? AND tenant_id=?",
            (unknown_id, user_id, self._tenant(user_id)))
        return _row_dict(row) if row else None

    def list(self, user_id: str, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM unknown_records WHERE user_id=? AND tenant_id=?"
        params: list[Any] = [user_id, self._tenant(user_id)]
        if status:
            sql += " AND status=?"
            params.append(status.upper())
        sql += " ORDER BY datetime(updated_at) DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [_row_dict(r) for r in self.db.query(sql, params)]

    def identify(self, user_id: str, *, what_unknown: str, why_it_matters: str,
                 missing_evidence: list[dict[str, Any]], resolution_path: str,
                 relevant_object_ids: list[str], question_object_id: str | None = None,
                 confidence: float = 0.0, correlation_id: str | None = None) -> dict[str, Any]:
        for oid in relevant_object_ids:
            if not self.personal_state.get(user_id, oid):
                raise KeyError("A relevant unknown reference is outside the verified user scope.")
        if question_object_id and not self.personal_state.get(user_id, question_object_id):
            raise KeyError("Question object is outside the verified user scope.")
        if not question_object_id:
            question = self.personal_state.create(user_id, CognitiveObjectCreate(
                type="QUESTION", content=what_unknown, modality=Modality.QUESTIONED,
                confidence=0.0, provenance=Provenance.SYSTEM_DERIVED,
                source="v10.unknown", status="ACTIVE"), correlation_id=correlation_id)
            question_object_id = question["id"]
        fp = _fingerprint(question_object_id, *sorted(relevant_object_ids))
        existing = self.db.query_one(
            "SELECT * FROM unknown_records WHERE user_id=? AND tenant_id=? AND fingerprint=?",
            (user_id, self._tenant(user_id), fp))
        timestamp = now()
        if existing:
            self.db.execute(
                "UPDATE unknown_records SET missing_evidence_json=?, updated_at=? "
                "WHERE id=? AND user_id=? AND tenant_id=?",
                (_json(missing_evidence), timestamp, existing["id"], user_id, self._tenant(user_id)))
            return self.get(user_id, existing["id"])  # type: ignore[return-value]
        uid = _id("unknown")
        self.db.execute(
            "INSERT INTO unknown_records (id,user_id,tenant_id,question_object_id,status,what_unknown,"
            "why_it_matters,missing_evidence_json,resolution_path,relevant_object_ids_json,provenance_json,"
            "confidence,fingerprint,correlation_id,identified_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uid, user_id, self._tenant(user_id), question_object_id, UnknownStatus.OPEN.value,
             what_unknown, why_it_matters, _json(missing_evidence), resolution_path,
             _json(relevant_object_ids), _json({"source": "SYSTEM_DERIVED"}),
             max(0.0, min(1.0, confidence)), fp, correlation_id, timestamp, timestamp, timestamp))
        self._emit(user_id, "unknown.identified", what_unknown,
                   subject_kind="unknown", subject_id=uid, correlation_id=correlation_id,
                   payload={"question_object_id": question_object_id,
                            "missing_evidence": missing_evidence})
        return self.get(user_id, uid)  # type: ignore[return-value]

    def detect(self, user_id: str, contradictions: Iterable[dict[str, Any]], *,
               correlation_id: str | None = None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for item in contradictions:
            if item["classification"] == ContradictionClass.INSUFFICIENT_CONTEXT.value:
                found.append(self.identify(
                    user_id,
                    what_unknown="Which of these canonical statements applies, and in what context?",
                    why_it_matters="The system cannot safely update personal state while the relevant scope is unknown.",
                    missing_evidence=[{"kind": "RELEVANT_CONTEXT", "objects": [item["left_object_id"], item["right_object_id"]]}],
                    resolution_path="Ask the user to specify the context, time, or priority.",
                    relevant_object_ids=[item["left_object_id"], item["right_object_id"]],
                    correlation_id=correlation_id))
        return found

    def resolve(self, user_id: str, unknown_id: str, *, reason: str,
                correlation_id: str | None = None) -> dict[str, Any] | None:
        item = self.get(user_id, unknown_id)
        if item is None:
            return None
        if not reason.strip():
            raise ValueError("Resolving an unknown requires explicit evidence or a user answer.")
        self.db.execute(
            "UPDATE unknown_records SET status=?, resolved_at=?, updated_at=?, "
            "provenance_json=? WHERE id=? AND user_id=? AND tenant_id=?",
            (UnknownStatus.RESOLVED.value, now(), now(),
             _json({"source": "USER_CONFIRMED", "reason": reason}),
             unknown_id, user_id, self._tenant(user_id)))
        self._emit(user_id, "unknown.resolved", "An explicit unknown was resolved by supplied evidence.",
                   subject_kind="unknown", subject_id=unknown_id,
                   correlation_id=correlation_id, payload={"reason": reason})
        return self.get(user_id, unknown_id)


class ModelErrorService(V10ScopedService):
    """Adds error classification to V9 prediction/outcome records."""

    def __init__(self, db, bus, personal_state, *, tenant_id: str = "local"):
        super().__init__(db, bus, tenant_id=tenant_id)
        self.personal_state = personal_state

    def get(self, user_id: str, error_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM model_error_records WHERE id=? AND user_id=? AND tenant_id=?",
            (error_id, user_id, self._tenant(user_id)))
        return _row_dict(row) if row else None

    def list(self, user_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return [_row_dict(r) for r in self.db.query(
            "SELECT * FROM model_error_records WHERE user_id=? AND tenant_id=? ORDER BY datetime(created_at) DESC LIMIT ?",
            (user_id, self._tenant(user_id), max(1, min(limit, 500))))]

    @staticmethod
    def classify(*, execution_error: bool = False, world_changed: bool = False,
                 timing_error: bool = False, observation_error: bool = False,
                 causal_error: bool = False, random_outcome: bool = False,
                 user_model_evidence: bool = False,
                 missing_information: bool = False) -> str:
        if execution_error:
            return ModelErrorClass.EXECUTION_ERROR.value
        if observation_error:
            return ModelErrorClass.OBSERVATION_ERROR.value
        if missing_information:
            return ModelErrorClass.MISSING_INFORMATION.value
        if timing_error:
            return ModelErrorClass.TIMING_ERROR.value
        if causal_error:
            return ModelErrorClass.CAUSAL_MODEL_ERROR.value
        if world_changed:
            return ModelErrorClass.WORLD_MODEL_ERROR.value
        if random_outcome:
            return ModelErrorClass.RANDOM_OUTCOME.value
        if user_model_evidence:
            return ModelErrorClass.USER_MODEL_ERROR.value
        return ModelErrorClass.UNRESOLVED.value

    def record(self, user_id: str, *, expected_state: str, actual_observation: str,
               prediction_id: str | None = None, assumption_object_id: str | None = None,
               classification: str | None = None, evidence_refs: list[dict[str, Any]] | None = None,
               execution_error: bool = False, world_changed: bool = False,
               timing_error: bool = False, observation_error: bool = False,
               causal_error: bool = False, random_outcome: bool = False,
               missing_information: bool = False, user_model_evidence: bool = False,
               confidence: float = 0.5, learning_candidate: str | None = None,
               correlation_id: str | None = None) -> dict[str, Any]:
        if prediction_id:
            clause = self._canonical_scope("p")
            if not self.db.query_one(
                    f"SELECT 1 FROM predictions p WHERE p.id=? AND {clause}",
                    (prediction_id,) + self._canonical_scope_params(user_id)):
                raise KeyError("Prediction is not in the verified user and tenant scope.")
        if assumption_object_id and not self.personal_state.get(user_id, assumption_object_id):
            raise KeyError("Assumption is not in the verified user scope.")
        error_class = classification or self.classify(
            execution_error=execution_error, world_changed=world_changed,
            timing_error=timing_error, observation_error=observation_error,
            causal_error=causal_error, random_outcome=random_outcome,
            user_model_evidence=user_model_evidence,
            missing_information=missing_information)
        if error_class not in {item.value for item in ModelErrorClass}:
            raise ValueError(f"Unsupported model-error class: {error_class}")
        supported_evidence = bool(evidence_refs or prediction_id or assumption_object_id or
                                  execution_error or world_changed or timing_error or
                                  observation_error or causal_error or random_outcome or
                                  missing_information or user_model_evidence)
        if error_class != ModelErrorClass.UNRESOLVED.value and not supported_evidence:
            raise ValueError("A non-UNRESOLVED model-error classification requires evidence or an explicit evidence flag.")
        evidence_refs = evidence_refs or []
        if not all(isinstance(ref, dict) and ref.get("kind") for ref in evidence_refs):
            raise ValueError("Every model-error evidence reference must identify a kind.")
        for ref in evidence_refs:
            ref_id = ref.get("id")
            if not ref_id:
                continue
            if self.personal_state.get(user_id, str(ref_id)):
                continue
            kind_table = {"prediction": "predictions", "decision": "decisions",
                          "world_entity": "world_entities"}.get(str(ref.get("kind")))
            if kind_table:
                if not self.db.query_one(
                        f"SELECT 1 FROM {kind_table} v WHERE v.id=? AND {self._canonical_scope('v')}",
                        (str(ref_id),) + self._canonical_scope_params(user_id)):
                    raise KeyError("A model-error evidence reference is outside the verified user and tenant scope.")
        eid = _id("error")
        timestamp = now()
        self.db.execute(
            "INSERT INTO model_error_records (id,user_id,tenant_id,prediction_id,assumption_object_id,"
            "expected_state,actual_observation,observation_at,error_class,evidence_refs_json,provenance_json,"
            "confidence,learning_candidate,correlation_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, user_id, self._tenant(user_id), prediction_id, assumption_object_id, expected_state,
             actual_observation, timestamp, error_class, _json(evidence_refs or []),
             _json({"source": "SYSTEM_DERIVED", "explicit_classification": bool(classification)}),
             max(0.0, min(1.0, confidence)), learning_candidate, correlation_id, timestamp, timestamp))
        self._emit(user_id, "model_error.detected", "Compared an expected state with an actual observation.",
                   subject_kind="model_error", subject_id=eid, correlation_id=correlation_id,
                   payload={"prediction_id": prediction_id, "classification": error_class})
        self._emit(user_id, "model_error.classified", f"Model error classified as {error_class}.",
                   subject_kind="model_error", subject_id=eid, correlation_id=correlation_id,
                   payload={"classification": error_class, "learning_candidate": learning_candidate})
        return self.get(user_id, eid)  # type: ignore[return-value]

    def detect(self, user_id: str, *, correlation_id: str | None = None) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        clause = self._canonical_scope("p")
        rows = self.db.query(
            f"SELECT p.* FROM predictions p WHERE {clause} AND p.status='incorrect' ORDER BY p.evaluated_at ASC",
            self._canonical_scope_params(user_id))
        for row in rows:
            exists = self.db.query_one(
                "SELECT 1 FROM model_error_records WHERE user_id=? AND tenant_id=? AND prediction_id=?",
                (user_id, self._tenant(user_id), row["id"]))
            if exists:
                continue
            outcome = row["outcome"] or ""
            lower = outcome.lower()
            found.append(self.record(
                user_id, prediction_id=row["id"], expected_state=row["statement"],
                actual_observation=outcome or "No outcome observation was recorded.",
                execution_error=bool(re.search(r"could not execute|failed to execute|did not run|not executed", lower)),
                observation_error=not bool(outcome),
                missing_information=not bool(outcome),
                evidence_refs=[{"kind": "prediction", "id": row["id"]},
                               {"kind": "outcome", "id": row["id"], "observed": bool(outcome)}],
                confidence=0.65 if outcome else 0.2,
                learning_candidate=(None if not outcome else "Review the evidence before changing the user model."),
                correlation_id=correlation_id))
        return found


class MaintenanceProposalService(V10ScopedService):
    """Bounded proposals; confirmation and application are separate steps."""

    def __init__(self, db, bus, personal_state, debt, contradictions, unknowns,
                 autonomy, *, tenant_id: str = "local"):
        super().__init__(db, bus, tenant_id=tenant_id)
        self.personal_state = personal_state
        self.debt = debt
        self.contradictions = contradictions
        self.unknowns = unknowns
        self.autonomy = autonomy

    def get(self, user_id: str, proposal_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM maintenance_proposals WHERE id=? AND user_id=? AND tenant_id=?",
            (proposal_id, user_id, self._tenant(user_id)))
        return _row_dict(row) if row else None

    def list(self, user_id: str, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM maintenance_proposals WHERE user_id=? AND tenant_id=?"
        params: list[Any] = [user_id, self._tenant(user_id)]
        if status:
            sql += " AND status=?"
            params.append(status.upper())
        sql += " ORDER BY datetime(created_at) DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [_row_dict(r) for r in self.db.query(sql, params)]

    def propose(self, user_id: str, *, proposal_type: str, target_object_ids: list[str],
                current_state: dict[str, Any], proposed_state: dict[str, Any], reason: str,
                evidence_refs: list[dict[str, Any]], uncertainty: str,
                reversible: bool = True, required_authority: str = "CONFIRMATION",
                expires_at: str | None = None, correlation_id: str | None = None) -> dict[str, Any]:
        if proposal_type not in {item.value for item in ProposalType}:
            raise ValueError(f"Unsupported maintenance proposal type: {proposal_type}")
        # Every target must belong to this user or be a V10 record owned by it.
        for target in target_object_ids:
            owned = bool(self.personal_state.get(user_id, target)) or any(
                self.db.query_one(f"SELECT 1 FROM {table} WHERE id=? AND user_id=? AND tenant_id=?",
                                  (target, user_id, self._tenant(user_id)))
                for table in ("cognitive_debt", "contradiction_records", "unknown_records", "model_error_records"))
            if not owned:
                owned = any(self.db.query_one(
                    f"SELECT 1 FROM {table} v WHERE v.id=? AND {self._canonical_scope('v')}",
                    (target,) + self._canonical_scope_params(user_id))
                    for table in ("predictions", "decisions", "world_entities"))
            if not owned:
                raise KeyError("A proposal target is outside the verified user scope.")
        target_json = _json(target_object_ids)
        existing = self.db.query_one(
            "SELECT * FROM maintenance_proposals WHERE user_id=? AND tenant_id=? AND proposal_type=? "
            "AND target_object_ids_json=? AND status IN ('PROPOSED','DEFERRED')",
            (user_id, self._tenant(user_id), proposal_type, target_json))
        if existing:
            return _row_dict(existing)
        pid = _id("proposal")
        timestamp = now()
        self.db.execute(
            "INSERT INTO maintenance_proposals (id,user_id,tenant_id,proposal_type,status,target_object_ids_json,"
            "current_state_json,proposed_state_json,reason,evidence_refs_json,uncertainty,reversible,"
            "required_authority,expires_at,correlation_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, user_id, self._tenant(user_id), proposal_type, ProposalStatus.PROPOSED.value,
             _json(target_object_ids), _json(current_state), _json(proposed_state), reason,
             _json(evidence_refs), uncertainty, int(reversible), required_authority, expires_at,
             correlation_id, timestamp, timestamp))
        self._emit(user_id, "maintenance.proposed", reason[:180],
                   subject_kind="maintenance_proposal", subject_id=pid,
                   correlation_id=correlation_id,
                   payload={"proposal_type": proposal_type, "target_object_ids": target_object_ids,
                            "required_authority": required_authority, "reversible": reversible})
        return self.get(user_id, pid)  # type: ignore[return-value]

    def _update_status(self, user_id: str, proposal_id: str, status: str,
                       event_type: str, reason: str | None = None,
                       correlation_id: str | None = None) -> dict[str, Any] | None:
        item = self.get(user_id, proposal_id)
        if item is None:
            return None
        if item["status"] not in {ProposalStatus.PROPOSED.value, ProposalStatus.DEFERRED.value}:
            return item
        self.db.execute(
            "UPDATE maintenance_proposals SET status=?, updated_at=?, decided_at=? WHERE id=? AND user_id=? AND tenant_id=?",
            (status, now(), now(), proposal_id, user_id, self._tenant(user_id)))
        self._emit(user_id, event_type, f"Maintenance proposal is {status}.",
                   subject_kind="maintenance_proposal", subject_id=proposal_id,
                   correlation_id=correlation_id, payload={"status": status, "reason": reason})
        return self.get(user_id, proposal_id)

    def reject(self, user_id: str, proposal_id: str, *, reason: str | None = None,
               correlation_id: str | None = None) -> dict[str, Any] | None:
        return self._update_status(user_id, proposal_id, ProposalStatus.REJECTED.value,
                                    "maintenance.rejected", reason, correlation_id)

    def defer(self, user_id: str, proposal_id: str, *, until: str | None = None,
              reason: str | None = None, correlation_id: str | None = None) -> dict[str, Any] | None:
        result = self._update_status(user_id, proposal_id, ProposalStatus.DEFERRED.value,
                                      "maintenance.deferred", reason, correlation_id)
        if result and until:
            self.db.execute(
                "UPDATE maintenance_proposals SET expires_at=? "
                "WHERE id=? AND user_id=? AND tenant_id=?",
                (until, proposal_id, user_id, self._tenant(user_id)))
            result = self.get(user_id, proposal_id)
        return result

    def confirm(self, user_id: str, proposal_id: str, *, reason: str | None = None,
                correlation_id: str | None = None) -> dict[str, Any] | None:
        item = self.get(user_id, proposal_id)
        if item is None:
            return None
        if item["status"] not in {ProposalStatus.PROPOSED.value, ProposalStatus.DEFERRED.value}:
            return item
        governance = self.autonomy.authorize(
            user_id, f"maintenance:{proposal_id}", risk_class="update_memory",
            confidence=0.9, reversible=bool(item["reversible"]), correlation_id=correlation_id)
        if governance.get("disposition") == "BLOCKED":
            self.db.execute(
                "UPDATE maintenance_proposals SET status='BLOCKED', updated_at=? "
                "WHERE id=? AND user_id=? AND tenant_id=?",
                (now(), proposal_id, user_id, self._tenant(user_id)))
            return self.get(user_id, proposal_id)
        self.db.execute(
            "UPDATE maintenance_proposals SET status='CONFIRMED', updated_at=?, decided_at=? WHERE id=? AND user_id=? AND tenant_id=?",
            (now(), now(), proposal_id, user_id, self._tenant(user_id)))
        self._emit(user_id, "maintenance.confirmed", "A maintenance proposal was explicitly confirmed.",
                   subject_kind="maintenance_proposal", subject_id=proposal_id,
                   correlation_id=correlation_id, payload={"governance": governance, "reason": reason})
        try:
            self._apply(user_id, item, reason=reason, correlation_id=correlation_id)
        except (KeyError, ValueError) as exc:
            self.db.execute(
                "UPDATE maintenance_proposals SET status='BLOCKED', updated_at=? "
                "WHERE id=? AND user_id=? AND tenant_id=?",
                (now(), proposal_id, user_id, self._tenant(user_id)))
            self._emit(user_id, "maintenance.blocked", "Maintenance was blocked at the canonical mutation boundary.",
                       subject_kind="maintenance_proposal", subject_id=proposal_id,
                       correlation_id=correlation_id, payload={"status": "BLOCKED", "reason": str(exc),
                                                               "applied": False})
        return self.get(user_id, proposal_id)

    def _apply(self, user_id: str, item: dict[str, Any], *, reason: str | None,
               correlation_id: str | None) -> None:
        targets = item["target_object_ids"]
        typ = item["proposal_type"]
        if typ in {ProposalType.CLOSE_DEBT.value, ProposalType.RETIRE_ASSUMPTION.value}:
            debt = self.debt.get(user_id, targets[0]) if targets else None
            if debt:
                if debt["status"] == DebtStatus.OPEN.value:
                    self.debt.transition(user_id, debt["id"], DebtStatus.ACKNOWLEDGED.value,
                                         reason=reason or "User confirmed maintenance.", correlation_id=correlation_id)
                self.debt.transition(user_id, debt["id"], DebtStatus.RESOLVED.value,
                                     reason=reason or "User confirmed maintenance.", correlation_id=correlation_id)
            if typ == ProposalType.RETIRE_ASSUMPTION.value:
                # A stale-debt proposal stores the debt id followed by the
                # assumption and later evidence. Retire only the assumption.
                for target in targets:
                    obj = self.personal_state.get(user_id, target)
                    if obj and obj.get("type") == "ASSUMPTION":
                        self.personal_state.retire(user_id, target,
                                                   reason=reason or "User confirmed stale-assumption retirement.",
                                                   correlation_id=correlation_id)
                        break
        elif typ == ProposalType.RESOLVE_CONTRADICTION.value:
            # This proposal authorizes only the finding lifecycle transition.
            # A personal-state correction requires a separate proposal and a
            # separate PersonalStateService mutation.
            self.contradictions.resolve(user_id, targets[0], status="RESOLVED_FINDING",
                                        correlation_id=correlation_id)
        elif typ == ProposalType.CONFIRM_UNKNOWN.value:
            self.unknowns.resolve(user_id, targets[0],
                                  reason=reason or "User supplied confirming evidence.",
                                  correlation_id=correlation_id)
        elif typ == ProposalType.DEFER_MAINTENANCE.value:
            return
        else:
            # Unsupported material operations are represented as blocked, not
            # silently treated as applied.
            raise ValueError(f"Canonical application for {typ} is not configured.")
        self.db.execute(
            "UPDATE maintenance_proposals SET status='APPLIED', applied_at=?, updated_at=? "
            "WHERE id=? AND user_id=? AND tenant_id=?",
            (now(), now(), item["id"], user_id, self._tenant(user_id)))
        self._emit(user_id, "maintenance.applied", "Applied a confirmed maintenance proposal.",
                   subject_kind="maintenance_proposal", subject_id=item["id"],
                   correlation_id=correlation_id, payload={"proposal_type": typ, "targets": targets})


class CognitiveHealthService(V10ScopedService):
    """Decomposable health dimensions; no opaque human score is produced."""

    def __init__(self, db, bus, personal_state, debt, contradictions, unknowns,
                 errors, *, tenant_id: str = "local"):
        super().__init__(db, bus, tenant_id=tenant_id)
        self.personal_state = personal_state
        self.debt = debt
        self.contradictions = contradictions
        self.unknowns = unknowns
        self.errors = errors

    def compute(self, user_id: str, *, correlation_id: str | None = None,
                persist: bool = True) -> dict[str, Any]:
        objects = [o for o in self.personal_state.list(user_id, limit=500)
                   if o.get("status") not in _TERMINAL]
        debts = self.debt.list(user_id, status="OPEN", limit=500)
        contradictions = self.contradictions.list(user_id, status="OPEN", limit=500)
        unknowns = self.unknowns.list(user_id, status="OPEN", limit=500)
        errors = self.errors.list(user_id, limit=500)
        prediction_clause = self._canonical_scope("p")
        predictions = self.db.query(
            f"SELECT p.* FROM predictions p WHERE {prediction_clause}",
            self._canonical_scope_params(user_id))
        evaluated_predictions = [p for p in predictions if p["status"] in {"correct", "incorrect"}]
        explicit_evidence = sum(1 for o in objects if o.get("provenance") and (o.get("evidence") or o.get("provenance") in {"USER_STATED", "USER_CONFIRMED", "SYSTEM_OBSERVED"}))
        base = max(1, len(objects))
        dimensions = {
            "COHERENCE": self._dimension(1.0 - min(1.0, len(contradictions) / base),
                f"{len(contradictions)} unresolved contradiction record(s).", contradictions),
            "FRESHNESS": self._dimension(1.0 - min(1.0, sum(1 for d in debts if d["debt_type"] in {DebtType.STALE_ASSUMPTION.value, DebtType.STALE_WORLD_DEPENDENCY.value}) / max(1, len(debts) + 1)),
                "Derived from stale-assumption and stale-world-dependency debt.", [d["id"] for d in debts]),
            "EVIDENCE": self._dimension(explicit_evidence / base,
                f"{explicit_evidence} of {len(objects)} active semantic object(s) have explicit provenance/evidence.", [o["id"] for o in objects if o.get("evidence")]),
            "COMPLETENESS": self._dimension(1.0 - min(1.0, len(unknowns) / max(1, len(objects))),
                f"{len(unknowns)} material unknown(s) remain open.", [u["id"] for u in unknowns]),
            "PREDICTIVE_TRACKING": self._dimension((len(evaluated_predictions) / len(predictions)) if predictions else None,
                (f"{len(evaluated_predictions)} of {len(predictions)} prediction(s) have an explicitly evaluated outcome; expired and cancelled records are not counted as observed."
                 if predictions else "No prediction records exist."),
                [p["id"] for p in predictions if p["status"] not in {"correct", "incorrect"}]),
            "DECISION_CURRENCY": self._dimension(1.0 - min(1.0, sum(1 for d in debts if d["debt_type"] in {DebtType.UNRESOLVED_DECISION.value, DebtType.OVERDUE_COMMITMENT.value}) / max(1, len(debts) + 1)),
                "Derived from unresolved decision and overdue commitment debt.", [d["id"] for d in debts]),
            "MODEL_STABILITY": self._dimension(
                (sum(1 for p in evaluated_predictions if p["status"] == "correct") / len(evaluated_predictions))
                if len(evaluated_predictions) >= 3 else None,
                ("Derived only from at least three explicitly evaluated prediction outcomes; model-error records are not used as a penalty."
                 if len(evaluated_predictions) >= 3 else
                 "INSUFFICIENT_EVIDENCE: a capability reliability measure needs at least three evaluated prediction outcomes; model errors remain learning evidence."),
                [p["id"] for p in evaluated_predictions]),
        }
        core_dimensions = {"COHERENCE", "FRESHNESS", "EVIDENCE", "COMPLETENESS", "DECISION_CURRENCY"}
        unevaluable_dimensions = sorted(name for name, dimension in dimensions.items()
                                        if dimension["score"] is None)
        evaluable_dimensions = sorted(name for name, dimension in dimensions.items()
                                      if dimension["score"] is not None)
        core_evaluable = all(dimensions[name]["score"] is not None for name in core_dimensions)
        # A single isolated object is not enough evidence for a model-level
        # summary. Once core evidence exists, optional unevaluable dimensions
        # are exposed and omitted from the aggregate rather than silently
        # treated as zero or one.
        has_core_evidence = bool(objects and (len(objects) >= 2 or predictions or debts or unknowns))
        if not objects and not predictions and not debts and not unknowns and not errors:
            summary = "INSUFFICIENT_EVIDENCE"
            sufficiency = "INSUFFICIENT_EVIDENCE"
            summary_rule = "No canonical V9 state or findings exist."
        elif not has_core_evidence or not core_evaluable:
            summary = "INSUFFICIENT_EVIDENCE"
            sufficiency = "INSUFFICIENT_EVIDENCE"
            summary_rule = "Core dimensions cannot be evaluated from the available canonical evidence."
        elif any(dimensions[name]["score"] < 0.4 for name in evaluable_dimensions):
            summary = "DEGRADED"
            sufficiency = "PARTIAL" if unevaluable_dimensions else "SUFFICIENT"
            summary_rule = "Aggregate evaluated dimensions conservatively; unevaluable dimensions are not scored."
        elif any(dimensions[name]["score"] < 0.75 for name in evaluable_dimensions):
            summary = "ATTENTION_REQUIRED"
            sufficiency = "PARTIAL" if unevaluable_dimensions else "SUFFICIENT"
            summary_rule = "Aggregate evaluated dimensions conservatively; unevaluable dimensions are not scored."
        else:
            summary = "HEALTHY"
            sufficiency = "PARTIAL" if unevaluable_dimensions else "SUFFICIENT"
            summary_rule = "Aggregate evaluated dimensions conservatively; unevaluable dimensions are not scored."
        result = {"summary_status": summary, "dimensions": dimensions,
                  "finding_refs": {"debt": [d["id"] for d in debts],
                                    "contradictions": [c["id"] for c in contradictions],
                                    "unknowns": [u["id"] for u in unknowns],
                                    "model_errors": [e["id"] for e in errors]},
                  "evaluable_dimensions": evaluable_dimensions,
                  "unevaluable_dimensions": unevaluable_dimensions,
                  "summary_rule": summary_rule,
                  "evidence_sufficiency": sufficiency,
                  "checked_at": now(), "note": "This describes model state, not the person."}
        if persist:
            sid = _id("health")
            self.db.execute(
                "INSERT INTO cognitive_health_snapshots (id,user_id,tenant_id,summary_status,dimensions_json,"
                "finding_refs_json,evidence_sufficiency,correlation_id,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (sid, user_id, self._tenant(user_id), summary, _json(dimensions), _json(result["finding_refs"]),
                 sufficiency, correlation_id, result["checked_at"]))
            result["snapshot_id"] = sid
            self._emit(user_id, "cognitive_health.updated", f"Cognitive health is {summary}.",
                       subject_kind="cognitive_health", subject_id=sid,
                       correlation_id=correlation_id,
                       payload={"summary_status": summary, "dimensions": list(dimensions)})
        return result

    @staticmethod
    def _dimension(score: float | None, explanation: str, refs: list[Any]) -> dict[str, Any]:
        return {"score": None if score is None else round(max(0.0, min(1.0, score)), 3),
                "explanation": explanation, "finding_refs": refs,
                "status": "INSUFFICIENT_EVIDENCE" if score is None else ("ATTENTION_REQUIRED" if score < .75 else "SUPPORTED")}

    def explain(self, user_id: str, *, correlation_id: str | None = None) -> dict[str, Any]:
        result = self.compute(user_id, correlation_id=correlation_id, persist=False)
        result["explanations"] = {name: data["explanation"] for name, data in result["dimensions"].items()}
        return result

    def latest(self, user_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM cognitive_health_snapshots WHERE user_id=? AND tenant_id=? ORDER BY datetime(created_at) DESC LIMIT 1",
            (user_id, self._tenant(user_id)))
        if not row:
            return None
        return _row_dict(row)


class SelfMaintenanceOrchestrator(V10ScopedService):
    """Explicit, event-driven OBSERVE → AUDIT → PROPOSE → UPDATE loop."""

    def __init__(self, db, bus, personal_state, debt, contradictions, unknowns,
                 errors, proposals, health, autonomy, *, tenant_id: str = "local"):
        super().__init__(db, bus, tenant_id=tenant_id)
        self.personal_state = personal_state
        self.debt = debt
        self.contradictions = contradictions
        self.unknowns = unknowns
        self.errors = errors
        self.proposals = proposals
        self.health = health
        self.autonomy = autonomy

    def _drift(self, user_id: str, *, correlation_id: str | None = None) -> list[dict[str, Any]]:
        clause = self._canonical_scope("v")
        rows = self.db.query(
            f"SELECT v.* FROM personal_state_versions v WHERE {clause} ORDER BY v.version DESC LIMIT 2",
            self._canonical_scope_params(user_id))
        if len(rows) < 2:
            return []
        newer, older = rows[0], rows[1]
        newer_objects = {o["id"]: o for o in _loads(newer["snapshot_json"], {}).get("objects", [])}
        older_objects = {o["id"]: o for o in _loads(older["snapshot_json"], {}).get("objects", [])}
        drift: list[dict[str, Any]] = []
        for oid in sorted(newer_objects.keys() & older_objects.keys()):
            a, b = older_objects[oid], newer_objects[oid]
            if a == b:
                continue
            if b.get("provenance") in {"USER_STATED", "USER_CONFIRMED"} or _explicit_evolution(b.get("content", "")):
                kind = "USER_CONFIRMED_CHANGE"
            elif b.get("type") in {"FACT", "OBSERVATION"}:
                kind = "WORLD_DRIVEN_CHANGE"
            else:
                kind = "SYSTEM_INFERRED_CHANGE"
            item = {"object_id": oid, "kind": kind, "before": a, "after": b}
            drift.append(item)
            self._emit(user_id, "model_drift.detected", f"Detected {kind} in personal state.",
                       subject_kind="cognitive_object", subject_id=oid,
                       correlation_id=correlation_id, payload={"kind": kind, "from_version": older["version"], "to_version": newer["version"]})
        return drift

    # All five bounded families. `audit(families=None)` preserves the exact
    # V10.0.1 full-audit behavior; the V10.1 runtime narrows deterministically.
    FAMILIES = ("debt", "contradictions", "unknowns", "model_errors", "drift")

    def _stage(self, lifecycle, user_id: str, thread_id: str | None,
               correlation_id: str, stage: str, status: str,
               detail: str | None = None) -> None:
        """Emit a truthful surface stage only when a lifecycle is attached.

        Stages are emitted strictly around work that executes; a surface
        emission failure never breaks the audit itself.
        """
        if lifecycle is None:
            return
        try:
            lifecycle.transition(user_id, thread_id or "maintenance",
                                 correlation_id, stage, status, detail=detail)
        except Exception:  # pragma: no cover - observability must not break work
            pass

    def audit(self, user_id: str, *, correlation_id: str | None = None,
              include_resolved: bool = False,
              families: list[str] | None = None,
              lifecycle=None, thread_id: str | None = None) -> dict[str, Any]:
        cid = correlation_id or _id("audit")
        selected = tuple(f for f in (families or self.FAMILIES) if f in self.FAMILIES)
        if families is not None and not selected:
            raise ValueError("No valid maintenance families were selected.")
        existing_run = self.db.query_one(
            "SELECT status,result_json FROM maintenance_runs WHERE correlation_id=? AND user_id=? AND tenant_id=?",
            (cid, user_id, self._tenant(user_id)))
        if existing_run:
            prior = _loads(existing_run["result_json"], {})
            return prior or {"correlation_id": cid, "status": existing_run["status"]}
        started = now()
        self.db.execute(
            "INSERT INTO maintenance_runs (id,user_id,tenant_id,correlation_id,status,started_at) VALUES (?,?,?,?,?,?)",
            (_id("run"), user_id, self._tenant(user_id), cid, "RUNNING", started))
        self._emit(user_id, "cognitive_model.audit_started", "Started an explicit cognitive model audit.",
                   subject_kind="maintenance_run", subject_id=cid, correlation_id=cid,
                   payload={"families": list(selected)})
        self._stage(lifecycle, user_id, thread_id, cid,
                    "AUDITING_PERSONAL_MODEL", "ACTIVE")
        try:
            debts: list[dict[str, Any]] = []
            contradictions: list[dict[str, Any]] = []
            unknowns: list[dict[str, Any]] = []
            errors: list[dict[str, Any]] = []
            drift: list[dict[str, Any]] = []
            if "debt" in selected:
                self._stage(lifecycle, user_id, thread_id, cid,
                            "CHECKING_FOR_STALE_STATE", "ACTIVE")
                debts = self.debt.detect(user_id, correlation_id=cid)
                self._stage(lifecycle, user_id, thread_id, cid,
                            "CHECKING_FOR_STALE_STATE", "COMPLETED")
            if "contradictions" in selected:
                self._stage(lifecycle, user_id, thread_id, cid,
                            "CHECKING_CONTRADICTIONS", "ACTIVE")
                contradictions = self.contradictions.audit(user_id, correlation_id=cid)
                self._stage(lifecycle, user_id, thread_id, cid,
                            "CHECKING_CONTRADICTIONS", "COMPLETED")
            if "unknowns" in selected:
                self._stage(lifecycle, user_id, thread_id, cid,
                            "CHECKING_OPEN_UNKNOWNS", "ACTIVE")
                unknowns = self.unknowns.detect(user_id, contradictions, correlation_id=cid)
                self._stage(lifecycle, user_id, thread_id, cid,
                            "CHECKING_OPEN_UNKNOWNS", "COMPLETED")
            if "model_errors" in selected:
                self._stage(lifecycle, user_id, thread_id, cid,
                            "COMPARING_PREDICTION_TO_OUTCOME", "ACTIVE")
                errors = self.errors.detect(user_id, correlation_id=cid)
                self._stage(lifecycle, user_id, thread_id, cid,
                            "COMPARING_PREDICTION_TO_OUTCOME", "COMPLETED")
                if errors:
                    # ANALYZING_MODEL_ERROR reflects the classification work
                    # that record() genuinely performed for these findings.
                    self._stage(lifecycle, user_id, thread_id, cid,
                                "ANALYZING_MODEL_ERROR", "COMPLETED")
            if "drift" in selected:
                self._stage(lifecycle, user_id, thread_id, cid,
                            "COMPARING_PERSONAL_STATE", "ACTIVE")
                drift = self._drift(user_id, correlation_id=cid)
                self._stage(lifecycle, user_id, thread_id, cid,
                            "COMPARING_PERSONAL_STATE", "COMPLETED")
            proposals: list[dict[str, Any]] = []
            if debts or contradictions:
                self._stage(lifecycle, user_id, thread_id, cid,
                            "EVALUATING_MAINTENANCE_OPTIONS", "ACTIVE")
            for debt in debts:
                if debt["debt_type"] == DebtType.STALE_ASSUMPTION.value:
                    targets = debt["object_ids"]
                    proposals.append(self.proposals.propose(
                        user_id, proposal_type=ProposalType.RETIRE_ASSUMPTION.value,
                        target_object_ids=[debt["id"], *targets],
                        current_state={"debt": debt, "assumption_id": targets[0]},
                        proposed_state={"assumption_status": "RETIRED", "debt_status": "RESOLVED"},
                        reason="Later canonical evidence weakens this assumption; confirm before changing personal state.",
                        evidence_refs=debt["evidence_refs"], uncertainty="The evidence is recorded, but retirement is a material state change.",
                        reversible=True, correlation_id=cid))
                elif debt["debt_type"] == DebtType.UNVALIDATED_PREDICTION.value:
                    proposals.append(self.proposals.propose(
                        user_id, proposal_type=ProposalType.RECORD_OUTCOME.value,
                        target_object_ids=[debt["id"]], current_state={"debt_status": debt["status"]},
                        proposed_state={"next": "collect an observation"},
                        reason="A prediction needs a real observation; no outcome is inferred.",
                        evidence_refs=debt["evidence_refs"], uncertainty="OUTCOME_NOT_OBSERVED",
                        reversible=True, correlation_id=cid))
            for contradiction in contradictions:
                if contradiction["classification"] == ContradictionClass.TRUE_CONTRADICTION.value:
                    proposals.append(self.proposals.propose(
                        user_id, proposal_type=ProposalType.RESOLVE_CONTRADICTION.value,
                        target_object_ids=[contradiction["id"]], current_state={"status": contradiction["status"]},
                        proposed_state={"finding_status": "RESOLVED_FINDING", "personal_state_mutated": False},
                        reason="A true contradiction finding can be closed; neither underlying object will be rewritten by this proposal.",
                        evidence_refs=contradiction["evidence_refs"], uncertainty="User context is still required to choose the durable state.",
                        reversible=True, correlation_id=cid))
            if debts or contradictions:
                self._stage(lifecycle, user_id, thread_id, cid,
                            "EVALUATING_MAINTENANCE_OPTIONS", "COMPLETED")
            health = self.health.compute(user_id, correlation_id=cid, persist=True)
            result = {"correlation_id": cid, "status": "COMPLETED", "started_at": started,
                      "completed_at": now(), "families": list(selected),
                      "debt": debts, "contradictions": contradictions,
                      "unknowns": unknowns, "model_errors": errors, "drift": drift,
                      "proposals": proposals, "cognitive_health": health}
            self.db.execute(
                "UPDATE maintenance_runs SET status='COMPLETED', result_json=?, completed_at=? WHERE correlation_id=? AND user_id=? AND tenant_id=?",
                (_json(result), result["completed_at"], cid, user_id, self._tenant(user_id)))
            self._emit(user_id, "cognitive_model.audit_completed", "Completed the cognitive model audit.",
                       subject_kind="maintenance_run", subject_id=cid, correlation_id=cid,
                       payload={"status": "COMPLETED", "families": list(selected), "counts": {"debt": len(debts), "contradictions": len(contradictions), "unknowns": len(unknowns), "model_errors": len(errors), "proposals": len(proposals)}})
            self._stage(lifecycle, user_id, thread_id, cid,
                        "AUDITING_PERSONAL_MODEL", "COMPLETED")
            return result
        except Exception as exc:
            self.db.execute(
                "UPDATE maintenance_runs SET status='FAILED', error=?, completed_at=? WHERE correlation_id=? AND user_id=? AND tenant_id=?",
                (str(exc)[:500], now(), cid, user_id, self._tenant(user_id)))
            self._emit(user_id, "cognitive_model.audit_completed", "Cognitive model audit failed honestly.",
                       subject_kind="maintenance_run", subject_id=cid, correlation_id=cid,
                       payload={"status": "FAILED", "error": str(exc)[:300]})
            self._stage(lifecycle, user_id, thread_id, cid,
                        "AUDITING_PERSONAL_MODEL", "FAILED",
                        detail=f"Audit failed: {type(exc).__name__}")
            raise

    def run(self, user_id: str, correlation_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT result_json FROM maintenance_runs WHERE correlation_id=? AND user_id=? AND tenant_id=?",
            (correlation_id, user_id, self._tenant(user_id)))
        return _loads(row["result_json"], {}) if row else None
