# ZORQ PHASE 2.2 TRUST-BOUNDARY CLOSURE

**Status:** Implemented hardening notes for current source; not production-ready.

## Implemented

- Action Kernel uses authoritative session validation through `SessionManager.validate_for_action`.
- Retained immutable `Session` objects no longer carry authority if the authoritative session store revoked or expired them.
- Sessions are bound to security epoch; emergency stop revokes existing sessions and bumps epoch.
- `ZorqCore` no longer exposes public `lease_issuer` capability.
- Device Agent receives only verification capability and cannot mint leases.
- Action Kernel stores issuer privately and issues one-use leases only after authorization and grant-call consumption.
- Unexpected Device Agent exceptions are contained and converted into truthful `FAILED`/`UNKNOWN` `ActionResult`s.
- Idempotency waiters are woken on unexpected failures.
- Active leases are revoked on unexpected failure when still unconsumed.
- Output limits are enforced for text reads; oversized output fails closed.
- Registry is sealed after trusted composition; Phase 2.2 has no runtime capability installer.
- Public grants mapping is read-only.
- Filesystem policy reports `SUPPORTED`, `DEGRADED`, or `UNAVAILABLE` posture.

## Verified

Verified by `tests/test_phase22_trust_boundary.py` plus the existing Phase 2.1 regression suite:

- direct lease issuer exposure closed at public object graph;
- stale retained session object rejected at Kernel boundary;
- new session required after stop/recovery;
- unexpected Device Agent exception finalizes state and audit;
- oversized read output fails closed;
- root replacement detected where identity primitives are available;
- parent symlink/substitution denied;
- runtime capability installation denied;
- public grants mapping immutable;
- direct Device Agent and direct Kernel adversarial calls fail closed.

## HMAC trust model

The current HMAC lease proof is **issuer-authenticated within the trusted in-process control plane**. It is not asymmetric cryptographic issuer-only authentication across a process boundary. A malicious actor with arbitrary in-process private attribute access is outside the security claim for Phase 2.2.

Future cross-process Device Agent designs must use a reviewed asymmetric signature mechanism, hardware-backed key storage/attestation where appropriate, and explicit key lifecycle. No such hardware or asymmetric attestation is claimed here.

## Platform-specific posture

Filesystem enforcement is platform-dependent:

- `SUPPORTED`: root identity and directory-file-descriptor mutation primitives are available; mutation operations may proceed with hardening.
- `DEGRADED`: mutation primitives are unavailable; mutation operations fail closed rather than silently falling back.
- `UNAVAILABLE`: root identity primitives are unavailable; policy construction fails closed.

Windows-specific tests are included but skipped unless run on Windows. This package does **not** claim Windows validation because packaging ran on a non-Windows host.

## Still deferred / forbidden

Production MEMORY//OS integration, voice, multilingual runtime, browser/GUI/vision control, proactive operation, self-improvement, cloud integrations, generic shell/PowerShell/interpreter execution, generic command execution, generic application launch, and production deployment remain outside this phase.
