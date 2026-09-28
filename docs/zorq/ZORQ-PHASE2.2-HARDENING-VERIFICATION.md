# ZORQ PHASE 2.2 HARDENING VERIFICATION REPORT

**Status:** Phase 2.2 hardened standalone control-plane slice; not production-ready and not a security approval.
**Date:** 2026-09-27

## 1. Pre-hardening baseline

Before Phase 2.2 source hardening, the Phase 2.1 suite was reproduced:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
Ran 44 tests
OK
```

The pre-hardening gap baseline is recorded in `ZORQ-PHASE2.2-PRE-HARDENING-GAP-BASELINE.md`. It reproduced:

- direct `LeaseIssuer` authority exposure from `ZorqCore`;
- stale retained `Session` dataclass accepted at direct Kernel boundary after stop/resume;
- unexpected `DeviceAgent.execute` exception escaping and leaving unresolved idempotency/lease/call state;
- `read_text_file` returning content larger than `max_output_bytes`.

## 2. Phase 2.2 hardening implemented

- `ActionKernel` now validates caller-supplied sessions through authoritative `SessionManager` before authorization.
- `Session` carries a security epoch; `SessionManager` stores and validates current epoch/session state.
- Emergency stop revokes sessions and bumps epoch; retained session dataclasses remain invalid.
- `ZorqCore` no longer exposes a public `lease_issuer` attribute.
- `ActionKernel` stores the issuer as a private trust-boundary member; `DeviceAgent` receives only `LeaseVerifier`.
- HMAC lease proof is documented as in-process hardening, not asymmetric cross-process issuer-only authentication.
- Unexpected Device Agent exceptions produce `FAILED` if the lease was still active and could be revoked, otherwise `UNKNOWN`.
- Unexpected exception results finalize idempotency, wake waiters, clear in-flight cancellation state, revoke active lease where possible, and audit `action.execution_exception`.
- `read_text_file` enforces `max_file_bytes` and `max_output_bytes`; oversize output fails closed and is not silently truncated.
- Device filesystem policy exposes `SUPPORTED`, `DEGRADED`, or `UNAVAILABLE` posture.
- Mutation operations require supported root identity and dir-fd mutation primitives; otherwise mutation fails closed.
- Approved-root replacement detection is tested where filesystem identity is available.
- Capability registry is sealed after trusted composition; runtime registration raises `PermissionError`.
- Public grants view is read-only.

## 3. Verification command

From `/home/user/zroq`:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

## 4. Result

- Tests discovered/executed by unittest: **76**
- Passed: **68**
- Skipped: **8** Windows-specific tests skipped because the packaging host is not Windows
- Failed: **0**
- Errors: **0**
- Network required: **No**
- Hosted provider required: **No**
- Production MEMORY//OS implementation added: **No**
- Phase 3 functionality added: **No**

## 5. Phase 2.2 test coverage

New Phase 2.2 security and validation tests include:

- `test_core_does_not_expose_direct_lease_issuer`
- `test_kernel_rejects_revoked_session_object`
- `test_kernel_requires_authoritative_session_validation`
- `test_old_session_cannot_execute_after_stop_and_resume`
- `test_new_session_required_after_recovery`
- `test_unexpected_device_exception_finalizes_idempotency`
- `test_unexpected_device_exception_does_not_leave_active_lease`
- `test_unexpected_device_exception_wakes_idempotent_waiters`
- `test_unexpected_device_exception_records_audit`
- `test_unexpected_device_exception_never_returns_verified`
- `test_stop_during_unexpected_execution_exception_finalizes`
- `test_read_output_limit_enforced`
- `test_read_output_limit_never_exceeds_manifest`
- `test_large_file_read_has_truthful_status`
- `test_filesystem_posture_is_explicit`
- `test_root_replacement_detected_where_identity_supported`
- `test_parent_directory_substitution_denied`
- direct Device Agent forged/after-stop tests;
- direct Kernel during-stop test;
- sealed registry/runtime-installation denial test;
- read-only public grants mapping test;
- direct confirmation construction cannot overcome session revocation;
- completed action replay does not duplicate effect.

## 6. Windows validation status

A Windows-specific validation suite is present in `tests/test_phase22_trust_boundary.py`, covering approved root setup, directory/file create/read, traversal, symlink/reparse where available, parent/root substitution, Unicode/spaces/long path patterns, cancellation/stop, lease invalidation, and session invalidation.

Packaging host platform: non-Windows. Therefore Windows-specific tests were **skipped**, not verified. Do not claim Windows security verification from this package alone.

## 7. Static review result

Static scans over `src` and `tests` found no implemented `subprocess`, `shell=True`, `os.system`, PowerShell/cmd, `python -c`, `eval`, `exec`, dynamic import, network client, browser automation, or cloud provider path.

Lease issuance references in source are limited to trusted composition and private ActionKernel issuance. Tests assert `ZorqCore`, `DeviceAgent`, registry, and public Kernel attributes do not expose `lease_issuer`.

Registry registration exists for trusted startup composition only and is sealed after initialization. Runtime registration test fails closed.

## 8. Boundary classification

| Boundary | Enforcement type |
|---|---|
| Model/provider cannot execute OS | deterministic logic + absent capability |
| Planner/specialist cannot execute OS | deterministic logic + absent capability |
| Kernel session validity | authoritative `SessionManager` validation |
| Lease issuance | object-capability isolation inside trusted in-process Kernel boundary |
| Lease authenticity | in-process HMAC + active registry + one-use consumption |
| Lease cross-process issuer-only proof | **NOT IMPLEMENTED** |
| DeviceAgent forged/stale/old lease rejection | deterministic verifier logic |
| Registry install at runtime | sealed registry deterministic logic |
| Public grant mutation | read-only public mapping; private state remains trusted-process assumption |
| Filesystem containment | canonical root checks + root fingerprinting + dir-fd mutation where supported; not full cross-platform race proof |

## 9. Known limitations

- Not production-ready.
- HMAC is in-process hardening, not asymmetric cryptographic issuer-only proof across process boundaries.
- Python object privacy is not a hard sandbox against malicious in-process code using private attributes.
- Grant accounting and idempotency remain process-local and not crash-durable/distributed.
- Local secret authenticator is not production identity.
- Windows validation tests are present but skipped on this non-Windows host.
- Filesystem race-free containment is not claimed for all OS/filesystem semantics.
- Real MEMORY//OS v10.2.0 integration remains unimplemented.
- Audit is hash-chained but not remote WORM.

## 10. Deferred/forbidden

Still deferred/forbidden: production MEMORY//OS integration, voice, multilingual runtime, browser/GUI/vision control, proactive daemon, self-improvement, cloud integrations, arbitrary shell/PowerShell/interpreter execution, generic command execution, generic application launch, credentials/passwords, payments, account-security operations, and production deployment.
