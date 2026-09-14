"""Model-assisted extraction: parsing, validation and honest fallback."""

import pytest

from app.cognition.llm_extract import (LLMExtractor, parse_payload, validate,
                                       ENTITY_KINDS)


def test_parse_payload_handles_markdown_fences_and_prose():
    raw = 'Sure!\n```json\n{"intent": {"goal": "ship v2"}}\n```'
    assert parse_payload(raw)["intent"]["goal"] == "ship v2"


def test_parse_payload_extracts_embedded_object_with_nested_braces():
    raw = 'Here: {"a": {"b": "}"}, "c": 1} trailing words'
    assert parse_payload(raw)["c"] == 1


def test_parse_payload_rejects_output_without_json():
    with pytest.raises(ValueError):
        parse_payload("I cannot help with that.")


def test_validate_drops_invalid_entity_kinds():
    payload = {"entities": [{"kind": "project", "name": "billing migration"},
                            {"kind": "spaceship", "name": "enterprise"}]}
    result = validate(payload, "migrate billing")
    assert [e["kind"] for e in result.entities] == ["project"]
    assert all(e["kind"] in ENTITY_KINDS for e in result.entities)


def test_validate_drops_hallucinated_memories_unrelated_to_utterance():
    payload = {"memories": [
        {"content": "Prefers Stripe for billing", "type": "preference"},
        {"content": "Owns seventeen alpacas in Peru", "type": "personal"}]}
    result = validate(payload, "I want to use Stripe for billing")
    assert [m["content"] for m in result.memories] == ["Prefers Stripe for billing"]


def test_validate_clamps_out_of_range_scores():
    payload = {"memories": [{"content": "Uses Stripe daily", "type": "preference",
                             "importance": 9.5, "confidence": -3}]}
    memory = validate(payload, "I use Stripe daily").memories[0]
    assert 0.0 <= memory["importance"] <= 1.0
    assert 0.0 <= memory["confidence"] <= 1.0


def test_validate_rejects_unknown_need_type():
    result = validate({"need": {"type": "telepathy", "confidence": 1}}, "hello")
    assert result.need is None


def test_validate_ignores_null_goal_strings():
    result = validate({"intent": {"goal": "null", "confidence": 0.9}}, "hi")
    assert result.intent is None


def test_extractor_reports_not_configured_without_llm(runtime):
    """The demo provider must never be presented as real understanding."""
    extractor = LLMExtractor(runtime.provider)
    if runtime.provider.status().name == "demo":
        assert extractor.available is False
        assert extractor.status()["state"] == "NOT CONFIGURED"
        result = extractor.extract("anything")
        assert result.available is False
        assert result.source == "none"
