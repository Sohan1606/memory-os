"""V8.5 SECURITY: rate limits, CSRF, oversized/malformed input, secret
leakage, stack-trace leakage, production config gate, secret scanning."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from app.config import Settings
from app.security.observability import redact

from conftest_v85 import (bearer, make_secure_runtime, register_and_login,
                          secure_client, teardown)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ------------------------------------------------------------- rate limiting
@pytest.fixture()
def limited_env():
    runtime, tmp = make_secure_runtime(
        rate_limit_enabled=True, rate_limit_auth_per_minute=30,
        rate_limit_api_per_minute=5, rate_limit_portability_per_minute=2)
    client = secure_client(runtime)
    yield runtime, client
    teardown(runtime, tmp)


@pytest.fixture()
def auth_limited_env():
    runtime, tmp = make_secure_runtime(
        rate_limit_enabled=True, rate_limit_auth_per_minute=3)
    client = secure_client(runtime)
    yield runtime, client
    teardown(runtime, tmp)


def test_auth_rate_limit(auth_limited_env):
    runtime, client = auth_limited_env
    for i in range(3):
        client.post("/api/auth/login", json={
            "email": "nobody@rl.test", "password": "wrong-password-1"})
    r = client.post("/api/auth/login", json={
        "email": "nobody@rl.test", "password": "wrong-password-1"})
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "RATE_LIMITED"
    assert r.headers.get("Retry-After") == "60"
    # The violation is observable in metrics AND on the audit bus.
    assert runtime.metrics.count("security.rate_limited_total") >= 1
    events = runtime.cognition.bus.recent(
        runtime.settings.demo_user_id, types=["security.rate_limited"])
    assert events


def test_api_rate_limit_per_principal(limited_env):
    runtime, client = limited_env
    _u, token, _ = register_and_login(client, "rl@rl.test")
    statuses = [client.get("/api/memories", headers=bearer(token)).status_code
                for _ in range(7)]
    assert 429 in statuses
    # Another principal is NOT affected by the first one's bucket.
    _u2, token2, _ = register_and_login(client, "rl2@rl.test")
    assert client.get("/api/memories", headers=bearer(token2)).status_code == 200


def test_portability_rate_limit(limited_env):
    runtime, client = limited_env
    _u, token, _ = register_and_login(client, "rlp@rl.test")
    statuses = [client.get("/api/portability/v1/exports",
                           headers=bearer(token)).status_code for _ in range(4)]
    assert 429 in statuses


# --------------------------------------------------------------------- CSRF
@pytest.fixture(scope="module")
def env():
    runtime, tmp = make_secure_runtime()
    client = secure_client(runtime)
    yield runtime, client
    teardown(runtime, tmp)


def test_cookie_mutation_requires_csrf_header(env):
    runtime, client = env
    register_and_login(client, "csrf@sec.test")
    login = client.post("/api/auth/login", json={
        "email": "csrf@sec.test", "password": "correct-horse-battery"})
    csrf = login.json()["csrf_token"]
    # Cookie is now set on the client. A mutation WITHOUT the header fails...
    r = client.post("/api/memories", json={
        "content": "csrf probe memory", "category": "FACT"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "CSRF_REJECTED"
    # ...with the header it succeeds...
    r = client.post("/api/memories", json={
        "content": "csrf pass memory", "category": "FACT"},
        headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201
    # ...and a WRONG header fails.
    r = client.post("/api/memories", json={
        "content": "csrf wrong memory", "category": "FACT"},
        headers={"X-CSRF-Token": "forged-token"})
    assert r.status_code == 403
    # GETs never need CSRF.
    assert client.get("/api/memories").status_code == 200
    client.cookies.clear()


def test_bearer_requests_are_csrf_exempt(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "bearer@sec.test")
    client.cookies.clear()
    r = client.post("/api/memories", headers=bearer(token), json={
        "content": "bearer memory without csrf", "category": "FACT"})
    assert r.status_code == 201
    client.cookies.clear()


# ---------------------------------------------------------------- input hardening
def test_oversized_request_rejected(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "big@sec.test")
    client.cookies.clear()
    r = client.post("/api/memories", headers={
        **bearer(token), "Content-Length": str(10**9),
        "Content-Type": "application/json"})
    assert r.status_code == 413
    assert runtime.metrics.count("security.oversized_requests") >= 1


def test_malformed_input_rejected_cleanly(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "mal@sec.test")
    client.cookies.clear()
    # Bad JSON body → 422, structured error, no stack trace.
    r = client.post("/api/memories", headers={
        **bearer(token), "Content-Type": "application/json"},
        content=b"{not-json")
    assert r.status_code == 422
    assert "Traceback" not in r.text
    # Out-of-range parameters.
    r = client.post("/api/memories", headers=bearer(token), json={
        "content": "x", "category": "FACT", "importance": 99})
    assert r.status_code == 422


def test_errors_never_leak_stack_traces_or_paths(env):
    runtime, client = env
    _u, token, _ = register_and_login(client, "leak@sec.test")
    client.cookies.clear()
    for path in ("/api/memories/does-not-exist",
                 "/api/portability/v1/exports/nope",
                 "/api/cognition/turn/turn_ffffffffffff"):
        r = client.get(path, headers=bearer(token))
        assert r.status_code == 404
        assert "Traceback" not in r.text
        assert ".py" not in r.text
        assert "/home/" not in r.text and "/app/" not in r.text
        # Every error carries the correlation id for internal diagnosis.
        assert r.json().get("request_id") or r.json()["error"].get("request_id")


def test_login_response_never_contains_password_or_hash(env):
    runtime, client = env
    register_and_login(client, "nopw@sec.test")
    r = client.post("/api/auth/login", json={
        "email": "nopw@sec.test", "password": "correct-horse-battery"})
    assert "correct-horse-battery" not in r.text
    assert "pbkdf2" not in r.text
    client.cookies.clear()


# ------------------------------------------------------------------ redaction
def test_redaction_masks_secret_shapes():
    # "example" keeps this fixture string out of the static scan's findings.
    assert "[REDACTED]" in redact("api_key=sk-examplekeyexamplekey12")
    assert "hunter2secret" not in redact("password: hunter2secret")
    assert "Bearer abcdef123456" not in redact("Authorization: Bearer abcdef123456")
    # Ordinary text is untouched.
    assert redact("the weather is fine") == "the weather is fine"


# ------------------------------------------------------------- secret scanning
_SECRET_RES = (
    re.compile(r"sk-[A-Za-z0-9]{20,}"),                       # OpenAI-style
    re.compile(r"AKIA[0-9A-Z]{16}"),                          # AWS access key
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)(password|secret|api_key)\s*=\s*['\"][^'\"]{12,}['\"]\s*$",
               re.MULTILINE),
)
_SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".md",
                  ".yml", ".yaml", ".toml", ".ini", ".env", ".example"}
_SKIP_PARTS = {".git", "node_modules", "__pycache__", ".next",
               "data", ".data", "chroma", "site-packages"}


def _skip_dir(part: str) -> bool:
    # Any virtualenv-like directory, regardless of its exact name.
    return (part in _SKIP_PARTS or part.startswith(".venv")
            or part.startswith("venv") or part.startswith("env"))


def _scannable_files():
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(_skip_dir(part) for part in path.parts):
            continue
        if path.suffix.lower() in _SCAN_SUFFIXES or path.name == ".env.example":
            yield path


def test_static_secret_scan_of_repository():
    findings = []
    for path in _scannable_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pattern in _SECRET_RES:
            for m in pattern.finditer(text):
                snippet = m.group(0)
                # Allow obvious placeholders/docs.
                if any(w in snippet.lower() for w in
                       ("example", "placeholder", "your-", "changeme", "redacted")):
                    continue
                findings.append(f"{path.relative_to(REPO_ROOT)}: {snippet[:40]}")
    assert not findings, "Potential secrets committed:\n" + "\n".join(findings)


def test_env_example_contains_no_values():
    text = (REPO_ROOT / ".env.example").read_text()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        if any(s in key.upper() for s in ("KEY", "SECRET", "PASSWORD", "TOKEN")):
            assert value.strip() in ("", '""', "''"), f"{key} has a value in .env.example"


# ------------------------------------------------------- production config gate
def test_production_flag_requires_safe_configuration(tmp_path):
    cfg = Settings(
        data_dir=tmp_path, sqlite_path=tmp_path / "m.db",
        checkpoint_path=tmp_path / "c.db", chroma_path=tmp_path / "chroma",
        production=True, auth_mode="disabled")
    problems = cfg.validate_production()
    assert any("AUTH_MODE" in p for p in problems)
    assert any("CORS" in p for p in problems)
    from app.runtime import Runtime
    with pytest.raises(RuntimeError):
        Runtime(cfg)


def test_production_flag_accepts_safe_configuration(tmp_path):
    cfg = Settings(
        data_dir=tmp_path, sqlite_path=tmp_path / "m.db",
        checkpoint_path=tmp_path / "c.db", chroma_path=tmp_path / "chroma",
        production=True, auth_mode="required", cookie_secure=True,
        cors_origins="https://app.example.com",
        auth_pbkdf2_iterations=100000)
    assert cfg.validate_production() == []
