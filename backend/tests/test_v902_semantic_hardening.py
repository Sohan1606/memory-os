"""V9.0.2 strict local-model response-boundary tests."""
from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.cognition.events import EventBus
from app.cognition.focus import FocusTracker
from app.cognition.meaning import MeaningCompiler, MeaningKernel
from app.cognition.personal_state import PersonalStateService
from app.persistence.db import Database
from app.schemas.semantic import SemanticRepresentation

TEXT = "I might leave this job next year."


def proposal(text: str = TEXT) -> dict:
    return {
        "schema_version": "9.0", "input_text": text, "source": "local_model",
        "candidates": [{
            "type": "HYPOTHESIS", "content": text, "modality": "POSSIBLE",
            "confidence": .64, "provenance": "MODEL_HYPOTHESIS",
            "source": "local_model", "temporal_scope": {
                "expression": "next year", "start": None, "end": None,
                "kind": "FUTURE"}, "status": "PROPOSED", "evidence": [],
            "relationships": [], "material": False}],
        "ambiguous": False, "ambiguity_reason": None,
        "compiler": "model-assisted"}


class Report:
    def supports(self, _cap): return True
    def reason(self, _cap): return ""


class Router:
    def report(self): return Report()


class RawModel:
    def __init__(self, output=None, error=None):
        self.output, self.error = output, error
    def invoke(self, _messages):
        if self.error: raise self.error
        return AIMessage(content=self.output)


class Runner:
    def __init__(self, output): self.output = output
    def invoke(self, _messages): return self.output


class StructuredModel(RawModel):
    def __init__(self, output):
        super().__init__("plain path must not run")
        self.output, self.calls = output, []
    def with_structured_output(self, schema, **kwargs):
        self.calls.append((schema, kwargs))
        return Runner(self.output)
    def invoke(self, _messages):
        raise AssertionError("plain invoke used despite structured-output support")


class Provider:
    def __init__(self, model=None, *, available=True, name="ollama"):
        self.model, self.available, self.name = model or RawModel(), available, name
    def status(self):
        return SimpleNamespace(name=self.name, available=self.available,
                               model="llama3.2:3b", detail="test provider")
    def chat_model(self): return self.model


class BusyLock:
    def acquire(self, blocking=False): return False
    def release(self): raise AssertionError("unacquired lock released")


def kernel(tmp_path: Path, provider: Provider, lock=None):
    db = Database(tmp_path / f"{time.time_ns()}.db")
    state = PersonalStateService(db, EventBus(db))
    result = MeaningKernel(db, EventBus(db), state, FocusTracker(db, EventBus(db)),
                           provider=provider, capability_router=Router(),
                           model_lock=lock)
    return db, result


def run(tmp_path, output=None, *, error=None, provider=None, lock=None, text=TEXT):
    provider = provider or Provider(RawModel(output, error))
    db, meaning = kernel(tmp_path, provider, lock)
    try:
        return meaning.process("u", text, persist=False)
    finally:
        db.close()


@pytest.mark.parametrize("wrapped", [
    lambda raw: raw,
    lambda raw: f"```json\n{raw}\n```",
    lambda raw: [{"type": "text", "text": raw}],
    lambda raw: [{"type": "json", "json": json.loads(raw)}],
])
def test_narrow_safe_serialization_forms_are_accepted(wrapped):
    parsed = MeaningCompiler.validate_model_output(wrapped(json.dumps(proposal())))
    assert parsed.input_text == TEXT
    assert parsed.candidates[0].temporal_scope.kind == "FUTURE"


def test_langchain_ollama_structured_output_is_preferred(tmp_path):
    parsed = SemanticRepresentation.model_validate(proposal())
    model = StructuredModel({"raw": AIMessage(content=json.dumps(proposal())),
                             "parsed": parsed, "parsing_error": None})
    result = run(tmp_path, provider=Provider(model))
    assert result["semantic_mode"] == "MODEL"
    schema, options = model.calls[0]
    assert issubclass(schema, SemanticRepresentation)
    assert options == {"method": "json_schema", "include_raw": True}


@pytest.mark.parametrize("output", [
    "{malformed",
    "Here is the semantic representation.",
    "",
    "Explanation before " + json.dumps(proposal()),
    [
        {"type": "text", "text": json.dumps(proposal())},
        {"type": "text", "text": "second block"},
    ],
])
def test_malformed_prose_empty_or_ambiguous_containers_fall_back(tmp_path, output):
    result = run(tmp_path, output)
    assert result["semantic_mode"] == "DETERMINISTIC"
    assert result["model_fallback_reason"]


def unsafe_payload(case: str) -> dict:
    value = deepcopy(proposal())
    candidate = value["candidates"][0]
    if case == "schema-invalid": candidate["modality"] = "CERTAINLY"
    elif case == "input-mismatch": value["input_text"] = "different authorized text"
    elif case == "paraphrase": candidate["content"] = "I may quit later."
    elif case == "too-many": value["candidates"] = [deepcopy(candidate) for _ in range(4)]
    elif case == "confidence": candidate["confidence"] = .86
    elif case == "fact": candidate["type"] = "FACT"
    elif case == "provenance": candidate["provenance"] = "USER_STATED"
    elif case == "missing-temporal": candidate.pop("temporal_scope")
    else: raise AssertionError(case)
    return value


@pytest.mark.parametrize("case", [
    "schema-invalid", "input-mismatch", "paraphrase", "too-many",
    "confidence", "fact", "provenance", "missing-temporal",
])
def test_schema_or_semantic_policy_violations_fall_back(tmp_path, case):
    result = run(tmp_path, json.dumps(unsafe_payload(case)))
    assert result["semantic_mode"] == "DETERMINISTIC"
    assert result["semantic"]["candidates"][0]["provenance"] == "USER_STATED"
    assert result["model_fallback_reason"]


def test_timeout_unavailable_and_busy_fall_back(tmp_path):
    timeout = run(tmp_path, error=TimeoutError("timed out"))
    unavailable = run(tmp_path, provider=Provider(available=False))
    busy = run(tmp_path, json.dumps(proposal()), lock=BusyLock())
    assert timeout["semantic_mode"] == "DETERMINISTIC"
    assert unavailable["semantic_mode"] == "DETERMINISTIC"
    assert busy["semantic_mode"] == "DETERMINISTIC"
    assert "timed out" in timeout["model_fallback_reason"]
    assert unavailable["model_fallback_reason"].startswith("NOT_CONNECTED")
    assert "busy" in busy["model_fallback_reason"]


def test_ambiguous_model_statement_remains_ambiguous_and_nonmaterial(tmp_path):
    value = proposal("I don't think this plan makes sense anymore.")
    value["candidates"][0].update({
        "type": "BELIEF", "content": value["input_text"],
        "modality": "TENTATIVE", "temporal_scope": {
            "expression": None, "start": None, "end": None,
            "kind": "UNSPECIFIED"}})
    value["ambiguous"] = True
    value["ambiguity_reason"] = "The plan reference requires authorized focus."
    result = run(tmp_path, json.dumps(value), text=value["input_text"])
    assert result["semantic_mode"] == "MODEL"
    assert result["semantic"]["ambiguous"] is True
    assert result["semantic"]["candidates"][0]["material"] is False
