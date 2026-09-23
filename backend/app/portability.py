"""User-owned export, import and rollback-safe restore for MEMORY//OS.

This module is deliberately built on the existing SQLite database and cognitive
EventBus.  It does not create a second store and it never deserializes arbitrary
Python objects.  Packages are deterministic JSON files inside a bounded ZIP
archive; all archive paths, hashes, schemas, relationships and conflicts are
checked before a restore transaction is allowed to run.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import re
import shutil
import time
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

PACKAGE_FORMAT = "memory-os-export"
PACKAGE_FORMAT_VERSION = 1
SCHEMA_VERSION = "9.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({
    "8.1", "8.2", "8.3", "8.3.1", "8.4.1", "8.4.2", "8.4.3", "8.4.4",
    "8.5", "9.0", "10.0",
})
# The package remains backward-compatible with the established V9 schema
# marker; this records the additive V10 maintenance payload explicitly.
V10_MAINTENANCE_VERSION = "10.0.1"

DOMAINS = (
    "memories", "events", "world", "user_model", "intents", "needs",
    "experiences", "skills", "principles", "causality", "predictions",
    "decisions", "commitments", "plans", "goals", "research",
    "explanations", "conversations", "configuration", "semantic_state",
    "maintenance",
)

# Table names come from the existing persistence schema. Keeping this allowlist
# explicit prevents an imported filename from becoming a SQL identifier.
TABLES = (
    "memories", "memory_versions", "memory_relationships", "memory_events",
    "conversations", "messages", "cognitive_events", "world_entities",
    "world_links", "intents", "predictions", "causal_links", "decisions",
    "trust_records", "memory_reputation", "policies", "sandbox_runs",
    "interventions", "arbitration_records", "memory_influences",
    "intent_transitions", "need_hypotheses", "world_changes",
    "capability_trust", "execution_traces", "continuity_items", "focus_state",
    "missions", "mission_steps", "mission_events", "mission_links",
    "observations", "background_cycles", "documents", "connectors",
    "research_sessions", "experiences", "experience_evidence",
    "experience_transitions", "knowledge_items", "knowledge_evidence",
    "knowledge_validations", "abstraction_reputation", "knowledge_usages",
    "knowledge_transitions", "explanation_snapshots", "research_sessions_v2",
    "research_sources", "research_fetches", "research_evidence",
    "research_claims", "research_conflicts", "research_world_updates",
    "meaning_compilations", "cognitive_objects", "cognitive_object_versions",
    "cognitive_relationships", "personal_state_versions",
    "cognitive_debt", "contradiction_records", "unknown_records",
    "model_error_records", "maintenance_proposals", "cognitive_health_snapshots",
    "maintenance_runs",
)

TABLE_DOMAINS: dict[str, tuple[str, ...]] = {
    "memories": ("memories",), "memory_versions": ("memories",),
    "memory_relationships": ("memories",), "memory_events": ("memories", "events"),
    "conversations": ("conversations",), "messages": ("conversations", "events"),
    "cognitive_events": ("events",),
    "world_entities": ("world", "goals", "plans", "commitments"),
    "world_links": ("world", "goals", "plans", "commitments"),
    "world_changes": ("world", "events"),
    "intents": ("intents", "user_model"), "intent_transitions": ("intents", "events"),
    "predictions": ("predictions",), "causal_links": ("causality",),
    "decisions": ("decisions", "causality"),
    "trust_records": ("user_model",), "capability_trust": ("user_model",),
    "memory_reputation": ("memories", "user_model"), "policies": ("user_model",),
    "sandbox_runs": ("events",), "interventions": ("user_model", "events"),
    "arbitration_records": ("events",), "memory_influences": ("memories", "events"),
    "need_hypotheses": ("needs",), "execution_traces": ("events",),
    "continuity_items": ("user_model", "events"), "focus_state": ("user_model",),
    "missions": ("world", "plans"), "mission_steps": ("world", "plans"),
    "mission_events": ("world", "events"), "mission_links": ("world",),
    "observations": ("experiences", "events"), "background_cycles": ("events",),
    "documents": ("experiences",), "connectors": ("configuration",),
    "research_sessions": ("research",), "experiences": ("experiences",),
    "experience_evidence": ("experiences",), "experience_transitions": ("experiences", "events"),
    "knowledge_items": ("skills", "principles"), "knowledge_evidence": ("skills", "principles"),
    "knowledge_validations": ("skills", "principles"), "abstraction_reputation": ("skills", "principles"),
    "knowledge_usages": ("skills", "principles", "events"),
    "knowledge_transitions": ("skills", "principles", "events"),
    "explanation_snapshots": ("explanations",),
    "research_sessions_v2": ("research",), "research_sources": ("research",),
    "research_fetches": ("research",), "research_evidence": ("research",),
    "research_claims": ("research",), "research_conflicts": ("research",),
    "research_world_updates": ("research", "world"),
    "meaning_compilations": ("semantic_state", "events"),
    "cognitive_objects": ("semantic_state", "user_model"),
    "cognitive_object_versions": ("semantic_state", "events"),
    "cognitive_relationships": ("semantic_state", "causality"),
    "personal_state_versions": ("semantic_state", "user_model", "events"),
    # V10 findings are derived from canonical semantic/prediction/outcome state
    # but remain portable so an audit can be reconstructed deterministically.
    "cognitive_debt": ("maintenance", "semantic_state", "events"),
    "contradiction_records": ("maintenance", "semantic_state", "events"),
    "unknown_records": ("maintenance", "semantic_state", "events"),
    "model_error_records": ("maintenance", "semantic_state", "predictions", "events"),
    "maintenance_proposals": ("maintenance", "semantic_state", "events"),
    "cognitive_health_snapshots": ("maintenance", "user_model", "events"),
    "maintenance_runs": ("maintenance", "events"),
}

# A small explicit dependency graph. A request for a high-level object brings
# along its audit/provenance rows; it never creates dangling records.
DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "memories": ("events",),
    "world": ("events",),
    "goals": ("world", "events"),
    "plans": ("world", "events"),
    "commitments": ("world", "events"),
    "experiences": ("events",),
    "skills": ("experiences", "events"),
    "principles": ("skills", "experiences", "events"),
    "research": ("events",),
    "explanations": ("events",),
    "predictions": ("causality", "events"),
    "decisions": ("causality", "events"),
    "causality": ("events",),
    "semantic_state": ("events",),
}

SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|access[_-]?key|refresh[_-]?token|authorization|password|"
    r"secret|credential|private[_-]?key|session[_-]?secret|cookie)", re.I)
INSTRUCTION_MARKERS = re.compile(
    r"(?:ignore\s+(?:all\s+)?previous|system\s+prompt|developer\s+message|"
    r"jailbreak|BEGIN\s+(?:SYSTEM|PRIVATE)|<\/?(?:system|instruction))", re.I)

DEFAULT_LIMITS = {
    "package_bytes": 50 * 1024 * 1024,
    "member_bytes": 20 * 1024 * 1024,
    "decompressed_bytes": 200 * 1024 * 1024,
    "records": 100_000,
    "files": 128,
    "relationships": 200_000,
    "nesting_depth": 12,
    "processing_seconds": 30.0,
}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=str).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_safe(value: Any, *, key: str | None = None, redactions: list[str] | None = None) -> Any:
    """Convert SQLite values to JSON without carrying credentials across."""
    if key and SENSITIVE_KEY.search(key):
        if redactions is not None:
            redactions.append(key)
        return "[REDACTED]"
    if isinstance(value, bytes):
        return {"__memory_os_bytes__": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): json_safe(v, key=str(k), redactions=redactions)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v, redactions=redactions) for v in value]
    return value


def canonical_row(row: dict[str, Any]) -> bytes:
    return canonical_bytes(row)


def _table_info(db, table: str) -> tuple[list[str], list[str]]:
    columns = [r["name"] for r in db.query(f"PRAGMA table_info({table})")]
    pks = [r["name"] for r in sorted(
        db.query(f"PRAGMA table_info({table})"), key=lambda x: x["pk"]) if r["pk"]]
    return columns, pks


def _normalize_domains(domains: Iterable[str] | None) -> list[str]:
    if not domains:
        return list(DOMAINS)
    aliases = {
        "all": DOMAINS, "memory": ("memories",), "event": ("events",),
        "world_model": ("world",), "user": ("user_model",), "needs": ("needs",),
        "evidence": ("research",), "provenance": ("research",),
        "research_evidence": ("research",), "recovery": ("events",),
        "v10": ("maintenance",), "cognitive_maintenance": ("maintenance",),
    }
    result: list[str] = []
    for raw in domains:
        key = str(raw).strip().lower().replace("-", "_").replace(" ", "_")
        values = aliases.get(key, (key,))
        for value in values:
            if value not in DOMAINS:
                raise ValueError(f"Unsupported portability domain: {raw}")
            if value not in result:
                result.append(value)
    # Expand dependencies until stable. This is returned to callers so the UI
    # can show that provenance/audit dependencies were included intentionally.
    changed = True
    while changed:
        changed = False
        for domain in tuple(result):
            for dep in DEPENDENCIES.get(domain, ()):
                if dep not in result:
                    result.append(dep)
                    changed = True
    return result


def _row_matches_domains(table: str, row: dict[str, Any], domains: set[str]) -> bool:
    available = set(TABLE_DOMAINS.get(table, ()))
    if available & domains:
        # A shared knowledge table needs row-level filtering so selecting skills
        # cannot silently import principles too.
        if table.startswith("knowledge_") or table == "abstraction_reputation":
            kind = str(row.get("kind") or row.get("item_kind") or "")
            if kind in {"skill", "principle"}:
                return kind in domains
        if table == "world_entities":
            kind = str(row.get("kind") or "").lower()
            specific = {d for d in domains if d in {"goals", "plans", "commitments"}}
            if specific and "world" not in domains:
                return kind.rstrip("s") in {d.rstrip("s") for d in specific}
        return True
    return False


def _record_depth(value: Any, depth: int = 0) -> int:
    if depth > 50:
        return depth
    if isinstance(value, dict):
        return max([depth] + [_record_depth(v, depth + 1) for v in value.values()])
    if isinstance(value, list):
        return max([depth] + [_record_depth(v, depth + 1) for v in value])
    return depth


def _safe_member(name: str) -> bool:
    p = Path(name)
    return bool(name) and not p.is_absolute() and ".." not in p.parts and "\\" not in name


def _key_for(table: str, row: dict[str, Any], pks: list[str]) -> str:
    values = {pk: row.get(pk) for pk in pks} if pks else {"id": row.get("id")}
    return json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _version(row: dict[str, Any]) -> tuple[int | None, str | None]:
    version = row.get("version")
    try:
        version_number = int(version) if version is not None else None
    except (TypeError, ValueError):
        version_number = None
    timestamp = row.get("updated_at") or row.get("created_at") or row.get("resolved_at")
    return version_number, str(timestamp) if timestamp else None


class PortabilityService:
    """Owns the package lifecycle while reusing the live Database/EventBus."""

    def __init__(self, db, bus, settings, explanation_engine=None) -> None:
        self.db = db
        self.bus = bus
        self.settings = settings
        self.explanation_engine = explanation_engine
        self.root = Path(settings.data_dir) / "portability"
        self.exports_dir = self.root / "exports"
        self.imports_dir = self.root / "imports"
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self.imports_dir.mkdir(parents=True, exist_ok=True)
        self.limits = dict(DEFAULT_LIMITS)
        self.limits.update({
            "package_bytes": int(getattr(settings, "portability_max_package_bytes", DEFAULT_LIMITS["package_bytes"])),
            "member_bytes": int(getattr(settings, "portability_max_member_bytes", DEFAULT_LIMITS["member_bytes"])),
            "decompressed_bytes": int(getattr(settings, "portability_max_decompressed_bytes", DEFAULT_LIMITS["decompressed_bytes"])),
            "records": int(getattr(settings, "portability_max_records", DEFAULT_LIMITS["records"])),
            "processing_seconds": float(getattr(settings, "portability_max_processing_seconds", DEFAULT_LIMITS["processing_seconds"])),
        })

    # ----------------------------------------------------------- common state
    def _emit(self, user_id: str, event_type: str, summary: str, *, subject_kind: str,
              subject_id: str, payload: dict[str, Any] | None = None) -> None:
        try:
            self.bus.emit(user_id, event_type, summary, subject_kind=subject_kind,
                          subject_id=subject_id, payload=payload or {})
        except Exception:  # audit failure must not turn a completed operation into a lie
            log.exception("Portability audit event failed: %s", event_type)

    def _tenant_for_user(self, user_id: str) -> str:
        row = self.db.query_one("SELECT tenant_id FROM auth_users WHERE namespace=?", (user_id,))
        return str(row["tenant_id"]) if row else "local"

    def _user_rows(self, table: str, user_id: str) -> list[dict[str, Any]]:
        columns, _ = _table_info(self.db, table)
        if not columns:
            return []
        if "user_id" in columns:
            if "tenant_id" in columns:
                rows = self.db.query(f"SELECT * FROM {table} WHERE user_id = ? AND tenant_id = ?",
                                     (user_id, self._tenant_for_user(user_id)))
            else:
                # V9 canonical tables have no tenant column; their user_id is
                # scoped through auth_users by the V10 boundary services.
                rows = self.db.query(f"SELECT * FROM {table} WHERE user_id = ?", (user_id,))
        elif table == "memory_versions":
            ids = [r["id"] for r in self.db.query("SELECT id FROM memories WHERE user_id=?", (user_id,))]
            rows = self._in_query(table, "memory_id", ids)
        elif table == "memory_relationships":
            ids = [r["id"] for r in self.db.query("SELECT id FROM memories WHERE user_id=?", (user_id,))]
            rows = self.db.query("SELECT * FROM memory_relationships WHERE source_id IN ({}) OR target_id IN ({})".format(
                ",".join("?" * len(ids)) or "NULL", ",".join("?" * len(ids)) or "NULL"), ids + ids) if ids else []
        elif table == "world_links":
            ids = [r["id"] for r in self.db.query("SELECT id FROM world_entities WHERE user_id=?", (user_id,))]
            rows = self.db.query("SELECT * FROM world_links WHERE source_id IN ({}) OR target_id IN ({})".format(
                ",".join("?" * len(ids)) or "NULL", ",".join("?" * len(ids)) or "NULL"), ids + ids) if ids else []
        elif table == "mission_links":
            ids = [r["id"] for r in self.db.query("SELECT id FROM missions WHERE user_id=?", (user_id,))]
            rows = self._in_query(table, "mission_id", ids)
        else:
            rows = []
        return [dict(row) for row in rows]

    def _in_query(self, table: str, column: str, values: list[str]) -> list[Any]:
        if not values:
            return []
        return self.db.query(f"SELECT * FROM {table} WHERE {column} IN ({','.join('?' * len(values))})", values)

    def _selected_rows(self, user_id: str, domains: list[str]) -> dict[str, list[dict[str, Any]]]:
        selected = set(domains)
        output: dict[str, list[dict[str, Any]]] = {}
        for table in TABLES:
            raw = self._user_rows(table, user_id)
            rows = [row for row in raw if _row_matches_domains(table, row, selected)]
            # Stable ordering is essential for reproducible exports and hashes.
            _, pks = _table_info(self.db, table)
            rows.sort(key=lambda row: _key_for(table, row, pks))
            output[table] = rows
        return output

    def _manifest_for(self, export_id: str, user_id: str, domains: list[str],
                      files: list[dict[str, Any]], counts: dict[str, int],
                      redactions: list[str]) -> dict[str, Any]:
        return {
            "format": PACKAGE_FORMAT,
            "format_version": PACKAGE_FORMAT_VERSION,
            "schema_version": SCHEMA_VERSION,
            "maintenance_schema_version": V10_MAINTENANCE_VERSION,
            "application": "MEMORY//OS",
            "application_release": SCHEMA_VERSION,
            "export_id": export_id,
            "user_id": user_id,
            "tenant_id": self._tenant_for_user(user_id),
            "exported_at": now(),
            "selected_domains": domains,
            "object_counts": counts,
            "files": files,
            "integrity": {"algorithm": "sha256", "manifest_verified_by": "integrity.json"},
            "ownership": {
                "user_generated": True,
                "derived_cognitive_state": True,
                "external_evidence": True,
                "system_configuration": True,
                "secrets": False,
            },
            "redactions": sorted(set(redactions)),
            "deterministic_serialization": {
                "encoding": "UTF-8", "json": "sorted keys, compact separators",
                "row_order": "primary-key order",
            },
        }

    def create_export(self, user_id: str, domains: Iterable[str] | None = None) -> dict[str, Any]:
        export_id = f"exp_{uuid.uuid4().hex[:16]}"
        selected = _normalize_domains(domains)
        self._emit(user_id, "export.started", "Started a user-owned data export.",
                   subject_kind="export", subject_id=export_id,
                   payload={"domains": selected})
        try:
            rows_by_table = self._selected_rows(user_id, selected)
            names: list[tuple[str, bytes, int, list[str]]] = []
            counts: dict[str, int] = {}
            redactions: list[str] = []
            for table in TABLES:
                records = [json_safe(row, redactions=redactions) for row in rows_by_table[table]]
                body = canonical_bytes(records)
                name = f"data/{table}.json"
                object_hashes = [sha256_bytes(canonical_row(record)) for record in records]
                names.append((name, body, len(records), object_hashes))
                counts[table] = len(records)

            relationship_rows = []
            for table in ("memory_relationships", "world_links", "mission_links", "causal_links"):
                for row in rows_by_table.get(table, []):
                    relationship_rows.append({"table": table, "record": row})
            relationship_body = canonical_bytes(relationship_rows)
            names.append(("relationships.json", relationship_body, len(relationship_rows),
                          [sha256_bytes(canonical_row(x)) for x in relationship_rows]))
            report = self._report_text(export_id, user_id, selected, counts, redactions)
            names.append(("report.md", report.encode("utf-8"), 0, []))
            # Only non-secret, relevant runtime metadata is portable. API keys,
            # tokens, credentials and local paths are deliberately absent.
            config_metadata = {
                "schema_version": SCHEMA_VERSION,
                "maintenance_schema_version": V10_MAINTENANCE_VERSION,
                "application_release": SCHEMA_VERSION,
                "model_provider": str(getattr(self.settings, "model_provider", "unknown")),
                "embedding_mode": "disabled" if getattr(self.settings, "disable_embeddings", False) else "configured",
                "portability_limits": {k: self.limits[k] for k in sorted(self.limits)},
                "secrets_included": False,
            }
            names.append(("metadata/configuration.json", canonical_bytes(config_metadata), 1,
                          [sha256_bytes(canonical_row(config_metadata))]))

            file_entries = [{"path": name, "sha256": sha256_bytes(body), "bytes": len(body),
                             "records": records, "object_hashes": object_hashes}
                            for name, body, records, object_hashes in names]
            manifest = self._manifest_for(export_id, user_id, selected, file_entries, counts, redactions)
            manifest_body = canonical_bytes(manifest)
            integrity = {
                "format": "memory-os-integrity",
                "algorithm": "sha256",
                "manifest_sha256": sha256_bytes(manifest_body),
                "files": {entry["path"]: entry["sha256"] for entry in file_entries},
            }
            integrity_body = canonical_bytes(integrity)
            package_path = self.exports_dir / f"{export_id}.zip"
            self._write_zip(package_path, {"manifest.json": manifest_body,
                                           "integrity.json": integrity_body,
                                           **{name: body for name, body, _, _ in names}})
            package_hash = sha256_bytes(package_path.read_bytes())
            self.db.execute(
                "INSERT INTO portability_exports (id,user_id,package_path,package_sha256,manifest,status,selected_domains,created_at,completed_at)"
                " VALUES (?,?,?,?,?,?,?,?,?)",
                (export_id, user_id, str(package_path), package_hash, manifest_body.decode("utf-8"),
                 "COMPLETED", json.dumps(selected), now(), now()))
            self._emit(user_id, "export.completed", "Completed a user-owned data export.",
                       subject_kind="export", subject_id=export_id,
                       payload={"package_sha256": package_hash, "object_counts": counts})
            return self.inspect_export(user_id, export_id)
        except Exception as exc:
            self._emit(user_id, "export.failed", "The data export failed before completion.",
                       subject_kind="export", subject_id=export_id,
                       payload={"error": str(exc)[:500]})
            raise

    def _write_zip(self, path: Path, files: dict[str, bytes]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name in sorted(files):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, files[name])

    def _report_text(self, export_id: str, user_id: str, domains: list[str],
                     counts: dict[str, int], redactions: list[str]) -> str:
        total = sum(counts.values())
        lines = ["# MEMORY//OS data export", "", f"- Export id: `{export_id}`",
                 f"- Owner scope: `{user_id}`", f"- Schema: `{SCHEMA_VERSION}`",
                 f"- Objects: **{total}**", f"- Domains: {', '.join(domains)}", "",
                 "This report is derived from the package manifest. The machine-readable JSON files are the restoration source of truth.",
                 "No secrets, tokens, passwords, session credentials, caches, or runtime databases are included.", ""]
        for table, count in sorted(counts.items()):
            lines.append(f"- `{table}`: {count}")
        if redactions:
            lines += ["", "Sensitive field names were redacted: " + ", ".join(sorted(set(redactions)))]
        return "\n".join(lines) + "\n"

    def _export_row(self, user_id: str, export_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM portability_exports WHERE id=? AND user_id=?", (export_id, user_id))
        return dict(row) if row else None

    def inspect_export(self, user_id: str, export_id: str) -> dict[str, Any]:
        row = self._export_row(user_id, export_id)
        if not row:
            raise KeyError("Export package not found.")
        manifest = json.loads(row["manifest"])
        verified = self.verify_export(user_id, export_id, emit_event=False)
        return {"status": row["status"], "export": {"id": row["id"], "status": row["status"],
                            "package_sha256": row["package_sha256"],
                            "created_at": row["created_at"], "completed_at": row["completed_at"],
                            "download": f"/api/portability/v1/exports/{export_id}/download"},
                "manifest": manifest, "integrity": verified}

    def list_exports(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT id,status,package_sha256,created_at,completed_at,manifest FROM portability_exports "
                             "WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, min(max(limit, 1), 200)))
        result = []
        for row in rows:
            manifest = json.loads(row["manifest"])
            result.append({"id": row["id"], "status": row["status"],
                           "package_sha256": row["package_sha256"], "created_at": row["created_at"],
                           "completed_at": row["completed_at"], "object_counts": manifest.get("object_counts", {}),
                           "integrity": self.verify_export(user_id, row["id"], emit_event=False)})
        return result

    def export_path(self, user_id: str, export_id: str) -> Path:
        row = self._export_row(user_id, export_id)
        if not row:
            raise KeyError("Export package not found.")
        path = Path(row["package_path"]).resolve()
        if path.parent != self.exports_dir.resolve() or path.name != f"{export_id}.zip":
            raise ValueError("Export path failed safety validation.")
        if not path.is_file():
            raise FileNotFoundError("Export package is not present.")
        return path

    def verify_export(self, user_id: str, export_id: str, *, emit_event: bool = True) -> dict[str, Any]:
        path = self.export_path(user_id, export_id)
        try:
            manifest, _, errors, warnings = self._read_archive(path.read_bytes(), expected_export_id=export_id)
            result = {"status": "VALID" if not errors else "INVALID", "manifest_valid": not errors,
                      "integrity_valid": not errors, "errors": errors, "warnings": warnings,
                      "package_sha256": sha256_bytes(path.read_bytes()),
                      "object_count": sum(manifest.get("object_counts", {}).values()) if manifest else 0}
        except Exception as exc:
            result = {"status": "INVALID", "manifest_valid": False, "integrity_valid": False,
                      "errors": [str(exc)], "warnings": [], "package_sha256": sha256_bytes(path.read_bytes()),
                      "object_count": 0}
        if emit_event:
            self._emit(user_id, "import.validated" if result["status"] == "VALID" else "import.rejected",
                       "Verified an export package integrity manifest.", subject_kind="export", subject_id=export_id,
                       payload=result)
        return result

    # --------------------------------------------------------------- imports
    def stage_import(self, user_id: str, package_bytes: bytes, filename: str | None = None) -> dict[str, Any]:
        if len(package_bytes) > self.limits["package_bytes"]:
            raise ValueError("Package exceeds the configured maximum size.")
        import_id = f"imp_{uuid.uuid4().hex[:16]}"
        path = self.imports_dir / f"{import_id}.zip"
        path.write_bytes(package_bytes)
        self.db.execute(
            "INSERT INTO portability_imports (id,user_id,package_path,package_sha256,filename,state,created_at) VALUES (?,?,?,?,?,?,?)",
            (import_id, user_id, str(path), sha256_bytes(package_bytes), (filename or "package.zip")[:255], "RECEIVED", now()))
        self._emit(user_id, "import.started", "Staged an untrusted package for validation.",
                   subject_kind="import", subject_id=import_id,
                   payload={"bytes": len(package_bytes), "filename": filename or "package.zip"})
        return self.inspect_import(user_id, import_id)

    def _import_row(self, user_id: str, import_id: str) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM portability_imports WHERE id=? AND user_id=?", (import_id, user_id))
        return dict(row) if row else None

    def inspect_import(self, user_id: str, import_id: str) -> dict[str, Any]:
        row = self._import_row(user_id, import_id)
        if not row:
            raise KeyError("Import session not found.")
        return {"status": row["state"],
                "import": {k: row[k] for k in ("id", "filename", "state", "package_sha256", "created_at", "validated_at") if k in row},
                "validation": json.loads(row["validation_json"]) if row.get("validation_json") else None,
                "manifest": json.loads(row["manifest"]) if row.get("manifest") else None}

    def validate_import(self, user_id: str, import_id: str) -> dict[str, Any]:
        row = self._import_row(user_id, import_id)
        if not row:
            raise KeyError("Import session not found.")
        package = Path(row["package_path"])
        errors: list[str] = []
        warnings: list[str] = []
        manifest: dict[str, Any] | None = None
        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        try:
            manifest, rows_by_table, errors, warnings = self._read_archive(package.read_bytes())
            if manifest and manifest.get("user_id") != user_id:
                errors.append("Package owner does not match the authenticated restore scope.")
            if manifest and manifest.get("maintenance_schema_version") not in {None, V10_MAINTENANCE_VERSION}:
                errors.append("Unsupported V10 maintenance payload version.")
            if (manifest and manifest.get("tenant_id") is not None and
                    manifest.get("tenant_id") != self._tenant_for_user(user_id)):
                errors.append("Package tenant does not match the authenticated restore scope.")
            if manifest and not manifest.get("selected_domains"):
                errors.append("Manifest selected_domains is empty.")
            if not errors:
                errors.extend(self._structural_errors(rows_by_table, manifest or {}, user_id))
                warnings.extend(self._untrusted_content_warnings(rows_by_table))
        except Exception as exc:
            errors.append(str(exc))
        state = "VALIDATED" if not errors else "REJECTED"
        validation = {"status": state, "valid": not errors, "errors": errors, "warnings": warnings,
                      "manifest_valid": not errors, "integrity_valid": not errors,
                      "tables": {k: len(v) for k, v in rows_by_table.items()},
                      "object_count": sum(len(v) for v in rows_by_table.values()),
                      "package_sha256": sha256_bytes(package.read_bytes())}
        self.db.execute(
            "INSERT INTO portability_validation_results (id,import_id,user_id,stage,status,result_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (f"val_{uuid.uuid4().hex[:16]}", import_id, user_id, "package_validation", state,
             json.dumps(validation, ensure_ascii=False, sort_keys=True), now()))
        self.db.execute(
            "UPDATE portability_imports SET state=?,manifest=?,validation_json=?,validated_at=? WHERE id=? AND user_id=?",
            (state, json.dumps(manifest, ensure_ascii=False, sort_keys=True) if manifest else None,
             json.dumps(validation, ensure_ascii=False, sort_keys=True), now(), import_id, user_id))
        if not errors:
            conflicts = self._analyze_conflicts(user_id, import_id, rows_by_table, manifest or {})
            validation["conflict_count"] = len(conflicts)
            self.db.execute("UPDATE portability_imports SET validation_json=? WHERE id=? AND user_id=?",
                            (json.dumps(validation, ensure_ascii=False, sort_keys=True), import_id, user_id))
            self._emit(user_id, "import.validated", "Validated an import package without touching live cognitive state.",
                       subject_kind="import", subject_id=import_id, payload=validation)
        else:
            self._emit(user_id, "import.rejected", "Rejected an import package before restore.",
                       subject_kind="import", subject_id=import_id, payload=validation)
        return self.inspect_import(user_id, import_id) | {"status": state, "valid": not errors, "validation": validation}

    def _read_archive(self, data: bytes, expected_export_id: str | None = None) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]], list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        deadline = time.monotonic() + float(self.limits["processing_seconds"])
        if len(data) > self.limits["package_bytes"]:
            raise ValueError("Package exceeds the configured maximum size.")
        if not zipfile.is_zipfile(io.BytesIO(data)):
            raise ValueError("Package is not a ZIP archive.")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > self.limits["files"]:
                raise ValueError("Package contains too many files.")
            names = [i.filename for i in infos]
            if len(names) != len(set(names)):
                raise ValueError("Package contains duplicate filenames.")
            for info in infos:
                if time.monotonic() > deadline:
                    raise ValueError("Package validation exceeded the configured processing-time limit.")
                # ZIP symlinks are never followed or restored.
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    errors.append(f"Archive symlink is not allowed: {info.filename}")
                if not _safe_member(info.filename):
                    errors.append(f"Unsafe archive filename: {info.filename!r}")
                if info.file_size > self.limits["member_bytes"]:
                    errors.append(f"Archive member exceeds size limit: {info.filename}")
                if info.file_size and info.compress_size and info.file_size / info.compress_size > 100:
                    errors.append(f"Archive compression ratio exceeds limit: {info.filename}")
                if info.file_size > self.limits["decompressed_bytes"]:
                    errors.append(f"Archive member exceeds decompressed limit: {info.filename}")
            if errors:
                raise ValueError("; ".join(errors))
            total = sum(i.file_size for i in infos)
            if total > self.limits["decompressed_bytes"]:
                raise ValueError("Package decompressed size exceeds the configured limit.")
            if "manifest.json" not in names or "integrity.json" not in names:
                raise ValueError("Package must contain manifest.json and integrity.json.")
            try:
                manifest = json.loads(archive.read("manifest.json"))
                integrity = json.loads(archive.read("integrity.json"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(f"Manifest or integrity metadata is not valid UTF-8 JSON: {exc}") from exc
            if not isinstance(manifest, dict) or not isinstance(integrity, dict):
                raise ValueError("Manifest and integrity metadata must be JSON objects.")
            if manifest.get("format") != PACKAGE_FORMAT:
                raise ValueError("Unsupported export package format.")
            if manifest.get("format_version") != PACKAGE_FORMAT_VERSION:
                raise ValueError("Unsupported export package version.")
            if manifest.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS:
                raise ValueError(f"Unsupported MEMORY//OS schema version: {manifest.get('schema_version')!r}")
            if expected_export_id and manifest.get("export_id") != expected_export_id:
                raise ValueError("Manifest export id does not match the requested package.")
            if not isinstance(manifest.get("files"), list) or not isinstance(manifest.get("user_id"), str):
                raise ValueError("Manifest is missing required structural fields.")
            if integrity.get("algorithm") != "sha256":
                raise ValueError("Unsupported integrity algorithm.")
            manifest_bytes = canonical_bytes(manifest)
            if integrity.get("manifest_sha256") != sha256_bytes(manifest_bytes):
                raise ValueError("Manifest integrity check failed.")
            listed: dict[str, dict[str, Any]] = {}
            for entry in manifest["files"]:
                if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                    errors.append("Manifest contains an invalid file entry.")
                    continue
                path = entry["path"]
                if not _safe_member(path) or path not in names or path in {"manifest.json", "integrity.json"}:
                    errors.append(f"Manifest references an unsafe or missing file: {path}")
                    continue
                listed[path] = entry
                body = archive.read(path)
                if entry.get("sha256") != sha256_bytes(body):
                    errors.append(f"Integrity hash mismatch for {path}.")
                if entry.get("bytes") != len(body):
                    errors.append(f"Byte count mismatch for {path}.")
                expected = integrity.get("files", {}).get(path)
                if expected != sha256_bytes(body):
                    errors.append(f"Integrity metadata mismatch for {path}.")
            if set(integrity.get("files", {})) != set(listed):
                errors.append("Integrity file list does not match the manifest file list.")
            extras = set(names) - set(listed) - {"manifest.json", "integrity.json"}
            if extras:
                errors.append(f"Package contains unlisted files: {sorted(extras)}")
            rows_by_table: dict[str, list[dict[str, Any]]] = {}
            record_total = 0
            for path in sorted(listed):
                if time.monotonic() > deadline:
                    raise ValueError("Package validation exceeded the configured processing-time limit.")
                if not path.startswith("data/") or not path.endswith(".json"):
                    continue
                table = path[5:-5]
                if table not in TABLES:
                    errors.append(f"Package contains an unknown data table: {table}")
                    continue
                try:
                    records = json.loads(archive.read(path))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    errors.append(f"Malformed JSON in {path}: {exc}")
                    continue
                if not isinstance(records, list):
                    errors.append(f"Data file {path} must contain a JSON array.")
                    continue
                record_total += len(records)
                if record_total > self.limits["records"]:
                    errors.append("Package contains too many records.")
                    break
                hashes = listed[path].get("object_hashes", [])
                if hashes and len(hashes) != len(records):
                    errors.append(f"Object hash count mismatch for {path}.")
                good_records: list[dict[str, Any]] = []
                columns, _ = _table_info(self.db, table)
                for index, record in enumerate(records):
                    if not isinstance(record, dict):
                        errors.append(f"Record {index} in {path} is not an object.")
                        continue
                    if _record_depth(record) > self.limits["nesting_depth"]:
                        errors.append(f"Record {index} in {path} exceeds nesting depth.")
                    unknown = set(record) - set(columns)
                    if unknown:
                        errors.append(f"Record {index} in {path} contains unknown columns: {sorted(unknown)}")
                    if hashes and index < len(hashes) and hashes[index] != sha256_bytes(canonical_row(record)):
                        errors.append(f"Object integrity hash mismatch for {path} record {index}.")
                    good_records.append(record)
                rows_by_table[table] = good_records
            return manifest, rows_by_table, errors, warnings

    def _structural_errors(self, rows_by_table: dict[str, list[dict[str, Any]]], manifest: dict[str, Any], user_id: str) -> list[str]:
        errors: list[str] = []
        counts = manifest.get("object_counts", {})
        for table, rows in rows_by_table.items():
            if table in counts and int(counts[table]) != len(rows):
                errors.append(f"Manifest count mismatch for {table}.")
            _, pks = _table_info(self.db, table)
            for row in rows:
                if not pks or any(row.get(pk) is None for pk in pks):
                    errors.append(f"Record in {table} is missing its primary-key fields.")
                if "user_id" in row and row["user_id"] != user_id:
                    errors.append(f"Record in {table} has a different user scope.")
                if ("tenant_id" in row and manifest.get("tenant_id") is not None and
                        row["tenant_id"] != manifest.get("tenant_id")):
                    errors.append(f"Record in {table} has a different tenant scope.")
        v10_tables = {"cognitive_debt", "contradiction_records", "unknown_records",
                      "model_error_records", "maintenance_proposals",
                      "cognitive_health_snapshots", "maintenance_runs"}
        if any(rows_by_table.get(table) for table in v10_tables) and not manifest.get("tenant_id"):
            errors.append("V10 maintenance rows require an explicit manifest tenant scope.")
        relationship_count = sum(len(rows_by_table.get(table, [])) for table in
                                 ("memory_relationships", "world_links", "mission_links", "causal_links"))
        if relationship_count > self.limits["relationships"]:
            errors.append("Relationship count exceeds the configured limit.")
        # Internal references must either be in this package or already exist in
        # the live owner namespace. Missing references are blocked at restore.
        memories = {str(r.get("id")) for r in rows_by_table.get("memories", [])}
        for row in rows_by_table.get("memory_versions", []):
            if str(row.get("memory_id")) not in memories and not self.db.query_one(
                    "SELECT id FROM memories WHERE id=? AND user_id=?", (row.get("memory_id"), user_id)):
                errors.append(f"memory_versions references a missing memory: {row.get('memory_id')}")
        world_ids = {str(r.get("id")) for r in rows_by_table.get("world_entities", [])}
        for row in rows_by_table.get("memory_relationships", []):
            if not ({str(row.get("source_id")), str(row.get("target_id"))} <= memories):
                errors.append("memory_relationships contains a dangling package reference.")
        for row in rows_by_table.get("world_links", []):
            if not ({str(row.get("source_id")), str(row.get("target_id"))} <= world_ids):
                errors.append("world_links contains a dangling package reference.")
        mission_ids = {str(r.get("id")) for r in rows_by_table.get("missions", [])}
        for row in rows_by_table.get("mission_links", []):
            if str(row.get("mission_id")) not in mission_ids:
                errors.append("mission_links contains a dangling mission reference.")
        knowledge_ids = {str(r.get("id")) for r in rows_by_table.get("knowledge_items", [])}
        for table in ("knowledge_evidence", "knowledge_validations", "knowledge_usages", "knowledge_transitions", "abstraction_reputation"):
            for row in rows_by_table.get(table, []):
                if str(row.get("item_id")) not in knowledge_ids:
                    errors.append(f"{table} contains a dangling learned-object reference.")

        # V10 findings are portable derived state, not free-floating JSON.
        # Every linked id must resolve inside this package or in the same
        # authenticated canonical V9 namespace. This catches duplicate,
        # dangling, and cross-tenant references before restore.
        package_ids = {str(row.get("id")) for rows in rows_by_table.values()
                       for row in rows if row.get("id") is not None}
        linked: list[tuple[str, Any]] = []
        def json_value(row: dict[str, Any], key: str, default: Any) -> Any:
            value = row.get(key)
            if isinstance(value, str):
                try:
                    return json.loads(value)
                except (TypeError, ValueError):
                    return default
            return value if value is not None else default
        for row in rows_by_table.get("cognitive_debt", []):
            linked.extend(("cognitive_debt.object_ids", x) for x in json_value(row, "object_ids_json", []))
            linked.extend(("cognitive_debt.evidence_refs", x.get("id"))
                          for x in json_value(row, "evidence_refs_json", []) if isinstance(x, dict))
        for row in rows_by_table.get("contradiction_records", []):
            linked.extend(("contradiction_records", row.get(key)) for key in ("left_object_id", "right_object_id"))
        for row in rows_by_table.get("unknown_records", []):
            linked.append(("unknown_records.question_object_id", row.get("question_object_id")))
            linked.extend(("unknown_records.relevant_object_ids", x)
                          for x in json_value(row, "relevant_object_ids_json", []))
        for row in rows_by_table.get("model_error_records", []):
            linked.extend(("model_error_records", row.get(key))
                          for key in ("prediction_id", "assumption_object_id"))
        for row in rows_by_table.get("maintenance_proposals", []):
            linked.extend(("maintenance_proposals.target_object_ids", x)
                          for x in json_value(row, "target_object_ids_json", []))
        for label, ref in linked:
            if ref in (None, "", "null"):
                continue
            ref = str(ref)
            if ref in package_ids:
                continue
            found = False
            for table in TABLES:
                columns, _ = _table_info(self.db, table)
                if "id" not in columns or "user_id" not in columns:
                    continue
                if self.db.query_one(f"SELECT 1 FROM {table} WHERE id=? AND user_id=?", (ref, user_id)):
                    found = True
                    break
            if not found:
                errors.append(f"{label} references a missing canonical object: {ref}")
        return errors

    def _untrusted_content_warnings(self, rows_by_table: dict[str, list[dict[str, Any]]]) -> list[str]:
        warnings: list[str] = []
        found = 0
        def walk(value: Any) -> None:
            nonlocal found
            if isinstance(value, str) and INSTRUCTION_MARKERS.search(value):
                found += 1
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)
        for rows in rows_by_table.values():
            for row in rows:
                walk(row)
        if found:
            warnings.append(f"Detected {found} instruction-like string(s); imported content remains inert data and will not be executed.")
        return warnings

    # ---------------------------------------------------------- conflicts
    def _existing_by_key(self, table: str, user_id: str) -> dict[str, dict[str, Any]]:
        columns, pks = _table_info(self.db, table)
        rows = self._user_rows(table, user_id)
        return {_key_for(table, row, pks): row for row in rows}

    def _analyze_conflicts(self, user_id: str, import_id: str, rows_by_table: dict[str, list[dict[str, Any]]], manifest: dict[str, Any]) -> list[dict[str, Any]]:
        self.db.execute("DELETE FROM portability_conflicts WHERE import_id=? AND user_id=?", (import_id, user_id))
        conflicts: list[dict[str, Any]] = []
        for table in TABLES:
            _, pks = _table_info(self.db, table)
            local = self._existing_by_key(table, user_id)
            for imported in rows_by_table.get(table, []):
                key = _key_for(table, imported, pks)
                current = local.get(key)
                if current is None:
                    continue
                left = dict(current)
                right = dict(imported)
                # user_id is scope, not content. Missing columns in older
                # packages are filled by the current schema for comparison.
                if left == right:
                    state, reason = "duplicate", "The same primary-keyed record already exists with identical contents."
                else:
                    lv, lt = _version(left)
                    iv, it = _version(right)
                    if lv is not None and iv is not None and lv != iv:
                        state = "imported_newer" if iv > lv else "local_newer"
                    elif lt and it and lt != it:
                        state = "imported_newer" if it > lt else "local_newer"
                    else:
                        state = "divergent"
                    reason = "The package and local record share an identity but differ in contents."
                if state != "duplicate":
                    conflict_id = f"cnf_{uuid.uuid4().hex[:16]}"
                    record = {"id": conflict_id, "import_id": import_id, "user_id": user_id,
                              "table_name": table, "object_key": key, "state": state,
                              "reason": reason, "local": left, "imported": right,
                              "resolution": None}
                    conflicts.append(record)
                    self.db.execute(
                        "INSERT INTO portability_conflicts (id,import_id,user_id,table_name,object_key,state,reason,local_record,imported_record,resolution,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (conflict_id, import_id, user_id, table, key, state, reason,
                         json.dumps(left, ensure_ascii=False, sort_keys=True),
                         json.dumps(right, ensure_ascii=False, sort_keys=True), None, now()))
                    self._emit(user_id, "restore.conflict_detected", "Detected a restore conflict that requires an explicit choice.",
                               subject_kind="import", subject_id=import_id,
                               payload={"conflict_id": conflict_id, "table": table, "state": state})
        return conflicts

    def list_conflicts(self, user_id: str, import_id: str, state: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM portability_conflicts WHERE import_id=? AND user_id=?"
        params: list[Any] = [import_id, user_id]
        if state:
            sql += " AND state=?"; params.append(state)
        sql += " ORDER BY created_at, id"
        result = []
        for row in self.db.query(sql, params):
            item = dict(row)
            item["local"] = json.loads(item.pop("local_record"))
            item["imported"] = json.loads(item.pop("imported_record"))
            result.append(item)
        return result

    # ----------------------------------------------------------- restore plan
    def _validated_rows(self, user_id: str, import_id: str) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        row = self._import_row(user_id, import_id)
        if not row:
            raise KeyError("Import session not found.")
        if row["state"] != "VALIDATED":
            raise ValueError("Import package must pass validation before a restore plan can be created.")
        manifest = json.loads(row["manifest"])
        path = Path(row["package_path"])
        fresh_manifest, rows, errors, _ = self._read_archive(path.read_bytes())
        if errors:
            raise ValueError("Package integrity no longer passes: " + "; ".join(errors[:4]))
        if fresh_manifest != manifest:
            raise ValueError("Package manifest changed after validation; validate again.")
        return manifest, rows

    def dry_run(self, user_id: str, import_id: str, domains: Iterable[str] | None = None,
                resolutions: dict[str, str] | None = None) -> dict[str, Any]:
        manifest, rows = self._validated_rows(user_id, import_id)
        selected = _normalize_domains(domains or manifest.get("selected_domains"))
        selected_set = set(selected)
        filtered = {table: [r for r in records if _row_matches_domains(table, r, selected_set)]
                    for table, records in rows.items()}
        conflicts = self.list_conflicts(user_id, import_id)
        normalized_resolutions = self._normalize_resolutions(resolutions or {})
        blockers = []
        for conflict in conflicts:
            choice = normalized_resolutions.get(conflict["id"]) or conflict.get("resolution")
            if choice not in {"skip", "keep_local", "replace"}:
                blockers.append({"conflict_id": conflict["id"], "state": conflict["state"],
                                 "reason": conflict["reason"]})
        inserts = 0
        updates = 0
        skips = 0
        conflict_keys = {(c["table_name"], c["object_key"]): c for c in conflicts}
        for table, records in filtered.items():
            _, pks = _table_info(self.db, table)
            local = self._existing_by_key(table, user_id)
            for record in records:
                conflict = conflict_keys.get((table, _key_for(table, record, pks)))
                if not conflict:
                    inserts += 1 if _key_for(table, record, pks) not in local else 0
                    continue
                choice = normalized_resolutions.get(conflict["id"]) or conflict.get("resolution")
                if choice == "replace": updates += 1
                else: skips += 1
        plan_id = f"plan_{uuid.uuid4().hex[:16]}"
        plan = {"id": plan_id, "import_id": import_id, "selected_domains": selected,
                "tables": {k: len(v) for k, v in filtered.items() if v},
                "records": sum(len(v) for v in filtered.values()),
                "inserts": inserts, "updates": updates, "skips": skips,
                "conflicts": conflicts, "blockers": blockers,
                "status": "BLOCKED" if blockers else "READY",
                "created_at": now()}
        self.db.execute(
            "INSERT INTO portability_restore_plans (id,import_id,user_id,selected_domains,plan_json,status,created_at) VALUES (?,?,?,?,?,?,?)",
            (plan_id, import_id, user_id, json.dumps(selected), json.dumps(plan, ensure_ascii=False, sort_keys=True),
             plan["status"], plan["created_at"]))
        self._emit(user_id, "restore.dry_run", "Completed a dry-run restore plan without changing live state.",
                   subject_kind="restore_plan", subject_id=plan_id,
                   payload={"status": plan["status"], "records": plan["records"], "blockers": len(blockers)})
        return {"status": plan["status"], "plan": plan,
                "explanation": self.explain(user_id, "restore_plan", plan_id, "what_changed")}

    def _normalize_resolutions(self, resolutions: dict[str, str]) -> dict[str, str]:
        allowed = {"skip", "keep_local", "replace", "use_imported", "imported"}
        normalized = {}
        for key, value in resolutions.items():
            choice = str(value).lower().strip()
            if choice not in allowed:
                raise ValueError(f"Unsupported conflict resolution: {value}")
            normalized[str(key)] = "replace" if choice in {"use_imported", "imported"} else choice
        return normalized

    def apply_restore(self, user_id: str, import_id: str, *, confirm: bool,
                      domains: Iterable[str] | None = None, resolutions: dict[str, str] | None = None) -> dict[str, Any]:
        if not confirm:
            raise ValueError("Explicit confirm=true is required; no restore was applied.")
        manifest, rows = self._validated_rows(user_id, import_id)
        selected = _normalize_domains(domains or manifest.get("selected_domains"))
        normalized = self._normalize_resolutions(resolutions or {})
        conflicts = self.list_conflicts(user_id, import_id)
        # Write choices only when the caller named a real conflict. Unknown
        # ids cannot be used to smuggle an operation into another namespace.
        valid_ids = {c["id"] for c in conflicts}
        unknown = set(normalized) - valid_ids
        if unknown:
            raise ValueError("Resolution contains unknown conflict id(s).")
        for conflict in conflicts:
            if conflict["table_name"] not in set(self._tables_for_domains(selected)):
                continue
            choice = normalized.get(conflict["id"]) or conflict.get("resolution")
            if conflict["state"] not in {"duplicate"} and choice not in {"skip", "keep_local", "replace"}:
                self._emit(user_id, "restore.failed", "Restore was blocked by unresolved conflicts.",
                           subject_kind="import", subject_id=import_id,
                           payload={"unresolved_conflict": conflict["id"]})
                raise ValueError(f"Conflict {conflict['id']} requires an explicit resolution.")
        self._emit(user_id, "restore.confirmed", "User confirmed a restore with explicit conflict policy.",
                   subject_kind="import", subject_id=import_id,
                   payload={"domains": selected, "resolutions": normalized})
        operation_id = f"rop_{uuid.uuid4().hex[:16]}"
        started = now()
        tables = self._tables_for_domains(selected)
        filtered = {table: [r for r in records if _row_matches_domains(table, r, set(selected))]
                    for table, records in rows.items() if table in tables}
        applied = 0
        skipped = 0
        try:
            conn = self.db.connect()
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Parent-first ordering avoids relationship references being
                # written before their records. Tables have no arbitrary code.
                for table in tables:
                    _, pks = _table_info(self.db, table)
                    local = self._existing_by_key(table, user_id)
                    for record in filtered.get(table, []):
                        record = dict(record)
                        if "user_id" in record:
                            record["user_id"] = user_id
                        key = _key_for(table, record, pks)
                        current = local.get(key)
                        conflict = next((c for c in conflicts if c["table_name"] == table and c["object_key"] == key), None)
                        choice = normalized.get(conflict["id"]) if conflict else None
                        if current is not None and conflict and choice != "replace":
                            skipped += 1
                            continue
                        if current is not None and conflict is None:
                            skipped += 1
                            continue
                        columns, _ = _table_info(self.db, table)
                        insert_columns = [c for c in columns if c in record]
                        values = [record[c] for c in insert_columns]
                        placeholders = ",".join("?" * len(insert_columns))
                        if current is not None and choice == "replace":
                            where = " AND ".join(f"{pk}=?" for pk in pks)
                            conn.execute(f"DELETE FROM {table} WHERE {where}", [record.get(pk) for pk in pks])
                        conn.execute(f"INSERT INTO {table} ({','.join(insert_columns)}) VALUES ({placeholders})", values)
                        applied += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            self.db.execute(
                "INSERT INTO portability_operations (id,import_id,user_id,status,selected_domains,applied_count,skipped_count,detail,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (operation_id, import_id, user_id, "APPLIED", json.dumps(selected), applied, skipped,
                 json.dumps({"package_sha256": self._import_row(user_id, import_id)["package_sha256"]}), started, now()))
            self._emit(user_id, "restore.applied", "Applied a rollback-safe restore transaction.",
                       subject_kind="restore", subject_id=operation_id,
                       payload={"import_id": import_id, "applied": applied, "skipped": skipped})
            return {"status": "APPLIED",
                    "operation": {"id": operation_id, "status": "APPLIED", "applied": applied,
                                   "skipped": skipped, "import_id": import_id},
                    "explanation": self.explain(user_id, "restore", operation_id, "what_changed")}
        except Exception as exc:
            self.db.execute(
                "INSERT INTO portability_operations (id,import_id,user_id,status,selected_domains,applied_count,skipped_count,detail,started_at,finished_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (operation_id, import_id, user_id, "ROLLED_BACK", json.dumps(selected), applied, skipped,
                 json.dumps({"error": str(exc)[:500]}), started, now()))
            self._emit(user_id, "restore.failed", "Restore failed and its transaction was rolled back.",
                       subject_kind="restore", subject_id=operation_id,
                       payload={"error": str(exc)[:500], "applied_before_failure": applied})
            self._emit(user_id, "restore.rolled_back", "Rollback completed; live state was left consistent.",
                       subject_kind="restore", subject_id=operation_id,
                       payload={"import_id": import_id})
            raise

    def _tables_for_domains(self, domains: Iterable[str]) -> list[str]:
        selected = set(domains)
        return [table for table in TABLES if set(TABLE_DOMAINS.get(table, ())) & selected]

    def restore_history(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.query("SELECT * FROM portability_operations WHERE user_id=? ORDER BY started_at DESC LIMIT ?",
                             (user_id, min(max(limit, 1), 200)))
        result = []
        for row in rows:
            item = dict(row)
            try:
                item["selected_domains"] = json.loads(item["selected_domains"])
                item["detail"] = json.loads(item["detail"]) if item.get("detail") else {}
            except (TypeError, json.JSONDecodeError):
                pass
            result.append(item)
        return result

    def explain(self, user_id: str, subject_kind: str, subject_id: str, intent: str = "why") -> dict[str, Any]:
        if self.explanation_engine:
            try:
                return self.explanation_engine.explain(user_id, subject_kind=subject_kind,
                                                       subject_id=subject_id,
                                                       explanation_type="PORTABILITY_RESTORE" if subject_kind in {"restore", "restore_plan"} else None,
                                                       query_intent=intent, persist=True)
            except Exception as exc:
                log.warning("Portability explanation unavailable: %s", exc)
        return {"explanation_type": "PORTABILITY", "subject": {"kind": subject_kind, "id": subject_id},
                "summary": "Explanation records are not available.", "evidence": [], "status": "INSUFFICIENT_EVIDENCE"}
