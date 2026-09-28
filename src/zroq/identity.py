"""Local session and device identity abstractions.

This Phase 2.6 implementation intentionally provides a narrow local owner-session
abstraction. It is not speaker verification, passkey infrastructure, or a
replacement for the MEMORY//OS identity model.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import hmac
import secrets
import threading
from typing import Callable, Dict

from .contracts import AssuranceLevel, DeviceIdentity, PrincipalType, Session, utc_now


@dataclass(frozen=True)
class IdentityEvent:
    event_type: str
    subject: str
    detail: str


@dataclass(frozen=True)
class SessionValidation:
    valid: bool
    reason: str
    session: Session | None = None


class LocalOwnerAuthenticator:
    """Explicit local development authenticator.

    It accepts a configured secret to establish an A2 local owner session.
    The secret is never returned in a claim. This is an abstraction boundary,
    not a production passkey or biometric implementation.
    """

    def __init__(self, owner_id: str, configured_secret: str):
        if not owner_id or not configured_secret:
            raise ValueError("owner_id and configured_secret are required")
        self.owner_id = owner_id
        self._secret_digest = hashlib.sha256(configured_secret.encode("utf-8")).digest()
        self.events: list[IdentityEvent] = []

    def authenticate(self, presented_secret: str) -> bool:
        candidate = hashlib.sha256(presented_secret.encode("utf-8")).digest()
        ok = hmac.compare_digest(candidate, self._secret_digest)
        self.events.append(IdentityEvent("AUTHENTICATE_SUCCESS" if ok else "AUTHENTICATE_FAILURE", self.owner_id, "local_factor"))
        return ok


class SessionManager:
    """Authoritative in-process session store and validator.

    Callers may hold old immutable Session dataclasses, but authority is based
    only on the current entry in this store plus the current security epoch.
    """

    def __init__(
        self,
        authenticator: LocalOwnerAuthenticator,
        device: DeviceIdentity,
        ttl_seconds: int = 900,
        security_epoch_provider: Callable[[], int] | None = None,
    ):
        self.authenticator = authenticator
        self.device = device
        self.ttl_seconds = ttl_seconds
        self._security_epoch_provider = security_epoch_provider or (lambda: 0)
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.RLock()
        self.events: list[IdentityEvent] = []

    def establish_owner_session(self, presented_secret: str) -> Session | None:
        if not self.authenticator.authenticate(presented_secret):
            self.events.append(IdentityEvent("SESSION_DENIED", self.device.owner_id, "factor_failure"))
            return None
        now = utc_now()
        session = Session(
            session_id=secrets.token_urlsafe(18),
            owner_id=self.device.owner_id,
            principal_id=self.device.owner_id,
            principal_type=PrincipalType.OWNER,
            device_id=self.device.device_id,
            assurance=AssuranceLevel.A2,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            active=True,
            security_epoch=self._security_epoch_provider(),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        self.events.append(IdentityEvent("SESSION_ESTABLISHED", self.device.owner_id, session.session_id))
        return session

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None or not session.is_valid():
            return None
        if session.device_id != self.device.device_id:
            return None
        if session.security_epoch != self._security_epoch_provider():
            return None
        return session

    def validate_for_action(self, session: Session, owner_id: str, principal_id: str, device_id: str, security_epoch: int) -> SessionValidation:
        with self._lock:
            current = self._sessions.get(session.session_id)
        if current is None:
            return SessionValidation(False, "session_unknown")
        if not current.is_valid():
            return SessionValidation(False, "session_invalid_or_revoked")
        if current.security_epoch != security_epoch:
            return SessionValidation(False, "session_security_epoch_invalid")
        if session.security_epoch != current.security_epoch:
            return SessionValidation(False, "supplied_session_epoch_stale")
        if current.owner_id != owner_id or current.principal_id != principal_id or current.device_id != device_id:
            return SessionValidation(False, "session_scope_mismatch")
        if session.owner_id != current.owner_id or session.principal_id != current.principal_id or session.device_id != current.device_id:
            return SessionValidation(False, "supplied_session_scope_mismatch")
        if session.session_id != current.session_id:
            return SessionValidation(False, "session_id_mismatch")
        return SessionValidation(True, "session_valid", current)

    def revoke(self, session_id: str, reason: str = "revoked") -> bool:
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            self._sessions[session_id] = Session(**{**session.__dict__, "active": False})
        self.events.append(IdentityEvent("SESSION_REVOKED", session.owner_id, reason))
        return True

    def revoke_all(self, reason: str = "emergency_stop") -> None:
        with self._lock:
            ids = list(self._sessions)
        for sid in ids:
            self.revoke(sid, reason)
