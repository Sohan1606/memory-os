"""Deterministic V9 semantic-core, state history and EventBus coverage."""
from pathlib import Path

import pytest

from app.cognition.events import EventBus
from app.cognition.meaning import MeaningCompiler, MeaningKernel
from app.cognition.personal_state import PersonalStateService
from app.persistence.db import Database
from app.schemas.semantic import (CognitiveObjectCreate, CognitiveObjectUpdate,
                                  CognitiveType, Modality, Provenance,
                                  RelationshipCreate)


@pytest.fixture
def semantic(tmp_path: Path):
    db = Database(tmp_path / "v9.db")
    bus = EventBus(db)
    state = PersonalStateService(db, bus)
    yield db, bus, state, MeaningKernel(db, bus, state)
    db.close()


def test_all_provenance_categories_validate():
    assert {p.value for p in Provenance} == {
        "USER_STATED", "USER_INFERRED", "MODEL_HYPOTHESIS",
        "EXTERNAL_EVIDENCE", "SYSTEM_OBSERVED", "SYSTEM_DERIVED"}
    for provenance in Provenance:
        model = CognitiveObjectCreate(type=CognitiveType.CLAIM, content="x",
                                      provenance=provenance)
        assert model.provenance == provenance


def test_every_initial_cognitive_type_validates_and_persists(semantic):
    _, _, state, _ = semantic
    for kind in CognitiveType:
        obj = state.create("u", CognitiveObjectCreate(
            type=kind, content=f"Example {kind.value}", modality=Modality.ASSERTED,
            confidence=.7, provenance=Provenance.USER_STATED))
        assert obj["type"] == kind.value
        assert obj["provenance"] == "USER_STATED"
    assert state.current("u")["version"] == len(CognitiveType)


@pytest.mark.parametrize(("text", "kind", "modality"), [
    ("I am based in Pune.", "FACT", "ASSERTED"),
    ("I think remote work is better.", "BELIEF", "TENTATIVE"),
    ("Maybe the launch date is wrong.", "HYPOTHESIS", "POSSIBLE"),
    ("I prefer concise explanations.", "PREFERENCE", "ASSERTED"),
    ("My goal is to ship this year.", "GOAL", "DESIRED"),
    ("I intend to call tomorrow.", "INTENT", "INTENDED"),
    ("I commit to finishing this.", "COMMITMENT", "OBLIGATED"),
    ("What did we decide last time?", "QUESTION", "QUESTIONED"),
    ("Actually, I meant Tuesday.", "CORRECTION", "ASSERTED"),
    ("That is false and contradicts the record.", "CONTRADICTION", "NEGATED"),
    ("I predict this will take a month.", "PREDICTION", "TENTATIVE"),
])
def test_meaning_compiler_preserves_semantics(text, kind, modality):
    result = MeaningCompiler().compile(text)
    assert result.candidates[0].type.value == kind
    assert result.candidates[0].modality.value == modality
    assert result.candidates[0].provenance.value == "USER_STATED"


def test_temporal_scope_and_uncertainty_are_not_upgraded():
    result = MeaningCompiler().compile(
        "I think I should leave my current job next year.")
    candidate = result.candidates[0]
    assert candidate.type == CognitiveType.BELIEF
    assert candidate.modality == Modality.TENTATIVE
    assert candidate.confidence < 1
    assert candidate.temporal_scope.expression == "next year"
    assert candidate.temporal_scope.kind == "FUTURE"


def test_invalid_model_output_fails_closed():
    with pytest.raises(ValueError, match="Invalid semantic extraction"):
        MeaningCompiler.validate_model_output({
            "input_text": "x", "candidates": [{"type": "MADE_UP"}]})
    with pytest.raises(ValueError):
        MeaningCompiler.validate_model_output("not json")


def test_ambiguous_correction_is_not_materialized(semantic):
    db, _, _, kernel = semantic
    result = kernel.process("u", "Actually, change that.", persist=True)
    assert result["semantic"]["ambiguous"] is True
    assert result["created_objects"] == []
    assert db.query_one("SELECT status FROM meaning_compilations WHERE id=?",
                        (result["compilation_id"],))["status"] == "VALID"


def test_question_is_compiled_but_not_long_term_state(semantic):
    _, _, state, kernel = semantic
    result = kernel.process("u", "What am I missing?", persist=True)
    assert result["semantic"]["candidates"][0]["type"] == "QUESTION"
    assert state.current("u")["version"] == 0


def test_transient_feeling_is_understood_without_long_term_storage(semantic):
    _, _, state, kernel = semantic
    result = kernel.process("u", "I'm confused about what I should do next.", persist=True)
    assert result["semantic"]["candidates"][0]["type"] == "CLAIM"
    assert result["created_objects"] == []
    assert state.current("u")["version"] == 0


def test_state_versions_reconstruct_and_diff(semantic):
    _, _, state, _ = semantic
    first = state.create("u", CognitiveObjectCreate(
        type=CognitiveType.PREFERENCE, content="I prefer text.",
        provenance=Provenance.USER_STATED))
    state.update("u", first["id"], CognitiveObjectUpdate(
        confidence=.6, reason="Preference became uncertain"))
    v1 = state.reconstruct("u", version=1)
    v2 = state.reconstruct("u", version=2)
    assert v1["snapshot"]["objects"][0]["confidence"] == 1.0
    assert v2["snapshot"]["objects"][0]["confidence"] == .6
    diff = state.diff("u", 1, 2)
    assert diff["confirmed"]["changed"][0]["fields"]["confidence"] == {
        "before": 1.0, "after": .6}
    assert diff["proposed_interpretation"] is None


def test_supersession_and_relationship_are_auditable(semantic):
    _, bus, state, _ = semantic
    old = state.create("u", CognitiveObjectCreate(
        type=CognitiveType.BELIEF, content="The launch is Monday.",
        provenance=Provenance.USER_STATED))
    result = state.supersede("u", old["id"], CognitiveObjectCreate(
        type=CognitiveType.CORRECTION, content="The launch is Tuesday.",
        provenance=Provenance.USER_STATED), reason="User correction")
    assert result["previous"]["status"] == "SUPERSEDED"
    assert result["previous"]["superseded_by"] == result["replacement"]["id"]
    relationships = state.relationships("u", object_id=old["id"])
    assert relationships[0]["kind"] == "supersedes"
    types = [e.type for e in bus.recent("u", 100)]
    assert "semantic.object_superseded" in types
    assert "personal_state.version_created" in types


def test_relationship_rejects_cross_user_reference(semantic):
    _, _, state, _ = semantic
    a = state.create("a", CognitiveObjectCreate(type=CognitiveType.FACT,
                     content="A", provenance=Provenance.USER_STATED))
    b = state.create("b", CognitiveObjectCreate(type=CognitiveType.FACT,
                     content="B", provenance=Provenance.USER_STATED))
    with pytest.raises(KeyError):
        state.add_relationship("a", RelationshipCreate(
            source_id=a["id"], target_id=b["id"], kind="related_to"))
    assert state.get("a", b["id"]) is None


def test_state_isolation(semantic):
    _, _, state, _ = semantic
    obj = state.create("alice", CognitiveObjectCreate(
        type=CognitiveType.VALUE, content="Privacy matters.",
        provenance=Provenance.USER_STATED))
    assert state.get("alice", obj["id"])
    assert state.get("bob", obj["id"]) is None
    assert state.list("bob") == []
