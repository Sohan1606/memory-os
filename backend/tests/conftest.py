"""Shared fixtures. Each test module gets an isolated data directory so
Chroma/SQLite state never leaks between suites."""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.config import Settings
from app.runtime import Runtime


@pytest.fixture(scope="module")
def runtime():
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-test-"))
    cfg = Settings(
        data_dir=tmp, sqlite_path=tmp / "memory.db",
        checkpoint_path=tmp / "checkpoints.db", chroma_path=tmp / "chroma",
    )
    rt = Runtime(cfg)
    yield rt
    rt.close()
    shutil.rmtree(tmp, ignore_errors=True)


@pytest.fixture
def user(runtime):
    return runtime.settings.demo_user_id
