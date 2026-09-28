# ZORQ Phase 2.5 Pre-Hardening Gap Baseline

**Status:** Captured before Phase 2.5 source changes.
**Scope:** execution-barrier, failure-containment, and filesystem-boundary closure only.
**Production status:** not production-ready; not a security approval.

## Existing Phase 2.4 baseline

Command run from `/home/user/zroq` before Phase 2.5 source changes:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed result:

```text
Ran 115 tests in 0.444s

OK (skipped=8)
```

Interpretation:

- Discovered: 115
- Passed on this host: 107
- Skipped: 8
- Failures: 0
- Errors: 0
- Windows tests present: YES
- Windows tests executed here: NO
- Windows security validation: NOT VERIFIED

## New Phase 2.5 regression tests before hardening

A new focused test file was added before source changes:

```text
tests/test_phase25_execution_barrier.py
```

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests/test_phase25_execution_barrier.py -v
```

Observed pre-hardening result:

```text
Ran 20 tests in 1.298s

FAILED (failures=8, errors=5)
```

Some tests already passed because Phase 2.4 behavior was already safe for those subcases, but the failing/erroring tests reproduced the four concrete Phase 2.5 findings plus the posture-detection issue.

## Finding 1 — emergency stop is not a full pre-commit barrier

Pre-hardening failing tests:

- `test_stop_between_lease_verification_and_commit_blocks_effect`
- `test_old_epoch_cannot_cross_commit_barrier`
- `test_post_resume_requires_fresh_authorization`

Observed behavior:

- tests forced execution after lease verification and immediately before filesystem commit;
- emergency stop or epoch bump occurred before the commit was allowed to proceed;
- the target directory was still created;
- old/pre-stop authorization could silently cross the commit point.

Representative failure:

```text
AssertionError: True is not false
```

meaning the target existed despite stop/epoch invalidation before commit.

## Finding 2 — internal exceptions outside DeviceAgent execution are not contained

Pre-hardening erroring tests:

- `test_governance_exception_finalizes_idempotency`
- `test_authority_exception_finalizes_idempotency`
- `test_lease_issue_exception_finalizes_idempotency`
- `test_internal_exception_never_returns_verified`
- `test_internal_exception_preserves_conservative_accounting`
- `test_waiters_are_woken_on_internal_exception`

Observed behavior:

- governance, authority, registry-resolution, and lease-issuance exceptions escaped out of `ActionKernel.execute`;
- idempotency records could remain unresolved/in-progress;
- waiting callers were not woken with the same finalized result;
- exception paths did not produce a truthful `FAILED`/`UNKNOWN` `ActionResult`.

Representative error:

```text
RuntimeError: phase25-governance-boom
RuntimeError: phase25-authority-boom
RuntimeError: phase25-lease-issue-boom
RuntimeError: phase25-resolution-boom
```

## Finding 3 — directory inspection follows symlinks for child classification

Pre-hardening failing tests:

- `test_directory_inspection_does_not_follow_symlink`
- `test_directory_inspection_does_not_reveal_outside_target_type`

Observed behavior:

- `inspect_directory` used `entry.is_file()` / `entry.is_dir()` after `lstat()`;
- those calls followed symlinks;
- a symlink inside the approved root to an outside file was reported as `is_file=True`;
- a symlink inside the approved root to an outside directory was reported as `is_dir=True`.

Representative failures:

```text
AssertionError: True is not false
```

for the target type fields on symlink entries.

## Finding 4 — directory-entry limit is enforced after full materialization/sort

Pre-hardening failing test:

- `test_directory_limit_rejects_without_full_materialization`

Observed behavior:

- `inspect_directory` called `sorted(path.iterdir(), ...)` before checking the entry limit;
- a fake directory that raised when enumeration exceeded `max_directory_entries + 1` triggered an `AssertionError` from full enumeration;
- this reproduced the resource-bound issue: huge directories would be fully materialized/sorted before rejection.

Representative failure:

```text
AssertionError: directory enumeration exceeded bounded limit
```

## Finding 5 — filesystem mutation posture did not require O_NOFOLLOW availability

Pre-hardening failing test:

- `test_missing_o_nofollow_does_not_advertise_supported`

Observed behavior:

- `_supports_dir_fd_mutation()` returned `True` when `os.mkdir` and `os.open` were in `os.supports_dir_fd` even if `O_NOFOLLOW` was simulated as unavailable;
- the actual mutation implementation uses `O_NOFOLLOW` as part of the safe primitive set;
- posture should not advertise `SUPPORTED` if this primitive is absent.

Representative failure:

```text
AssertionError: True is not false
```

## Required closure

Phase 2.5 must:

1. add a pre-commit execution barrier with final stop and security-epoch validation;
2. contain unexpected internal failures across the entire reserved Kernel lifecycle and finalize idempotency;
3. classify directory entries using non-following metadata;
4. enumerate directories at most `max_directory_entries + 1` before rejecting;
5. require all primitives used by mutation implementation, including `O_NOFOLLOW`, before advertising filesystem posture `SUPPORTED`.

These findings do not claim protection from arbitrary malicious code inside the same Python interpreter. The intended closure is deterministic trusted-process control-plane behavior and truthful failure reporting.
