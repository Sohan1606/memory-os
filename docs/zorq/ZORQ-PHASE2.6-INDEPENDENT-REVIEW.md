# ZORQ Phase 2.6 Review Notes

**Phase:** 2.6 — Action Snapshot + Execution Integrity Closure
**Version:** 0.2.6
**Review type:** second-pass implementation/security review performed in this environment; not an external third-party certification
**Status:** hardened standalone control-plane slice; **not production-ready**

## Review question

Does the current Phase 2.6 implementation ensure that the exact authorized action state is the state executed by the Device Agent?

Required invariant:

```text
UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot
```

Required wording:

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

## Review method

Reviewed:

- source code paths from Kernel entry to Device Agent execution;
- snapshot construction and digest computation;
- confirmation and idempotency binding;
- direct Device Agent call behavior;
- Phase 2.6 regression tests;
- docs and package truthfulness claims;
- static grep scans for forbidden dynamic execution surfaces and credential/private-key markers;
- clean extraction reproduction.

## Findings checked and disposition

| Finding / question | Disposition |
|---|---|
| Top-level frozen dataclass is insufficient because nested `parameters` may mutate. | Closed by `ActionSnapshot` and recursive `freeze_action_value()`. |
| Caller mutates original parameters after authorization. | Tests pass; execution uses snapshot. |
| Caller mutates after lease issuance. | Tests pass; Device Agent receives snapshot. |
| Caller mutates after lease verification and before commit. | Tests pass; authorized target/effect preserved. |
| Caller replaces `parameters` object using object-level mutation. | Tests pass; snapshot remains authoritative. |
| Nested list/custom mapping alias remains reachable. | Tests pass; nested values are copied/frozen. |
| Confirmation for A could approve mutated B. | Tests pass; snapshot digest mismatch requires authorization. |
| Same idempotency key with mutated action could dispatch again. | Tests pass; same-key/different-snapshot conflict fails before dispatch. |
| Device Agent direct call with mutable ActionRequest could bypass Kernel snapshotting. | Closed by runtime `ActionSnapshot` check and direct-call regression test. |
| Type annotations alone enforce snapshot contract. | Rejected; runtime type check added. |
| Phase 2.6 claims cryptographic immutability. | Not claimed; docs state in-process object-integrity hardening only. |
| Windows verification can be claimed. | Not claimed; docs preserve Windows tests present YES / executed here NO / security validation NOT VERIFIED. |
| Production readiness can be claimed. | Not claimed. |

## Verification reviewed

Latest working-tree verification:

```text
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed result:

```text
Ran 148 tests in 0.634s

OK (skipped=8)
```

Clean extraction reproduction:

```text
Ran 148 tests

OK (skipped=8)
```

The exact duration is environment-dependent; clean extraction runs in this environment completed in approximately half a second.

## Review conclusion

Within the stated standalone Phase 2.6 scope, the action-snapshot/execution-integrity gap is closed: the Kernel snapshots untrusted action input before authority checks, the snapshot is used through the authorization and execution lifecycle, and the Device Agent rejects mutable direct-call `ActionRequest` objects.

This is not a production security certification. It does not verify Windows security, cryptographic immutability, cross-process isolation, arbitrary malicious-code containment inside Python, or real MEMORY//OS integration.
