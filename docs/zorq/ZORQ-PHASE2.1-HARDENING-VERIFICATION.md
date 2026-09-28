# ZORQ PHASE 2.1 HARDENING VERIFICATION REPORT

**Status:** Hardened standalone control-plane vertical slice; not production-ready and not a security approval.
**Date:** 2026-09-27

## 1. Baseline before hardening

Before source changes, the existing Phase 2 suite was reproduced:

```text
python -m unittest discover -s tests -v
Ran 27 tests
OK
```

The hardening gap baseline is recorded in `docs/ZORQ-PHASE2.1-HARDENING-GAP-BASELINE.md`. It reproduced six security gaps:

- forgeable valid-looking Device Agent lease;
- concurrent duplicate dispatch under same idempotency key;
- unenforced `PermissionGrant.max_calls`;
- generic configurable command process escaping approved filesystem intent;
- unauthenticated emergency-stop resume;
- confirmation assurance not checked.

## 2. Hardening implemented

Implemented Phase 2.1 hardening only:

- issuer-authenticated execution leases with HMAC proof and active lease registry;
- one-use lease consumption by Device Agent before side effect;
- lease binding to action ID, action digest, owner, principal, device, capability, version, operation, issued/expires, lease ID, policy version, and security epoch;
- security epoch invalidation on emergency stop;
- authenticated owner resume through local factor;
- session revocation on emergency stop;
- confirmation policy version/security epoch binding;
- confirmation assurance validation;
- atomic in-flight idempotency reservation;
- process-local grant max-calls accounting;
- audit of grant consumption/rejection and idempotency conflicts/waits;
- removal of generic command and generic application-launch capabilities;
- filesystem root fingerprinting and existing-parent mutation checks;
- parent file-descriptor based create operations where supported.

No Phase 3 functionality was added.

## 3. Verification command

From `/home/user/zroq`:

```text
python -m unittest discover -s tests -v
```

## 4. Result

- Test cases executed: **44**
- Passed: **44**
- Failed: **0**
- Errors: **0**
- Network required: **No**
- Hosted provider required: **No**
- Production MEMORY//OS implementation added: **No**
- Phase 3 functionality added: **No**

## 5. Security regression tests added

Phase 2.1 regression coverage includes:

- `test_device_agent_rejects_forged_valid_looking_lease`
- `test_lease_issuer_proof_is_bound_to_action_digest`
- `test_lease_invalid_after_security_epoch_change`
- `test_lease_for_another_device_fails`
- `test_stale_lease_fails`
- `test_concurrent_same_idempotency_key_dispatches_once`
- `test_idempotency_conflict_rejects_changed_digest`
- `test_grant_max_calls_is_enforced`
- `test_concurrent_grant_call_accounting`
- `test_generic_command_capability_is_unavailable`
- `test_application_allowlist_cannot_become_shell_path`
- `test_resume_requires_authenticated_owner`
- `test_emergency_stop_invalidates_old_leases`
- `test_confirmation_assurance_is_checked`
- `test_confirmation_invalid_after_security_epoch_change`
- `test_stop_before_dispatch`
- `test_stop_during_execution_cancels_inflight`

Security regression count: **17 named Phase 2.1 tests** in `tests/test_phase21_hardening.py`, plus updated integration tests for removed command/application capabilities.

## 6. Real action evidence

Still implemented and verified:

- approved root;
- real directory creation;
- actual filesystem postcondition readback;
- `VERIFIED` only after postcondition check;
- audit integrity verification.

Negative real-action evidence remains:

- outside-root target denied;
- path traversal denied;
- symlink escape denied;
- fake success cannot verify;
- forged/unissued lease denied;
- old epoch/stale/wrong-device leases denied.

## 7. Removed capabilities

Removed from implemented Phase 2.1 capability surface:

- `command.allowlist` / `execute_allowlisted_command`;
- `application.allowlist` / `open_allowlisted_application`.

Reason: Phase 1 keeps terminal/PowerShell/generic process authority disabled for the initial implementation, and a mere configurable “harmless” argv convention is not a security boundary.

## 8. Static security search summary

Static searches found no implemented `shell=True`, `os.system`, or `subprocess.*` execution path in `src/zroq`. Remaining mentions of command/application capability names are negative tests and documentation stating removal/forbidden status.

Secret-file scan found no packaged `.env`, private-key, token, or credential files. Source and tests contain local development secret terminology for the explicit non-production authenticator and test fixtures only.

## 9. Known limitations

- Not production-ready.
- HMAC lease issuer is in-process, not hardware attestation.
- Grant call accounting is process-local, not durable/distributed.
- Idempotency is process-local, not durable/distributed.
- Local secret authenticator is not production identity/passkey recovery.
- Filesystem race hardening is improved but not claimed complete for every OS/filesystem.
- Test-only MEMORY//OS adapter is not MEMORY//OS.
- No browser/document prompt-injection runtime exists or is verified.
- No external provider or real-world outcome verification exists.

## 10. Deferred/forbidden

Deferred or forbidden remain:

- production MEMORY//OS integration;
- voice and multilingual runtime;
- browser, GUI, computer vision, and broad OS control;
- proactive daemon behavior;
- self-improvement/online learning;
- arbitrary PowerShell/shell/interpreter execution;
- generic application launch;
- cloud integrations;
- credentials/passwords, payments, account-security operations;
- production deployment.

## 11. Phase boundary

- **Phase 0:** historical design/specification baseline; unchanged.
- **Phase 1:** historical architecture-hardening contract baseline; unchanged.
- **Phase 2.1:** hardened standalone control-plane vertical slice; implemented and verified for its narrow scope; partial relative to the full ZORQ product; not production-ready.
