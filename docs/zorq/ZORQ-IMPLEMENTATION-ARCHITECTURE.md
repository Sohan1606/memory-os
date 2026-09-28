# ZORQ IMPLEMENTATION ARCHITECTURE — PHASE 2.6

**Status:** Phase 2.6 action-snapshot and execution-integrity closure; hardened standalone control-plane slice; not production-ready and not a security approval.

See also:

- `ZORQ-PHASE2.6-PRE-HARDENING-GAP-BASELINE.md`
- `ZORQ-PHASE2.6-ACTION-SNAPSHOT-CLOSURE.md`
- `ZORQ-PHASE2.6-VERIFICATION.md`
- `ZORQ-PHASE2.6-SECURITY-REVIEW.md`

**Project:** standalone `/zroq` project.
**Core principle:** cognition proposes; deterministic control decides; the Action Kernel snapshots untrusted caller actions, resolves trusted authority, derives trusted resource/timeout ceilings, contains internal failures, and dispatches; the local Device Agent executes only immutable snapshots with lease-bound, table-supported operations behind a pre-commit execution barrier; verification observes; audit records.

## Phase 2.6 trust-boundary invariant

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

Normative execution input flow:

```text
UNTRUSTED ActionRequest
  -> canonicalize + deep-freeze
  -> ActionSnapshot
  -> digest / idempotency / capability / grant / governance / confirmation / authorization / timeout / lease / Device Agent / verification / audit
```

`ActionRequest` remains the caller-facing request type. `ActionSnapshot` is the internal trusted action value after boundary crossing. The snapshot captures every security-relevant action field and recursively freezes nested parameters so later caller mutations cannot change the object used for authorization or execution.

## Implemented scope

Implemented control-plane components:

- shared typed contracts, canonical action digests, and immutable-by-value `ActionSnapshot`;
- recursive deep-freeze for action parameters and manifest-owned structures;
- local owner-session abstraction with explicit secret factor;
- device identity abstraction;
- versioned MEMORY//OS adapter interface with unavailable/default fail-closed adapter;
- deterministic provider/planner/specialist review/orchestrator;
- sealed capability registry with recursively immutable manifest-owned structures;
- sealed grant authority binding capability version, manifest digest, policy version, scope, risk ceiling, confirmation mode, max calls, expiry, and state;
- Action Kernel with trust-boundary action snapshotting, internal manifest/grant resolution, trusted timeout derivation, lifecycle-wide failure containment, and idempotency finalization;
- ActionRequest/ActionSnapshot field-integrity validation for risk, version, operation, expected effect, verification requirement, bounded parameters, and timeout ceilings;
- process-local grant call accounting against authoritative grant identity;
- policy-version/security-epoch-bound confirmation abstraction that validates against snapshot digests;
- atomic in-flight idempotency reservation and waiter wakeup on internal exceptions;
- issuer-authenticated one-use execution leases bound to snapshot digests;
- security epoch invalidation for stop/recovery;
- least-privilege local Device Agent with immutable trusted execution capability table, runtime snapshot requirement, and pre-commit mutation barrier;
- bounded filesystem, time, and metadata capabilities;
- non-following directory child classification;
- bounded directory enumeration;
- resource ceiling enforcement for file size, output size, directory entries, path length, and operation lease windows;
- postcondition verification against the executed snapshot;
- cancellation and emergency stop;
- append-only hash-chained audit;
- structured observability/security events;
- deterministic, adversarial, and Phase 2.1/2.2/2.3/2.4/2.5/2.6 regression test suites.

## Explicitly not implemented

The project does not implement or claim:

- a real MEMORY//OS integration;
- hosted model/provider integration;
- voice, speaker verification, or multilingual processing;
- UI/HUD/dashboard;
- arbitrary computer control;
- administrator/root privileges;
- arbitrary shell/PowerShell/cmd;
- generic command execution;
- arbitrary interpreter invocation;
- generic application launch;
- browser automation or browser profiles;
- credentials/passwords/account-security actions;
- payments or financial actions;
- hidden recording;
- background daemon/persistence;
- automatic capability installation/authorization;
- self-modifying security/policy code;
- production deployment or security approval.

## Module boundaries

| Module | Responsibility | Authority | Boundary |
|---|---|---|---|
| `contracts.py` | typed protocol objects, status enums, canonical digests, `ActionSnapshot`, recursive action value freezing | none; data only | pure in-process values; snapshot boundary support |
| `identity.py` | local factor/session lifecycle | establishes narrow session claims | local identity abstraction; no canonical identity replacement |
| `memory.py` | MEMORY//OS adapter protocol and explicit unavailable/degraded states | canonical authority remains external | no direct store access; governance receives snapshots |
| `providers.py` | deterministic/local model proposal interface | proposal only | untrusted provider output |
| `planning.py` | plan construction and specialist review | no authorization/execution | planning sandbox |
| `capabilities.py` | trusted manifests, recursive freeze, sealed registry, manifest digest | capability availability/material policy source | no runtime installation after seal |
| `grants.py` | trusted grant store/resolution | grant authority source for Kernel | sealed after composition; caller grants ignored; resolution receives snapshots |
| `authority.py` | deterministic policy/risk/grant/confirmation/field-integrity decision | allow/deny/confirmation requirement | control-plane boundary over snapshots |
| `confirmation.py` | digest/policy/epoch-bound confirmation record | consent within existing policy | no policy override; binds to snapshot digest |
| `leases.py` | HMAC lease issuer/verifier, registry, security epoch | issuer proof and lease lifecycle | not a device execution path; lease digests bind snapshots |
| `action_kernel.py` | trust-boundary snapshotting, final dispatch gate, internal authority resolution, trusted timeout derivation, idempotency, grant accounting, lifecycle containment, leases, stop | concrete execution gate | separates cognition/callers from device |
| `device_agent.py` | bounded local execution with runtime snapshot requirement and commit barrier | lease-bound + table-supported execution only | local OS boundary; rejects mutable caller requests |
| `verification.py` | actual postcondition checks | evidence classification | separate from provider/model narrative; verifies snapshot effects |
| `audit.py` | append-only hash chain | records only | protected evidence boundary |
| `observability.py` | counters and security events | may record security signal | no authority grant |
| `orchestrator.py` | sequence user request through plan/action/report | coordination only | cannot call OS or create permission |
| `core.py` | trusted composition root and developer/test API | startup wiring/configuration | no UI or network |

