"""Trusted Phase 2.6 Action Kernel."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from datetime import timedelta
from typing import Dict

from .audit import AuditLog
from .authority import AuthorityEngine
from .capabilities import CapabilityRegistry
from .contracts import (
    ActionRequest,
    ActionSnapshot,
    action_snapshot,
    ActionResult,
    ActionStatus,
    CapabilityManifest,
    Confirmation,
    ExecutionObservation,
    GovernanceDecision,
    GovernanceState,
    PermissionGrant,
    Session,
    VerificationStatus,
    utc_now,
)
from .device_agent import LocalDeviceAgent
from .grants import GrantAuthority
from .leases import LeaseIssuer, LeaseRegistry, SecurityEpoch
from .memory import MemoryOSAdapter
from .observability import Observability
from .verification import VerificationSubsystem


@dataclass
class _IdempotencyRecord:
    digest: str
    event: threading.Event
    result: ActionResult | None = None


class GrantCallLedger:
    """Process-local grant max_calls accounting."""

    def __init__(self) -> None:
        self._used: dict[str, int] = {}
        self._lock = threading.RLock()

    def reserve(self, grant: PermissionGrant) -> tuple[bool, int, int]:
        with self._lock:
            limit = grant.max_calls
            used = self._used.get(grant.grant_id, 0)
            if limit < 0:
                return False, used, limit
            if used >= limit:
                return False, used, limit
            used += 1
            self._used[grant.grant_id] = used
            return True, used, limit

    def used(self, grant_id: str) -> int:
        with self._lock:
            return self._used.get(grant_id, 0)


class ActionKernel:
    """Only component allowed to dispatch a concrete side-effecting action.

    The Kernel accepts untrusted ActionRequests and optional confirmations. It
    resolves authoritative manifests and grants internally from trusted stores.
    Caller-supplied manifests/grants are not accepted at this boundary.
    """

    def __init__(
        self,
        memory: MemoryOSAdapter,
        authority: AuthorityEngine,
        registry: CapabilityRegistry,
        grant_authority: GrantAuthority,
        device_agent: LocalDeviceAgent,
        verifier: VerificationSubsystem,
        audit: AuditLog,
        observability: Observability,
        lease_issuer: LeaseIssuer,
        lease_registry: LeaseRegistry,
        security_epoch: SecurityEpoch,
        session_validator,
    ) -> None:
        self.memory = memory
        self.authority = authority
        self.registry = registry
        self.grant_authority = grant_authority
        self.device_agent = device_agent
        self.verifier = verifier
        self.audit = audit
        self.observability = observability
        self.__lease_issuer = lease_issuer
        self.__lease_registry = lease_registry
        self.security_epoch = security_epoch
        self.session_validator = session_validator
        self.grant_calls = GrantCallLedger()
        self._global_stop = threading.Event()
        self._cancel_events: Dict[str, threading.Event] = {}
        self._idempotency: Dict[str, _IdempotencyRecord] = {}
        self._lock = threading.RLock()

    def execute(self, action: ActionRequest | ActionSnapshot, session: Session, confirmation: Confirmation | None = None) -> ActionResult:
        try:
            action = action_snapshot(action)
        except Exception as exc:  # noqa: BLE001 - snapshot creation is part of trust-boundary validation
            action_id = getattr(action, "action_id", "unknown")
            evidence = {"reason": "action_snapshot_failed", "exception_type": type(exc).__name__, "exception_message": str(exc)}
            try:
                self.audit.append("action.snapshot_failed", getattr(session, "principal_id", "unknown"), getattr(action, "task_id", None), action_id, evidence)
            except Exception:
                pass
            return ActionResult(action_id, ActionStatus.FAILED, "action_snapshot_failed", evidence=evidence)
        if self._global_stop.is_set():
            actor = session.principal_id if session is not None else "unknown"
            self.audit.append("action.stopped_before_session_validation", actor, action.task_id, action.action_id, {"security_epoch": self.security_epoch.current()})
            return ActionResult(action.action_id, ActionStatus.STOPPED, "emergency stop is active")

        session_validation = self.session_validator.validate_for_action(
            session,
            owner_id=action.owner_id,
            principal_id=action.principal_id,
            device_id=action.device_id,
            security_epoch=self.security_epoch.current(),
        )
        if not session_validation.valid or session_validation.session is None:
            actor = session.principal_id if session is not None else "unknown"
            reason = f"session_authority_{session_validation.reason}"
            self.audit.append("session.validation_failed", actor, action.task_id, action.action_id, {"reason": reason, "security_epoch": self.security_epoch.current()})
            return ActionResult(action.action_id, ActionStatus.DENIED, reason, evidence={"reason": reason})
        session = session_validation.session

        digest = action.digest()
        cancel_event: threading.Event | None = None
        wait_event: threading.Event | None = None
        with self._lock:
            existing = self._idempotency.get(action.idempotency_key)
            if existing is not None:
                if existing.digest != digest:
                    self.observability.security("idempotency_conflict", "high", session.principal_id, "retry changed action digest", action.action_id, action.task_id)
                    self.audit.append("idempotency.conflict", session.principal_id, action.task_id, action.action_id, {"existing_digest": existing.digest, "attempt_digest": digest})
                    return ActionResult(action.action_id, ActionStatus.FAILED, "retry changed target/parameters/scope; no dispatch", evidence={"reason": "idempotency_conflict"})
                if existing.result is not None:
                    return existing.result
                wait_event = existing.event
            else:
                wait_event = threading.Event()
                self._idempotency[action.idempotency_key] = _IdempotencyRecord(digest, wait_event)
                cancel_event = threading.Event()
                self._cancel_events[action.action_id] = cancel_event

        if cancel_event is not None:
            return self._execute_reserved(action, session, confirmation, cancel_event)

        self.audit.append("idempotency.await_original", session.principal_id, action.task_id, action.action_id, {"digest": digest})
        assert wait_event is not None
        wait_event.wait(timeout=self._idempotency_wait_timeout_seconds(action, session))
        with self._lock:
            existing = self._idempotency.get(action.idempotency_key)
            if existing is not None and existing.result is not None:
                return existing.result
        return ActionResult(action.action_id, ActionStatus.UNKNOWN, "idempotent original action still in progress or unavailable", evidence={"reason": "idempotency_in_progress"})

    def _execute_reserved(
        self,
        action: ActionSnapshot,
        session: Session,
        confirmation: Confirmation | None,
        cancel_event: threading.Event,
    ) -> ActionResult:
        state: dict[str, object] = {
            "lease": None,
            "authorization": None,
            "execution": None,
            "dispatch_started": False,
        }
        try:
            return self._execute_reserved_impl(action, session, confirmation, cancel_event, state)
        except Exception as exc:  # noqa: BLE001 - reserved lifecycle containment boundary
            return self._handle_internal_exception(action, session, exc, state)

    def _execute_reserved_impl(
        self,
        action: ActionSnapshot,
        session: Session,
        confirmation: Confirmation | None,
        cancel_event: threading.Event,
        state: dict[str, object],
    ) -> ActionResult:
        self._audit("action.proposed", session.principal_id, action, {"digest": action.digest(), "capability": action.capability_id})
        if self._global_stop.is_set():
            result = ActionResult(action.action_id, ActionStatus.STOPPED, "emergency stop is active")
            return self._finish(action, result, session.principal_id)

        resolution = self.registry.resolve(action.capability_id, action.operation)
        if not resolution.available or resolution.manifest is None:
            self._audit("capability.resolution_failed", session.principal_id, action, {"reason": resolution.reason})
            result = ActionResult(action.action_id, ActionStatus.DENIED, "capability_" + resolution.reason, evidence={"reason": resolution.reason})
            return self._finish(action, result, session.principal_id)
        manifest = resolution.manifest
        grant_resolution = self.grant_authority.resolve(action, session, manifest)
        if not grant_resolution.allowed or grant_resolution.grant is None:
            self._audit("grant.resolution_failed", session.principal_id, action, {"reason": grant_resolution.reason})
            result = ActionResult(action.action_id, ActionStatus.DENIED, "grant_" + grant_resolution.reason, evidence={"reason": grant_resolution.reason})
            return self._finish(action, result, session.principal_id)
        grant = grant_resolution.grant

        governance = self._governance(action, manifest)
        authorization = self.authority.evaluate(action, session, manifest, grant, governance, confirmation, security_epoch=self.security_epoch.current())
        state["authorization"] = authorization
        self._audit(
            "action.authorization",
            session.principal_id,
            action,
            {"allowed": authorization.allowed, "reason": authorization.reason, "governance": authorization.governance.value, "security_epoch": self.security_epoch.current()},
        )
        if not authorization.allowed:
            authorization_required_reasons = {
                "confirmation_required",
                "confirmation_invalid_or_digest_mismatch",
                "insufficient_session_assurance",
                "confirmation_insufficient_assurance",
                "confirmation_policy_version_invalid",
                "confirmation_security_epoch_invalid",
            }
            status = ActionStatus.AUTHORIZATION_REQUIRED if authorization.reason in authorization_required_reasons else ActionStatus.DENIED
            result = ActionResult(action.action_id, status, authorization.reason, authorization=authorization)
            return self._finish(action, result, session.principal_id)

        if self._global_stop.is_set() or cancel_event.is_set():
            result = ActionResult(action.action_id, ActionStatus.CANCELLED, "cancelled before dispatch", authorization=authorization)
            return self._finish(action, result, session.principal_id)

        issued_at = utc_now()
        effective_timeout = self._effective_timeout_seconds(action, session, manifest, issued_at)
        if effective_timeout <= 0:
            self._audit(
                "action.timeout_denied",
                session.principal_id,
                action,
                {
                    "caller_timeout_seconds": action.timeout_seconds,
                    "manifest_timeout_seconds": manifest.timeout_seconds,
                    "device_timeout_seconds": getattr(self.device_agent.policy.limits, "max_operation_seconds", None),
                    "session_remaining_seconds": max(0.0, (session.expires_at - issued_at).total_seconds()),
                },
            )
            result = ActionResult(action.action_id, ActionStatus.DENIED, "effective_timeout_unavailable", authorization=authorization)
            return self._finish(action, result, session.principal_id)

        reserved, used, limit = self.grant_calls.reserve(grant)
        if not reserved:
            self._audit("grant.call_rejected", session.principal_id, action, {"grant_id": grant.grant_id, "used": used, "max_calls": limit})
            result = ActionResult(action.action_id, ActionStatus.DENIED, "grant_max_calls_exhausted", authorization=authorization)
            return self._finish(action, result, session.principal_id)
        self._audit("grant.call_consumed", session.principal_id, action, {"grant_id": grant.grant_id, "used": used, "max_calls": limit})

        lease = self.__lease_issuer.issue(
            action,
            lease_id=authorization.lease_id or action.action_id,
            issued_at=issued_at,
            expires_at=min(session.expires_at, issued_at + timedelta(seconds=effective_timeout)),
            policy_version=authorization.policy_version,
            security_epoch=self.security_epoch.current(),
            max_calls=1,
        )
        state["lease"] = lease
        self._audit(
            "action.ready",
            session.principal_id,
            action,
            {
                "authorization_id": authorization.decision_id,
                "lease_id": lease.lease_id,
                "security_epoch": lease.security_epoch,
                "caller_timeout_seconds": action.timeout_seconds,
                "effective_timeout_seconds": effective_timeout,
                "manifest_timeout_seconds": manifest.timeout_seconds,
            },
        )
        self._audit("action.executing", session.principal_id, action, {})
        try:
            state["dispatch_started"] = True
            execution = self.device_agent.execute(action, cancel_event, lease)
            state["execution"] = execution
        except Exception as exc:  # noqa: BLE001 - contain unexpected device failures truthfully
            lease_revoked = self.__lease_registry.revoke(lease.lease_id)
            effect_started = not lease_revoked
            status = ActionStatus.FAILED if lease_revoked else ActionStatus.UNKNOWN
            evidence = {"reason": "unexpected_device_exception", "exception_type": type(exc).__name__, "lease_revoked": lease_revoked}
            self._safe_audit("action.execution_exception", session.principal_id, action, evidence)
            execution = ExecutionObservation(action.action_id, False, effect_started, evidence, error="unexpected_device_exception")
            result = ActionResult(action.action_id, status, "unexpected_device_exception", authorization, execution, evidence=evidence)
            return self._finish(action, result, session.principal_id)

        if execution.cancelled and not execution.effect_started:
            self.__lease_registry.revoke(lease.lease_id)
            result = ActionResult(action.action_id, ActionStatus.CANCELLED, execution.error or "cancelled", authorization, execution)
            return self._finish(action, result, session.principal_id)
        if not execution.executed:
            self.__lease_registry.revoke(lease.lease_id)
            status = ActionStatus.UNKNOWN if execution.effect_started else ActionStatus.FAILED
            result = ActionResult(action.action_id, status, execution.error or "execution failed", authorization, execution)
            return self._finish(action, result, session.principal_id)

        self._audit("action.completed", session.principal_id, action, {"operation_id": execution.operation_id, "effect_started": execution.effect_started})
        verification = self.verifier.verify(action, execution)
        self._audit("action.verification", session.principal_id, action, {"status": verification.status.value, "confidence": verification.confidence_class, "evidence": verification.evidence})
        if verification.status == VerificationStatus.VERIFIED:
            result = ActionResult(action.action_id, ActionStatus.VERIFIED, verification.message, authorization, execution, verification, verification.evidence)
        elif verification.status == VerificationStatus.UNKNOWN:
            result = ActionResult(action.action_id, ActionStatus.UNKNOWN, verification.message, authorization, execution, verification, verification.evidence)
        else:
            result = ActionResult(action.action_id, ActionStatus.FAILED, verification.message, authorization, execution, verification, verification.evidence)
        return self._finish(action, result, session.principal_id)

    def _handle_internal_exception(self, action: ActionSnapshot, session: Session, exc: Exception, state: dict[str, object]) -> ActionResult:
        lease = state.get("lease")
        execution = state.get("execution")
        dispatch_started = bool(state.get("dispatch_started"))
        lease_revoked = None
        if lease is not None:
            try:
                lease_revoked = self.__lease_registry.revoke(lease.lease_id)
            except Exception:  # noqa: BLE001 - best-effort cleanup only
                lease_revoked = False
        effect_started = False
        if isinstance(execution, ExecutionObservation):
            effect_started = execution.effect_started
        elif dispatch_started and lease_revoked is False:
            effect_started = True
        status = ActionStatus.UNKNOWN if effect_started else ActionStatus.FAILED
        evidence = {
            "reason": "unexpected_internal_exception",
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "dispatch_started": dispatch_started,
            "effect_started": effect_started,
            "lease_revoked": lease_revoked,
        }
        self._safe_audit("action.internal_exception", session.principal_id if session else "unknown", action, evidence)
        try:
            self.observability.security("internal_exception", "high", session.principal_id if session else "unknown", str(exc), action.action_id, action.task_id)
        except Exception:
            pass
        if not isinstance(execution, ExecutionObservation):
            execution = ExecutionObservation(action.action_id, False, effect_started, evidence, error="unexpected_internal_exception")
        result = ActionResult(
            action.action_id,
            status,
            "unexpected_internal_exception",
            state.get("authorization"),
            execution,
            evidence=evidence,
        )
        return self._finish(action, result, session.principal_id if session else "unknown")

    def _effective_timeout_seconds(
        self,
        action: ActionSnapshot,
        session: Session,
        manifest: CapabilityManifest,
        now=None,
    ) -> float:
        """Trusted runtime ceiling for a caller-requested action timeout."""

        now = now or utc_now()
        try:
            requested = float(action.timeout_seconds)
            manifest_timeout = float(manifest.timeout_seconds)
            device_timeout = float(getattr(self.device_agent.policy.limits, "max_operation_seconds", manifest_timeout))
        except (TypeError, ValueError):
            return 0.0
        if requested <= 0 or manifest_timeout <= 0 or device_timeout <= 0:
            return 0.0
        session_remaining = max(0.0, (session.expires_at - now).total_seconds())
        authoritative_ceiling = min(manifest_timeout, device_timeout, session_remaining)
        return max(0.0, min(requested, authoritative_ceiling))

    def _idempotency_wait_timeout_seconds(self, action: ActionSnapshot, session: Session) -> float:
        resolution = self.registry.resolve(action.capability_id, action.operation)
        if not resolution.available or resolution.manifest is None:
            return 10.0
        effective = self._effective_timeout_seconds(action, session, resolution.manifest)
        if effective <= 0:
            return 1.0
        return min(effective + 1.0, float(resolution.manifest.timeout_seconds) + 1.0)

    def cancel(self, action_id: str, actor: str = "owner") -> bool:
        with self._lock:
            event = self._cancel_events.get(action_id)
            if event is None:
                self.audit.append("action.cancel_unknown", actor, None, action_id, {"reason": "action_not_active"})
                return False
            event.set()
        self.device_agent.cancel(action_id)
        self.audit.append("action.cancel_requested", actor, None, action_id, {})
        return True

    def emergency_stop(self, actor: str = "owner", reason: str = "requested") -> int:
        self._global_stop.set()
        epoch = self.security_epoch.bump()
        with self._lock:
            for event in self._cancel_events.values():
                event.set()
        self.__lease_registry.revoke_all()
        self.device_agent.stop()
        self.audit.append("emergency_stop.activated", actor, None, None, {"reason": reason, "security_epoch": epoch})
        self.observability.security("emergency_stop", "critical", actor, reason)
        return epoch

    def resume_after_stop(self, session: Session) -> bool:
        validation = self.session_validator.validate_for_action(
            session,
            owner_id=session.owner_id,
            principal_id=session.principal_id,
            device_id=session.device_id,
            security_epoch=self.security_epoch.current(),
        )
        if not validation.valid or validation.session is None or validation.session.owner_id != validation.session.principal_id:
            self.audit.append("emergency_stop.resume_denied", session.principal_id if session else "unknown", None, None, {"reason": "invalid_owner_session"})
            return False
        self._global_stop.clear()
        self.device_agent.clear_stop()
        self.audit.append("emergency_stop.cleared", validation.session.principal_id, None, None, {"security_epoch": self.security_epoch.current()})
        return True

    def active_lease_count(self) -> int:
        return self.__lease_registry.active_count()

    def _governance(self, action: ActionSnapshot, manifest: CapabilityManifest) -> GovernanceDecision:
        if not manifest.requires_memory_governance:
            return GovernanceDecision(GovernanceState.NOT_APPLICABLE, "not-applicable", action.owner_id, action.digest(), "capability declares no governed memory dependency", "zorq-local")
        return self.memory.governance(action)

    def _finish(self, action: ActionSnapshot, result: ActionResult, actor: str) -> ActionResult:
        with self._lock:
            record = self._idempotency.get(action.idempotency_key)
            if record is not None:
                record.result = result
                record.event.set()
                if not (result.execution is not None and result.status in {ActionStatus.VERIFIED, ActionStatus.UNKNOWN, ActionStatus.FAILED, ActionStatus.CANCELLED}):
                    self._idempotency.pop(action.idempotency_key, None)
            self._cancel_events.pop(action.action_id, None)
        self.observability.increment("actions." + result.status.value.lower())
        return result

    def _audit(self, event_type: str, actor: str, action: ActionSnapshot, payload: dict) -> None:
        self.audit.append(event_type, actor, action.task_id, action.action_id, payload)

    def _safe_audit(self, event_type: str, actor: str, action: ActionSnapshot, payload: dict) -> None:
        try:
            self._audit(event_type, actor, action, payload)
        except Exception:
            pass
