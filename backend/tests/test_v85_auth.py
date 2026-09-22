"""V8.5 AUTH: login, logout, expiry, revocation, invalid credentials,
session fixation, cookie security."""
from __future__ import annotations

import pytest

from app.security.identity import AuthError, hash_password, verify_password

from conftest_v85 import (bearer, make_secure_runtime, register_and_login,
                          secure_client, teardown)


@pytest.fixture(scope="module")
def env():
    runtime, tmp = make_secure_runtime()
    client = secure_client(runtime)
    yield runtime, client
    teardown(runtime, tmp)


def test_password_hashing_roundtrip_and_no_plaintext():
    stored = hash_password("a-strong-password", 1000)
    assert "a-strong-password" not in stored
    assert stored.startswith("pbkdf2_sha256$1000$")
    assert verify_password("a-strong-password", stored)
    assert not verify_password("wrong-password", stored)


def test_register_login_session_logout(env):
    runtime, client = env
    user, token, _csrf = register_and_login(client, "alice@example.com")
    assert user["email"] == "alice@example.com"
    assert user["role"] == "owner"      # first account owns the default tenant

    session = client.get("/api/auth/session", headers=bearer(token)).json()
    assert session["auth_mode"] == "required"
    assert session["user"]["email"] == "alice@example.com"
    # No secret material in the session surface.
    assert "token" not in session and "csrf_token" not in str(session["user"])

    r = client.post("/api/auth/logout", headers=bearer(token))
    assert r.status_code == 200
    # The token is dead after logout.
    assert client.get("/api/memories", headers=bearer(token)).status_code == 401


def test_invalid_credentials_are_uniform(env):
    runtime, client = env
    register_and_login(client, "bob@example.com")
    wrong_pw = client.post("/api/auth/login", json={
        "email": "bob@example.com", "password": "not-the-password"})
    no_account = client.post("/api/auth/login", json={
        "email": "ghost@example.com", "password": "whatever-password"})
    assert wrong_pw.status_code == 401
    assert no_account.status_code == 401
    # Same message → no account-existence oracle.
    assert wrong_pw.json()["detail"] == no_account.json()["detail"]


def test_unauthenticated_requests_rejected(env):
    runtime, _client = env
    # Fresh client: no cookies from earlier logins in this module.
    fresh = secure_client(runtime)
    for path in ("/api/memories", "/api/world", "/api/missions",
                 "/api/portability/v1/exports", "/api/cognition/events"):
        r = fresh.get(path)
        assert r.status_code == 401, path
        assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_session_expiry(env):
    runtime, client = env
    _user, token, _ = register_and_login(client, "expiry@example.com")
    # Force the session to be expired in the store.
    runtime.db.execute(
        "UPDATE auth_sessions SET expires_at='2000-01-01T00:00:00.000Z' "
        "WHERE token_hash IN (SELECT token_hash FROM auth_sessions)")
    assert client.get("/api/memories", headers=bearer(token)).status_code == 401


def test_session_revocation(env):
    runtime, client = env
    _user, token, _ = register_and_login(client, "revoke@example.com")
    sessions = client.get("/api/auth/sessions", headers=bearer(token)).json()["sessions"]
    live = [s for s in sessions if not s["revoked_at"]]
    assert live
    r = client.post(f"/api/auth/sessions/{live[0]['id']}/revoke", headers=bearer(token))
    assert r.status_code == 200
    assert client.get("/api/memories", headers=bearer(token)).status_code == 401


def test_no_session_fixation_new_token_every_login(env):
    runtime, client = env
    register_and_login(client, "fix@example.com")
    t1 = client.post("/api/auth/login", json={
        "email": "fix@example.com", "password": "correct-horse-battery"}).json()["token"]
    t2 = client.post("/api/auth/login", json={
        "email": "fix@example.com", "password": "correct-horse-battery"}).json()["token"]
    assert t1 != t2
    # Both are independently revocable; revoking one leaves the other alive.
    p1 = runtime.identity.verify_session(t1)
    runtime.identity.revoke_session(p1, p1.session_id, reason="test")
    with pytest.raises(AuthError):
        runtime.identity.verify_session(t1)
    assert runtime.identity.verify_session(t2).email == "fix@example.com"


def test_tokens_stored_only_hashed(env):
    runtime, client = env
    _user, token, _ = register_and_login(client, "hash@example.com")
    rows = runtime.db.query("SELECT token_hash FROM auth_sessions")
    assert rows
    for row in rows:
        assert row["token_hash"] != token
        assert len(row["token_hash"]) == 64      # sha256 hex


def test_cookie_flags(env):
    runtime, client = env
    r = client.post("/api/auth/login", json={
        "email": "hash@example.com", "password": "correct-horse-battery"})
    cookie = r.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie


def test_disabled_account_cannot_login_or_use_session(env):
    runtime, client = env
    owner_user, owner_token, owner_csrf = register_and_login(client, "own2@example.com")
    # Owner of a fresh tenant creates a member, then disables them.
    r = client.post("/api/admin/users", headers=bearer(owner_token), json={
        "email": "victim@example.com", "password": "victim-password-1",
        "display_name": "Victim", "role": "member"})
    assert r.status_code == 201, r.text
    victim_id = r.json()["user"]["id"]
    v_login = client.post("/api/auth/login", json={
        "email": "victim@example.com", "password": "victim-password-1"})
    assert v_login.status_code == 200
    v_token = v_login.json()["token"]
    r = client.post(f"/api/admin/users/{victim_id}/disable", headers=bearer(owner_token))
    assert r.status_code == 200
    # Existing session is revoked and new logins fail uniformly.
    assert client.get("/api/memories", headers=bearer(v_token)).status_code == 401
    assert client.post("/api/auth/login", json={
        "email": "victim@example.com", "password": "victim-password-1"}).status_code == 401


def test_registration_validation(env):
    runtime, client = env
    assert client.post("/api/auth/register", json={
        "email": "not-an-email", "password": "long-enough-pass",
        "display_name": "X"}).status_code == 400
    assert client.post("/api/auth/register", json={
        "email": "short@pw.com", "password": "short",
        "display_name": "X"}).status_code == 422
    # Duplicate email rejected.
    register_and_login(client, "dup@example.com")
    assert client.post("/api/auth/register", json={
        "email": "dup@example.com", "password": "another-password-1",
        "display_name": "X"}).status_code == 400
