# ZORQ PHASE 2.5 HARDENING VERIFICATION REPORT

**Status:** Phase 2.5 execution-barrier, failure-containment and filesystem-boundary closure; hardened standalone control-plane slice; not production-ready.
**Date:** 2026-09-27

## 1. Pre-hardening baseline

Before Phase 2.5 source changes, current Phase 2.4 baseline was reproduced:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
Ran 115 tests in 0.444s
OK (skipped=8)
```

A new Phase 2.5 regression file was added and run before hardening:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests/test_phase25_execution_barrier.py -v
Ran 20 tests in 1.298s
FAILED (failures=8, errors=5)
```

The reproduced findings are documented in `ZORQ-PHASE2.5-PRE-HARDENING-GAP-BASELINE.md`.

## 2. Phase 2.5 changes implemented

- Added Device Agent pre-commit execution barrier for filesystem mutations.
- Barrier validates stop/cancel state and current security epoch immediately before irreversible effect.
- Old epoch/pre-stop actions cannot cross the commit barrier.
- Post-resume actions require fresh session and fresh authorization.
- Extended Kernel failure containment across the full reserved lifecycle.
- Internal exceptions now finalize idempotency, wake waiters, and return `FAILED` or `UNKNOWN` truthfully.
- Lease issuance failures after grant accounting preserve accounting conservatively.
- Directory inspection uses non-following `lstat` mode classification and does not reveal symlink target type.
- Directory inspection performs bounded enumeration before sorting.
- Filesystem mutation posture now requires `O_NOFOLLOW` and `O_DIRECTORY` in addition to `dir_fd` support.
- Updated tests and documentation for stop-before-commit, post-commit truthful semantics, symlink inspection, bounded enumeration, posture requirements, and known limitations.

## 3. Final verification command

From `/home/user/zroq`:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed result:

```text
Ran 135 tests in 0.510s

OK (skipped=8)
```

## 4. Exact final counts

- Total unittest cases discovered: **135**
- Passed on this host: **127**
- Skipped: **8**
- Failures: **0**
- Errors: **0**
- Phase 2.5 new tests: **20**
- Source module count: **19**
- Windows tests present: **YES**
- Windows tests executed here: **NO**
- Windows tests skipped here: **8**
- Windows security validation: **NOT VERIFIED**

## 5. Phase 2.4 retained tests

All Phase 2.4 regression tests in `tests/test_phase24_contract_integrity.py` remain in the full suite and pass on this host. Retained Phase 2.4 coverage includes:

- trusted timeout ceilings;
- lease expiry ceiling;
- shorter caller timeout not expanded;
- recursive manifest immutability;
- no mutable nested manifest aliases;
- ActionKernel API shape;
- hostile ActionRequest field validation;
- sealed grant installation denial;
- file/path/directory resource limit enforcement.

## 6. Phase 2.5 new tests

New tests in `tests/test_phase25_execution_barrier.py`: **20**

### Execution-barrier tests

- `test_stop_between_lease_verification_and_commit_blocks_effect`
- `test_old_epoch_cannot_cross_commit_barrier`
- `test_stop_during_commit_reports_truthful_outcome`
- `test_post_resume_requires_fresh_authorization`

### Failure-containment tests

- `test_governance_exception_finalizes_idempotency`
- `test_authority_exception_finalizes_idempotency`
- `test_lease_issue_exception_finalizes_idempotency`
- `test_waiters_are_woken_on_internal_exception`
- `test_internal_exception_never_returns_verified`
- `test_internal_exception_preserves_conservative_accounting`

### Filesystem-boundary tests

- `test_directory_inspection_does_not_follow_symlink`
- `test_directory_inspection_does_not_reveal_outside_target_type`
- `test_directory_inspection_handles_broken_symlink`
- `test_directory_inspection_remains_within_root`
- `test_missing_o_nofollow_does_not_advertise_supported`
- `test_policy_reports_degraded_when_mutation_primitives_incomplete`

### Resource-limit tests

- `test_directory_limit_rejects_without_full_materialization`
- `test_directory_limit_never_returns_more_than_manifest`
- `test_large_directory_fails_closed`
- `test_directory_listing_order_is_deterministic_within_limit`

## 7. Direct-call adversarial review

| Attempt | Result / defense |
|---|---|
| stop during lease verification / before commit | blocked by Device Agent execution barrier |
| old epoch before commit | blocked by final epoch validation |
| governance exception | contained by Kernel lifecycle wrapper, idempotency finalized |
| authority exception | contained by Kernel lifecycle wrapper, idempotency finalized |
| lease issue exception | contained; conservative grant accounting preserved |
| audit failure | best-effort audit/security event, no verified success from exception |
| symlink inspection | non-following mode classification; target type not revealed |
| huge directory inspection | bounded enumeration at max+1 before fail closed |
| unsupported mutation primitive | posture degraded/fail-closed |

Defense classifications are deterministic validation, object-boundary checks, trusted-process assumptions, and platform-dependent fail-closed posture. No sandboxing against arbitrary malicious code inside the Python process is claimed.

## 8. Static security review

Static source/test scans cover:

- subprocess / shell / PowerShell / cmd.exe / os.system;
- arbitrary interpreter `eval` / `exec`;
- dynamic import;
- network clients;
- browser automation;
- runtime capability/grant installation;
- unrestricted filesystem APIs;
- hidden persistence / runtime databases / daemon startup;
- secret/private-key filename patterns.

Observed scan result during Phase 2.5 review:

- no subprocess/shell/PowerShell/cmd.exe/os.system matches;
- no arbitrary interpreter `eval`/`exec` matches;
- no dynamic import matches;
- no network/browser/client automation matches;
- registry/grant/lease `register`/`install` methods exist only in trusted composition or issuer-bound lifecycle and are covered by post-seal/lease tests;
- filesystem API matches are limited to audit append/read and bounded Device Agent/security-policy primitives (`os.open`, `os.mkdir`) under approved-root policy;
- no hidden persistence/runtime database/daemon startup pattern was found;
- no generated/cache/dependency directories remained after cleanup.

## 9. Windows status

```text
Windows tests present: YES
Windows tests executed here: NO
Windows tests skipped here: 8
Windows security validation: NOT VERIFIED
```

Skipped Windows tests are not Windows verification.

## 10. Remaining known risks

- Not production-ready.
- Passing tests are not a production security approval.
- Python private attributes, mapping proxies, and dataclass immutability are not a sandbox against malicious code inside the same interpreter.
- HMAC is in-process issuer authentication only, not asymmetric cross-process authentication or hardware attestation.
- Grant accounting and idempotency remain process-local and non-durable.
- Local secret authentication is a development abstraction, not production identity.
- Production MEMORY//OS integration remains absent.
- Windows validation requires execution on a real Windows host.
- Audit remains local hash-chain evidence, not remote WORM storage.
- Phase 2.5 does not claim perfect race-free protection against adversarial kernels/filesystems, junction/reparse behavior on untested platforms, or post-commit rollback.

## 11. Stop condition

Phase 2.5 stops after this verification and packaging. Do not begin Phase 3, production MEMORY//OS integration, voice, multilingual runtime, browser/GUI automation, proactive operation, learning/evolution, or broad OS control without another independent review.
