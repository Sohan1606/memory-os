"""Semantic retrieval must work without literal keyword overlap."""
import pytest


def test_semantic_match_without_keyword_overlap(runtime, user):
    if runtime.vectors.mode != "semantic":
        pytest.skip("running in keyword fallback mode")
    runtime.memory.create(user, "I enjoy designing cloud infrastructure systems.",
                          category="GOAL", allow_duplicate=True)
    results = runtime.memory.search(user, "What technology areas interest me?")
    assert results, "semantic retrieval returned nothing"
    contents = " ".join(r.memory.content.lower() for r in results)
    assert any(w in contents for w in ("cloud", "kubernetes", "devops", "embedding",
                                       "infrastructure", "python"))


def test_category_intent_ranks_projects_first(runtime, user):
    results = runtime.memory.search(user, "What do you remember about my projects?")
    assert results
    assert results[0].memory.category == "PROJECT"
    assert any("Category intent" in r for r in results[0].reasons)


def test_no_strong_match_is_honest(runtime, user):
    results = runtime.memory.search(user, "zqxwv plutonium submarine recipe")
    assert results == []


def test_empty_query_returns_nothing(runtime, user):
    assert runtime.memory.search(user, "   ") == []


def test_every_result_explains_itself(runtime, user):
    for r in runtime.memory.search(user, "how should you explain things to me?"):
        assert r.reasons
        assert r.strength in {"strong", "weak"}
