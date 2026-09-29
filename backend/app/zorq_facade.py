"""ZORQ state facade (Phase Z-UI.1, WP-UI-1 — DQ-16).

GET-only, read-mostly endpoints exposing authoritative ZORQ state for the
frontend control surface. The facade composes a real in-process ZorqCore
(restricted configuration: GET-only exposure, no session establishment, no
action dispatch, no known owner secret) and reports ONLY what is true:

* The Action Plane lifecycle contract and the sealed capability registry —
  real contents of the installed zroq-core package.
* A real, hash-chained audit tail of the facade core (boot/lifecycle events).
* The memory-governance integration state of the 3B.2 production adapter.
* Today's truthful runtime values: connection LOCAL, sync NOT-CONFIGURED,
  current device only, voice/multimodal NOT-IMPLEMENTED (Phases 3F/Z-LD.1).

Fail-closed: if zroq-core is not importable in this process, every endpoint
returns a truthful UNAVAILABLE payload — never fabricated state.

Security invariants honored here:
  UI VISIBILITY != AUTHORIZATION — the facade exposes no execution path.
  No secrets cross this boundary (owner secret is generated per boot and
  never returned; no session tokens are issued).
  MEMORY//OS remains the canonical memory/governance subsystem (DQ-17).
"""
from __future__ import annotations

import secrets as _secrets
import threading
from importlib import metadata as _metadata
from typing import Any

from fastapi import APIRouter

from .config import settings

router = APIRouter(prefix="/api/zorq", tags=["zorq"])

_LOCK = threading.Lock()
_CORE_STATE: dict[str, Any] | None = None

# Phase attribution for not-yet-implemented capabilities (truthful labels).
_PHASE_ATTRIBUTION = {
    "voice": "Phase 3F",
    "multimodal": "future phase",
    "sync": "Phase Z-DIST.1",
    "offline_runtime": "Phase Z-LD.1",
    "action_initiation": "action surfaces phase (DQ-16: GET-only first)",
}


def _compose_core() -> dict[str, Any] | None:
    """Compose the restricted facade ZorqCore once (thread-safe).

    Returns None (with the reason recorded) if zroq-core cannot be imported
    or composed — the fail-closed path.
    """
    global _CORE_STATE
    with _LOCK:
        if _CORE_STATE is not None:
            return _CORE_STATE.get("core")
        try:
            from zroq.capabilities import manifest_digest
            from zroq.contracts import sha256_digest
            from zroq.core import CoreConfig, ZorqCore

            try:
                package_version = _metadata.version("zroq-core")
            except Exception:  # pragma: no cover - editable install metadata
                package_version = "unknown"

            # Restricted, truthful composition:
            # - dedicated approved root (exists, real, inert — no action can
            #   be dispatched through this facade anyway: GET-only).
            # - owner secret generated per boot and never exposed; the facade
            #   provides no session-establishment route, so no session can
            #   ever exist for this core through HTTP.
            # - persistent audit file so the audit tail survives restarts
            #   (an audit surface that silently resets would be untruthful).
            facade_root = settings.data_dir / "zorq-facade-root"
            facade_root.mkdir(parents=True, exist_ok=True)
            audit_path = settings.data_dir / "zorq_facade_audit.jsonl"
            config = CoreConfig(
                owner_id="zorq-local",
                device_id="local-device",
                owner_secret=_secrets.token_urlsafe(32),
                approved_roots=(facade_root,),
                audit_path=audit_path,
            )
            core = ZorqCore(config)
            # The REAL sealed registry of the composed core (not a re-derivation).
            manifests = core.registry.all_manifests()
            per_manifest = sorted(
                manifest_digest(m) for m in manifests
            )
            registry_digest = sha256_digest({"manifests": per_manifest})
            _CORE_STATE = {
                "core": core,
                "package_version": package_version,
                "manifests": manifests,
                "registry_digest": registry_digest,
                "reason": "",
            }
            return core
        except Exception as exc:  # pragma: no cover - environment dependent
            _CORE_STATE = {
                "core": None,
                "reason": (
                    "zroq-core is not importable in this backend process "
                    f"({type(exc).__name__}: {exc}). Install it with "
                    "`pip install -e .` at the repository root to enable the "
                    "ZORQ state facade."
                ),
            }
            return None


