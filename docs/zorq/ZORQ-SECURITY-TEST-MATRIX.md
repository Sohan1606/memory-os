# ZORQ SECURITY TEST MATRIX — PHASE 2.6

**Status:** Executed deterministic/adversarial/security-regression test plan for the Phase 2.6 action-snapshot, execution-integrity, execution-barrier, failure-containment and filesystem-boundary control-plane slice.
**Test command:** `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`

## Matrix contract

- **Purpose:** prove that the implemented control plane refuses authority bypass, enforces resource ceilings, executes the immutable authorized snapshot, blocks pre-commit stop/epoch races, contains internal failures, and verifies actual bounded effects for the scoped implementation.
- **Authority:** tests may fail the build/review; they cannot authorize production exceptions.
- **Inputs:** test-only MEMORY//OS contract double, temporary approved roots, deterministic provider, explicit sessions/confirmations, hostile `ActionRequest` objects, mutated nested parameter objects, hostile manifests/grants/leases, concurrent requests, stop/resume states, symlinks, fake large directory iterator, primitive simulation.
- **Outputs:** pass/fail evidence, audit state, actual filesystem state.
- **Invariants:** no fake success, no model-direct OS, default unavailable memory fails closed, side effects remain bounded, caller-supplied manifests/grants/timeouts are not authoritative, caller-owned mutable action objects are not authoritative after snapshot creation, generic command/application authority removed, pre-commit stop blocks mutation, unexpected internal exceptions do not strand idempotency.
- **Trust boundary:** tests exercise provider, adapter, registry, grant authority, AuthorityEngine, Kernel, action snapshotting, lease issuer/verifier, Device Agent, verifier, and audit separately.
- **Failure behavior:** test failure blocks Phase 2.6 acceptance.
- **Audit requirements:** real actions inspect audit chain and actual state.
- **Versioning:** test suite is tied to project `0.2.6` and current contract versions.

## Executed matrix

| Category | Test evidence | Expected result |
|---|---|---|
| Unit/contracts | action digest, `ActionSnapshot`, state objects, manifest/registry | deterministic typed contracts |
| Snapshot immutability | direct mutation of snapshot fields/parameters | immutable/fail closed |
| Nested snapshot freeze | dictionaries, mappings, lists, tuples, sets, nested combinations | recursively frozen; no caller-owned mutable alias |
| Source parameter aliasing | mutate original parameters after snapshot | snapshot unchanged |
| Mutable custom mapping | custom mapping source mutated after snapshot | snapshot unchanged |
| Mutation after authorization | mutate caller parameters after authority approval | Device Agent executes authorized snapshot only |
| Mutation after lease issuance | mutate after lease is created | lease/device/verifier still use snapshot |
| Mutation after lease verification | mutate immediately before commit | authorized target/effect preserved; mutated B not executed |
| Parameter object replacement | replace original `parameters` object | no effect on snapshot execution |
| Confirmation binding | confirmation for A, then mutate to B | B denied/not silently approved |
| Idempotency snapshot binding | same key/same snapshot; same key/different snapshot | replay allowed by policy; conflict rejected before dispatch |
| Direct Device Agent call | caller-owned mutable `ActionRequest` to `execute()` | rejected before side effect |
| Identity/session | wrong/stale/revoked session | denied |
| Memory boundary | unavailable adapter, contradictory governance, governance exception | explicit unavailable/deny/fail-contained; no fabrication |
| Intent/provider | unknown request, injection provider | unavailable; no action |
| Suggestions | meeting preparation proposal | suggestions only; zero action results |
| Capability resolution | unknown/unregistered capability, resolution exception | denied/unavailable or fail-contained before side effect |
| Manifest authority | hostile ActionRequest attempting forged manifest effects | ignored/rejected; no side effect |
| Grant authority | missing/forged grant, post-seal install | ignored/rejected; no side effect |
| Grant binding | inactive, expired/version-mismatched/digest-mismatched/purpose-mismatched grant | denied before side effect |
| Manifest immutability | nested mappings/lists/sets/source aliases after registration | immutable/frozen; mutation attempts fail |
| Timeout ceiling | caller 3600s vs filesystem manifest 5s | lease does not exceed trusted ceiling |
| Execution barrier | stop/epoch between lease verification and commit | commit blocked; no target created |
| Post-commit truthfulness | stop after actual commit | reported based on evidence; no false pre-commit denial claim |
| Failure containment | governance/authority/lease issue/resolution exceptions | idempotency finalized; waiters woken; no VERIFIED |
| Action field integrity | wrong version/operation/risk/effect/verification | denied before side effect |
| Confirmation digest | digest mismatch | no dispatch |
| Confirmation assurance | A0 confirmation for R1 action | authorization required; no dispatch |
| Confirmation epoch | old confirmation after security epoch change | authorization required; no dispatch |
| Lease authenticity | forged valid-looking lease | rejected before side effect |
| Lease proof binding | tampered digest | rejected before side effect |
| Lease epoch/device/stale | old epoch, wrong device, expired | rejected before side effect |
| Device table | unsupported capability/version/operation | refused before handler side effect |
| Idempotency concurrency | same key/same digest concurrent threads | one Device Agent dispatch or same failure result |
| Idempotency mutation | same key/different digest | failed conflict; no second target |
| Grant max calls | max_calls=1, two actions | first dispatch only; second denied |
| Concurrent grant accounting | two actions race same grant | one dispatch only |
| Command removal | `command.allowlist` resolution | unavailable |
| Application removal | `application.allowlist` resolution | unavailable |
| Path security | traversal/outside-root/too-long target | denied; no file created |
| Symlink security | mutation symlink escape; inspection symlink target | mutation denied; inspection does not follow target |
| Directory entries | too many entries / fake huge iterator | failed closed with bounded enumeration; no verified truncation |
| Real filesystem | create approved directory | actual directory exists and is verified |
| Text file | create/read UTF-8 file and oversized content | bounded result or failed closed |
| Fake success | claimed execution without postcondition | verification not verified |
| Cancellation/stop | stop before/during dispatch and before commit | stopped/cancelled; no filesystem effect where pre-commit |
| Emergency resume | wrong secret vs correct secret; fresh authorization after resume | wrong denied; stale action blocked; fresh authorization works |
| Unexpected exception | injected Device Agent/internal exceptions | contained; no unresolved active lease/idempotency hang |
| Audit integrity | hash chain tampering | integrity error detected |
| Offline/no network | full suite without network/provider | passes; no external calls |

