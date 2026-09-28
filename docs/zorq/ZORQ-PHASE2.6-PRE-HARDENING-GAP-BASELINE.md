# ZORQ Phase 2.6 Pre-Hardening Gap Baseline

**Status:** Captured before Phase 2.6 source changes.
**Scope:** action-snapshot and execution-integrity closure only.
**Production status:** not production-ready; not a security approval.

## Existing Phase 2.5 baseline

Command run from `/home/user/zroq` before Phase 2.6 source changes:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed result:

```text
Ran 135 tests in 0.489s

OK (skipped=8)
```

Interpretation:

- Discovered: 135
- Passed on this host: 127
- Skipped: 8
- Failures: 0
- Errors: 0
- Windows tests present: YES
- Windows tests executed here: NO
- Windows security validation: NOT VERIFIED

## Reproduced Phase 2.6 vulnerability

A focused regression was added before source changes to force this sequence:

```text
create valid filesystem action for file A
authorize / confirm A
lease verification completes
mutate caller-owned action.parameters to file B
allow execution to continue
```

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests/test_phase26_action_snapshot.py -v
```

Observed pre-hardening result:

```text
Ran 1 test
FAILED (errors=1)
```

The failure occurred because the authorized file A did not exist. A one-off reproduction captured the exact state:

```text
original_authorized_digest=87f198f58988fd46406cdb8a4a4b8d06d665424f0c6ceb7d5199c1d3d63e683a
mutated_runtime_parameters={'path': '/tmp/.../approved/mutated-B.txt', 'content': 'mutated'}
result_status=VERIFIED
file_a_exists=False
file_b_exists=True
file_b_content=mutated
verification_evidence={'path': '/tmp/.../approved/mutated-B.txt', 'content_matches': True, 'bytes': 7}
```

## Finding

`ActionRequest` was a frozen dataclass only at the top level. Its nested `parameters` mapping remained caller-owned mutable state. The Kernel authorized one digest, but the Device Agent later read from the same mutable object. A caller could mutate parameters after authorization/lease verification and before commit, causing the runtime side effect and verification to use mutated parameters.

This shows that a validated digest is insufficient if the object reaching execution can later mutate.

## Required closure

Phase 2.6 must:

1. create an immutable-by-value internal `ActionSnapshot` at the Kernel trust boundary;
2. compute digest/idempotency/governance/confirmation/authorization/lease/verification from that snapshot;
3. pass the snapshot, not the caller-owned `ActionRequest`, to the Device Agent;
4. make nested snapshot parameters recursively immutable and unaliased from caller-owned state;
5. reject or neutralize mutations after authorization, confirmation, lease issuance, lease verification, and immediately before commit.

This is in-process object-integrity hardening. It is not cryptographic immutability and not a sandbox against arbitrary malicious code already executing in the same Python interpreter.
