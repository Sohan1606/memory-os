"""V8.4.4 data portability, validation, conflict and restore safety tests."""
from __future__ import annotations

import io
import json
import tempfile
import zipfile
from pathlib import Path

import pytest

from app.cognition.events import EventBus
from app.config import Settings
from app.persistence.db import Database, SCHEMA, SCHEMA_V83, SCHEMA_V841, SCHEMA_V842, SCHEMA_V843
from app.portability import PortabilityService, canonical_bytes


@pytest.fixture
def service():
    root = Path(tempfile.mkdtemp(prefix="memoryos-v844-"))
    cfg = Settings(data_dir=root, sqlite_path=root / "memory.db",
                   checkpoint_path=root / "checkpoints.db", chroma_path=root / "chroma",
                   disable_embeddings=True)
    db = Database(cfg.sqlite_path)
    svc = PortabilityService(db, EventBus(db), cfg)
    user = "portability-test-user"
    yield svc, db, user
    db.close()


def _memory(db, user, content="original", updated="2026-01-01T00:00:00+00:00"):
    db.execute(
        "INSERT INTO memories(id,user_id,content,category,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("memory-1", user, content, "CONTEXT", "2026-01-01T00:00:00+00:00", updated),
    )


def _roundtrip(svc, user):
    export = svc.create_export(user)
    path = svc.export_path(user, export["export"]["id"])
    staged = svc.stage_import(user, path.read_bytes(), "export.zip")
    import_id = staged["import"]["id"]
    validation = svc.validate_import(user, import_id)
    return export, path, import_id, validation


def test_export_is_deterministic_json_hashed_and_human_readable(service):
    svc, db, user = service
    _memory(db, user)
    result, path, _, _ = _roundtrip(svc, user)
    assert result["integrity"]["status"] == "VALID"
    with zipfile.ZipFile(path) as archive:
        assert "manifest.json" in archive.namelist()
        assert "integrity.json" in archive.namelist()
        assert "report.md" in archive.namelist()
        records = json.loads(archive.read("data/memories.json"))
        assert records[0]["content"] == "original"
        assert archive.read("data/memories.json") == canonical_bytes(records)
    assert svc.verify_export(user, result["export"]["id"])["status"] == "VALID"


