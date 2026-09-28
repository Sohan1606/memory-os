# ZORQ PHASE 2 VERIFICATION SUMMARY — CURRENT PHASE 2.6

**Current package target:** Phase 2.6 action-snapshot + execution-integrity closure.
**Status:** hardened standalone control-plane slice; not production-ready and not a security approval.

## Current verification

Command from `/home/user/zroq`:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Result:

```text
Ran 148 tests in 0.634s

OK (skipped=8)
```

The 8 skipped tests are Windows-specific and were not run on this non-Windows host.

## Phase progression

- Phase 2.0: initial bounded vertical slice.
- Phase 2.1: hardening of leases, idempotency, grant accounting, stop/recovery, confirmation assurance, and command/application-surface removal.
- Phase 2.2: trust-boundary/failure-containment closure for session validation, public lease issuer removal, exception containment, output limits, registry sealing, filesystem posture, and Windows test presence.
- Phase 2.3: authoritative capability/grant boundary closure; Kernel no longer accepts caller-supplied manifest/grant authority and resolves trusted state internally.
- Phase 2.4: resource-ceiling and contract-integrity closure; Kernel enforces trusted timeouts, manifests are recursively frozen, and authority tests exercise the current API directly.
- Phase 2.5: execution-barrier, lifecycle failure-containment, and filesystem-boundary closure; stop/epoch is checked immediately before mutation commit, internal exceptions finalize idempotency, directory inspection does not follow symlinks, directory enumeration is bounded, and mutation posture requires the actual primitive set.
- Phase 2.6: action-snapshot and execution-integrity closure; untrusted caller-owned `ActionRequest` objects are canonicalized/deep-frozen into `ActionSnapshot` at the Kernel trust boundary, and authorization/execution/verification use the same immutable-by-value snapshot.

## Phase 2.6 closed finding

Pre-hardening Phase 2.6 reproduction confirmed that a frozen top-level dataclass was not enough: nested `ActionRequest.parameters` remained caller-owned mutable state. A caller could authorize action A, then mutate parameters after authorization/lease verification and before commit so the Device Agent executed mutated action B. The reproduced side effect created file B, not authorized file A, while the result still reported `VERIFIED` for the mutated runtime parameters.

Phase 2.6 closes this by enforcing the invariant:

```text
UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot
```

Required wording:

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

Implemented closures:

- added `ActionSnapshot`, `freeze_action_value()`, and `action_snapshot()`;
- recursively froze mappings, lists, tuples, sets, nested combinations, and supported scalar values;
- updated Action Kernel to snapshot at entry and use the snapshot through digest/idempotency/capability/grant/governance/confirmation/authorization/timeout/lease/Device Agent/verification/audit paths;
- updated Device Agent to require `ActionSnapshot` at runtime and reject mutable direct-call `ActionRequest` inputs;
- bound confirmations and idempotency to snapshot digests;
- added 13 Phase 2.6 regression/adversarial tests.

## Current test files

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

## Current docs

- `ZORQ-PHASE2.6-PRE-HARDENING-GAP-BASELINE.md`
- `ZORQ-PHASE2.6-ACTION-SNAPSHOT-CLOSURE.md`
- `ZORQ-PHASE2.6-VERIFICATION.md`
- `ZORQ-PHASE2.6-SECURITY-REVIEW.md`
- `ZORQ-IMPLEMENTATION-ARCHITECTURE.md`
- `ZORQ-ACTION-KERNEL-IMPLEMENTATION.md`
- `ZORQ-DEVICE-AGENT-IMPLEMENTATION.md`
- `ZORQ-CORE-CONTRACTS.md`
- `ZORQ-SECURITY-TEST-MATRIX.md`

Historical Phase 2.1-2.5 hardening documents remain in `docs/` and are intentionally not rewritten to hide prior gaps.

## Scope not claimed

- No production MEMORY//OS integration.
- No production identity/recovery.
- No arbitrary shell, PowerShell, interpreter, browser, GUI, or application launch.
- No cloud integration or hosted provider requirement.
- No voice authorization.
- No persistent daemon.
- No administrator/root capability.
- No Phase 3 functionality.
- No Windows verification on this host.
- No cryptographic immutability claim for action snapshots.
- No protection against arbitrary malicious code already running inside the same trusted Python process.
- No perfect race-free guarantee against adversarial kernels/filesystems or untested junction/reparse behavior.

Passing tests demonstrate current slice behavior only, not production security.
