# ZORQ Phase 2.2 Pre-Hardening Gap Baseline

**Status:** Captured before Phase 2.2 implementation source changes.
**Scope:** Control-plane hardening and validation only.
**Production readiness:** Not production-ready.

## Existing suite reproduction

Command run from `/home/user/zroq` before Phase 2.2 source hardening:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Result:

```text
Ran 44 tests in 0.256s

OK
```

## Reproduced Phase 2.2 trust-boundary/failure-containment gaps

The following were reproduced with explicit pre-hardening regression scripts against the Phase 2.1 object graph. They are recorded as failing security regression requirements.

| Gap | Baseline result | Required regression outcome |
|---|---|---|
| A. Direct `LeaseIssuer` authority exposure | `REPRODUCED`: `ZorqCore` exposed `lease_issuer`; a test issued a valid lease without ActionKernel authorization and `LocalDeviceAgent` executed a real directory creation. | Lease issuance must be encapsulated inside the ActionKernel trust boundary. Ordinary components/callers must not receive a callable issuer. |
| B. Stale session object at Kernel boundary | `REPRODUCED`: after emergency stop and authenticated resume, a retained old `Session` dataclass plus fresh test-created confirmation was accepted by a direct `ActionKernel.execute` call and created a directory. | Kernel must use authoritative session validation, not trust caller-supplied session fields. Revoked/old sessions must remain invalid after resume. |
| C. Unexpected DeviceAgent exception | `REPRODUCED`: injected `DeviceAgent.execute` exception escaped; idempotency record remained unresolved, wait event unset, active lease remained, cancel event remained, and grant call was consumed. | Unexpected execution failure must return a defined truthful `ActionResult`, revoke active lease, finalize idempotency, wake waiters, clear in-flight state, and audit reason. |
| D. Output-limit enforcement | `REPRODUCED`: reading a file larger than `max_output_bytes` but smaller than `max_file_bytes` returned full content and `VERIFIED`. | `read_text_file` must enforce output limits with explicit fail-closed or partial-result semantics. |

## One-off reproduction output

```text
direct_lease_issuer_authority_exposure: REPRODUCED: core_has_lease_issuer=True, executed=True, error=None, target_exists=True
stale_session_object_at_kernel_boundary: REPRODUCED: session_a_still_dataclass_active=True, session_b=FfbeQ7AwN3VPlIaVR2qyas87, status=VERIFIED, target_exists=True
unexpected_device_exception_unresolved_state: REPRODUCED: escaped=True, idem_result=None, idem_event_set=False, active_leases=1, cancel_event_present=True, grant_used=1
read_text_file_output_limit_not_enforced: REPRODUCED: status=VERIFIED, content_len=64, max_output_bytes=16
```

## Boundary statement

This baseline is a hardening input only. It does not classify Phase 2.1 or Phase 2.2 as secure or production-ready. Passing the prior 44-test suite did not cover these trust-boundary and failure-containment requirements.
