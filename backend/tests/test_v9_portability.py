"""V9 semantic-state export, validation and restore round trip."""
import shutil
import tempfile
from pathlib import Path

from app.config import Settings
from app.runtime import Runtime
from app.schemas.semantic import CognitiveObjectCreate, CognitiveType, Provenance


def test_export_validate_restore_preserves_v9_provenance():
    root = Path(tempfile.mkdtemp(prefix="memoryos-v9-portability-"))
    runtime = Runtime(Settings(
        data_dir=root, sqlite_path=root / "m.db", checkpoint_path=root / "c.db",
        chroma_path=root / "chroma", disable_embeddings=True))
    try:
        user = runtime.settings.demo_user_id
        obj = runtime.cognition.personal_state.create(user, CognitiveObjectCreate(
            type=CognitiveType.VALUE, content="Evidence before confidence.",
            provenance=Provenance.USER_STATED, confidence=.91))
        exported = runtime.portability.create_export(user, domains=["semantic_state"])
        package = runtime.portability.export_path(
            user, exported["export"]["id"]).read_bytes()
        staged = runtime.portability.stage_import(user, package, "v9.zip")
        import_id = staged["import"]["id"]
        validation = runtime.portability.validate_import(user, import_id)
        assert validation["validation"]["status"] == "VALIDATED"

        # Simulate loss of the semantic domain only. The package must restore
        # canonical objects, versions and provenance without touching V8 state.
        for table in ("cognitive_relationships", "cognitive_object_versions",
                      "personal_state_versions", "cognitive_objects",
                      "meaning_compilations"):
            runtime.db.execute(f"DELETE FROM {table} WHERE user_id=?", (user,))
        plan = runtime.portability.dry_run(
            user, import_id, domains=["semantic_state"])["plan"]
        assert plan["status"] == "READY"
        restored = runtime.portability.apply_restore(
            user, import_id, confirm=True, domains=["semantic_state"])
        assert restored["operation"]["status"] == "APPLIED"
        loaded = runtime.cognition.personal_state.get(user, obj["id"])
        assert loaded["provenance"] == "USER_STATED"
        assert loaded["confidence"] == .91
        assert runtime.cognition.personal_state.current(user)["version"] >= 1
    finally:
        runtime.close()
        shutil.rmtree(root, ignore_errors=True)
