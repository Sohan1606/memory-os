"""IdentityService: users, tenants, sessions.

Storage is the canonical `Database` (additive V8.5 tables) and every
security-relevant mutation is audited through the canonical `EventBus`.
This module is the abstraction layer the handoff asks for: the FastAPI app
talks only to IdentityService, so the identity provider can evolve (e.g. to
OIDC) without touching request handlers.

Security properties:
- passwords stored as PBKDF2-HMAC-SHA256 (salted, configurable iterations);
- session tokens are 256-bit random values; ONLY their SHA-256 hash is
  persisted, so a database leak reveals no usable token;
- verification uses constant-time comparison via hash lookup + `hmac.compare_digest`;
- sessions expire, can be revoked individually or in bulk, and rotate on
  every login (a fresh login never reuses a token — no fixation);
- audit events carry identifiers and coarse metadata only, never credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, TYPE_CHECKING

from .authz import ROLES, role_permissions
from .principal import Principal

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..cognition.events import EventBus
    from ..persistence.db import Database

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,24}$")

# Namespace of the pre-V8.5 single-user deployment. `migrate_legacy_namespace`
# lets the bootstrap owner adopt it deterministically.
DEFAULT_TENANT_ID = "ten_default"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class AuthError(Exception):
    """Authentication failed. The message is always safe to show a user."""


class AuthorizationError(Exception):
    """The principal lacks permission. Message is safe to show a user."""


def hash_password(password: str, iterations: int) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def redact_email(email: str) -> str:
    """`alice@example.com` → `a***@example.com` for audit summaries."""
    local, _, domain = email.partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


class IdentityService:
    """Authoritative identity/session store on the canonical Database + EventBus."""

    def __init__(self, db: "Database", bus: "EventBus", settings: Any) -> None:
        self.db = db
        self.bus = bus
        self.settings = settings
        self._ensure_default_tenant()
        self._bootstrap_from_env()

    # ------------------------------------------------------------ audit
    def _audit(self, namespace: str, event_type: str, summary: str, *,
               subject_kind: str, subject_id: str,
               payload: dict[str, Any] | None = None) -> None:
        """Audit through the canonical EventBus. Never raises into callers."""
        try:
            self.bus.emit(namespace, event_type, summary,
                          subject_kind=subject_kind, subject_id=subject_id,
                          payload=payload or {})
        except Exception:  # pragma: no cover - audit must not break auth
            import logging
            logging.getLogger(__name__).exception("Audit emit failed for %s", event_type)

    # ------------------------------------------------------------ tenants
    def _ensure_default_tenant(self) -> None:
        if not self.db.query_one("SELECT id FROM tenants WHERE id=?", (DEFAULT_TENANT_ID,)):
            self.db.execute(
                "INSERT INTO tenants (id,name,slug,status,created_at) VALUES (?,?,?,?,?)",
                (DEFAULT_TENANT_ID, "Default workspace", "default", "active", _iso(_now())))

    def create_tenant(self, name: str) -> dict[str, Any]:
        tenant_id = f"ten_{uuid.uuid4().hex[:12]}"
        slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")[:40] or tenant_id
        if self.db.query_one("SELECT id FROM tenants WHERE slug=?", (slug,)):
            slug = f"{slug}-{tenant_id[-6:]}"
        self.db.execute(
            "INSERT INTO tenants (id,name,slug,status,created_at) VALUES (?,?,?,?,?)",
            (tenant_id, name.strip()[:120], slug, "active", _iso(_now())))
        return self.get_tenant(tenant_id)

    def get_tenant(self, tenant_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM tenants WHERE id=?", (tenant_id,))
        if not row:
            raise KeyError("Tenant not found.")
        return dict(row)

    # ------------------------------------------------------------ users
    def _user_row(self, *, user_id: str | None = None,
                  email: str | None = None) -> dict[str, Any] | None:
        if user_id is not None:
            row = self.db.query_one("SELECT * FROM auth_users WHERE id=?", (user_id,))
        else:
            row = self.db.query_one("SELECT * FROM auth_users WHERE email=?",
                                    ((email or "").strip().lower(),))
        return dict(row) if row else None

    @staticmethod
    def _public_user(row: dict[str, Any]) -> dict[str, Any]:
        return {k: row[k] for k in ("id", "tenant_id", "email", "display_name",
                                    "role", "status", "namespace", "created_at")}

    def _validate_new_credentials(self, email: str, password: str) -> str:
        email = email.strip().lower()
        if not _EMAIL_RE.match(email):
            raise ValueError("A valid email address is required.")
        if len(password) < 10 or len(password) > 256:
            raise ValueError("Password must be between 10 and 256 characters.")
        if self._user_row(email=email):
            raise ValueError("An account with this email already exists.")
        return email

    def create_user(self, *, email: str, password: str, display_name: str,
                    tenant_id: str, role: str = "member",
                    namespace: str | None = None,
                    actor: Principal | None = None) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role!r}")
        email = self._validate_new_credentials(email, password)
        self.get_tenant(tenant_id)  # KeyError if missing
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        # The namespace IS the established cognitive `user_id` value. It is
        # server-generated and unique; callers cannot choose an arbitrary one
        # (no mass assignment into someone else's data).
        ns = namespace if namespace is not None else f"ns_{uuid.uuid4().hex[:16]}"
        if self.db.query_one("SELECT id FROM auth_users WHERE namespace=?", (ns,)):
            raise ValueError("Namespace is already owned by another account.")
        now = _iso(_now())
        self.db.execute(
            "INSERT INTO auth_users (id,tenant_id,email,display_name,password_hash,"
            "role,status,namespace,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, tenant_id, email, display_name.strip()[:120] or email,
             hash_password(password, self.settings.auth_pbkdf2_iterations),
             role, "active", ns, now, now))
        self._audit(ns, "user.created", "Created a user account.",
                    subject_kind="user", subject_id=user_id,
                    payload={"email": redact_email(email), "role": role,
                             "tenant_id": tenant_id,
                             "by": actor.user_id if actor else "registration"})
        return self._public_user(self._user_row(user_id=user_id))  # type: ignore[arg-type]

    def register(self, *, email: str, password: str,
                 display_name: str) -> dict[str, Any]:
        """Open self-registration.

        The FIRST account becomes the owner of the default workspace (and is
        the deterministic adoption point for a migrated V8.4.4 database).
        Every later self-registered account gets its own fresh workspace and
        owns it — tenants are never implicitly shared.
        """
        if not self.settings.auth_allow_registration:
            raise AuthorizationError("Self-registration is disabled.")
        first = self.db.query_one("SELECT COUNT(*) AS n FROM auth_users")["n"] == 0
        if first:
            tenant_id = DEFAULT_TENANT_ID
        else:
            tenant_id = self.create_tenant(f"{display_name or email} workspace")["id"]
        user = self.create_user(email=email, password=password,
                                display_name=display_name, tenant_id=tenant_id,
                                role="owner")
        self._audit(user["namespace"], "auth.registered", "Registered a new account.",
                    subject_kind="user", subject_id=user["id"],
                    payload={"email": redact_email(user["email"]),
                             "tenant_id": tenant_id, "first_account": first})
        return user

    def _bootstrap_from_env(self) -> None:
        """Deterministic admin bootstrap from environment (never from source)."""
        email = self.settings.auth_bootstrap_email
        password = self.settings.auth_bootstrap_password
        if not email or not password:
            return
        if self._user_row(email=email):
            return
        try:
            self.create_user(email=email, password=password,
                             display_name="Bootstrap owner",
                             tenant_id=DEFAULT_TENANT_ID, role="owner")
        except ValueError as exc:
            import logging
            logging.getLogger(__name__).warning("Bootstrap owner not created: %s", exc)

    def list_users(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM auth_users WHERE tenant_id=? ORDER BY created_at", (tenant_id,))
        return [self._public_user(dict(r)) for r in rows]

    def get_user(self, user_id: str) -> dict[str, Any]:
        row = self._user_row(user_id=user_id)
        if not row:
            raise KeyError("User not found.")
        return self._public_user(row)

    def disable_user(self, actor: Principal, user_id: str) -> dict[str, Any]:
        target = self._user_row(user_id=user_id)
        if not target or target["tenant_id"] != actor.tenant_id:
            raise KeyError("User not found.")   # no cross-tenant existence oracle
        if target["id"] == actor.user_id:
            raise ValueError("You cannot disable your own account.")
        if target["role"] == "owner" and actor.role != "owner":
            raise AuthorizationError("Only the owner may disable an owner account.")
        self.db.execute(
            "UPDATE auth_users SET status='disabled', disabled_at=?, updated_at=? WHERE id=?",
            (_iso(_now()), _iso(_now()), user_id))
        revoked = self.revoke_all_sessions(user_id, reason="user_disabled")
        self._audit(actor.namespace, "user.disabled", "Disabled a user account.",
                    subject_kind="user", subject_id=user_id,
                    payload={"by": actor.user_id, "sessions_revoked": revoked})
        return self.get_user(user_id)

    def change_role(self, actor: Principal, user_id: str, role: str) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError(f"Unknown role: {role!r}")
        target = self._user_row(user_id=user_id)
        if not target or target["tenant_id"] != actor.tenant_id:
            raise KeyError("User not found.")
        if target["id"] == actor.user_id:
            raise ValueError("You cannot change your own role.")
        old_role = target["role"]
        self.db.execute("UPDATE auth_users SET role=?, updated_at=? WHERE id=?",
                        (role, _iso(_now()), user_id))
        self._audit(actor.namespace, "permission.changed", "Changed a user's role.",
                    subject_kind="user", subject_id=user_id,
                    payload={"by": actor.user_id, "from": old_role, "to": role})
        return self.get_user(user_id)

    # ------------------------------------------------------------ sessions
    def login(self, *, email: str, password: str,
              client: str | None = None) -> tuple[str, Principal]:
        """Verify credentials; return (raw_token, principal). Token is never stored."""
        row = self._user_row(email=email)
        # Uniform failure: same error whether the account exists, the password
        # is wrong or the account is disabled — no account-existence oracle.
        generic = AuthError("Invalid email or password.")
        if not row:
            # Burn comparable time so absent accounts are not distinguishable.
            verify_password(password, hash_password(
                "timing-equalizer", self.settings.auth_pbkdf2_iterations))
            self._audit("security", "auth.failed", "Rejected a sign-in attempt.",
                        subject_kind="auth", subject_id="unknown",
                        payload={"email": redact_email(email), "reason": "unknown_account"})
            raise generic
        if not verify_password(password, row["password_hash"]):
            self._audit(row["namespace"], "auth.failed", "Rejected a sign-in attempt.",
                        subject_kind="user", subject_id=row["id"],
                        payload={"reason": "bad_credentials"})
            raise generic
        if row["status"] != "active":
            self._audit(row["namespace"], "auth.failed", "Rejected a sign-in attempt.",
                        subject_kind="user", subject_id=row["id"],
                        payload={"reason": "account_disabled"})
            raise generic
        token, session_id, csrf = self._create_session(row["id"], client=client)
        principal = self._principal_from_rows(row, session_id, csrf)
        self._audit(row["namespace"], "auth.login", "Signed in.",
                    subject_kind="session", subject_id=session_id,
                    payload={"client": (client or "")[:120]})
        return token, principal

    def _create_session(self, user_id: str, *,
                        client: str | None = None) -> tuple[str, str, str]:
        token = secrets.token_urlsafe(32)          # 256 bits of entropy
        csrf = secrets.token_urlsafe(32)
        session_id = f"ses_{uuid.uuid4().hex[:12]}"
        now = _now()
        expires = now + timedelta(hours=self.settings.session_ttl_hours)
        self.db.execute(
            "INSERT INTO auth_sessions (id,user_id,token_hash,csrf_token,created_at,"
            "expires_at,last_seen_at,client) VALUES (?,?,?,?,?,?,?,?)",
            (session_id, user_id, _token_hash(token), csrf, _iso(now),
             _iso(expires), _iso(now), (client or "")[:200]))
        return token, session_id, csrf

    def _principal_from_rows(self, user_row: dict[str, Any], session_id: str | None,
                             csrf: str | None) -> Principal:
        return Principal(
            user_id=user_row["id"], tenant_id=user_row["tenant_id"],
            email=user_row["email"], display_name=user_row["display_name"],
            role=user_row["role"], namespace=user_row["namespace"],
            session_id=session_id, csrf_token=csrf,
            permissions=role_permissions(user_row["role"]))

    def verify_session(self, token: str) -> Principal:
        """Resolve a raw token to a live principal or raise AuthError."""
        if not token or len(token) > 512:
            raise AuthError("Not authenticated.")
        row = self.db.query_one("SELECT * FROM auth_sessions WHERE token_hash=?",
                                (_token_hash(token),))
        if not row:
            raise AuthError("Not authenticated.")
        session = dict(row)
        if session["revoked_at"] is not None:
            raise AuthError("Session has been revoked.")
        if _parse(session["expires_at"]) <= _now():
            user = self._user_row(user_id=session["user_id"])
            if user:
                self._audit(user["namespace"], "session.expired", "A session expired.",
                            subject_kind="session", subject_id=session["id"])
            raise AuthError("Session has expired.")
        user = self._user_row(user_id=session["user_id"])
        if not user or user["status"] != "active":
            raise AuthError("Account is not active.")
        self.db.execute("UPDATE auth_sessions SET last_seen_at=? WHERE id=?",
                        (_iso(_now()), session["id"]))
        return self._principal_from_rows(user, session["id"], session["csrf_token"])

    def logout(self, principal: Principal) -> bool:
        if not principal.session_id:
            return False
        self.db.execute(
            "UPDATE auth_sessions SET revoked_at=?, revoked_reason='logout' "
            "WHERE id=? AND revoked_at IS NULL",
            (_iso(_now()), principal.session_id))
        self._audit(principal.namespace, "auth.logout", "Signed out.",
                    subject_kind="session", subject_id=principal.session_id)
        return True

    def revoke_session(self, actor: Principal, session_id: str, *,
                       reason: str = "revoked_by_admin") -> bool:
        row = self.db.query_one("SELECT * FROM auth_sessions WHERE id=?", (session_id,))
        if not row:
            raise KeyError("Session not found.")
        target_user = self._user_row(user_id=row["user_id"])
        if not target_user:
            raise KeyError("Session not found.")
        own = row["user_id"] == actor.user_id
        if not own:
            if target_user["tenant_id"] != actor.tenant_id or not actor.can("users.manage"):
                raise KeyError("Session not found.")  # no cross-tenant oracle
        self.db.execute(
            "UPDATE auth_sessions SET revoked_at=?, revoked_reason=? "
            "WHERE id=? AND revoked_at IS NULL",
            (_iso(_now()), reason[:80], session_id))
        self._audit(actor.namespace, "session.revoked", "Revoked a session.",
                    subject_kind="session", subject_id=session_id,
                    payload={"by": actor.user_id, "reason": reason,
                             "own_session": own})
        return True

    def revoke_all_sessions(self, user_id: str, *, reason: str) -> int:
        cur = self.db.execute(
            "UPDATE auth_sessions SET revoked_at=?, revoked_reason=? "
            "WHERE user_id=? AND revoked_at IS NULL",
            (_iso(_now()), reason[:80], user_id))
        return cur.rowcount or 0

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT id,created_at,expires_at,revoked_at,revoked_reason,last_seen_at,client"
            " FROM auth_sessions WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
            (user_id,))
        return [dict(r) for r in rows]

    # ------------------------------------------------------------ migration
    def migrate_legacy_namespace(self, owner_user_id: str,
                                 legacy_namespace: str) -> dict[str, Any]:
        """Deterministically adopt the single-user V8.4.4 namespace.

        The cognitive tables already key everything by `user_id`; adoption is
        a pure identity-mapping change (the account's namespace pointer moves
        to the legacy value). No cognitive row is rewritten, so the migration
        is idempotent and loss-free by construction.
        """
        user = self._user_row(user_id=owner_user_id)
        if not user:
            raise KeyError("User not found.")
        if user["namespace"] == legacy_namespace:
            return {"migrated": False, "reason": "already_adopted",
                    "namespace": legacy_namespace}
        claimed = self.db.query_one(
            "SELECT id FROM auth_users WHERE namespace=?", (legacy_namespace,))
        if claimed:
            raise ValueError("That namespace is already owned by another account.")
        self.db.execute("UPDATE auth_users SET namespace=?, updated_at=? WHERE id=?",
                        (legacy_namespace, _iso(_now()), owner_user_id))
        self._audit(legacy_namespace, "admin.action",
                    "Adopted the pre-V8.5 single-user namespace.",
                    subject_kind="user", subject_id=owner_user_id,
                    payload={"action": "namespace_adoption",
                             "from": user["namespace"], "to": legacy_namespace})
        return {"migrated": True, "namespace": legacy_namespace,
                "previous_namespace": user["namespace"]}
