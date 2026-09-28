# ZORQ Phase 2.1 Hardening Gap Baseline

**Status:** Pre-hardening baseline captured before source changes for Phase 2.1.
**Scope:** Security/control-plane hardening only.
**Production readiness:** Not production-ready.

## Baseline test reproduction

Command run from `/home/user/zroq` before hardening source changes:

```text
python -m unittest discover -s tests -v
```

Result:

```text
Ran 27 tests in 0.061s

OK
```

## Reproduced security gaps

The following were reproduced with one-off baseline verification scripts against the pre-hardening source. They are recorded as failing security regression requirements; the reproduction scripts created only temporary test state.

| Gap | Baseline result | Required regression outcome |
|---|---|---|
| Forgeable but structurally valid Device Agent lease | `REPRODUCED`: a manually constructed matching `ExecutionLease` executed a real directory creation through `LocalDeviceAgent` without Action Kernel issuance proof. | Device Agent must reject any lease not authenticated as issued by the trusted Action Kernel/control plane before OS side effect. |
| Concurrent duplicate dispatch with same idempotency key | `REPRODUCED`: two concurrent identical requests produced two observable command side effects and both returned `VERIFIED`. | Atomic in-flight idempotency reservation must prevent a second physical dispatch. |
| `PermissionGrant.max_calls` not enforced | `REPRODUCED`: default grant had `max_calls=1`, yet two separate filesystem actions using the same grant both returned `VERIFIED`. | Grant calls must be atomically consumed after authorization boundary and fail closed once exhausted. |
| Configurable "harmless" command allowing unsafe fixed process | `REPRODUCED`: a configured command using Python `-c` wrote outside the approved root and returned `VERIFIED`. | Generic command execution/interpreter authority must be removed from Phase 2.1 unless independently sandboxed; this phase removes it. |
| Unauthenticated emergency-stop resume | `REPRODUCED`: `resume_after_stop()` required no presented secret/session/confirmation and allowed a fresh action after stop. | Resume must require authenticated owner authorization, increment/observe security epoch semantics, and not revive old leases/confirmations. |
| Confirmation assurance not validated | `REPRODUCED`: a manually constructed `Confirmation` with `AssuranceLevel.A0` and matching digest/session authorized an R1 filesystem side effect. | Authority must validate confirmation assurance, session binding, policy version, and security epoch. |

## One-off reproduction output

```text
forgeable_valid_looking_device_lease: REPRODUCED: executed=True, error=None, target_exists=True
concurrent_same_idempotency_duplicate_dispatch: REPRODUCED: physical_marker_count=2, statuses=['VERIFIED', 'VERIFIED']
permission_grant_max_calls_not_enforced: REPRODUCED: grant max_calls=1 but statuses=VERIFIED,VERIFIED
configurable_harmless_command_allows_unsafe_fixed_process: REPRODUCED: status=VERIFIED, outside_exists=True
unauthenticated_emergency_stop_resume: REPRODUCED: stopped=STOPPED, resumed=VERIFIED
confirmation_assurance_not_validated: REPRODUCED: weak_assurance=A0, status=VERIFIED
```

## Boundary statement

This baseline is a hardening input only. It does not classify Phase 2 as secure or production-ready. Passing the old 27-test suite did not cover these regression properties.
