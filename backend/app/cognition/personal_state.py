"""V9 cognitive objects, semantic relationships and versioned personal state.

This is an additive semantic layer over the existing V8 systems. It records
what an utterance *means* without pretending that a semantic object is an
operational mission, a verified world fact, or a performed action.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..schemas.semantic import (CognitiveObjectCreate, CognitiveObjectUpdate,
                                RelationshipCreate, SemanticCandidate)

RELATIONSHIP_KINDS = frozenset({
    "supports", "contradicts", "refines", "supersedes", "derived_from",
    "caused_by", "depends_on", "related_to", "motivates", "constrains",
    "predicts", "resulted_in",
})
TERMINAL_STATUSES = frozenset({"RETIRED", "SUPERSEDED", "DELETED"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class PersonalStateService:
    """User-scoped canonical semantic state with immutable snapshots."""

    def __init__(self, db, bus) -> None:
        self.db = db
        self.bus = bus

    @staticmethod
    def _object(row: Any) -> dict[str, Any]:
        obj = dict(row)
        obj["temporal_scope"] = _loads(obj.pop("temporal_scope_json", None), {})
        obj["evidence"] = _loads(obj.pop("evidence_json", None), [])
        obj["metadata"] = _loads(obj.pop("metadata_json", None), {})
        return obj

    def get(self, user_id: str, object_id: str) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM cognitive_objects WHERE id=? AND user_id=?",
            (object_id, user_id))
        return self._object(row) if row else None

    def list(self, user_id: str, *, type: str | None = None,
             status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cognitive_objects WHERE user_id=?"
        params: list[Any] = [user_id]
        if type:
            sql += " AND type=?"
            params.append(type.upper())
        if status:
            sql += " AND status=?"
            params.append(status.upper())
        sql += " ORDER BY datetime(updated_at) DESC, id DESC LIMIT ?"
        params.append(max(1, min(limit, 500)))
        return [self._object(r) for r in self.db.query(sql, params)]

    def create(self, user_id: str, data: CognitiveObjectCreate | SemanticCandidate,
               *, thread_id: str | None = None, correlation_id: str | None = None,
               compilation_id: str | None = None, create_version: bool = True) -> dict[str, Any]:
        oid = f"cog_{uuid.uuid4().hex[:20]}"
        now = _now()
        status = data.status.upper()
        # Compiler candidates are PROPOSED until policy explicitly records them.
        if status == "PROPOSED":
            status = "ACTIVE"
        metadata = getattr(data, "metadata", {}) or {}
        self.db.execute(
            "INSERT INTO cognitive_objects "
            "(id,user_id,type,content,modality,temporal_scope_json,confidence,"
            "provenance,source,status,evidence_json,metadata_json,superseded_by,"
            "compilation_id,thread_id,object_version,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (oid, user_id, data.type.value, data.content, data.modality.value,
             _json(data.temporal_scope.model_dump()), float(data.confidence),
             data.provenance.value, data.source, status,
             _json([e.model_dump() for e in data.evidence]), _json(metadata), None,
             compilation_id, thread_id, 1, now, now))
        obj = self.get(user_id, oid)
        self._record_object_version(user_id, obj, "created")
        self.bus.emit(
            user_id, "semantic.object_created", f"Created {data.type.value} semantic object.",
            subject_kind="cognitive_object", subject_id=oid,
            correlation_id=correlation_id,
            payload={"type": data.type.value, "modality": data.modality.value,
                     "provenance": data.provenance.value, "status": status})
        if create_version:
            self.create_state_version(user_id, reason=f"Added {data.type.value}",
                                      correlation_id=correlation_id,
                                      changed_object_ids=[oid])
        return obj

    def update(self, user_id: str, object_id: str, data: CognitiveObjectUpdate,
               *, correlation_id: str | None = None,
               create_version: bool = True) -> dict[str, Any] | None:
        current = self.get(user_id, object_id)
        if current is None:
            return None
        values = {
            "content": data.content if data.content is not None else current["content"],
            "modality": data.modality.value if data.modality else current["modality"],
            "confidence": data.confidence if data.confidence is not None else current["confidence"],
            "status": data.status.upper() if data.status else current["status"],
            "temporal_scope_json": _json(data.temporal_scope.model_dump()) if data.temporal_scope else _json(current["temporal_scope"]),
            "evidence_json": _json([e.model_dump() for e in data.evidence]) if data.evidence is not None else _json(current["evidence"]),
        }
        version = int(current["object_version"]) + 1
        now = _now()
        self.db.execute(
            "UPDATE cognitive_objects SET content=?, modality=?, confidence=?, status=?,"
            " temporal_scope_json=?, evidence_json=?, object_version=?, updated_at=?"
            " WHERE id=? AND user_id=?",
            (values["content"], values["modality"], values["confidence"],
             values["status"], values["temporal_scope_json"], values["evidence_json"],
             version, now, object_id, user_id))
        updated = self.get(user_id, object_id)
        self._record_object_version(user_id, updated, data.reason)
        self.bus.emit(
            user_id, "semantic.object_updated", f"Updated {updated['type']} semantic object.",
            subject_kind="cognitive_object", subject_id=object_id,
            correlation_id=correlation_id,
            payload={"object_version": version, "reason": data.reason,
                     "changed_fields": [k for k in values if k.replace("_json", "") not in () and values[k] != ({"temporal_scope_json": _json(current['temporal_scope']), "evidence_json": _json(current['evidence'])}.get(k, current.get(k)))]})
        if create_version:
            self.create_state_version(user_id, reason=data.reason,
                                      correlation_id=correlation_id,
                                      changed_object_ids=[object_id])
        return updated

    def retire(self, user_id: str, object_id: str, *, reason: str,
               correlation_id: str | None = None) -> dict[str, Any] | None:
        return self.update(user_id, object_id, CognitiveObjectUpdate(
            status="RETIRED", reason=reason), correlation_id=correlation_id)

    def supersede(self, user_id: str, object_id: str, replacement: CognitiveObjectCreate,
                  *, reason: str, correlation_id: str | None = None,
                  thread_id: str | None = None) -> dict[str, Any] | None:
        old = self.get(user_id, object_id)
        if old is None:
            return None
        new = self.create(user_id, replacement, thread_id=thread_id,
                          correlation_id=correlation_id, create_version=False)
        self.db.execute(
            "UPDATE cognitive_objects SET status='SUPERSEDED', superseded_by=?,"
            " object_version=object_version+1, updated_at=? WHERE id=? AND user_id=?",
            (new["id"], _now(), object_id, user_id))
        old_updated = self.get(user_id, object_id)
        self._record_object_version(user_id, old_updated, reason)
        rel = RelationshipCreate(source_id=new["id"], target_id=object_id,
                                 kind="supersedes", provenance=replacement.provenance)
        self.add_relationship(user_id, rel, correlation_id=correlation_id,
                              create_version=False)
        self.bus.emit(
            user_id, "semantic.object_superseded", f"Superseded {old['type']} semantic object.",
            subject_kind="cognitive_object", subject_id=object_id,
            correlation_id=correlation_id,
            payload={"superseded_by": new["id"], "reason": reason})
        self.create_state_version(user_id, reason=reason, correlation_id=correlation_id,
                                  changed_object_ids=[object_id, new["id"]])
        return {"previous": old_updated, "replacement": new}

    def add_relationship(self, user_id: str, data: RelationshipCreate,
                         *, correlation_id: str | None = None,
                         create_version: bool = True) -> dict[str, Any]:
        if data.kind not in RELATIONSHIP_KINDS:
            raise ValueError(f"Unsupported semantic relationship: {data.kind}")
        # Both lookups include user_id: foreign ids are indistinguishable from unknown ids.
        if not self.get(user_id, data.source_id) or not self.get(user_id, data.target_id):
            raise KeyError("One or both cognitive objects are unknown.")
        rid = f"rel_{uuid.uuid4().hex[:20]}"
        now = _now()
        self.db.execute(
            "INSERT INTO cognitive_relationships "
            "(id,user_id,source_id,target_id,kind,confidence,provenance,evidence_json,created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, user_id, data.source_id, data.target_id, data.kind,
             data.confidence, data.provenance.value,
             _json([e.model_dump() for e in data.evidence]), now))
        self.bus.emit(user_id, "semantic.relationship_created",
                      f"Recorded semantic relationship: {data.kind}.",
                      subject_kind="cognitive_relationship", subject_id=rid,
                      correlation_id=correlation_id,
                      payload={"source_id": data.source_id, "target_id": data.target_id,
                               "kind": data.kind})
        if create_version:
            self.create_state_version(user_id, reason=f"Relationship {data.kind} added",
                                      correlation_id=correlation_id,
                                      changed_object_ids=[data.source_id, data.target_id])
        row = self.db.query_one(
            "SELECT * FROM cognitive_relationships WHERE id=? AND user_id=?",
            (rid, user_id))
        item = dict(row)
        item["evidence"] = _loads(item.pop("evidence_json", None), [])
        return item

    def relationships(self, user_id: str, *, object_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM cognitive_relationships WHERE user_id=?"
        params: list[Any] = [user_id]
        if object_id:
            sql += " AND (source_id=? OR target_id=?)"
            params += [object_id, object_id]
        sql += " ORDER BY datetime(created_at), id"
        rows = []
        for row in self.db.query(sql, params):
            item = dict(row)
            item["evidence"] = _loads(item.pop("evidence_json", None), [])
            rows.append(item)
        return rows

    def _record_object_version(self, user_id: str, obj: dict[str, Any], reason: str) -> None:
        self.db.execute(
            "INSERT INTO cognitive_object_versions "
            "(object_id,user_id,object_version,snapshot_json,reason,created_at)"
            " VALUES (?,?,?,?,?,?)",
            (obj["id"], user_id, obj["object_version"], _json(obj), reason, _now()))

    def _snapshot(self, user_id: str) -> dict[str, Any]:
        # State history must be complete, not constrained by the public list
        # endpoint's presentation limit.
        objects = [self._object(row) for row in self.db.query(
            "SELECT * FROM cognitive_objects WHERE user_id=?"
            " ORDER BY datetime(updated_at) DESC, id DESC", (user_id,))]
        active = [o for o in objects if o["status"] not in TERMINAL_STATUSES]
        active.sort(key=lambda o: o["id"])
        relationships = self.relationships(user_id)
        return {"objects": active, "relationships": relationships}

    def create_state_version(self, user_id: str, *, reason: str,
                             correlation_id: str | None = None,
                             changed_object_ids: list[str] | None = None) -> dict[str, Any]:
        prior = self.db.query_one(
            "SELECT version FROM personal_state_versions WHERE user_id=?"
            " ORDER BY version DESC LIMIT 1", (user_id,))
        version = int(prior["version"]) + 1 if prior else 1
        snapshot = self._snapshot(user_id)
        encoded = _json(snapshot)
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = _now()
        self.db.execute(
            "INSERT INTO personal_state_versions "
            "(user_id,version,snapshot_json,snapshot_hash,reason,changed_object_ids,correlation_id,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (user_id, version, encoded, digest, reason,
             _json(changed_object_ids or []), correlation_id, now))
        payload = {"version": version, "reason": reason,
                   "changed_object_ids": changed_object_ids or [],
                   "snapshot_hash": digest}
        self.bus.emit(user_id, "personal_state.version_created",
                      f"Created personal state version {version}.",
                      subject_kind="personal_state", subject_id=str(version),
                      correlation_id=correlation_id, payload=payload)
        self.bus.emit(user_id, "personal_state.updated",
                      f"Personal state advanced to version {version}.",
                      subject_kind="personal_state", subject_id=str(version),
                      correlation_id=correlation_id, payload=payload)
        return {**payload, "created_at": now, "snapshot": snapshot}

    def current(self, user_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM personal_state_versions WHERE user_id=?"
            " ORDER BY version DESC LIMIT 1", (user_id,))
        if not row:
            return {"version": 0, "snapshot": {"objects": [], "relationships": []},
                    "reason": "No material semantic state recorded."}
        return self._version_row(row)

    def reconstruct(self, user_id: str, *, version: int | None = None,
                    at: str | None = None) -> dict[str, Any] | None:
        if version is not None:
            row = self.db.query_one(
                "SELECT * FROM personal_state_versions WHERE user_id=? AND version=?",
                (user_id, version))
        elif at is not None:
            row = self.db.query_one(
                "SELECT * FROM personal_state_versions WHERE user_id=? AND created_at<=?"
                " ORDER BY version DESC LIMIT 1", (user_id, at))
        else:
            row = self.db.query_one(
                "SELECT * FROM personal_state_versions WHERE user_id=?"
                " ORDER BY version DESC LIMIT 1", (user_id,))
        return self._version_row(row) if row else None

    @staticmethod
    def _version_row(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["snapshot"] = _loads(item.pop("snapshot_json", None), {})
        item["changed_object_ids"] = _loads(item.get("changed_object_ids"), [])
        return item

    def diff(self, user_id: str, from_version: int, to_version: int) -> dict[str, Any] | None:
        before = self.reconstruct(user_id, version=from_version)
        after = self.reconstruct(user_id, version=to_version)
        if before is None or after is None:
            return None
        a = {o["id"]: o for o in before["snapshot"].get("objects", [])}
        b = {o["id"]: o for o in after["snapshot"].get("objects", [])}
        added = [b[k] for k in sorted(b.keys() - a.keys())]
        removed = [a[k] for k in sorted(a.keys() - b.keys())]
        changed = []
        watched = ("content", "modality", "confidence", "status", "temporal_scope",
                   "superseded_by", "object_version")
        for oid in sorted(a.keys() & b.keys()):
            fields = {name: {"before": a[oid].get(name), "after": b[oid].get(name)}
                      for name in watched if a[oid].get(name) != b[oid].get(name)}
            if fields:
                changed.append({"id": oid, "type": b[oid]["type"], "fields": fields})
        ar = {r["id"]: r for r in before["snapshot"].get("relationships", [])}
        br = {r["id"]: r for r in after["snapshot"].get("relationships", [])}
        return {
            "from_version": from_version, "to_version": to_version,
            "confirmed": {"added": added, "removed_or_retired": removed,
                          "changed": changed,
                          "relationships_added": [br[k] for k in sorted(br.keys() - ar.keys())],
                          "relationships_removed": [ar[k] for k in sorted(ar.keys() - br.keys())]},
            "proposed_interpretation": None,
        }
