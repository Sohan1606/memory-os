"""Digest-bound confirmation helpers.

There is no voice or UI implementation in Phase 2.1. This module only creates a
confirmation record on an already authenticated local session and binds it to
the current policy version and security epoch.
"""

from __future__ import annotations

from datetime import timedelta
import uuid

from .contracts import ActionRequest, Confirmation, Session, utc_now


def issue_confirmation(
    action: ActionRequest,
    session: Session,
    method: str = "developer-test",
    ttl_seconds: int = 120,
    policy_version: str = "zorq-phase2-policy-v1",
    security_epoch: int = 0,
) -> Confirmation:
    if not session.is_valid():
        raise ValueError("session is not valid")
    if not method:
        raise ValueError("confirmation method is required")
    now = utc_now()
    return Confirmation(
        confirmation_id=str(uuid.uuid4()),
        principal_id=session.principal_id,
        session_id=session.session_id,
        action_digest=action.digest(),
        method=method,
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl_seconds),
        assurance=session.assurance,
        accepted=True,
        policy_version=policy_version,
        security_epoch=security_epoch,
    )
