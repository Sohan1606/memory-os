"""Capability manifests, registry, and Phase 2.6 safe capability definitions."""

from __future__ import annotations

from collections.abc import Mapping as MappingABC, Set as SetABC
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Any, Mapping

from .contracts import CapabilityManifest, CapabilityState, ConfirmationMode, RiskLevel, sha256_digest


@dataclass(frozen=True)
class CapabilityResolution:
    capability_id: str
    available: bool
    manifest: CapabilityManifest | None
    reason: str


class CapabilityRegistry:
    """Authoritative Phase 2 registry mutable only during trusted composition."""

    def __init__(self) -> None:
        self._manifests: dict[str, CapabilityManifest] = {}
        self._sealed = False

    def register(self, manifest: CapabilityManifest) -> None:
        if self._sealed:
            raise PermissionError("capability registry is sealed; runtime installation is unavailable in Phase 2.6")
        frozen = freeze_manifest(manifest)
        if frozen.capability_id in self._manifests:
            raise ValueError(f"capability already registered: {frozen.capability_id}")
        if frozen.state not in {CapabilityState.REVIEWED, CapabilityState.ENABLED}:
            raise ValueError("only reviewed/enabled capabilities may be registered")
        if frozen.risk > RiskLevel.R1:
            raise ValueError("Phase 2 registry rejects capabilities above R1")
        if not frozen.operations:
            raise ValueError("capability must declare operations")
        self._manifests[frozen.capability_id] = frozen

    def seal(self) -> None:
        self._sealed = True

    @property
    def sealed(self) -> bool:
        return self._sealed

    def resolve(self, capability_id: str, operation: str | None = None) -> CapabilityResolution:
        manifest = self._manifests.get(capability_id)
        if manifest is None:
            return CapabilityResolution(capability_id, False, None, "REPORT_UNAVAILABLE")
        if manifest.state != CapabilityState.ENABLED:
            return CapabilityResolution(capability_id, False, manifest, f"capability_{manifest.state.value}")
        if operation is not None and operation not in manifest.operations:
            return CapabilityResolution(capability_id, False, manifest, "operation_unavailable")
        return CapabilityResolution(capability_id, True, manifest, "available")

    def all_manifests(self) -> tuple[CapabilityManifest, ...]:
        return tuple(self._manifests.values())


def deep_freeze(value: Any) -> Any:
    """Recursively freeze manifest-owned structures.

    This is in-process object-integrity hardening only. It is not cryptographic
    immutability and not a sandbox against arbitrary malicious interpreter code.
    """

    if isinstance(value, MappingABC):
        return MappingProxyType({key: deep_freeze(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, list):
        return tuple(deep_freeze(item) for item in value)
    if isinstance(value, SetABC) and not isinstance(value, (str, bytes, bytearray, frozenset)):
        return frozenset(deep_freeze(item) for item in value)
    if isinstance(value, frozenset):
        return frozenset(deep_freeze(item) for item in value)
    if isinstance(value, (str, int, float, bool, type(None), RiskLevel, CapabilityState, ConfirmationMode)):
        return value
    raise TypeError(f"unsupported manifest value type for deep freeze: {type(value).__name__}")


def _frozen_mapping(values: Mapping[str, object]) -> Mapping[str, object]:
    return deep_freeze(values)


def freeze_manifest(manifest: CapabilityManifest) -> CapabilityManifest:
    """Return a manifest with recursively immutable security-relevant structures."""

    return replace(
        manifest,
        operations=tuple(deep_freeze(tuple(manifest.operations))),
        permissions=tuple(deep_freeze(tuple(manifest.permissions))),
        input_schema=_frozen_mapping(manifest.input_schema),
        output_schema=_frozen_mapping(manifest.output_schema),
        resource_limits=_frozen_mapping(manifest.resource_limits),
        audit_events=tuple(deep_freeze(tuple(manifest.audit_events))),
    )


def manifest_digest(manifest: CapabilityManifest) -> str:
    """Digest the authoritative manifest identity and security material.

    This is an in-process integrity/keying digest, not a signature.
    """

    return sha256_digest({
        "capability_id": manifest.capability_id,
        "version": manifest.version,
        "operations": manifest.operations,
        "permissions": manifest.permissions,
        "risk": manifest.risk.value,
        "input_schema": manifest.input_schema,
        "output_schema": manifest.output_schema,
        "cancellation": manifest.cancellation,
        "verification": manifest.verification,
        "audit_events": manifest.audit_events,
        "failure_behavior": manifest.failure_behavior,
        "timeout_seconds": manifest.timeout_seconds,
        "resource_limits": manifest.resource_limits,
        "state": manifest.state.value,
        "requires_memory_governance": manifest.requires_memory_governance,
        "confirmation_mode": manifest.confirmation_mode.value,
    })


def phase2_manifests() -> tuple[CapabilityManifest, ...]:
    """Return only the narrow capabilities permitted in Phase 2.6."""
    common_limits = {"max_calls": 1, "max_seconds": 5, "max_output_bytes": 16_384}
    return (
        CapabilityManifest(
            capability_id="local.time",
            version="1.0.0",
            operations=("read_current_time",),
            permissions=("local.time.read",),
            risk=RiskLevel.R0,
            input_schema={},
            output_schema={"iso_time": "string", "timezone": "string"},
            cancellation="instant_no_side_effect",
            verification="direct_local_observation",
            audit_events=("action.proposed", "action.completed", "action.verified"),
            failure_behavior="REPORT_FAILED",
            timeout_seconds=2,
            resource_limits=common_limits,
            requires_memory_governance=False,
            confirmation_mode=ConfirmationMode.NONE,
            description="Read current UTC time from the local runtime.",
        ),
        CapabilityManifest(
            capability_id="device.metadata",
            version="1.0.0",
            operations=("inspect_device_metadata",),
            permissions=("device.metadata.read",),
            risk=RiskLevel.R0,
            input_schema={},
            output_schema={"platform": "string", "release": "string", "machine": "string"},
            cancellation="instant_no_side_effect",
            verification="direct_local_observation",
            audit_events=("action.proposed", "action.completed", "action.verified"),
            failure_behavior="REPORT_FAILED",
            timeout_seconds=2,
            resource_limits=common_limits,
            requires_memory_governance=False,
            confirmation_mode=ConfirmationMode.NONE,
            description="Inspect bounded non-secret device metadata.",
        ),
        CapabilityManifest(
            capability_id="filesystem.approved",
            version="1.0.0",
            operations=("inspect_directory", "create_directory", "create_text_file", "read_text_file"),
            permissions=("filesystem.approved.read", "filesystem.approved.write"),
            risk=RiskLevel.R1,
            input_schema={"path": "approved-path", "content": "text-optional"},
            output_schema={"path": "string", "entries": "bounded-list", "content": "text-optional"},
            cancellation="before_commit_only",
            verification="postcondition_readback_or_fail_closed_output_limit",
            audit_events=("action.proposed", "action.ready", "action.executing", "action.completed", "action.verification"),
            failure_behavior="REPORT_FAILED_OR_UNKNOWN",
            timeout_seconds=5,
            resource_limits={**common_limits, "max_file_bytes": 1_048_576, "max_entries": 256},
            requires_memory_governance=True,
            confirmation_mode=ConfirmationMode.EXPLICIT,
            description="Bounded filesystem operations under explicitly approved roots.",
        ),
    )
