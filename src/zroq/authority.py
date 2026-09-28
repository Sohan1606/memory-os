"""Deterministic execution authority and policy decision engine."""

from __future__ import annotations

import uuid

from .contracts import (
    ActionRequest,
    ActionSnapshot,
    AssuranceLevel,
    AuthorizationDecision,
    CapabilityManifest,
    Confirmation,
    ConfirmationMode,
    GovernanceDecision,
    GovernanceState,
    PermissionGrant,
    RiskLevel,
    Session,
)


class AuthorityEngine:
    policy_version = "zorq-phase2-policy-v1"
    phase2_max_risk = RiskLevel.R1

    def required_assurance(self, action: ActionRequest | ActionSnapshot, manifest: CapabilityManifest, grant: PermissionGrant) -> AssuranceLevel:
        if action.risk >= RiskLevel.R1 or manifest.confirmation_mode == ConfirmationMode.EXPLICIT or grant.confirmation_mode == ConfirmationMode.EXPLICIT:
            return AssuranceLevel.A2
        return AssuranceLevel.A0

    def evaluate(
        self,
        action: ActionRequest | ActionSnapshot,
        session: Session,
        manifest: CapabilityManifest,
        grant: PermissionGrant,
        governance: GovernanceDecision,
        confirmation: Confirmation | None,
        security_epoch: int = 0,
    ) -> AuthorizationDecision:
        def deny(reason: str, gov: GovernanceState = governance.state) -> AuthorizationDecision:
            return AuthorizationDecision(False, str(uuid.uuid4()), reason, action.risk, False, gov, self.policy_version)

        if not session.is_valid():
            return deny("session_invalid")
        if session.owner_id != action.owner_id or session.principal_id != action.principal_id:
            return deny("session_principal_mismatch")
        if session.device_id != action.device_id:
            return deny("session_device_mismatch")
        if action.risk > self.phase2_max_risk:
            return deny("phase2_risk_ceiling")
        if manifest.state.value != "enabled":
            return deny("capability_not_enabled")
        if action.capability_version != manifest.version or action.operation not in manifest.operations:
            return deny("manifest_operation_mismatch")
        if action.risk != manifest.risk:
            return deny("action_risk_mismatch")
        if action.expected_effect != manifest.description:
            return deny("action_expected_effect_mismatch")
        if action.verification_requirement != manifest.verification:
            return deny("action_verification_requirement_mismatch")
        allowed, grant_reason = grant.allows(action)
        if not allowed:
            return deny("grant_" + grant_reason)
        if manifest.requires_memory_governance and governance.state != GovernanceState.ALLOW:
            return deny("memoryos_governance_" + governance.state.value, governance.state)
        if not manifest.requires_memory_governance and governance.state not in {GovernanceState.NOT_APPLICABLE, GovernanceState.ALLOW}:
            return deny("unexpected_governance_state_" + governance.state.value, governance.state)

        required_assurance = self.required_assurance(action, manifest, grant)
        confirmation_required = required_assurance > AssuranceLevel.A0
        if confirmation_required:
            if confirmation is None:
                return AuthorizationDecision(False, str(uuid.uuid4()), "confirmation_required", action.risk, True, governance.state, self.policy_version)
            if session.assurance < required_assurance:
                return deny("insufficient_session_assurance")
            if not confirmation.is_valid_for(action, session):
                return AuthorizationDecision(False, str(uuid.uuid4()), "confirmation_invalid_or_digest_mismatch", action.risk, True, governance.state, self.policy_version)
            if confirmation.assurance < required_assurance:
                return AuthorizationDecision(False, str(uuid.uuid4()), "confirmation_insufficient_assurance", action.risk, True, governance.state, self.policy_version)
            if confirmation.policy_version != self.policy_version:
                return AuthorizationDecision(False, str(uuid.uuid4()), "confirmation_policy_version_invalid", action.risk, True, governance.state, self.policy_version)
            if confirmation.security_epoch != security_epoch:
                return AuthorizationDecision(False, str(uuid.uuid4()), "confirmation_security_epoch_invalid", action.risk, True, governance.state, self.policy_version)

        return AuthorizationDecision(True, str(uuid.uuid4()), "authorized", action.risk, confirmation_required, governance.state, self.policy_version, lease_id=str(uuid.uuid4()))
