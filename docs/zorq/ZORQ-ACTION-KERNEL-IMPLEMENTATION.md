# ZORQ ACTION KERNEL IMPLEMENTATION — PHASE 2.6

**Status:** authoritative control-plane dispatch gate with action snapshotting, execution-barrier coordination, and lifecycle-wide failure containment; not production-ready and not a security approval.

## Purpose and authority

`zroq.action_kernel.ActionKernel` is the only component in this project that dispatches a concrete action to the local Device Agent. It does not parse natural language, infer identity, install capabilities, define grants, or create policy.

The normal Kernel API is:

```python
kernel.execute(action, session, confirmation=None)
```

The Kernel treats the caller-provided `ActionRequest` as untrusted input. Caller-supplied manifests/grants are not API parameters and are not authority.

## Phase 2.6 trust-boundary invariant

```text
UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot
```

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

At `execute()` entry, the Kernel creates an `ActionSnapshot`. The internal variable used for the rest of the lifecycle is the snapshot. The original `ActionRequest` is not consulted for digest, idempotency, authority, lease, Device Agent dispatch, verification, or audit after that point.

If snapshot creation fails because parameters contain unsupported action values, execution fails closed before authorization or dispatch.

## Phase 2.6 invariants

1. Caller-supplied `CapabilityManifest` and `PermissionGrant` objects are not Kernel API parameters.
2. Capability authority comes from sealed `CapabilityRegistry` only.
3. Grant authority comes from sealed `GrantAuthority` only.
4. Caller-supplied risk, confirmation mode, memory-governance flag, resource limit, timeout, expected effect, verification requirement, or call budget cannot lower authoritative policy.
5. Caller-owned mutable `ActionRequest` state is canonicalized/deep-frozen into `ActionSnapshot` before it becomes authoritative.
6. Caller-created substitute grants cannot reset `max_calls`; accounting is keyed to the authoritative grant ID.
7. Session dataclasses are revalidated through the authoritative session store and current security epoch.
8. Every dispatched side effect has a capability/version, target/parameters, snapshot digest, authoritative grant, call record, effective trusted timeout, and issuer-authenticated one-use lease.
9. Confirmation validates the snapshot digest; confirmation for A cannot silently approve mutated B.
10. Idempotency is keyed to the snapshot digest; same key/different snapshot is rejected before dispatch.
11. Mutation effects must pass the Device Agent pre-commit barrier after lease verification and immediately before irreversible filesystem commit.
12. Internal exceptions in the reserved execution lifecycle finalize idempotency and wake waiters.
13. Completed execution and verified outcome remain separate.
14. Unknown verification remains `UNKNOWN`.
15. Emergency stop increments security epoch, invalidates active leases, revokes sessions through the composition root, blocks new dispatch, and prevents pre-commit actions from crossing the barrier.
16. Generic command execution and generic application launch are absent.

## Lifecycle

```text
UNTRUSTED ACTIONREQUEST
→ SNAPSHOT CREATION / DEEP-FREEZE
→ SESSION VALIDATION
→ IDEMPOTENCY RESERVATION OVER SNAPSHOT DIGEST
→ AUTHORITATIVE CAPABILITY RESOLUTION
→ AUTHORITATIVE GRANT RESOLUTION
→ TRUSTED TIMEOUT / RESOURCE POLICY DERIVATION
→ GOVERNANCE / AUTHORIZATION CHECK
→ GRANT CALL CONSUMPTION
→ LEASE ISSUANCE BOUND TO SNAPSHOT DIGEST
→ READY
→ EXECUTING
→ DEVICE SNAPSHOT REQUIREMENT
→ DEVICE LEASE VERIFICATION
→ DEVICE PRE-COMMIT BARRIER FOR MUTATION
→ COMPLETED
→ VERIFICATION OF SNAPSHOT EFFECT
→ VERIFIED / UNKNOWN / FAILED
```

Additional outcomes are `AUTHORIZATION_REQUIRED`, `DENIED`, `CANCELLED`, and `STOPPED`.

`COMPLETED` is only an execution observation. `VERIFIED` requires an operation-specific verifier to establish the postcondition.

## Snapshot fields

The snapshot captures every security-relevant action field:

```text
action_id, task_id, owner_id, principal_id, device_id, capability_id,
capability_version, operation, parameters, purpose, risk, expected_effect,
verification_requirement, idempotency_key, created_at, timeout_seconds
```

