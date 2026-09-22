"""V8.5 production trust layer.

This package adds authentication, authorization, tenancy, rate limiting,
observability and production-safe error handling ON TOP of the existing
architecture:

- identity/session/tenant rows live in the SAME SQLite database
  (`app.persistence.db.Database`), added as additive V8.5 tables;
- every security-relevant action is audited through the SAME canonical
  EventBus (`app.cognition.events.EventBus`) — there is no second audit store;
- the authenticated principal maps onto the SAME `user_id` namespace column
  every cognitive table has used since V8: one account owns exactly one
  namespace, so no cognitive storage was forked or duplicated.

`AUTH_MODE=disabled` (default) preserves V8.4.1–V8.4.4 behavior exactly:
no login exists and requests run in the single-user demo namespace.
`AUTH_MODE=required` turns on real multi-user enforcement.
"""
from .principal import Principal
from .identity import IdentityService
from .authz import ROLES, PERMISSIONS, role_permissions, has_permission

__all__ = ["Principal", "IdentityService", "ROLES", "PERMISSIONS",
           "role_permissions", "has_permission"]
