"""Authoritative in-process grant store for Phase 2.6.

The Kernel resolves grants from this sealed store. Caller-supplied grant objects
are not accepted as authority at the execution boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .capabilities import manifest_digest
from .contracts import ActionRequest, ActionSnapshot, CapabilityManifest, PermissionGrant, Session


@dataclass(frozen=True)
class GrantResolution:
    allowed: bool
    grant: PermissionGrant | None
    reason: str


class GrantAuthority:
    def __init__(self, policy_version: str):
        self.policy_version = policy_version
        self._grants: dict[tuple[str, str, str, str], PermissionGrant] = {}
        self._sealed = False

    def install(self, grant: PermissionGrant) -> None:
        if self._sealed:
            raise PermissionError("grant authority is sealed; runtime grant installation is unavailable in Phase 2.6")
        if not grant.capability_version:
            raise ValueError("grant must bind capability_version")
        if not grant.manifest_digest:
            raise ValueError("grant must bind manifest_digest")
        if grant.policy_version != self.policy_version:
            raise ValueError("grant policy version mismatch")
        key = self._key(grant.principal_id, grant.capability_id, grant.operation, grant.capability_version)
        if key in self._grants:
            raise ValueError("grant already installed")
        self._grants[key] = grant

    def seal(self) -> None:
        self._sealed = True

    @property
    def sealed(self) -> bool:
        return self._sealed

    def resolve(self, action: ActionRequest | ActionSnapshot, session: Session, manifest: CapabilityManifest, now: datetime | None = None) -> GrantResolution:
        digest = manifest_digest(manifest)
        key = self._key(session.principal_id, action.capability_id, action.operation, manifest.version)
        grant = self._grants.get(key)
        if grant is None:
            return GrantResolution(False, None, "grant_not_found")
        if grant.capability_version != manifest.version:
            return GrantResolution(False, grant, "grant_capability_version_mismatch")
        if grant.manifest_digest != digest:
            return GrantResolution(False, grant, "grant_manifest_digest_mismatch")
        if grant.policy_version != self.policy_version:
            return GrantResolution(False, grant, "grant_policy_version_mismatch")
        allowed, reason = grant.allows(action, now)
        if not allowed:
            return GrantResolution(False, grant, reason)
        return GrantResolution(True, grant, "grant_allows")

    def _get_for_test(self, principal_id: str, capability_id: str, operation: str, capability_version: str) -> PermissionGrant | None:
        return self._grants.get(self._key(principal_id, capability_id, operation, capability_version))

    def _replace_for_test(self, grant: PermissionGrant) -> None:
        key = self._key(grant.principal_id, grant.capability_id, grant.operation, grant.capability_version)
        self._grants[key] = grant

    def _remove_for_test(self, principal_id: str, capability_id: str, operation: str, capability_version: str) -> PermissionGrant | None:
        return self._grants.pop(self._key(principal_id, capability_id, operation, capability_version), None)

    def public_view(self) -> Mapping[tuple[str, str, str, str], PermissionGrant]:
        return MappingProxyType(self._grants)

    def legacy_public_view(self) -> Mapping[tuple[str, str, str], PermissionGrant]:
        return MappingProxyType({(g.principal_id, g.capability_id, g.operation): g for g in self._grants.values()})

    @staticmethod
    def _key(principal_id: str, capability_id: str, operation: str, capability_version: str) -> tuple[str, str, str, str]:
        return (principal_id, capability_id, operation, capability_version)
