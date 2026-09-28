# ZORQ CORE CONTRACTS — PHASE 2.6

**Status:** Implementation contract summary for the standalone Phase 2.6 action-snapshot, execution-integrity, execution-barrier, failure-containment, and filesystem-boundary control-plane slice.

## Identity/session

- **Authority:** local authenticator establishes A2 owner session; MEMORY//OS identity remains canonical in production.
- **Inputs:** owner ID, device ID, configured local factor, presented factor, TTL, current security epoch.
- **Outputs:** session with principal, device, assurance, issued/expiry, active state, security epoch.
- **Invariants:** expired/revoked/stale-epoch sessions fail; device mismatch fails; voice/model cannot create sessions.
- **Trust boundary:** identity abstraction outside cognition and device execution.
- **Failure:** no session or inactive/stale session; no fallback to conversational confidence.
- **Verification:** wrong factor, expiry, revocation, device mismatch, stale session, epoch invalidation tests.

## MEMORY//OS adapter

- **Authority:** MEMORY//OS canonical cognitive/memory/governance policy remains external.
- **Inputs:** owner/task/purpose/action snapshot, typed operation, contract version.
- **Outputs:** read/propose/confirmed mutation/history/evidence context; governance allow/deny/hold/unavailable/contradictory.
- **Invariants:** no direct store, no fabricated capability, no silent governance fallback.
- **Failure:** unavailable/contradictory governance fails closed for governed effects; governance exceptions are contained by the Kernel reserved lifecycle.
- **Verification:** explicit unavailable, contradictory, and exception containment tests.

## Intent/context/plan

- **Authority:** provider and planner propose only.
- **Inputs:** user text, session context, memory state, registry.
- **Outputs:** `Intent`, `Context`, `Plan`, `PlanStep`, optional `Proposal` objects.
- **Invariants:** no side effect, no authorization, no direct tool/OS calls, no invented capability.
- **Failure:** provider unavailable, ambiguous request, unknown capability, or plan-boundary finding produces safe report/ask.

## Capability registry

- **Authority:** sealed `CapabilityRegistry` is the Kernel's authoritative manifest source.
- **Inputs:** trusted startup manifests only.
- **Outputs:** `CapabilityResolution` containing available/unavailable state and immutable manifest.
- **Invariants:** availability is not permission; registry cannot self-authorize; runtime installation denied after seal; R2+ rejected in Phase 2; manifest-owned mappings/sequences/sets are recursively frozen after trusted composition.
- **Failure:** unknown, disabled, retired, operation-mismatched, unavailable, or exceptioning resolution cannot produce verified execution.
- **Verification:** runtime registration denial, unknown capability, version authority, deep manifest immutability, source alias tests, and internal resolution exception containment.

## Grant authority

- **Authority:** sealed `GrantAuthority` is the Kernel's authoritative grant source.
- **Inputs:** trusted startup grants only.
- **Outputs:** authoritative `PermissionGrant` resolution or denial reason.
- **Invariants:** caller-supplied grants are ignored; grants bind owner, principal, capability, operation, capability version, manifest digest, policy version, roots/scope, purpose, risk ceiling, confirmation mode, max calls, expiry, and active state.
- **Failure:** missing, inactive, expired, version-mismatched, digest-mismatched, policy-mismatched, over-risk, out-of-scope, or purpose-mismatched grant denied before side effect.
- **Verification:** forged/missing grant rejection, call-budget bypass rejection, inactive/version/digest/purpose-mismatched grant tests, concurrent max-call tests, post-seal install denial.

## Authority/confirmation

- **Authority:** deterministic `AuthorityEngine`; confirmation only expresses consent within policy.
- **Inputs:** session, authoritative manifest, authoritative grant, MEMORY//OS governance, action snapshot digest, confirmation.
- **Outputs:** `AuthorizationDecision` with decision and optional lease ID.
- **Invariants:** no model policy; no stale session; no governance weakening; digest mismatch fails; confirmation binds the immutable action snapshot; caller-supplied risk/confirmation/governance/effect/verification cannot lower policy.
- **Failure:** deny, require confirmation, fail closed on unavailable/contradictory governance; authority exceptions are contained by Kernel failure handling.

## Action snapshot

