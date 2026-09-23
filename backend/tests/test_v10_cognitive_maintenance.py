"""Focused V10 tests exercising the real composition-root services."""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.cognition.events import EVENT_TYPES
from app.runtime import Runtime
from app.schemas.semantic import CognitiveObjectCreate, CognitiveObjectUpdate, CognitiveType, Modality, Provenance
from app.schemas.v10 import ContradictionClass, DebtStatus, ModelErrorClass


def make_runtime():
    root = Path(tempfile.mkdtemp(prefix="memoryos-v10-"))
    return Runtime(Settings(data_dir=root, sqlite_path=root / "m.db",
                            checkpoint_path=root / "c.db", chroma_path=root / "chroma",
                            disable_embeddings=True)), root


def obj(rt, user, typ, content, *, provenance=Provenance.USER_STATED, metadata=None, modality=Modality.ASSERTED):
    return rt.cognition.personal_state.create(user, CognitiveObjectCreate(
        type=typ, content=content, provenance=provenance, metadata=metadata or {}, modality=modality))


def test_v10_schema_is_additive_and_preserves_v9_rows():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        memory = rt.memory.create(user, "V9 memory survives V10", "FACT")["memory"]
        tables = {r["name"] for r in rt.db.query("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"cognitive_debt", "contradiction_records", "unknown_records",
                "model_error_records", "maintenance_proposals",
                "cognitive_health_snapshots", "maintenance_runs"} <= tables
        assert rt.memory.get(memory["id"]).content == "V9 memory survives V10"
        assert "cognitive_model.audit_started" in EVENT_TYPES
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_reopen_v9_database_adds_v10_without_rewriting_v9_data():
    root = Path(tempfile.mkdtemp(prefix="memoryos-v10-migration-"))
    path = root / "v9.db"
    from app.persistence.db import Database
    db = Database(path)
    db.execute("INSERT INTO memories(id,user_id,content,category,created_at,updated_at) "
               "VALUES (?,?,?,?,datetime('now'),datetime('now'))",
               ("legacy-memory", "legacy-user", "legacy V9 row", "FACT"))
    for table in ("cognitive_debt", "contradiction_records", "unknown_records",
                  "model_error_records", "maintenance_proposals",
                  "cognitive_health_snapshots", "maintenance_runs"):
        db.execute(f"DROP TABLE {table}")
    db.close()
    reopened = Database(path)
    try:
        assert reopened.query_one("SELECT content FROM memories WHERE id=?", ("legacy-memory",))["content"] == "legacy V9 row"
        assert all(reopened.query_one("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                   for table in ("cognitive_debt", "maintenance_runs"))
    finally:
        reopened.close(); shutil.rmtree(root, ignore_errors=True)


def test_stale_assumption_creates_evidence_backed_debt_and_confirmed_proposal():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption = obj(rt, user, CognitiveType.ASSUMPTION, "This project depends on X.")
        evidence = obj(rt, user, CognitiveType.OBSERVATION,
                       "Verified: the project no longer depends on X.",
                       provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        result = rt.cognition.self_maintenance.audit(user, correlation_id="v10-stale")
        debt = next(d for d in result["debt"] if d["debt_type"] == "STALE_ASSUMPTION")
        assert assumption["id"] in debt["object_ids"]
        assert evidence["id"] in debt["object_ids"]
        assert debt["status"] == DebtStatus.OPEN.value
        proposal = next(p for p in result["proposals"] if p["proposal_type"] == "RETIRE_ASSUMPTION")
        assert proposal["status"] == "PROPOSED"
        assert rt.cognition.personal_state.get(user, assumption["id"])["status"] == "ACTIVE"
        applied = rt.cognition.maintenance_proposals.confirm(user, proposal["id"], reason="I confirm the evidence.")
        assert applied["status"] == "APPLIED"
        assert rt.cognition.personal_state.get(user, assumption["id"])["status"] == "RETIRED"
        assert rt.cognition.cognitive_debt.get(user, debt["id"])["status"] == "RESOLVED"
        assert rt.cognition.personal_state.get(user, evidence["id"])["status"] == "ACTIVE"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_contradiction_engine_preserves_context_and_value_evolution():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        saving = obj(rt, user, CognitiveType.VALUE, "I value saving money.")
        trip = obj(rt, user, CognitiveType.OBSERVATION, "I spent heavily on this trip.", provenance=Provenance.SYSTEM_OBSERVED)
        relation = rt.cognition.contradictions.classify(saving, trip)
        assert relation["classification"] in {ContradictionClass.TEMPORARY_EXCEPTION.value,
                                                ContradictionClass.CONTEXTUAL_TRADEOFF.value,
                                                ContradictionClass.NOT_A_CONTRADICTION.value}
        old = obj(rt, user, CognitiveType.VALUE, "Career stability is more important than exploration.")
        new = obj(rt, user, CognitiveType.VALUE,
                  "I've changed my priorities. Exploration matters more to me now.")
        evolution = rt.cognition.contradictions.classify(old, new)
        assert evolution["classification"] == ContradictionClass.VALUE_EVOLUTION.value
        assert rt.cognition.personal_state.get(user, old["id"])["content"] != rt.cognition.personal_state.get(user, new["id"])["content"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_unknown_is_explicit_when_preference_context_is_missing():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        left = obj(rt, user, CognitiveType.PREFERENCE, "I prefer tea in the morning.")
        right = obj(rt, user, CognitiveType.PREFERENCE, "I prefer coffee in the morning.")
        result = rt.cognition.self_maintenance.audit(user, correlation_id="v10-unknown")
        unknown = result["unknowns"]
        assert unknown
        assert any(left["id"] in u["relevant_object_ids"] and right["id"] in u["relevant_object_ids"] for u in unknown)
        assert all(u["status"] == "OPEN" for u in unknown)
        assert all(u["confidence"] == 0.0 for u in unknown)
        resolved = rt.cognition.unknowns.resolve(user, unknown[0]["id"], reason="The preference is context-dependent.")
        assert resolved["status"] == "RESOLVED"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_model_error_distinguishes_execution_from_user_model_error():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        prediction = rt.cognition.predictions.create(user, "The deployment will complete today", .8)
        execution = rt.cognition.model_errors.record(
            user, prediction_id=prediction["id"], expected_state=prediction["statement"],
            actual_observation="The command failed to execute; no deployment ran.", execution_error=True)
        assert execution["error_class"] == ModelErrorClass.EXECUTION_ERROR.value
        unresolved = rt.cognition.model_errors.record(
            user, expected_state="The market will rise", actual_observation="The market fell",
            confidence=.6)
        assert unresolved["error_class"] == ModelErrorClass.UNRESOLVED.value
        assert len(rt.cognition.model_errors.list(user)) == 2
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_health_is_decomposable_and_insufficient_evidence_is_honest():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        empty = rt.cognition.cognitive_health.compute(user, persist=False)
        assert empty["summary_status"] == "INSUFFICIENT_EVIDENCE"
        obj(rt, user, CognitiveType.GOAL, "Build a reliable release process.")
        health = rt.cognition.cognitive_health.compute(user, persist=True)
        assert health["summary_status"] == "INSUFFICIENT_EVIDENCE"
        assert "MODEL_STABILITY" in health["unevaluable_dimensions"]
        assert set(health["dimensions"]) == {"COHERENCE", "FRESHNESS", "EVIDENCE", "COMPLETENESS",
                                               "PREDICTIVE_TRACKING", "DECISION_CURRENCY", "MODEL_STABILITY"}
        assert all("explanation" in value and "finding_refs" in value for value in health["dimensions"].values())
        assert "person" in health["note"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_audit_run_and_events_are_correlated_and_idempotent():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        result = rt.cognition.self_maintenance.audit(user, correlation_id="v10-run")
        loaded = rt.cognition.self_maintenance.run(user, "v10-run")
        assert loaded["status"] == "COMPLETED"
        events = rt.cognition.bus.for_correlation("v10-run")
        assert events
        assert all(e.user_id == user and e.payload.get("tenant_id") == "local" for e in events)
        assert {e.type for e in events} >= {"cognitive_model.audit_started", "cognitive_model.audit_completed"}
        assert result["correlation_id"] == "v10-run"
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_v10_portability_export_contains_derived_state():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        obj(rt, user, CognitiveType.ASSUMPTION, "A portable assumption.")
        rt.cognition.self_maintenance.audit(user, correlation_id="v10-export")
        export = rt.portability.create_export(user, domains=["maintenance"])
        package = rt.portability.export_path(user, export["export"]["id"]).read_bytes()
        import zipfile, io
        with zipfile.ZipFile(io.BytesIO(package)) as archive:
            debt = json.loads(archive.read("data/cognitive_debt.json"))
            runs = json.loads(archive.read("data/maintenance_runs.json"))
        assert runs and isinstance(debt, list)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_adversarial_proposition_matrix_never_uses_shared_tokens_as_contradiction():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        def compare(left, right, *, typ=CognitiveType.PREFERENCE, left_meta=None, right_meta=None,
                    left_mod=Modality.ASSERTED, right_mod=Modality.ASSERTED):
            a = obj(rt, user, typ, left, metadata=left_meta, modality=left_mod)
            b = obj(rt, user, typ, right, metadata=right_meta, modality=right_mod)
            return rt.cognition.contradictions.classify(a, b)["classification"]

        assert compare("I like coffee", "I do not like coffee") == ContradictionClass.TRUE_CONTRADICTION.value
        assert compare("I like coffee", "I like tea") != ContradictionClass.TRUE_CONTRADICTION.value
        assert compare("I value saving money", "I spent heavily on vacation", typ=CognitiveType.VALUE) != ContradictionClass.TRUE_CONTRADICTION.value
        assert compare("I prefer working from home", "I prefer working from the team office") != ContradictionClass.TRUE_CONTRADICTION.value
        assert compare("I have changed my priorities. I value exploration now.", "I value career stability", typ=CognitiveType.VALUE) == ContradictionClass.VALUE_EVOLUTION.value
        assert compare("I like coffee", "I do not like coffee", left_mod=Modality.HYPOTHETICAL) == ContradictionClass.INSUFFICIENT_CONTEXT.value
        assert compare("I like coffee", "I do not like coffee", left_meta={"scope": "morning"}, right_meta={"scope": "evening"}) == ContradictionClass.DIFFERENT_SCOPE.value
        assert compare("I like coffee", "I do not like coffee", left_meta={"temporal_scope": {"kind": "PAST"}}, right_meta={"temporal_scope": {"kind": "FUTURE"}}) == ContradictionClass.DIFFERENT_TIME.value
        assert compare("Alice likes coffee", "Bob does not like coffee", left_meta={"subject": "alice", "predicate": "like", "object": "coffee"}, right_meta={"subject": "bob", "predicate": "like", "object": "coffee"}) != ContradictionClass.TRUE_CONTRADICTION.value
        assert compare("I like coffee", "The office is not coffee", typ=CognitiveType.CLAIM) != ContradictionClass.TRUE_CONTRADICTION.value

        old = obj(rt, user, CognitiveType.VALUE, "I value saving money.")
        corrected = obj(rt, user, CognitiveType.CORRECTION, "Correction: I do not value saving money.",
                        metadata={"corrects_object_id": old["id"]})
        assert rt.cognition.contradictions.classify(old, corrected)["classification"] == ContradictionClass.SUPERSESSION.value
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_contradiction_resolution_is_finding_only_and_proposal_is_bounded():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        left = obj(rt, user, CognitiveType.PREFERENCE, "I like coffee")
        right = obj(rt, user, CognitiveType.PREFERENCE, "I do not like coffee")
        record = rt.cognition.contradictions._record(
            user, left, right, rt.cognition.contradictions.classify(left, right), correlation_id="finding-only")
        before_left = rt.cognition.personal_state.get(user, left["id"])
        resolved = rt.cognition.contradictions.resolve(user, record["id"], correlation_id="finding-only")
        assert resolved["status"] == "RESOLVED_FINDING"
        assert rt.cognition.personal_state.get(user, left["id"])["content"] == before_left["content"]
        proposal = rt.cognition.maintenance_proposals.propose(
            user, proposal_type="RESOLVE_CONTRADICTION", target_object_ids=[record["id"]],
            current_state={"status": "OPEN"}, proposed_state={"finding_status": "RESOLVED_FINDING"},
            reason="Close the finding after review.", evidence_refs=record["evidence_refs"],
            uncertainty="Underlying preference remains unchanged.")
        # already resolved proposals remain bounded and do not replay a V9 mutation
        applied = rt.cognition.maintenance_proposals.confirm(user, proposal["id"], reason="Confirmed finding only.")
        assert applied["status"] == "APPLIED"
        assert rt.cognition.personal_state.get(user, right["id"])["content"] == right["content"]
        assert any(e.type == "contradiction.finding_resolved" for e in rt.cognition.bus.for_correlation("finding-only"))
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_v10_canonical_lookup_and_model_error_evidence_are_scoped():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        other = "other-tenant-user"
        prediction = rt.cognition.predictions.create(user, "The release ships", .8)
        with __import__("pytest").raises(KeyError):
            rt.cognition.model_errors.record(other, prediction_id=prediction["id"],
                                             expected_state="x", actual_observation="y")
        with __import__("pytest").raises(ValueError):
            rt.cognition.model_errors.record(user, expected_state="x", actual_observation="y",
                                             classification=ModelErrorClass.WORLD_MODEL_ERROR.value)
        unresolved = rt.cognition.model_errors.record(user, expected_state="x", actual_observation="y")
        assert unresolved["error_class"] == ModelErrorClass.UNRESOLVED.value
        assert rt.cognition.model_errors.get(other, unresolved["id"]) is None
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_v10_portability_validates_derived_references_and_v9_to_v10_restore():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption = obj(rt, user, CognitiveType.ASSUMPTION, "The project depends on API X.")
        evidence = obj(rt, user, CognitiveType.OBSERVATION, "The project no longer depends on API X.",
                       provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        audit = rt.cognition.self_maintenance.audit(user, correlation_id="portable-v10")
        assert audit["debt"]
        exported = rt.portability.create_export(user, domains=["maintenance"])
        staged = rt.portability.stage_import(user, rt.portability.export_path(user, exported["export"]["id"]).read_bytes())
        validated = rt.portability.validate_import(user, staged["import"]["id"])
        assert validated["validation"]["status"] == "VALIDATED"
        with __import__("zipfile").ZipFile(rt.portability.export_path(user, exported["export"]["id"])) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            debt = json.loads(archive.read("data/cognitive_debt.json"))
        assert manifest["maintenance_schema_version"] == "10.0.1"
        assert any(assumption["id"] in json.loads(item["object_ids_json"]) and evidence["id"] in json.loads(item["object_ids_json"]) for item in debt)
        assert rt.cognition.personal_state.get(user, assumption["id"])["content"] == "The project depends on API X."
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_evidence_backed_decision_debt_does_not_fabricate_unsupported_categories():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        rt.db.execute(
            "INSERT INTO decisions (id,user_id,summary,status,created_at) VALUES (?,?,?,?,datetime('now'))",
            ("decision-v10-open", user, "Choose a release strategy", "open"))
        debts = rt.cognition.cognitive_debt.detect(user, correlation_id="decision-debt")
        assert any(d["debt_type"] == "UNRESOLVED_DECISION" and "decision-v10-open" in d["object_ids"] for d in debts)
        assert not any(d["debt_type"] in {"OVERDUE_COMMITMENT", "OUTDATED_PRINCIPLE", "WEAKENED_CLAIM"} for d in debts)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_portability_restores_v10_rows_and_rejects_dangling_v10_references():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption = obj(rt, user, CognitiveType.ASSUMPTION, "The project depends on API Y.")
        evidence = obj(rt, user, CognitiveType.OBSERVATION, "The project no longer depends on API Y.",
                       provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        audit = rt.cognition.self_maintenance.audit(user, correlation_id="v10-restore")
        exported = rt.portability.create_export(user, domains=["maintenance"])
        path = rt.portability.export_path(user, exported["export"]["id"])
        staged = rt.portability.stage_import(user, path.read_bytes())
        assert rt.portability.validate_import(user, staged["import"]["id"])["validation"]["status"] == "VALIDATED"
        rt.db.execute("DELETE FROM cognitive_debt WHERE user_id=? AND tenant_id=?", (user, "local"))
        restored = rt.portability.apply_restore(user, staged["import"]["id"], confirm=True, domains=["maintenance"])
        assert restored["operation"]["status"] == "APPLIED"
        assert rt.cognition.cognitive_debt.list(user)
        assert rt.cognition.personal_state.get(user, assumption["id"])["content"] == "The project depends on API Y."

        manifest = {"tenant_id": "local", "object_counts": {}}
        errors = rt.portability._structural_errors(
            {"cognitive_debt": [{"id": "bad", "user_id": user, "tenant_id": "local",
                                 "object_ids_json": json.dumps(["missing-canonical-id"]),
                                 "evidence_refs_json": "[]"}]}, manifest, user)
        assert any("missing canonical object" in error for error in errors)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_contradiction_generalization_uses_canonical_relations_not_object_keywords():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        def relation(left, right, typ=CognitiveType.GOAL, left_metadata=None, right_metadata=None):
            a = obj(rt, user, typ, left, metadata=left_metadata)
            b = obj(rt, user, typ, right, metadata=right_metadata)
            return rt.cognition.contradictions.classify(a, b)

        flexibility = relation("I want maximum flexibility.", "I want a highly structured routine.")
        vocabulary_variant = relation("I want freedom in my schedule.", "I want a predictable daily routine.")
        assert flexibility["classification"] in {ContradictionClass.CONTEXTUAL_TRADEOFF.value,
                                                   ContradictionClass.INSUFFICIENT_CONTEXT.value}
        assert vocabulary_variant["classification"] == flexibility["classification"]
        assert "flexib" not in __import__("inspect").getsource(__import__("app.cognition.v10", fromlist=["ContradictionEngine"]).ContradictionEngine.classify)
        assert "structur" not in __import__("inspect").getsource(__import__("app.cognition.v10", fromlist=["ContradictionEngine"]).ContradictionEngine.classify)

        assert relation("I like coffee.", "I dislike coffee.", CognitiveType.PREFERENCE)["classification"] == ContradictionClass.TRUE_CONTRADICTION.value
        assert relation("I enjoy tea.", "I hate tea.", CognitiveType.PREFERENCE)["classification"] == ContradictionClass.TRUE_CONTRADICTION.value
        evolution_a = relation("My priorities changed; exploration matters more now.", "I value career stability.", CognitiveType.VALUE)
        evolution_b = relation("I have changed my mind: exploration matters more to me now.", "I value career stability.", CognitiveType.VALUE)
        assert evolution_a["classification"] == evolution_b["classification"] == ContradictionClass.VALUE_EVOLUTION.value
        exception_a = relation("I like coffee.", "I do not like coffee.", CognitiveType.PREFERENCE,
                               {"temporary_exception": True}, {"temporary_exception": True})
        exception_b = relation("I prefer coffee.", "I avoid coffee.", CognitiveType.PREFERENCE,
                               {"temporary_exception": True}, {"temporary_exception": True})
        assert exception_a["classification"] == exception_b["classification"] == ContradictionClass.TEMPORARY_EXCEPTION.value
        unrelated_exception = relation("I like coffee.", "I dislike tea.", CognitiveType.PREFERENCE,
                                       {"temporary_exception": True}, {"temporary_exception": True})
        assert unrelated_exception["classification"] != ContradictionClass.TEMPORARY_EXCEPTION.value
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_stale_assumption_requires_same_canonical_proposition_not_token_overlap():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption = obj(rt, user, CognitiveType.ASSUMPTION, "The project depends on API X.")
        unrelated = obj(rt, user, CognitiveType.OBSERVATION,
                        "The meeting about the project is not related to API X.",
                        provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        different_property = obj(rt, user, CognitiveType.OBSERVATION,
                                 "The project has a deadline in June.",
                                 provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        different_predicate = obj(rt, user, CognitiveType.OBSERVATION,
                                  "The project supports API X.",
                                  provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        different_subject = obj(rt, user, CognitiveType.OBSERVATION,
                                "The meeting does not depend on API X.",
                                provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        scoped = obj(rt, user, CognitiveType.OBSERVATION,
                     "The project no longer depends on API X.",
                     provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED,
                     metadata={"scope": "this trip"})
        timed = obj(rt, user, CognitiveType.OBSERVATION,
                    "The project no longer depends on API X.",
                    provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED,
                    metadata={"temporal_scope": {"kind": "FUTURE"}})
        reversal = obj(rt, user, CognitiveType.OBSERVATION,
                       "The project no longer depends on API X.",
                       provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        correction = obj(rt, user, CognitiveType.CORRECTION, "Correction: the project does not depend on API X.",
                         provenance=Provenance.USER_STATED,
                         metadata={"corrects_object_id": assumption["id"]})
        superseding = obj(rt, user, CognitiveType.OBSERVATION, "The project does not depend on API X.",
                          provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED,
                          metadata={"supersedes_object_id": assumption["id"]})
        debt = rt.cognition.cognitive_debt.detect(user, correlation_id="debt-adversarial")
        linked_sets = [set(item["object_ids"]) for item in debt if item["debt_type"] == "STALE_ASSUMPTION"]
        assert not any({assumption["id"], unrelated["id"]} <= ids for ids in linked_sets)
        assert not any({assumption["id"], different_property["id"]} <= ids for ids in linked_sets)
        assert not any({assumption["id"], different_predicate["id"]} <= ids for ids in linked_sets)
        assert not any({assumption["id"], different_subject["id"]} <= ids for ids in linked_sets)
        assert not any({assumption["id"], scoped["id"]} <= ids for ids in linked_sets)
        assert not any({assumption["id"], timed["id"]} <= ids for ids in linked_sets)
        assert any({assumption["id"], reversal["id"]} <= ids for ids in linked_sets)
        assert any({assumption["id"], correction["id"]} <= ids for ids in linked_sets)
        assert any({assumption["id"], superseding["id"]} <= ids for ids in linked_sets)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_health_summary_exposes_unevaluable_dimensions_without_penalizing_model_errors():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        empty = rt.cognition.cognitive_health.compute(user, persist=False)
        assert empty["summary_status"] == "INSUFFICIENT_EVIDENCE"
        sparse_object = obj(rt, user, CognitiveType.GOAL, "Build a reliable release process.")
        sparse = rt.cognition.cognitive_health.compute(user, persist=False)
        assert sparse["summary_status"] == "INSUFFICIENT_EVIDENCE"
        assert "MODEL_STABILITY" in sparse["unevaluable_dimensions"]

        # Two canonical objects make the core dimensions evaluable; optional
        # prediction/model dimensions remain explicitly partial.
        obj(rt, user, CognitiveType.OBSERVATION, "The release process is documented.",
            provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        partial = rt.cognition.cognitive_health.compute(user, persist=False)
        assert partial["summary_status"] in {"HEALTHY", "ATTENTION_REQUIRED", "DEGRADED"}
        assert partial["evidence_sufficiency"] == "PARTIAL"
        assert partial["unevaluable_dimensions"]

        predictions = []
        for index, correct in enumerate((True, True, False)):
            prediction = rt.cognition.predictions.create(user, f"Outcome {index}", .8)
            rt.cognition.predictions.evaluate(user, prediction["id"], correct, f"Observed {index}")
            predictions.append(prediction["id"])
        fully_evaluable = rt.cognition.cognitive_health.compute(user, persist=False)
        assert "MODEL_STABILITY" not in fully_evaluable["unevaluable_dimensions"]
        assert fully_evaluable["dimensions"]["MODEL_STABILITY"]["score"] == pytest.approx(2 / 3, abs=0.001)

        # A large model-error sample is retained as learning evidence and does
        # not by itself degrade the aggregate health summary.
        for index in range(8):
            rt.cognition.model_errors.record(user, expected_state=f"expected {index}",
                                             actual_observation=f"observed {index}",
                                             execution_error=True)
        with_errors = rt.cognition.cognitive_health.compute(user, persist=False)
        assert with_errors["dimensions"]["MODEL_STABILITY"]["score"] == pytest.approx(2 / 3, abs=0.001)
        assert with_errors["dimensions"]["MODEL_STABILITY"]["finding_refs"]

        # A real coherence finding is allowed to degrade coherence; this is
        # distinct from model-error count.
        for _ in range(3):
            a = obj(rt, user, CognitiveType.PREFERENCE, "I like coffee.")
            b = obj(rt, user, CognitiveType.PREFERENCE, "I do not like coffee.")
            rt.cognition.contradictions._record(user, a, b,
                                                rt.cognition.contradictions.classify(a, b),
                                                correlation_id="health-coherence")
        degraded = rt.cognition.cognitive_health.compute(user, persist=False)
        assert degraded["dimensions"]["COHERENCE"]["score"] < 0.75
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_v10_event_semantics_distinguish_resolution_dismissal_and_blocked_application():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        left = obj(rt, user, CognitiveType.PREFERENCE, "I like coffee")
        right = obj(rt, user, CognitiveType.PREFERENCE, "I do not like coffee")
        record = rt.cognition.contradictions._record(
            user, left, right, rt.cognition.contradictions.classify(left, right), correlation_id="event-resolve")
        rt.cognition.contradictions.resolve(user, record["id"], correlation_id="event-resolve")
        resolved_events = rt.cognition.bus.for_correlation("event-resolve")
        assert any(event.type == "contradiction.detected" for event in resolved_events)
        assert any(event.type == "contradiction.classified" for event in resolved_events)
        assert any(event.type == "contradiction.finding_resolved" for event in resolved_events)
        assert any(event.type == "contradiction.resolved" for event in resolved_events)
        assert all(event.payload.get("personal_state_mutated") is not True
                   for event in resolved_events if event.type in {"contradiction.finding_resolved", "contradiction.resolved"})

        other_left = obj(rt, user, CognitiveType.PREFERENCE, "I enjoy tea")
        other_right = obj(rt, user, CognitiveType.PREFERENCE, "I hate tea")
        dismissed = rt.cognition.contradictions._record(
            user, other_left, other_right, rt.cognition.contradictions.classify(other_left, other_right), correlation_id="event-dismiss")
        rt.cognition.contradictions.resolve(user, dismissed["id"], status="DISMISSED", correlation_id="event-dismiss")
        dismissed_events = rt.cognition.bus.for_correlation("event-dismiss")
        assert any(event.type == "contradiction.finding_resolved" for event in dismissed_events)
        assert not any(event.type == "contradiction.resolved" for event in dismissed_events)

        proposal = rt.cognition.maintenance_proposals.propose(
            user, proposal_type="RECONSTRUCT_DECISION", target_object_ids=[record["id"]],
            current_state={}, proposed_state={}, reason="Unsupported application boundary.",
            evidence_refs=[{"kind": "contradiction", "id": record["id"]}], uncertainty="Not configured")
        blocked = rt.cognition.maintenance_proposals.confirm(user, proposal["id"], reason="Try canonical boundary")
        assert blocked["status"] == "BLOCKED"
        proposal_events = rt.cognition.bus.for_subject("maintenance_proposal", proposal["id"], user_id=user)
        assert any(event.type == "maintenance.proposed" for event in proposal_events)
        assert any(event.type == "maintenance.confirmed" for event in proposal_events)
        assert any(event.type == "maintenance.blocked" for event in proposal_events)
        assert not any(event.type == "maintenance.applied" for event in proposal_events)
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_v10_portability_final_round_trip_preserves_all_maintenance_record_types():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        assumption = obj(rt, user, CognitiveType.ASSUMPTION, "The project depends on API Z.")
        evidence = obj(rt, user, CognitiveType.OBSERVATION, "The project no longer depends on API Z.",
                       provenance=Provenance.SYSTEM_OBSERVED, modality=Modality.OBSERVED)
        preference_a = obj(rt, user, CognitiveType.PREFERENCE, "I enjoy tea")
        preference_b = obj(rt, user, CognitiveType.PREFERENCE, "I enjoy coffee")
        prediction = rt.cognition.predictions.create(user, "The release is ready", .8)
        model_error = rt.cognition.model_errors.record(
            user, prediction_id=prediction["id"], expected_state=prediction["statement"],
            actual_observation="The release command failed to execute", execution_error=True)
        audit = rt.cognition.self_maintenance.audit(user, correlation_id="v10-final-portability")
        unknown = next(iter(audit["unknowns"]), None)
        if unknown is None:
            # The preference pair is explicitly context-dependent and should
            # produce a first-class unknown in the same audit.
            unknown = rt.cognition.unknowns.identify(
                user, what_unknown="Which preference applies?", why_it_matters="Context is missing.",
                missing_evidence=[{"kind": "preference_context", "objects": [preference_a["id"], preference_b["id"]]}],
                resolution_path="Ask the user for context.",
                relevant_object_ids=[preference_a["id"], preference_b["id"]], confidence=0.0,
                correlation_id="v10-final-portability")
        proposal = rt.cognition.maintenance_proposals.propose(
            user, proposal_type="CONFIRM_UNKNOWN", target_object_ids=[unknown["id"]],
            current_state={"status": unknown["status"]}, proposed_state={"status": "RESOLVED"},
            reason="Confirm the missing preference context.",
            evidence_refs=[{"kind": "unknown", "id": unknown["id"]}],
            uncertainty="User context is required.")
        health = rt.cognition.cognitive_health.compute(user, persist=True)
        expected_ids = {
            "cognitive_debt": {item["id"] for item in rt.cognition.cognitive_debt.list(user)},
            "contradiction_records": {item["id"] for item in rt.cognition.contradictions.list(user)},
            "unknown_records": {item["id"] for item in rt.cognition.unknowns.list(user)},
            "model_error_records": {model_error["id"]},
            "maintenance_proposals": {proposal["id"]},
            "cognitive_health_snapshots": {health["snapshot_id"]},
            "maintenance_runs": {rt.db.query_one(
                "SELECT id FROM maintenance_runs WHERE correlation_id=? AND user_id=? AND tenant_id=?",
                ("v10-final-portability", user, "local"))["id"]},
        }
        exported = rt.portability.create_export(user, domains=["maintenance"])
        package = rt.portability.export_path(user, exported["export"]["id"])
        staged = rt.portability.stage_import(user, package.read_bytes())
        validation = rt.portability.validate_import(user, staged["import"]["id"])
        assert validation["validation"]["status"] == "VALIDATED"
        import zipfile
        with zipfile.ZipFile(package) as archive:
            for table, ids in expected_ids.items():
                rows = json.loads(archive.read(f"data/{table}.json"))
                exported_ids = {str(row["id"]) for row in rows}
                assert ids <= exported_ids

        # Restore into the same owner after removing all V10 rows. Canonical
        # V9 objects remain, so reference validation proves links are retained.
        for table in expected_ids:
            rt.db.execute(f"DELETE FROM {table} WHERE user_id=? AND tenant_id=?", (user, "local"))
        restored = rt.portability.apply_restore(user, staged["import"]["id"], confirm=True,
                                                domains=["maintenance"])
        assert restored["operation"]["status"] == "APPLIED"
        assert expected_ids["cognitive_debt"] <= {item["id"] for item in rt.cognition.cognitive_debt.list(user)}
        assert expected_ids["contradiction_records"] <= {item["id"] for item in rt.cognition.contradictions.list(user)}
        assert expected_ids["unknown_records"] <= {item["id"] for item in rt.cognition.unknowns.list(user)}
        assert expected_ids["model_error_records"] <= {item["id"] for item in rt.cognition.model_errors.list(user)}
        assert expected_ids["maintenance_proposals"] <= {item["id"] for item in rt.cognition.maintenance_proposals.list(user)}
        assert rt.cognition.cognitive_health.latest(user) is not None
        assert rt.db.query_one("SELECT 1 FROM maintenance_runs WHERE correlation_id=? AND user_id=? AND tenant_id=?",
                               ("v10-final-portability", user, "local"))
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_maintenance_proposal_mutations_enforce_verified_user_and_tenant_scope():
    rt, root = make_runtime()
    try:
        tenant_a = rt.identity.create_tenant("Tenant A")
        tenant_b = rt.identity.create_tenant("Tenant B")
        account_a = rt.identity.create_user(
            email="tenant-a-v10@example.com", password="tenant-a-password-long",
            display_name="Tenant A User", tenant_id=tenant_a["id"])
        account_b = rt.identity.create_user(
            email="tenant-b-v10@example.com", password="tenant-b-password-long",
            display_name="Tenant B User", tenant_id=tenant_b["id"])
        user_a, user_b = account_a["namespace"], account_b["namespace"]

        unknown = rt.cognition.unknowns.identify(
            user_b, what_unknown="Tenant B context?", why_it_matters="A proposal needs a scoped finding.",
            missing_evidence=[], resolution_path="Ask Tenant B.", relevant_object_ids=[])
        proposal = rt.cognition.maintenance_proposals.propose(
            user_b, proposal_type="CONFIRM_UNKNOWN", target_object_ids=[unknown["id"]],
            current_state={"status": "PROPOSED"}, proposed_state={"status": "RESOLVED"},
            reason="Tenant B maintenance proposal.",
            evidence_refs=[{"kind": "unknown", "id": unknown["id"]}],
            uncertainty="Tenant B context.")
        proposal_id = proposal["id"]
        assert proposal["tenant_id"] == tenant_b["id"]

        # Direct-ID access, user substitution, expiry mutation, confirmation,
        # and application are all rejected at the verified Tenant A boundary.
        assert rt.cognition.maintenance_proposals.get(user_a, proposal_id) is None
        assert rt.cognition.maintenance_proposals.defer(
            user_a, proposal_id, until="2099-01-01T00:00:00+00:00") is None
        assert rt.cognition.maintenance_proposals.confirm(
            user_a, proposal_id, reason="Unauthorized cross-tenant attempt") is None
        raw = rt.db.query_one(
            "SELECT status, expires_at FROM maintenance_proposals "
            "WHERE id=? AND user_id=? AND tenant_id=?",
            (proposal_id, user_b, tenant_b["id"]))
        assert raw["status"] == "PROPOSED"
        assert raw["expires_at"] is None

        # The authorized same-tenant lifecycle remains functional.
        deferred = rt.cognition.maintenance_proposals.defer(
            user_b, proposal_id, until="2099-01-01T00:00:00+00:00", reason="Tenant B review")
        assert deferred["status"] == "DEFERRED"
        assert deferred["expires_at"] == "2099-01-01T00:00:00+00:00"
        applied = rt.cognition.maintenance_proposals.confirm(
            user_b, proposal_id, reason="Tenant B confirms")
        assert applied["status"] == "APPLIED"
        assert rt.db.query_one(
            "SELECT status FROM maintenance_proposals WHERE id=? AND user_id=? AND tenant_id=?",
            (proposal_id, user_b, tenant_b["id"]))["status"] == "APPLIED"
        assert rt.db.query_one(
            "SELECT 1 FROM maintenance_proposals WHERE id=? AND user_id=? AND tenant_id=?",
            (proposal_id, user_a, tenant_a["id"])) is None
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_correction_supersession_requires_the_compared_target_pair():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        value_a = obj(rt, user, CognitiveType.VALUE, "I value saving money.")
        correction_a = obj(
            rt, user, CognitiveType.CORRECTION,
            "Correction: I do not value saving money.",
            metadata={"corrects_object_id": value_a["id"]})
        unrelated = obj(rt, user, CognitiveType.PREFERENCE, "I like coffee.")
        correction_unrelated = obj(
            rt, user, CognitiveType.CORRECTION,
            "Correction: I do not like coffee.",
            metadata={"corrects_object_id": unrelated["id"]})

        assert rt.cognition.contradictions.classify(value_a, correction_a)["classification"] == ContradictionClass.SUPERSESSION.value
        assert rt.cognition.contradictions.classify(correction_a, value_a)["classification"] == ContradictionClass.SUPERSESSION.value
        assert rt.cognition.contradictions.classify(correction_a, unrelated)["classification"] != ContradictionClass.SUPERSESSION.value
        assert rt.cognition.contradictions.classify(unrelated, correction_a)["classification"] != ContradictionClass.SUPERSESSION.value
        assert rt.cognition.contradictions.classify(correction_a, correction_unrelated)["classification"] != ContradictionClass.SUPERSESSION.value
        explicit_superseder = obj(
            rt, user, CognitiveType.OBSERVATION,
            "The saving-money value is superseded.",
            metadata={"supersedes_object_id": value_a["id"]})
        assert rt.cognition.contradictions.classify(explicit_superseder, value_a)["classification"] == ContradictionClass.SUPERSESSION.value
        assert rt.cognition.contradictions.classify(explicit_superseder, unrelated)["classification"] != ContradictionClass.SUPERSESSION.value

        superseding = dict(value_a)
        superseding["superseded_by"] = correction_a["id"]
        assert rt.cognition.contradictions.classify(superseding, correction_a)["classification"] == ContradictionClass.SUPERSESSION.value

        evolved = obj(rt, user, CognitiveType.VALUE, "My priorities changed; exploration matters more now.")
        stable = obj(rt, user, CognitiveType.VALUE, "I value career stability.")
        assert rt.cognition.contradictions.classify(evolved, stable)["classification"] == ContradictionClass.VALUE_EVOLUTION.value
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)


def test_predictive_tracking_excludes_expired_and_cancelled_without_outcomes():
    rt, root = make_runtime()
    try:
        user = rt.settings.demo_user_id
        observed = rt.cognition.predictions.create(user, "Observed prediction", .8)
        rt.cognition.predictions.evaluate(user, observed["id"], True, "Observed outcome")
        expired = rt.cognition.predictions.create(user, "Expired prediction", .8)
        cancelled = rt.cognition.predictions.create(user, "Cancelled prediction", .8)
        rt.db.execute("UPDATE predictions SET status='expired' WHERE id=? AND user_id=?", (expired["id"], user))
        rt.db.execute("UPDATE predictions SET status='cancelled' WHERE id=? AND user_id=?", (cancelled["id"], user))

        health = rt.cognition.cognitive_health.compute(user, persist=False)
        tracking = health["dimensions"]["PREDICTIVE_TRACKING"]
        assert tracking["score"] == pytest.approx(1 / 3, abs=0.001)
        assert "1 of 3" in tracking["explanation"]
        assert "expired and cancelled" in tracking["explanation"]
        assert expired["id"] in tracking["finding_refs"]
        assert cancelled["id"] in tracking["finding_refs"]
    finally:
        rt.close(); shutil.rmtree(root, ignore_errors=True)