def _unavailable(reason: str) -> dict[str, Any]:
    return {"available": False, "status": "UNAVAILABLE", "reason": reason}


def _memory_integration() -> dict[str, Any]:
    """Truthful memory-governance integration state (Phase 3B.2)."""
    try:
        from zroq.adapters.memoryos_v10 import MEMORYOS_V10_ADAPTER_VERSION

        return {
            "canonical_authority": "MEMORY//OS",
            "canonical_authority_note": (
                "MEMORY//OS remains the canonical memory/governance subsystem; "
                "the ZORQ frontend renders its state, never replaces it."
            ),
            "production_adapter": {
                "contract_version": MEMORYOS_V10_ADAPTER_VERSION,
                "integration_status": "AVAILABLE_VERIFIED",
                "verification_record": (
                    "docs/zorq/ZORQ-PHASE3B2-VERIFICATION.md (gates G-0…G-5 "
                    "passed at bcdc3df)"
                ),
            },
        }
    except Exception:
        return {
            "canonical_authority": "MEMORY//OS",
            "production_adapter": {
                "integration_status": "UNAVAILABLE",
                "reason": "zroq adapters module not importable in this process",
            },
        }


def _runtime_truth() -> dict[str, Any]:
    """Today's truthful runtime values (§12.1.1 contracts, no simulation)."""
    return {
        "connection": "LOCAL",
        "connection_note": "Served by the local MEMORY//OS backend process.",
        "execution_origins": {
            "LOCAL": "SUPPORTED",
            "REMOTE": "NOT-IMPLEMENTED",
            "DELEGATED": "NOT-IMPLEMENTED",
        },
        "sync": "NOT-CONFIGURED",
        "sync_note": f"No synchronization exists yet ({_PHASE_ATTRIBUTION['sync']}).",
        "devices": "CURRENT-DEVICE-ONLY",
        "voice": {
            "state": "NOT-IMPLEMENTED",
            "phase": _PHASE_ATTRIBUTION["voice"],
            "note": "The voice runtime lands in Phase 3F; interaction "
                    "architecture is prepared, nothing is simulated.",
        },
        "multimodal": {
            "state": "NOT-IMPLEMENTED",
            "phase": _PHASE_ATTRIBUTION["multimodal"],
            "note": "No file/image understanding exists; no fake multimodal.",
        },
    }


def _plane_states() -> list[dict[str, Any]]:
    """The five-plane architecture with live, truthful status per plane."""
    return [
        {
            "plane": "Intelligence",
            "status": "DESIGNED",
            "responsibility": "Observe, orient, interrogate, research, simulate, decide, plan, optimize, propose.",
            "boundary": "Cannot authorize execution or memory governance.",
        },
        {
            "plane": "Continuity",
            "status": "PARTIALLY-IMPLEMENTED",
            "status_note": "Phase 3B personal-continuity slice + 3B.2 production "
                           "MEMORY//OS adapter implemented and verified.",
            "responsibility": "Store, retrieve, track, connect, version, delete, and govern personal history through the MEMORY//OS boundary.",
            "boundary": "Cannot execute real-world actions. Memory retrieval is not authority.",
        },
        {
            "plane": "Interaction",
            "status": "PARTIALLY-IMPLEMENTED",
            "status_note": "Conversational runtime (3C) live; voice is Phase 3F.",
            "responsibility": "Text/voice/file interaction, response generation, speech, interruption, pause/resume, branches, checkpoints.",
            "boundary": "Cannot bypass the Action Kernel; control commands only affect active contexts.",
        },
        {
            "plane": "Action",
            "status": "IMPLEMENTED",
            "status_note": "Phase 2.6 control-plane slice implemented and test-verified; not yet exposed over HTTP.",
            "responsibility": "Authorize, snapshot, lease, execute, cancel, verify, audit.",
            "boundary": "Deterministic authority; LLMs/specialists cannot bypass it.",
        },
        {
            "plane": "Evolution",
            "status": "DESIGNED",
            "responsibility": "Evaluate, propose experiments, canary behavioral changes, monitor and roll back.",
            "boundary": "Cannot silently alter identity, security policy, grants, emergency stop, audit, Action Kernel, or secrets.",
        },
    ]