## Runtime flow

1. `SessionManager` validates the local authenticated session.
2. Provider/planner produce untrusted intent/plan proposals.
3. Registry resolves the trusted manifest for caller-facing action construction.
4. Kernel receives an untrusted caller-owned `ActionRequest` and immediately creates an immutable-by-value `ActionSnapshot`.
5. Kernel revalidates the session and reserves idempotency using the snapshot digest.
6. Kernel resolves the authoritative manifest from sealed registry using the snapshot.
7. Kernel resolves the authoritative grant from sealed grant authority using the snapshot.
8. Kernel derives trusted resource/timeout policy from manifest, device policy, and session remaining lifetime.
9. Authority evaluates session, risk, capability, grant, governance, expected effect, verification requirement, policy epoch, confirmation, and field integrity against the snapshot.
10. Grant call budget is consumed if dispatch will occur.
11. Kernel issues a one-use authenticated lease expiring at the trusted effective timeout and bound to the snapshot digest.
12. Device Agent accepts only an `ActionSnapshot`, verifies/consumes the lease, and checks its trusted execution capability table.
13. For mutation, Device Agent reaches a final execution barrier and rechecks stop/cancel/security epoch immediately before commit.
14. Device Agent enforces filesystem/resource ceilings and performs the operation if still authorized.
15. Verification checks actual postcondition/evidence for the same snapshot.
16. Audit records state and evidence.
17. Unexpected internal exceptions finalize idempotency and produce truthful `FAILED`/`UNKNOWN`, never `VERIFIED`.

## Action snapshot semantics

`ActionSnapshot` captures:

```text
action_id, task_id, owner_id, principal_id, device_id, capability_id,
capability_version, operation, parameters, purpose, risk, expected_effect,
verification_requirement, idempotency_key, created_at, timeout_seconds
```

Deep-freeze behavior:

- mappings become read-only mapping proxies containing frozen values;
- lists/tuples become tuples containing frozen values;
- sets/frozensets become frozensets containing frozen values;
- supported scalar action values are retained/copied as stable values;
- unsupported action parameter values fail snapshot creation and fail closed.

Direct Device Agent calls with `ActionRequest` are rejected before any effect starts. Direct calls with snapshots still require a valid lease and a trusted capability-table entry.

## Execution barrier semantics

Before commit, stop/cancel/epoch mismatch blocks the effect. After commit, the effect may have occurred and must be reported truthfully; Phase 2.6 does not claim rollback or cancellation of an OS syscall that already committed.

## Resource and filesystem limits

Implemented limits:

- `max_file_bytes`: enforced before create/read text-file operations.
- `max_output_bytes`: enforced before read content is returned.
- `max_directory_entries`: enforced with max+1 bounded enumeration and fail-closed extra-entry detection.
- `max_path_length`: enforced before canonical path use.
- `timeout_seconds` / `max_operation_seconds`: enforced by Kernel lease expiry and idempotency wait ceiling.
- symlink child type: classified using non-following metadata; target type is not followed/revealed.

## Capability surface

Only the following remain implemented:

- `local.time/read_current_time`;
- `device.metadata/inspect_device_metadata`;
- `filesystem.approved/inspect_directory`;
- `filesystem.approved/create_directory`;
- `filesystem.approved/create_text_file`;
- `filesystem.approved/read_text_file`.

Generic command execution and generic application launch remain removed.

## Platform and verification limits

Filesystem mutation posture is `SUPPORTED` only with root identity, dir-fd `os.open`/`os.mkdir`, `O_NOFOLLOW`, and `O_DIRECTORY`. If required primitives are unavailable, mutation operations fail closed/degraded rather than claiming equivalent containment.

Windows tests present: YES.
Windows tests executed here: NO.
Windows security validation: NOT VERIFIED.

## Security caveats

Passing tests does not mean production security. This is an in-process Python prototype. Private attributes, mapping proxies, dataclass immutability, and HMAC leases are not a sandbox or a production inter-process authorization system. Phase 2.6 does not claim cryptographic immutability; it is in-process object-integrity hardening that prevents caller-owned mutable aliases from remaining authoritative after snapshot creation. Arbitrary malicious code already running inside the trusted interpreter is outside this phase's protection claim. Phase 2.6 does not claim complete race-free protection against adversarial kernels/filesystems or untested Windows junction/reparse semantics.
