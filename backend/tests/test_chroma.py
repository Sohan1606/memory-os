"""Full CRUD against the real Chroma collection."""
import pytest


@pytest.fixture(autouse=True)
def _requires_chroma(runtime):
    if not runtime.vectors.available:
        pytest.skip("ChromaDB unavailable")


def test_insert_query_update_delete(runtime, user):
    vs = runtime.vectors
    mid = "test_vec_1"
    vs.upsert(mid, "The user races vintage motorcycles at weekends.",
              {"user_id": user, "category": "HABIT"})
    assert vs.get(mid) is not None

    hits = vs.query("motorcycle racing hobby", user, top_k=5)
    assert any(h.memory_id == mid for h in hits)

    vs.upsert(mid, "The user restores vintage motorcycles in a garage.",
              {"user_id": user, "category": "HABIT"})
    assert "restores" in vs.get(mid)["document"]

    vs.delete(mid)
    assert vs.get(mid) is None
    assert all(h.memory_id != mid for h in vs.query("motorcycle", user, top_k=5))


def test_namespace_filter(runtime):
    vs = runtime.vectors
    vs.upsert("other_user_mem", "Completely separate person's secret preference.",
              {"user_id": "someone-else", "category": "PREFERENCE"})
    hits = vs.query("secret preference", runtime.settings.demo_user_id, top_k=10)
    assert all(h.memory_id != "other_user_mem" for h in hits)
    vs.delete("other_user_mem")
