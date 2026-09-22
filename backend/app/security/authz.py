"""Role → permission mapping (least privilege, no unrestricted admin).

Three roles, one tenant each:

- member : full access to their OWN cognitive namespace, nothing else.
- admin  : member + user/session management and security-event review for
           their tenant. Admins can NOT read another user's cognitive data —
           managing an account never grants access to its memories.
- owner  : admin + role management. Exactly one bootstrap owner exists per
           tenant.

Permissions are deliberately coarse-grained and explicit. Anything not listed
is denied.
"""
from __future__ import annotations

# Cognitive-data permissions apply only to the caller's own namespace. The
# enforcement point (`main.py` handlers via the principal dependency) never
# accepts a foreign namespace, so these do not need per-object ACLs.
P_COGNITION_READ = "cognition.read"        # memories/world/research/etc (own)
P_COGNITION_WRITE = "cognition.write"      # create/update/delete (own)
P_PORTABILITY_EXPORT = "portability.export"  # export own data
P_PORTABILITY_RESTORE = "portability.restore"  # import/restore own data
P_RESEARCH_RUN = "research.run"            # start research fetches (own)

# Operational permissions (tenant-scoped).
P_SECURITY_READ = "security.read"          # security/audit events, sessions
P_USERS_MANAGE = "users.manage"            # create/disable users, revoke sessions
P_ROLES_MANAGE = "roles.manage"            # change user roles
P_HEALTH_READ = "health.read"              # detailed dependency health

PERMISSIONS: frozenset[str] = frozenset({
    P_COGNITION_READ, P_COGNITION_WRITE,
    P_PORTABILITY_EXPORT, P_PORTABILITY_RESTORE, P_RESEARCH_RUN,
    P_SECURITY_READ, P_USERS_MANAGE, P_ROLES_MANAGE, P_HEALTH_READ,
})

_MEMBER = frozenset({
    P_COGNITION_READ, P_COGNITION_WRITE,
    P_PORTABILITY_EXPORT, P_PORTABILITY_RESTORE, P_RESEARCH_RUN,
    P_HEALTH_READ,
})
_ADMIN = _MEMBER | {P_SECURITY_READ, P_USERS_MANAGE}
_OWNER = _ADMIN | {P_ROLES_MANAGE}

ROLES: dict[str, frozenset[str]] = {
    "member": _MEMBER,
    "admin": _ADMIN,
    "owner": _OWNER,
}


def role_permissions(role: str) -> frozenset[str]:
    return ROLES.get(role, frozenset())


def has_permission(role: str, permission: str) -> bool:
    return permission in role_permissions(role)