## Tests executed

The suite currently contains **148 unittest cases**, with **8 Windows-specific tests skipped on this non-Windows host**:

- `tests/test_core.py`
- `tests/test_security.py`
- `tests/test_integration.py`
- `tests/test_adversarial.py`
- `tests/test_phase21_hardening.py`
- `tests/test_phase22_trust_boundary.py`
- `tests/test_phase23_authority_boundary.py`
- `tests/test_phase24_contract_integrity.py`
- `tests/test_phase25_execution_barrier.py`
- `tests/test_phase26_action_snapshot.py`

The Phase 2.6 suite adds **13** tests for action snapshot immutability, nested alias protection, mutation timing after authorization/lease issuance/lease verification, confirmation binding, idempotency snapshot binding, parameter replacement, nested list mutation, mutable custom mapping aliasing, and direct Device Agent rejection of mutable `ActionRequest` inputs.

## Security assumptions under test

The tests assume the Python process and operating system are not already compromised. They do not prove resistance to a compromised kernel, malicious owner endpoint, arbitrary malicious code inside the same Python interpreter, root-level malware, or a real provider breach. They prove that the implemented components do not voluntarily create the reproduced Phase 2.1/2.2/2.3/2.4/2.5/2.6 gaps through ordinary model/provider output, untrusted parameters, hostile ActionRequests, forged manifests/grants/leases, caller timeouts, caller-owned mutable nested action parameters, stop races before commit, symlink inspection, huge directory enumeration, or concurrent requests.

## Deferred matrix

Not executed because explicitly forbidden or unavailable in Phase 2:

- Windows UAC/secure desktop/protected-process behavior;
- browser/document prompt injection in a real browser;
- PowerShell/administrator/shell sandbox;
- generic command sandbox;
- generic application launch/sealed executable model;
- voice replay/deepfake/speaker verification;
- multilingual/code-switching/translation;
- hardware attestation;
- real MEMORY//OS v10.2.0 integration;
- external provider outcome semantics;
- package supply-chain and marketplace;
- production deployment/canary;
- distributed durable idempotency/call accounting.

## Acceptance interpretation

Final observed result:

```text
Ran 148 tests in 0.634s

OK (skipped=8)
```

This verifies the current vertical-slice behavior only. It does not prove general ZORQ security, real MEMORY//OS compatibility, OS compromise resistance, high-risk identity, external outcome verification, Windows security, malicious-code sandboxing, cryptographic immutability, or production readiness.
