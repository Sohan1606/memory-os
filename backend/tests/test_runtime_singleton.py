"""
Regression tests for the runtime singleton.

Found during clean-install verification: `@lru_cache` does not hold a lock while
its factory runs, so several concurrent first requests each constructed a
Runtime. That raced Chroma's client initialisation and some of them collapsed to
the keyword fallback, making /api/health report KEYWORD on a fresh install.
"""

from __future__ import annotations

import concurrent.futures
import functools

import pytest

from app import runtime as runtime_module
from app.config import Settings


@pytest.fixture()
def get_runtime(tmp_path):
    """A get_runtime bound to an isolated data dir, with the singleton reset."""
    cfg = Settings(
        data_dir=tmp_path, sqlite_path=tmp_path / "memory.db",
        checkpoint_path=tmp_path / "checkpoints.db", chroma_path=tmp_path / "chroma",
    )
    runtime_module.reset_runtime()
    yield functools.partial(runtime_module.get_runtime, cfg)
    runtime_module.reset_runtime()


def test_get_runtime_is_a_singleton(get_runtime):
    assert get_runtime() is get_runtime()


def test_concurrent_first_calls_build_exactly_one_runtime(get_runtime):
    """Six simultaneous cold calls must not each construct a Runtime."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        runtimes = [f.result() for f in
                    [pool.submit(get_runtime) for _ in range(6)]]

    assert len({id(r) for r in runtimes}) == 1


def test_concurrent_startup_keeps_semantic_retrieval(get_runtime):
    """
    The real symptom of the race: retrieval silently degrading to keyword mode.
    Embeddings are genuinely available here, so health must say so.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        [f.result() for f in
         [pool.submit(get_runtime) for _ in range(6)]]

    vector = get_runtime().health()["vector"]
    assert vector["error"] is None
    assert vector["mode"] == "semantic"
    assert vector["backend"] == "chromadb"


def test_reset_runtime_releases_the_instance(get_runtime):
    first = get_runtime()
    runtime_module.reset_runtime()
    assert get_runtime() is not first