`parameters` is recursively copied/frozen. Mutating the original mapping, replacing the original `parameters` object, or mutating nested dictionaries/lists/custom mappings after snapshot creation cannot affect execution.

## Execution barrier and stop semantics

The Kernel issues a lease bound to the current security epoch and snapshot digest. The Device Agent consumes and verifies the lease, then for mutation operations performs a final barrier immediately before commit:

```text
if stopped/cancelled: block effect
if lease.security_epoch != current_security_epoch: block effect
else: commit
```

A stop before commit is blockable. A stop after commit cannot be made retroactive; the result must be reported truthfully based on execution/verification evidence and may be `VERIFIED`, `FAILED`, or `UNKNOWN`.

Previously authorized actions do not silently resume after emergency-stop recovery because the security epoch has changed and the old session/lease/confirmation are stale.

## Caller-requested timeout vs trusted maximum timeout

`ActionRequest.timeout_seconds` is untrusted caller input and becomes authoritative only as frozen snapshot data. It can only narrow the lease window.

The Kernel computes:

```text
authoritative_ceiling = min(
    manifest.timeout_seconds,
    device_policy.max_operation_seconds,
    session_remaining_time
)

effective_timeout = min(snapshot.timeout_seconds, authoritative_ceiling)
```

Invalid/non-positive requested timeouts fail closed. Lease expiry uses `effective_timeout` directly.

## Action field integrity

The following caller-supplied fields are validated after snapshotting against authoritative state:

- capability ID and operation: resolved through sealed registry;
- capability version: checked against authoritative manifest and grant binding;
- risk: must equal authoritative manifest risk;
- expected effect: must equal authoritative manifest description;
- verification requirement: must equal authoritative manifest verification profile;
- parameters: bounded by grant scope and Device Agent policy;
- purpose: enforced when the authoritative grant binds purpose;
- timeout: can only narrow effective runtime.

## Failure containment

Phase 2.6 wraps the reserved execution lifecycle. Unexpected exceptions from snapshot creation, capability resolution, grant resolution, governance, authority evaluation, timeout derivation, grant accounting, lease issuance, Device Agent dispatch, audit append where feasible, and verification produce a truthful `ActionResult` rather than stranding idempotency.

Containment rules:

- finalize idempotency record;
- signal waiting callers;
- preserve grant accounting conservatively if already consumed;
- revoke unused lease if possible;
- return `FAILED` if absence of effect is established;
- return `UNKNOWN` if effect state cannot be established;
- never return `VERIFIED` because an exception occurred;
- record exception type/message in evidence and best-effort audit/security event.

This is a control-plane containment boundary, not an attempt to hide programmer errors or continue silently.

## Grant call accounting

`PermissionGrant.max_calls` is enforced by process-local `GrantCallLedger` after successful authorization and immediately before lease issuance. The ledger is keyed by the authoritative grant ID. Authorization denials and confirmation denials do not consume calls. VERIFIED, FAILED, UNKNOWN, and CANCELLED execution attempts consume because dispatch authority was used.

This is not durable/distributed accounting and is not claimed as such.

## Idempotency

The Kernel atomically reserves the idempotency key with the snapshot digest. A concurrent same-key/same-digest attempt awaits the original result and does not call the Device Agent. A same-key/different-snapshot attempt is rejected as `idempotency_conflict` without dispatch.

Terminal execution outcomes and internal-exception outcomes may be replayed from the process-local cache. Pre-dispatch authorization/confirmation denials are not cached so the same exact snapshot may be retried after supplying missing confirmation or restoring governance.

## Device dispatch and leases

After authorization, trusted timeout derivation, and grant-call consumption, the Kernel issues a one-use `ExecutionLease` through `LeaseIssuer`. The lease proof is an HMAC over material fields and is registered in `LeaseRegistry`. The local Device Agent verifies and consumes it before execution.

A forged matching dataclass, modified digest, stale lease, wrong-device lease, old-epoch lease, reused lease, direct mutable `ActionRequest`, or lease whose epoch becomes stale before commit is rejected before side effect.

HMAC is an in-process control-plane hardening mechanism only; it is not asymmetric issuer-only authentication across an independent process boundary.

## Limits

This boundary assumes trusted in-process composition. It does not protect against arbitrary malicious code already running with access to Python internals. Python private attributes, mapping proxies, and dataclass immutability are not a sandbox. Phase 2.6 does not claim cryptographic immutability; it closes the in-process caller-owned mutable aliasing gap for action execution.
