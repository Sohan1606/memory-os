# ZORQ Core — Phase 3B Personal Continuity + Phase 2.6 Action Integrity

This standalone workspace now contains the Phase 2.6 action-snapshot/execution-integrity closure, the Phase 3A/3A.1 domain and contextual-memory contracts, and the Phase 3B local personal-continuity implementation. It is **not** a UI, hosted assistant, broad computer-control system, production MEMORY//OS verification, voice runtime, browser/GUI automation system, or production security approval.

## Current Phase 3B status

Implemented locally: durable text conversation source archive, documented MEMORY//OS adapter harness, owner memory policy, exact/lexical/temporal/semantic-local-concept/relational retrieval, contextual activation runtime, derived memory, timeline, Memory Firewall, deletion propagation for implemented local representations, owner export, restart/idempotency tests, and action-authority separation.

The real MEMORY//OS v10.2.0 implementation/API was not accessible in this environment. Production MEMORY//OS integration is therefore **NOT VERIFIED / BLOCKED BY ENVIRONMENT**; the local documented adapter harness is not a canonical MEMORY//OS substitute.

## Safety posture

- Models/providers produce untrusted intent and plan proposals only.
- The Action Kernel accepts untrusted caller-owned `ActionRequest` objects only at the trust boundary.
- Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.
- Required flow: `UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot`.
- `ActionSnapshot` captures all security-relevant action fields and recursively freezes action parameters so caller mutations after snapshot creation cannot change authorization, lease, Device Agent execution, verification, or audit state.
- Caller-supplied `CapabilityManifest`, `PermissionGrant`, risk, confirmation mode, governance requirement, verification requirement, expected effect, and resource ceilings are not authoritative.
- Caller-requested timeout may narrow execution, but cannot extend beyond the trusted maximum timeout derived from manifest, device policy, and remaining session lifetime.
- The capability registry is sealed after trusted composition and returns manifests with recursively immutable manifest-owned mapping/sequence/set structures.
- The grant authority is sealed after startup; grants bind capability ID, capability version, manifest digest, policy version, scope, risk ceiling, confirmation mode, and max calls.
- Device Agent receives verification-only lease capability, an immutable execution capability table derived from trusted composition, and now rejects caller-owned mutable `ActionRequest` direct calls.
- Filesystem mutation has a final pre-commit execution barrier: stop/cancel/epoch invalidation before commit blocks the effect.
- Directory inspection uses non-following metadata for child classification and does not reveal outside-target type through symlinks.
- Directory inspection enumerates at most `max_directory_entries + 1` entries before failing closed.
- HMAC lease proof is in-process hardening only; it is not asymmetric issuer-only authentication across a process boundary or hardware attestation.
- Phase 2.6 has no administrator privileges, arbitrary shell, command capability, application-launch capability, browser automation, credentials, voice authorization, persistent background daemon, self-modification, cloud integration, or network requirement.

## Project layout

```text
zroq/
  src/zroq/                 # bounded core modules
  tests/                    # deterministic, adversarial, Phase 2.1-2.6 regression tests
  docs/                     # implementation contracts and verification reports
  pyproject.toml
```

## Run tests

From this directory:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Latest run in this environment:

```text
Ran 212 tests in 1.096s

OK (skipped=8)
```

No network, hosted model, or external provider is required for the verified local Phase 3B slice. A real MEMORY//OS repository/API was not available here, so production MEMORY//OS verification remains blocked by environment.

## Current capability set

The Phase 2.6 registry contains only:

- current time;
- bounded device metadata;
- approved-directory inspection;
- approved-directory creation where filesystem posture is SUPPORTED;
- approved text-file creation/read where filesystem posture and output limits allow;
- postcondition verification.

Generic command execution and generic application launch remain removed and forbidden/deferred.

## Resource, snapshot, and execution ceilings

Implemented/enforced resource ceilings:

- `max_file_bytes`: enforced before text-file creation and before text-file readback.
- `max_output_bytes`: enforced before text-file content is returned.
- `max_directory_entries`: enforced with bounded enumeration and fail-closed extra-entry detection.
- `max_path_length`: enforced by device filesystem policy before canonical path use.
- `timeout_seconds`: authoritative maximum comes from capability manifest plus device policy plus remaining session lifetime; caller timeout can only reduce the lease window.

Snapshot semantics:

- the original `ActionRequest` is untrusted after snapshot creation;
- confirmation, idempotency, governance, authorization, timeout, lease, Device Agent execution, verification, and audit use the same `ActionSnapshot`;
- same logical snapshot with the same idempotency key can replay according to policy;
- changed snapshot under the same idempotency key is rejected before dispatch;
- Phase 2.6 does not claim cryptographic immutability or protection from arbitrary malicious code already running inside the same Python interpreter.

Execution barrier semantics:

- before commit: stop/cancel/epoch mismatch is blockable;
- after commit: the effect may have occurred and must be reported truthfully as VERIFIED, FAILED, or UNKNOWN based on evidence;
- Phase 2.6 does not claim cancellation of an OS syscall that already committed.

## Platform posture

Filesystem policy reports explicit posture:

- `SUPPORTED`: root identity, directory-file-descriptor mutation primitives, `O_NOFOLLOW`, and `O_DIRECTORY` are available.
- `DEGRADED`: required mutation primitives are incomplete, so mutation operations fail closed.
- `UNAVAILABLE`: required root identity primitives are unavailable and policy construction fails closed.

Windows tests present: YES.
Windows tests executed here: NO.
Windows security validation: NOT VERIFIED.

## Not implemented by design

Production MEMORY//OS verification, voice, multilingual speech runtime, browser automation, GUI/computer vision, proactive daemon behavior, self-improvement, cloud integrations, generic shell/PowerShell/interpreter execution, generic application launch, broader OS control, and production deployment are outside this slice.