def test_corruption_and_modified_manifest_are_rejected_without_live_change(service):
    svc, db, user = service
    _memory(db, user)
    result = svc.create_export(user)
    original = svc.export_path(user, result["export"]["id"]).read_bytes()
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        corrupt_files = {name: source.read(name) for name in source.namelist()}
    corrupt_files["data/memories.json"] += b" "
    corrupt_buffer = io.BytesIO()
    with zipfile.ZipFile(corrupt_buffer, "w") as target:
        for name, body in corrupt_files.items():
            target.writestr(name, body)
    corrupt_staged = svc.stage_import(user, corrupt_buffer.getvalue())
    corrupt_result = svc.validate_import(user, corrupt_staged["import"]["id"])
    assert corrupt_result["validation"]["status"] == "REJECTED"

    staged = svc.stage_import(user, original)
    path = Path(svc._import_row(user, staged["import"]["id"])["package_path"])
    with zipfile.ZipFile(io.BytesIO(original)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    manifest = json.loads(files["manifest.json"])
    manifest["schema_version"] = "8.4.4"
    manifest["selected_domains"] = ["memories"]
    files["manifest.json"] = canonical_bytes(manifest)
    with zipfile.ZipFile(path, "w") as target:
        for name, body in files.items():
            target.writestr(name, body)
    rejected = svc.validate_import(user, staged["import"]["id"])
    assert rejected["validation"]["status"] == "REJECTED"
    assert db.query_one("SELECT COUNT(*) AS n FROM memories WHERE user_id=?", (user,))["n"] == 1


def test_same_id_different_contents_is_explicit_conflict_and_no_silent_overwrite(service):
    svc, db, user = service
    _memory(db, user, "local")
    result = svc.create_export(user)
    path = svc.export_path(user, result["export"]["id"])
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as source:
        files = {name: source.read(name) for name in source.namelist()}
    rows = json.loads(files["data/memories.json"])
    rows[0]["content"] = "imported divergent content"
    files["data/memories.json"] = canonical_bytes(rows)
    # Rebuild the package metadata exactly as a package producer would.
    manifest = json.loads(files["manifest.json"])
    for entry in manifest["files"]:
        if entry["path"] == "data/memories.json":
            entry["sha256"] = __import__("hashlib").sha256(files[entry["path"]]).hexdigest()
            entry["bytes"] = len(files[entry["path"]])
            entry["object_hashes"] = [__import__("hashlib").sha256(canonical_bytes(rows[0])).hexdigest()]
    files["manifest.json"] = canonical_bytes(manifest)
    integrity = json.loads(files["integrity.json"])
    integrity["manifest_sha256"] = __import__("hashlib").sha256(files["manifest.json"]).hexdigest()
    integrity["files"]["data/memories.json"] = __import__("hashlib").sha256(files["data/memories.json"]).hexdigest()
    files["integrity.json"] = canonical_bytes(integrity)
    modified = io.BytesIO()
    with zipfile.ZipFile(modified, "w") as target:
        for name, body in files.items():
            target.writestr(name, body)
    staged = svc.stage_import(user, modified.getvalue())
    svc.validate_import(user, staged["import"]["id"])
    conflicts = svc.list_conflicts(user, staged["import"]["id"])
    assert any(c["state"] == "divergent" for c in conflicts)
    with pytest.raises(ValueError, match="requires an explicit"):
        svc.apply_restore(user, staged["import"]["id"], confirm=True)
    assert db.query_one("SELECT content FROM memories WHERE id='memory-1'")["content"] == "local"


def test_restore_is_transactional_selective_and_repeatable(service):
    svc, db, user = service
    _memory(db, user)
    export, path, import_id, validation = _roundtrip(svc, user)
    assert validation["validation"]["status"] == "VALIDATED"
    db.execute("DELETE FROM memories WHERE id='memory-1'")
    plan = svc.dry_run(user, import_id, domains=["memories"])["plan"]
    assert plan["status"] == "READY"
    applied = svc.apply_restore(user, import_id, confirm=True, domains=["memories"])
    assert applied["operation"]["status"] == "APPLIED"
    assert db.query_one("SELECT content FROM memories WHERE id='memory-1'")
    repeat = svc.apply_restore(user, import_id, confirm=True, domains=["memories"])
    assert repeat["operation"]["applied"] == 0


def test_user_scope_and_unsafe_archive_are_rejected(service):
    svc, db, user = service
    _memory(db, user)
    result = svc.create_export(user)
    path = svc.export_path(user, result["export"]["id"])
    staged = svc.stage_import("another-user", path.read_bytes())
    rejected = svc.validate_import("another-user", staged["import"]["id"])
    assert rejected["validation"]["status"] == "REJECTED"

    malicious = io.BytesIO()
    with zipfile.ZipFile(malicious, "w") as archive:
        archive.writestr("../manifest.json", b"{}")
    staged_bad = svc.stage_import(user, malicious.getvalue())
    rejected_bad = svc.validate_import(user, staged_bad["import"]["id"])
    assert rejected_bad["validation"]["status"] == "REJECTED"


def test_additive_schema_opens_pre_v844_database_without_data_loss(tmp_path):
    path = tmp_path / "legacy.sqlite"
    import sqlite3
    conn = sqlite3.connect(path)
    for schema in (SCHEMA, SCHEMA_V83, SCHEMA_V841, SCHEMA_V842, SCHEMA_V843):
        conn.executescript(schema)
    conn.execute(
        "INSERT INTO memories(id,user_id,content,category,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        ("legacy-memory", "legacy-user", "survives", "CONTEXT", "2026-01-01", "2026-01-01"),
    )
    conn.commit(); conn.close()
    db = Database(path)
    assert db.query_one("SELECT content FROM memories WHERE id='legacy-memory'")["content"] == "survives"
    assert db.query_one("SELECT name FROM sqlite_master WHERE name='portability_exports'")
    db.close()


def test_required_portability_events_are_on_existing_event_bus(service):
    svc, db, user = service
    _memory(db, user)
    _, _, import_id, _ = _roundtrip(svc, user)
    svc.dry_run(user, import_id)
    types = {event.type for event in svc.bus.recent(user, 100)}
    assert {"export.started", "export.completed", "import.started", "import.validated", "restore.dry_run"} <= types
