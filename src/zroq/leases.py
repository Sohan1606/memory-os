"""Issuer-bound execution lease primitives for the Phase 2.1 control plane.

The Device Agent validates a lease through ``LeaseVerifier`` instead of trusting
that a dataclass with matching public fields was honestly issued. The current
standalone implementation uses an in-process HMAC issuer secret and active-lease
registry. This is not hardware attestation or a distributed credential system;
it is a concrete Phase 2.1 control-plane proof that the trusted Action Kernel
issued the lease in the current security epoch.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import hmac
import secrets
import threading
from datetime import datetime

from .contracts import ActionRequest, ActionSnapshot, ExecutionLease, canonical_json, utc_now


class SecurityEpoch:
    """Monotonic local security epoch invalidating stale confirmations/leases."""

    def __init__(self, initial: int = 0):
        self._epoch = initial
        self._lock = threading.RLock()

    def current(self) -> int:
        with self._lock:
            return self._epoch

    def bump(self) -> int:
        with self._lock:
            self._epoch += 1
            return self._epoch


@dataclass(frozen=True)
class LeaseVerification:
    valid: bool
    reason: str


class LeaseRegistry:
    """Tracks issued local leases and consumes each one at most once."""

    def __init__(self) -> None:
        self._active: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, lease: ExecutionLease) -> None:
        with self._lock:
            self._active[lease.lease_id] = lease.issuer_signature

    def consume(self, lease: ExecutionLease) -> LeaseVerification:
        with self._lock:
            signature = self._active.get(lease.lease_id)
            if signature is None:
                return LeaseVerification(False, "lease_not_active_or_already_used")
            if not hmac.compare_digest(signature, lease.issuer_signature):
                return LeaseVerification(False, "lease_registry_signature_mismatch")
            del self._active[lease.lease_id]
            return LeaseVerification(True, "lease_consumed")

    def revoke(self, lease_id: str) -> bool:
        with self._lock:
            return self._active.pop(lease_id, None) is not None

    def revoke_all(self) -> None:
        with self._lock:
            self._active.clear()

    def active_count(self) -> int:
        with self._lock:
            return len(self._active)


class LeaseIssuer:
    """Trusted control-plane lease issuer used by the Action Kernel only."""

    def __init__(self, registry: LeaseRegistry, issuer_id: str = "zorq-action-kernel", secret: bytes | None = None):
        self.registry = registry
        self.issuer_id = issuer_id
        self._secret = secret or secrets.token_bytes(32)

    def verifier(self) -> "LeaseVerifier":
        return LeaseVerifier(self.registry, self.issuer_id, self._secret)

    def issue(
        self,
        action: ActionRequest | ActionSnapshot,
        lease_id: str,
        issued_at: datetime,
        expires_at: datetime,
        policy_version: str,
        security_epoch: int,
        max_calls: int = 1,
    ) -> ExecutionLease:
        unsigned = ExecutionLease(
            lease_id=lease_id,
            action_id=action.action_id,
            action_digest=action.digest(),
            owner_id=action.owner_id,
            principal_id=action.principal_id,
            device_id=action.device_id,
            capability_id=action.capability_id,
            capability_version=action.capability_version,
            operation=action.operation,
            issued_at=issued_at,
            expires_at=expires_at,
            max_calls=max_calls,
            issuer_id=self.issuer_id,
            policy_version=policy_version,
            security_epoch=security_epoch,
            issuer_signature="",
        )
        signed = replace(unsigned, issuer_signature=self._sign(unsigned))
        self.registry.register(signed)
        return signed

    def _sign(self, lease: ExecutionLease) -> str:
        material = _lease_material(lease)
        return hmac.new(self._secret, canonical_json(material).encode("utf-8"), hashlib.sha256).hexdigest()


class LeaseVerifier:
    """Verification-only lease endpoint supplied to the Device Agent."""

    def __init__(self, registry: LeaseRegistry, issuer_id: str, secret: bytes):
        self._registry = registry
        self._issuer_id = issuer_id
        self._secret = secret

    def verify_and_consume(
        self,
        lease: ExecutionLease,
        action: ActionRequest | ActionSnapshot,
        policy_version: str,
        security_epoch: int,
    ) -> LeaseVerification:
        if lease.issuer_id != self._issuer_id:
            return LeaseVerification(False, "lease_issuer_mismatch")
        expected = hmac.new(self._secret, canonical_json(_lease_material(lease)).encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, lease.issuer_signature):
            return LeaseVerification(False, "lease_signature_invalid")
        if not lease.valid_for(action, utc_now(), policy_version=policy_version, security_epoch=security_epoch):
            return LeaseVerification(False, "lease_fields_or_epoch_invalid")
        return self._registry.consume(lease)


def _lease_material(lease: ExecutionLease) -> dict[str, object]:
    """Material covered by the issuer proof.

    The proof binds all fields required by the Phase 2.1 hardening request plus
    the local one-use call ceiling. ``issuer_signature`` is intentionally
    excluded so verification can recompute the HMAC.
    """

    return {
        "lease_id": lease.lease_id,
        "action_id": lease.action_id,
        "action_digest": lease.action_digest,
        "owner_id": lease.owner_id,
        "principal_id": lease.principal_id,
        "device_id": lease.device_id,
        "capability_id": lease.capability_id,
        "capability_version": lease.capability_version,
        "operation": lease.operation,
        "issued_at": lease.issued_at,
        "expires_at": lease.expires_at,
        "max_calls": lease.max_calls,
        "issuer_id": lease.issuer_id,
        "policy_version": lease.policy_version,
        "security_epoch": lease.security_epoch,
    }