@router.get("/status")
def zorq_status() -> dict[str, Any]:
    """ZORQ identity + live system state (single authoritative read)."""
    core = _compose_core()
    if core is None:
        reason = (_CORE_STATE or {}).get("reason", "unavailable")
        payload = _unavailable(reason)
        payload["identity"] = "ZORQ"
        return payload

    state = _CORE_STATE
    sessions = getattr(core, "sessions", None)
    active_sessions = len(getattr(sessions, "_sessions", {})) if sessions is not None else 0
    boot_events = [e for e in core.audit.events() if e.event_type == "core.boot"]
    boot = boot_events[-1] if boot_events else None

    return {
        "available": True,
        "identity": "ZORQ",
        "package": "zroq-core",
        "package_version": state["package_version"],
        "core": {
            "boot_event_id": boot.event_id if boot else None,
            "booted_at": boot.timestamp.isoformat() if boot else None,
            "security_epoch": core.security_epoch.current(),
            "filesystem_posture": core.device_policy.posture.value,
            "active_sessions": active_sessions,
            "sessions_note": "The facade composes a restricted core and never "
                             "establishes sessions; 0 is expected and truthful.",
            "audit_integrity": core.audit.verify_integrity(),
        },
        "planes": _plane_states(),
        "memory_integration": _memory_integration(),
        "runtime": _runtime_truth(),
        "capabilities_summary": {
            "count": len(state["manifests"]),
            "registry_digest": state["registry_digest"][:16],
            "visibility_note": "Visibility is not permission.",
        },
    }


@router.get("/capabilities")
def zorq_capabilities() -> dict[str, Any]:
    """Sealed capability registry contents (read-only; visibility ≠ permission)."""
    core = _compose_core()
    if core is None:
        return _unavailable((_CORE_STATE or {}).get("reason", "unavailable"))
    manifests = _CORE_STATE["manifests"]
    capabilities = []
    for m in manifests:
        capabilities.append({
            "capability_id": m.capability_id,
            "version": m.version,
            "operations": list(m.operations),
            "permissions": list(m.permissions),
            "risk": m.risk.name,
            "confirmation_mode": m.confirmation_mode.value,
            "verification": m.verification,
            "cancellation": m.cancellation,
            "timeout_seconds": m.timeout_seconds,
            "requires_memory_governance": m.requires_memory_governance,
            "state": m.state.value,
            "description": m.description,
            "ladder": {
                "visible": True,
                "available": True,
                "available_note": "A verified local implementation exists in the "
                                  "sealed registry.",
                "authorized": "SESSION-BOUND",
                "authorized_note": "Authorization is principal/session-bound and "
                                   "cannot be assessed through this facade.",
                "executable": False,
                "executable_note": "No execution path exists through this facade "
                                   "(GET-only; DQ-16).",
            },
        })
    return {
        "available": True,
        "registry_digest": _CORE_STATE["registry_digest"],
        "sealed": True,
        "visibility_note": "A capability shown here grants nothing. Visibility is "
                           "not permission; execution requires the full Action "
                           "Plane flow (authorization → snapshot → lease → "
                           "execution → verification → audit).",
        "ladder_model": [
            {"level": "VISIBLE", "meaning": "Rendered in the interface (grants nothing)."},
            {"level": "AVAILABLE", "meaning": "Verified implementation operable in the current device + connection context."},
            {"level": "AUTHORIZED", "meaning": "Current backend authorization state permits it for this principal now."},
            {"level": "EXECUTABLE", "meaning": "AVAILABLE + AUTHORIZED + preconditions satisfied (dispatchable now)."},
            {"level": "VERIFIED OUTCOME", "meaning": "Evidence-established result of an executed action (not a capability state)."},
        ],
        "capabilities": capabilities,
    }


