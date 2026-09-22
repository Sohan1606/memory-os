"""Shared helpers for the V8.5 production-trust suites.

Each test module builds an isolated Runtime with AUTH_MODE=required and a
low PBKDF2 cost (test speed; production default remains 600k iterations).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, rt
from app.runtime import Runtime


def make_secure_runtime(**overrides):
    tmp = Path(tempfile.mkdtemp(prefix="memoryos-v85-"))
    kwargs = dict(
        data_dir=tmp, sqlite_path=tmp / "m.db",
        checkpoint_path=tmp / "c.db", chroma_path=tmp / "chroma",
        auth_mode="required", auth_pbkdf2_iterations=1000,
        rate_limit_enabled=False)   # individual tests opt in
    kwargs.update(overrides)
    return Runtime(Settings(**kwargs)), tmp


def secure_client(runtime):
    app.dependency_overrides[rt] = lambda: runtime
    return TestClient(app)


def teardown(runtime, tmp):
    app.dependency_overrides.clear()
    runtime.close()
    shutil.rmtree(tmp, ignore_errors=True)


def register_and_login(client, email, password="correct-horse-battery",
                       name="Test User"):
    r = client.post("/api/auth/register", json={
        "email": email, "password": password, "display_name": name})
    assert r.status_code == 201, r.text
    user = r.json()["user"]
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    body = r.json()
    return user, body["token"], body["csrf_token"]


def bearer(token):
    return {"Authorization": f"Bearer {token}"}
