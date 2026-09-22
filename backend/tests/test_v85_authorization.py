"""V8.5 AUTHORIZATION: RBAC, denied access, privilege escalation,
cross-tenant admin boundaries, mass assignment."""
from __future__ import annotations

import pytest

from app.security.authz import ROLES, has_permission, role_permissions

from conftest_v85 import (bearer, make_secure_runtime, register_and_login,
                          secure_client, teardown)


@pytest.fixture(scope="module")
def env():
    runtime, tmp = make_secure_runtime()
    client = secure_client(runtime)
    yield runtime, client
    teardown(runtime, tmp)


def test_role_model_is_least_privilege():
    assert has_permission("member", "cognition.read")
    assert has_permission("member", "portability.export")
    assert not has_permission("member", "security.read")
    assert not has_permission("member", "users.manage")
    assert has_permission("admin", "users.manage")
    assert not has_permission("admin", "roles.manage")
    assert has_permission("owner", "roles.manage")
    # No role grants anything outside the declared permission set.
    from app.security.authz import PERMISSIONS
    for role, perms in ROLES.items():
        assert perms <= PERMISSIONS, role
    assert role_permissions("nonexistent") == frozenset()


def test_member_cannot_reach_admin_surfaces(env):
    runtime, client = env
    _owner, owner_token, _ = register_and_login(client, "owner@rbac.test")
    r = client.post("/api/admin/users", headers=bearer(owner_token), json={
        "email": "member@rbac.test", "password": "member-password-1",
        "display_name": "Member", "role": "member"})
    assert r.status_code == 201
    m_token = client.post("/api/auth/login", json={
        "email": "member@rbac.test", "password": "member-password-1"}).json()["token"]

    for method, path in (
            ("GET", "/api/admin/users"),
            ("GET", "/api/admin/security-events"),
            ("GET", "/api/admin/rate-limit"),
            ("POST", "/api/admin/migrate-legacy-namespace")):
        r = client.request(method, path, headers=bearer(m_token),
                           json={} if method == "POST" else None)
        assert r.status_code == 403, path
        assert r.json()["error"]["code"] == "FORBIDDEN"

    # Denials are audited on the canonical bus.
    denials = runtime.cognition.bus.recent(
        runtime.identity.get_user(_find_user(runtime, "member@rbac.test"))["namespace"],
        types=["authorization.denied"])
    assert denials


def _find_user(runtime, email):
    return runtime.db.query_one(
        "SELECT id FROM auth_users WHERE email=?", (email,))["id"]


def test_admin_cannot_escalate_to_owner(env):
    runtime, client = env
    _owner, owner_token, _ = register_and_login(client, "owner2@rbac.test")
    r = client.post("/api/admin/users", headers=bearer(owner_token), json={
        "email": "admin2@rbac.test", "password": "admin-password-12",
        "display_name": "Admin", "role": "member"})
    assert r.status_code == 201
    member_id = r.json()["user"]["id"]
    # Owner may promote to admin.
    r = client.post(f"/api/admin/users/{member_id}/role",
                    headers=bearer(owner_token), json={"role": "admin"})
    assert r.status_code == 200
    a_token = client.post("/api/auth/login", json={
        "email": "admin2@rbac.test", "password": "admin-password-12"}).json()["token"]
    # Admin cannot change roles (roles.manage is owner-only)...
    r = client.post(f"/api/admin/users/{member_id}/role",
                    headers=bearer(a_token), json={"role": "owner"})
    assert r.status_code == 403
    # ...and cannot create admin/owner accounts.
    r = client.post("/api/admin/users", headers=bearer(a_token), json={
        "email": "sneaky@rbac.test", "password": "sneaky-password-1",
        "display_name": "S", "role": "owner"})
    assert r.status_code == 403


def test_role_change_is_audited(env):
    runtime, client = env
    ns = runtime.identity.get_user(_find_user(runtime, "owner2@rbac.test"))["namespace"]
    events = runtime.cognition.bus.recent(ns, types=["permission.changed"])
    assert events
    assert events[0].payload["to"] == "admin"


def test_admin_cannot_manage_other_tenants(env):
    runtime, client = env
    # Two self-registered owners → two distinct tenants.
    _o1, t1, _ = register_and_login(client, "tenant-a@rbac.test")
    _o2, t2, _ = register_and_login(client, "tenant-b@rbac.test")
    a_id = _find_user(runtime, "tenant-a@rbac.test")
    b_id = _find_user(runtime, "tenant-b@rbac.test")
    # Owner A cannot see B in listings...
    users_a = client.get("/api/admin/users", headers=bearer(t1)).json()["users"]
    assert all(u["id"] != b_id for u in users_a)
    # ...cannot disable B (absence, not existence, is revealed)...
    assert client.post(f"/api/admin/users/{b_id}/disable",
                       headers=bearer(t1)).status_code == 404
    # ...and cannot change B's role.
    assert client.post(f"/api/admin/users/{b_id}/role", headers=bearer(t1),
                       json={"role": "member"}).status_code == 404


def test_mass_assignment_rejected(env):
    runtime, client = env
    _o, token, _ = register_and_login(client, "massassign@rbac.test")
    # Extra fields (tenant_id / namespace / role escalation vectors) are
    # rejected outright by the strict schemas.
    r = client.post("/api/admin/users", headers=bearer(token), json={
        "email": "m1@rbac.test", "password": "valid-password-12",
        "display_name": "M", "role": "member",
        "tenant_id": "ten_default", "namespace": "demo-user"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={
        "email": "m2@rbac.test", "password": "valid-password-12",
        "display_name": "M", "role": "owner", "namespace": "demo-user"})
    assert r.status_code == 422


def test_self_management_guards(env):
    runtime, client = env
    _o, token, _ = register_and_login(client, "selfguard@rbac.test")
    my_id = _find_user(runtime, "selfguard@rbac.test")
    assert client.post(f"/api/admin/users/{my_id}/disable",
                       headers=bearer(token)).status_code == 400
    assert client.post(f"/api/admin/users/{my_id}/role", headers=bearer(token),
                       json={"role": "member"}).status_code == 400
