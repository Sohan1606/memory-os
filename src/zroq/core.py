"""Standalone wiring for the ZORQ Phase 2.6 hardened secure core."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import uuid
from typing import Mapping

from .action_kernel import ActionKernel
from .audit import AuditLog
from .authority import AuthorityEngine
from .capabilities import CapabilityRegistry, manifest_digest, phase2_manifests
from .confirmation import issue_confirmation
from .contracts import ActionRequest, Confirmation, DeviceIdentity, PermissionGrant, Session, utc_now
from .device_agent import LocalDeviceAgent
from .grants import GrantAuthority
from .identity import LocalOwnerAuthenticator, SessionManager
from .leases import LeaseIssuer, LeaseRegistry, SecurityEpoch
from .memory import MemoryOSAdapter, UnavailableMemoryOSAdapter
from .observability import Observability
from .orchestrator import Orchestrator
from .planning import DeterministicPlanner, PlanBoundaryReviewer, SpecialistRegistry
from .providers import DeterministicProvider, ModelProvider
from .security import DeviceSecurityPolicy, ResourceLimits
from .verification import VerificationSubsystem


@dataclass(frozen=True)
class CoreConfig:
    owner_id: str
    device_id: str
    owner_secret: str
    approved_roots: tuple[Path, ...]
    audit_path: Path | None = None
    resource_limits: ResourceLimits = ResourceLimits()


class ZorqCore:
    """Phase 2.6 core/control-plane composition root."""

    def __init__(self, config: CoreConfig, memory: MemoryOSAdapter | None = None, provider: ModelProvider | None = None):
        if not config.approved_roots:
            raise ValueError("at least one approved root is required")
        for root in config.approved_roots:
            root = root.resolve(strict=False)
            if not root.exists() or not root.is_dir():
                raise ValueError(f"approved root must already exist and be a directory: {root}")
        self.config = config
        self.memory = memory or UnavailableMemoryOSAdapter()
        self.audit = AuditLog(config.audit_path)
        self.observability = Observability(self.audit)
        self.authority = AuthorityEngine()
        self.security_epoch = SecurityEpoch()
        self.registry = CapabilityRegistry()
        for manifest in phase2_manifests():
            self.registry.register(manifest)
        self.registry.seal()
        self.grant_authority = GrantAuthority(self.authority.policy_version)
        self.device_identity = DeviceIdentity(config.device_id, config.owner_id, "local", "D1", "0.2.6", False)
        self.authenticator = LocalOwnerAuthenticator(config.owner_id, config.owner_secret)
        self.sessions = SessionManager(self.authenticator, self.device_identity, security_epoch_provider=self.security_epoch.current)
        self.device_policy = DeviceSecurityPolicy(
            approved_roots=tuple(root.resolve(strict=False) for root in config.approved_roots),
            limits=self._effective_resource_limits(config.resource_limits),
        )
        lease_registry = LeaseRegistry()
        lease_issuer = LeaseIssuer(lease_registry)
        execution_capabilities = tuple(
            (manifest.capability_id, manifest.version, operation)
            for manifest in self.registry.all_manifests()
            for operation in manifest.operations
        )
        self.device_agent = LocalDeviceAgent(
            config.device_id,
            config.owner_id,
            self.device_policy,
            lease_issuer.verifier(),
            self.security_epoch.current,
            self.authority.policy_version,
            execution_capabilities,
        )
        self.verifier = VerificationSubsystem(self.device_policy)
        self._install_owner_grants()
        self.grant_authority.seal()
        self.kernel = ActionKernel(
            self.memory,
            self.authority,
            self.registry,
            self.grant_authority,
            self.device_agent,
            self.verifier,
            self.audit,
            self.observability,
            lease_issuer,
            lease_registry,
            self.security_epoch,
            self.sessions,
        )
        self.provider = provider or DeterministicProvider()
        self.orchestrator = Orchestrator(
            self.memory,
            self.provider,
            DeterministicPlanner(),
            SpecialistRegistry((PlanBoundaryReviewer(),)),
            self.registry,
            self.kernel,
        )
        self.audit.append("core.boot", config.owner_id, None, None, {"version": "0.2.6", "memory_adapter": self.memory.contract_version, "capability_count": len(self.registry.all_manifests()), "security_epoch": self.security_epoch.current(), "filesystem_posture": self.device_policy.posture.value})

    @property
    def grants(self) -> Mapping[tuple[str, str, str], PermissionGrant]:
        return self.grant_authority.legacy_public_view()

    def _effective_resource_limits(self, device_limits: ResourceLimits) -> ResourceLimits:
        filesystem = self.registry.resolve("filesystem.approved").manifest
        if filesystem is None:
            return device_limits
        manifest_limits = filesystem.resource_limits
        max_file_bytes = min(device_limits.max_file_bytes, int(manifest_limits.get("max_file_bytes", device_limits.max_file_bytes)))
        max_output_bytes = min(device_limits.max_output_bytes, int(manifest_limits.get("max_output_bytes", device_limits.max_output_bytes)))
        max_directory_entries = min(device_limits.max_directory_entries, int(manifest_limits.get("max_entries", device_limits.max_directory_entries)))
        return ResourceLimits(
            max_file_bytes=max_file_bytes,
            max_directory_entries=max_directory_entries,
            max_output_bytes=max_output_bytes,
            max_operation_seconds=device_limits.max_operation_seconds,
            max_path_length=device_limits.max_path_length,
        )

    def _install_owner_grants(self) -> None:
        for manifest in self.registry.all_manifests():
            digest = manifest_digest(manifest)
            for operation in manifest.operations:
                grant = PermissionGrant(
                    grant_id=str(uuid.uuid4()),
                    owner_id=self.config.owner_id,
                    principal_id=self.config.owner_id,
                    capability_id=manifest.capability_id,
                    operation=operation,
                    capability_version=manifest.version,
                    manifest_digest=digest,
                    policy_version=self.authority.policy_version,
                    allowed_roots=tuple(str(root.resolve(strict=False)) for root in self.config.approved_roots),
                    purpose="",
                    risk_ceiling=manifest.risk,
                    confirmation_mode=manifest.confirmation_mode,
                    max_calls=1,
                )
                self.grant_authority.install(grant)

    def establish_owner_session(self, presented_secret: str) -> Session | None:
        return self.sessions.establish_owner_session(presented_secret)

    def handle(self, user_text: str, session: Session, confirmation: Confirmation | None = None):
        current = self.sessions.get(session.session_id)
        if current is None:
            return self.orchestrator.handle(user_text, replace(session, active=False), confirmation)
        return self.orchestrator.handle(user_text, current, confirmation)

    def build_action(self, session: Session, capability_id: str, operation: str, parameters: Mapping[str, object], purpose: str | None = None) -> ActionRequest:
        resolution = self.registry.resolve(capability_id, operation)
        if not resolution.available or resolution.manifest is None:
            raise ValueError("capability or operation unavailable")
        return ActionRequest(
            action_id=str(uuid.uuid4()),
            task_id=str(uuid.uuid4()),
            owner_id=session.owner_id,
            principal_id=session.principal_id,
            device_id=session.device_id,
            capability_id=capability_id,
            capability_version=resolution.manifest.version,
            operation=operation,
            parameters=dict(parameters),
            purpose=purpose or resolution.manifest.description,
            risk=resolution.manifest.risk,
            expected_effect=resolution.manifest.description,
            verification_requirement=resolution.manifest.verification,
            idempotency_key=str(uuid.uuid4()),
            created_at=utc_now(),
            timeout_seconds=resolution.manifest.timeout_seconds,
        )

    def execute_action(self, action: ActionRequest, session: Session, confirmation: Confirmation | None = None):
        current = self.sessions.get(session.session_id)
        if current is None:
            session = replace(session, active=False)
        else:
            session = current
        return self.kernel.execute(action, session, confirmation)

    def confirm(self, action: ActionRequest, session: Session, method: str = "developer-test") -> Confirmation:
        current = self.sessions.get(session.session_id)
        if current is None:
            raise ValueError("session is not valid")
        return issue_confirmation(
            action,
            current,
            method=method,
            policy_version=self.authority.policy_version,
            security_epoch=self.security_epoch.current(),
        )

    def emergency_stop(self, reason: str = "owner_requested") -> None:
        self.sessions.revoke_all("emergency_stop")
        self.kernel.emergency_stop(self.config.owner_id, reason)

    def resume_after_stop(self, presented_secret: str) -> Session | None:
        session = self.sessions.establish_owner_session(presented_secret)
        if session is None:
            self.audit.append("emergency_stop.resume_denied", self.config.owner_id, None, None, {"reason": "authentication_failed", "security_epoch": self.security_epoch.current()})
            return None
        if not self.kernel.resume_after_stop(session):
            return None
        return session

    def verify_audit(self) -> bool:
        return self.audit.verify_integrity()
