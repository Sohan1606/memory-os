"""Real local embedding model must produce real vectors."""


def test_embedding_vector_is_real(runtime):
    if not runtime.vectors.available:
        import pytest
        pytest.skip("Chroma/embeddings unavailable in this environment")
    vec = runtime.vectors.embed("I enjoy designing cloud infrastructure systems.")
    assert vec is not None
    assert len(vec) == 384
    assert any(abs(v) > 1e-6 for v in vec)


def test_similar_text_scores_higher_than_unrelated(runtime):
    if not runtime.vectors.available:
        import pytest
        pytest.skip("embeddings unavailable")
    hits = runtime.vectors.query("kubernetes container orchestration",
                                 runtime.settings.demo_user_id, top_k=10)
    assert hits
    top = hits[0]
    assert "kubernetes" in top.document.lower() or "cloud" in top.document.lower()
