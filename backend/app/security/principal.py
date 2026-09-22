"""The authenticated principal for one request.

A Principal is the ONLY object request handlers may derive a namespace from.
In `disabled` mode there is exactly one anonymous principal bound to the
demo namespace (V8.4.4 behavior); in `required` mode a principal exists only
after session verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .authz import role_permissions


@dataclass(frozen=True)
class Principal:
    user_id: str            # auth_users.id ("local" in disabled mode)
    tenant_id: str          # tenants.id ("local" in disabled mode)
    email: str
    display_name: str
    role: str               # owner | admin | member
    namespace: str          # the user_id value used by every cognitive table
    session_id: str | None = None
    csrf_token: str | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)

    @staticmethod
    def local(namespace: str) -> "Principal":
        """The single-user principal used when AUTH_MODE=disabled."""
        return Principal(
            user_id="local", tenant_id="local", email="local@memory-os.local",
            display_name="Local user", role="owner", namespace=namespace,
            session_id=None, csrf_token=None,
            permissions=role_permissions("owner"))

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def as_dict(self) -> dict:
        """Safe representation for API surfaces. Never includes tokens."""
        return {
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "email": self.email,
            "display_name": self.display_name,
            "role": self.role,
            "namespace": self.namespace,
            "permissions": sorted(self.permissions),
        }