@router.get("/actions")
def zorq_actions() -> dict[str, Any]:
    """Action lifecycle contract + live action state (truthfully empty today)."""
    core = _compose_core()
    if core is None:
        return _unavailable((_CORE_STATE or {}).get("reason", "unavailable"))
    return {
        "available": True,
        "lifecycle": [
            {"phase": "PROPOSED", "meaning": "An action is requested. Proposing grants nothing."},
            {"phase": "AUTHORIZATION", "meaning": "Session, grant, capability and confirmation checks run. UI intent never substitutes."},
            {"phase": "SNAPSHOT", "meaning": "An immutable ActionSnapshot captures exactly what was authorized."},
            {"phase": "LEASE", "meaning": "A one-use execution lease is issued against the snapshot."},
            {"phase": "EXECUTION", "meaning": "The device agent executes within the lease and resource limits."},
            {"phase": "VERIFICATION", "meaning": "Evidence is checked. EXECUTED is not VERIFIED; COMPLETED is not VERIFIED."},
            {"phase": "AUDIT", "meaning": "The hash-chained audit trail records the full lifecycle."},
        ],
        "status_vocabulary": [
            "PROPOSED", "AUTHORIZATION_CHECK", "AUTHORIZATION_REQUIRED", "READY",
            "EXECUTING", "COMPLETED", "VERIFICATION", "VERIFIED", "UNKNOWN",
            "FAILED", "DENIED", "CANCEL_REQUESTED", "CANCELLED", "STOPPED",
        ],
        "records": [],
        "records_note": "The action runtime is not yet exposed over HTTP; no "
                        "action records exist. This surface renders the "
                        "lifecycle contract and will show real records when "
                        f"action-initiating endpoints arrive ({_PHASE_ATTRIBUTION['action_initiation']}).",
        "execution_origins": {
            "LOCAL": "SUPPORTED",
            "REMOTE": "NOT-IMPLEMENTED",
            "DELEGATED": "NOT-IMPLEMENTED",
        },
        "one_click_prohibition": "The interface provides no one-click path to "
                                 "irreversible action. Authorization boundaries "
                                 "are always explicit.",
    }


@router.get("/audit")
def zorq_audit() -> dict[str, Any]:
    """Real hash-chained audit tail of the ZORQ core (read-only)."""
    core = _compose_core()
    if core is None:
        return _unavailable((_CORE_STATE or {}).get("reason", "unavailable"))
    events = []
    for e in core.audit.events():
        events.append({
            "sequence": e.sequence,
            "event_id": e.event_id,
            "event_type": e.event_type,
            "timestamp": e.timestamp.isoformat(),
            "actor": e.actor,
            "action_id": e.action_id,
            "payload": dict(e.payload),
            "event_hash": e.event_hash,
        })
    return {
        "available": True,
        "integrity_verified": core.audit.verify_integrity(),
        "chain": "sha256 hash-chained (each event commits to the previous hash)",
        "persistence": "Persistent across backend restarts "
                       "(zorq_facade_audit.jsonl in the backend data dir).",
        "scope_note": "These are the real records of the ZORQ core composed in "
                      "this backend process. User-action records appear here "
                      "once the action runtime is exposed.",
        "events": events,
    }


@router.get("/devices")
def zorq_devices() -> dict[str, Any]:
    """Device & connectivity truth (current device only; no fabricated lists)."""
    core = _compose_core()
    if core is None:
        return _unavailable((_CORE_STATE or {}).get("reason", "unavailable"))
    d = core.device_identity
    return {
        "available": True,
        "current_device": {
            "device_id": d.device_id,
            "owner_id": d.owner_id,
            "device_class": d.device_class,
            "trust_tier": d.trust_tier,
            "agent_version": d.agent_version,
            "attested": d.attested,
        },
        "connection": "LOCAL",
        "execution_origin": "LOCAL",
        "sync": "NOT-CONFIGURED",
        "sync_note": f"Synchronization arrives in {_PHASE_ATTRIBUTION['sync']}; "
                     "nothing is simulated.",
        "authorized_devices": [],
        "authorized_devices_note": "No device enrollment exists yet; this list "
                                   "is truthfully empty.",
        "available_devices": [],
        "available_devices_note": "No discovery mechanism exists yet; this list "
                                  "is truthfully empty. Discovery would not be "
                                  "permission.",
        "distinctions": [
            "DEVICE TRUST ≠ USER AUTHORIZATION",
            "SYNC ≠ AUTHORIZATION",
            "DEVICE CAPABILITY ≠ USER PERMISSION",
        ],
        "state_vocabularies": {
            "connection": ["LOCAL", "ONLINE", "OFFLINE", "DEGRADED", "UNKNOWN", "UNAVAILABLE", "SYNCING"],
            "execution": ["LOCAL", "REMOTE", "DELEGATED"],
            "sync": ["SYNCED", "SYNCING", "PENDING", "CONFLICT", "FAILED", "NOT-CONFIGURED"],
        },
    }
