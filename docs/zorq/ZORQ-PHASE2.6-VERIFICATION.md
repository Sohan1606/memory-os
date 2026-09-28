# ZORQ Phase 2.6 Verification Report

**Phase:** 2.6 — Action Snapshot + Execution Integrity Closure
**Version:** 0.2.6
**Host used here:** Linux sandbox
**Date:** 2026-09-27
**Production status:** hardened standalone control-plane slice; **not production-ready**

## Summary

Phase 2.6 closes the remaining high-priority action aliasing gap by introducing an immutable-by-value `ActionSnapshot` at the Kernel trust boundary. Authorization, idempotency, confirmation, lease issuance, execution, verification, and audit all operate on that snapshot.

Required invariant:

```text
UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot
```

Required wording:

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

## Test results

### Phase 2.5 baseline before Phase 2.6 edits

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Result:

```text
Ran 135 tests in 0.489s

OK (skipped=8)
```

### Phase 2.6 final suite after implementation

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Result:

```text
Ran 148 tests in 0.634s

OK (skipped=8)
```

Interpretation:

- Total tests discovered: 148
- Passed on this Linux host: 140
- Skipped on this Linux host: 8
- Failures: 0
- Errors: 0
- Windows tests present: YES
- Windows tests executed here: NO
- Windows security validation: NOT VERIFIED

## New Phase 2.6 test coverage

New file:

```text
tests/test_phase26_action_snapshot.py
```

New tests: 13

Coverage includes:

1. action snapshot immutability;
2. nested mapping immutability;
3. nested sequence immutability;
4. original parameter mutation cannot affect snapshot;
5. no mutable nested alias;
6. parameter mutation after authorization executes the authorized snapshot;
7. parameter mutation after lease issuance executes the authorized snapshot;
8. mutation after lease verification does not change the execution target;
9. confirmation for action A does not authorize mutated action B;
10. same logical snapshot with same idempotency key replays according to policy;
11. changed snapshot under the same idempotency key is rejected;
12. replacing the parameters object and mutating nested list/custom mapping inputs cannot affect execution;
13. direct Device Agent calls with mutable `ActionRequest` objects are rejected before side effect.

## Direct-call adversarial review

Device Agent direct-call behavior was reviewed after source annotations were changed. Because Python annotations are not enforcement, `DeviceAgent.execute()` now performs a runtime type check and rejects direct calls that provide mutable caller-owned `ActionRequest` objects. It accepts only `ActionSnapshot` instances for execution. Existing stale/forged lease tests remain effective, and a direct-call unsupported-capability test was adjusted to use a snapshot so that the capability table path remains tested.

Resulting direct-call contract:

```text
DeviceAgent.execute(ActionSnapshot, cancel_event, lease) -> may execute only after lease/capability checks
DeviceAgent.execute(ActionRequest, cancel_event, lease) -> rejected; no side effect
```

Emergency stop/cancellation is still honored before the snapshot-type rejection and returns a cancelled observation.

## Static/syntax verification

Command:

```text
python -m compileall -q src tests
```

Result:

```text
passed
```

Additional static security scan: see `docs/ZORQ-PHASE2.6-SECURITY-REVIEW.md` once generated.

## Platform truthfulness

The skipped Windows tests are present but were not executed on an actual Windows host in this environment. Phase 2.6 must not be represented as Windows-verified. The Windows status is:

```text
Windows tests present: YES
Windows tests executed here: NO
Windows security validation: NOT VERIFIED
```

## Non-claims

Passing tests do not make ZORQ production-ready. Phase 2.6 is in-process object-integrity hardening, not cryptographic immutability and not a sandbox against arbitrary malicious Python code running inside the same interpreter.