- **Authority:** Action Kernel converts untrusted caller-owned `ActionRequest` objects into immutable-by-value `ActionSnapshot` objects at the trust boundary.
- **Inputs:** all security-relevant action fields: action ID, task ID, owner/principal/device IDs, capability ID/version, operation, parameters, purpose, risk, expected effect, verification requirement, idempotency key, created time, and timeout.
- **Outputs:** snapshot with recursively frozen parameters and deterministic digest.
- **Invariants:** `UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot`; original caller-owned objects are not authoritative after snapshot creation; mappings/sequences/sets/nested combinations are frozen without caller aliases; unsupported values fail closed.
- **Verification:** Phase 2.6 tests for immutability, nested alias protection, replacement of parameter objects, nested dictionary/list/custom mapping mutation, confirmation binding, idempotency conflict, and mutation after authorization/lease issuance/lease verification.

## Action/lease/device

- **Authority:** Action Kernel resolves manifest/grant internally using the snapshot, derives trusted timeout/resource ceilings, contains lifecycle failures, and issues a device-bound lease; Device Agent executes only snapshots with that lease and only operations in its immutable trusted execution table.
- **Inputs:** untrusted ActionRequest at the Kernel boundary, resulting ActionSnapshot internally, session, optional confirmation, device posture, cancellation event.
- **Outputs:** ActionResult, issuer-authenticated ExecutionLease, ExecutionObservation, verification, audit.
- **Invariants:** no direct model-to-OS; exact snapshot digest/target; caller-owned mutable action state is never authoritative after snapshot creation; no arbitrary shell; no generic command/application launch; no unsigned/unissued lease; concurrent idempotency prevents duplicate dispatch; grant `max_calls` is enforced process-locally against authoritative grant ID; caller timeout cannot expand beyond trusted ceiling; pre-commit stop/epoch mismatch blocks mutation.
- **Failure:** denied/failed/unknown/cancelled/stopped; no fake success; internal exceptions finalize idempotency and never return verified.
- **Verification:** real directory creation, readback, path traversal, symlink, forged lease, stale lease, wrong-device lease, epoch invalidation, cancellation, retry, grant accounting, idempotency, Device Agent table checks, timeout ceiling tests, execution barrier tests, action snapshot mutation/aliasing tests.

## Execution barrier

- **Authority:** Device Agent final pre-commit barrier using Kernel-issued lease epoch and local stop/cancel state.
- **Inputs:** action snapshot, cancel event, verified/consumed lease, current security epoch.
- **Outputs:** commit, cancelled observation, or failed observation.
- **Invariants:** stop or stale epoch before commit blocks effect; post-commit effects are reported truthfully and not retroactively denied.
- **Verification:** stop between lease verification and commit, old epoch barrier, stop during commit, post-resume fresh authorization tests.

## Resource and filesystem ceilings

- **Authority:** trusted capability manifests and trusted device policy ceilings; caller-supplied ActionRequest timeout/parameters cannot expand them.
- **Enforced limits:** max_file_bytes, max_output_bytes, max_directory_entries, max_path_length, timeout_seconds/max_operation_seconds.
- **Filesystem boundary:** directory inspection uses non-following metadata and bounded enumeration; mutation posture requires dir-fd open/mkdir support plus `O_NOFOLLOW` and `O_DIRECTORY`.
- **Verification:** Phase 2.2/2.3/2.4/2.5 resource, symlink, large-directory, and posture tests.

## Verification/audit

- **Authority:** verifier classifies observed evidence; audit records facts.
- **Inputs:** execution result and actual filesystem/device state.
- **Outputs:** VERIFIED/UNKNOWN/FAILED and hash-chained events.
- **Invariants:** provider/model claims cannot upgrade evidence; audit records are immutable after append in the implementation path.
- **Failure:** missing/contradictory state is unknown or failed; audit tamper raises integrity error; audit append exceptions during reserved action handling are best-effort recorded/contained where possible.

## Explicit limitation

Phase 2.6 does not claim protection from arbitrary malicious code already executing inside the same Python interpreter. Python private attributes, dataclass immutability, HMAC, and mapping proxies are not a sandbox. Phase 2.6 does not claim cryptographic immutability; action snapshots are in-process object-integrity hardening. Phase 2.6 also does not claim perfect race-free protection against adversarial kernels/filesystems or unverified Windows junction/reparse behavior.
