# ZORQ Phase 2.6 Security Review

**Phase:** 2.6 — Action Snapshot + Execution Integrity Closure
**Version:** 0.2.6
**Date:** 2026-09-27
**Scope:** standalone `/home/user/zroq` control-plane slice
**Production status:** hardened standalone control-plane slice; **not production-ready**

## Review objective

Verify that Phase 2.6 closes the high-priority mutable-action aliasing flaw without expanding the approved Phase 2 scope.

Required invariant:

```text
UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot
```

Required wording:

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

## Source review findings

Reviewed implementation paths:

- `src/zroq/contracts.py`
- `src/zroq/action_kernel.py`
- `src/zroq/device_agent.py`
- `src/zroq/authority.py`
- `src/zroq/grants.py`
- `src/zroq/leases.py`
- `src/zroq/memory.py`
- `src/zroq/verification.py`
- `tests/test_phase26_action_snapshot.py`

Confirmed source behavior:

1. `ActionSnapshot` captures all required security-relevant action fields.
2. `freeze_action_value()` recursively freezes mappings, lists, tuples, sets/frozensets, nested combinations, and supported scalar values.
3. `action_snapshot()` copies the caller-owned `ActionRequest` into immutable-by-value snapshot state at the Kernel boundary.
4. `ActionKernel.execute()` rebinds internal execution state to the snapshot before session validation, idempotency, capability/grant resolution, governance, confirmation, authorization, timeout derivation, lease issuance, Device Agent dispatch, verification, audit, and finish paths.
5. Confirmation validation uses snapshot digest equality.
6. Idempotency uses the snapshot digest; changed snapshot under the same key is rejected before dispatch.
7. `LocalDeviceAgent.execute()` rejects non-`ActionSnapshot` direct calls before any side effect starts, while still honoring emergency-stop/cancellation first.
8. Existing stale/forged lease checks still apply to snapshots.

## Dynamic execution surface scan

Command:

```text
grep -RInE "\b(subprocess|os\.system|popen|eval\(|exec\(|compile\(|pickle|marshal|shelve|socket|requests|urllib|http\.client|webbrowser|ctypes|importlib)\b" src tests || true
```

Observed result:

```text
(no matches)
```

Interpretation: no generic shell/subprocess fallback, dynamic eval/exec path, pickle/marshal/shelve deserialization, socket/client network call, browser launch, ctypes use, or dynamic import usage was found in `src` or `tests` by this string scan.

This is a static grep review, not a formal SAST proof.

## Credential/private-key marker scan

Command:

```text
grep -RInE "(password|passwd|secret|token|api[_-]?key|private[_-]?key|BEGIN (RSA|EC|OPENSSH|PRIVATE) KEY|credentials?)" src tests pyproject.toml README.md docs || true
```

Observed marker categories:

- `owner_secret`, `configured_secret`, `presented_secret` in the local development authenticator abstraction;
- `secrets.token_urlsafe()` for in-process session IDs;
- `secrets.token_bytes()` for in-process lease HMAC key material;
- `correct-secret` / `wrong-secret` deterministic test fixtures;
- documentation statements that credentials/password/account-security operations are not implemented.

No packaged `.env`, private key block, API token, downloaded credential file, or production credential was identified by this scan.

Interpretation: secret terminology is expected for the explicit non-production local owner-session abstraction and test fixtures. It is not production identity or credential handling.

## Version/scope scan

Current source and package metadata were checked for stale Phase 2.5 version markers:

```text
grep -RInE "0\.2\.5|Phase 2\.5|phase 2\.5|CURRENT PHASE 2\.5|PHASE 2\.5" src/zroq pyproject.toml README.md || true
```

Observed result:

```text
(no matches)
```

Current metadata references Phase 2.6 / 0.2.6.

Historical Phase 2.1-2.5 documents remain in `docs/` intentionally and are not rewritten to hide previous gaps.

## Syntax/static validation

Command:

```text
python -m compileall -q src tests
```

Observed result:

```text
passed
```

## Test validation

Focused Phase 2.6 command:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests/test_phase26_action_snapshot.py -v
```

Observed result:

```text
Ran 13 tests in 0.025s

OK
```

Full suite command:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed result:

```text
Ran 148 tests in 0.634s

OK (skipped=8)
```

Windows status:

```text
Windows tests present: YES
Windows tests executed here: NO
Windows security validation: NOT VERIFIED
```

## Scope containment

No Phase 3 work was added. Phase 2.6 remains within hardening/validation/control-plane closure.

Not implemented or claimed:

- production MEMORY//OS integration;
- persistent memory;
- voice or multilingual runtime;
- browser automation;
- GUI/computer vision;
- proactive daemon;
- self-improvement;
- cloud integrations;
- generic shell/PowerShell/interpreter execution;
- generic application launch;
- broader OS control;
- administrator privileges;
- credentials/password/account-security actions;
- payments;
- high-stakes autonomous decisions.

## Non-claims and residual limitations

- Passing tests does not equal production security.
- Phase 2.6 does not claim cryptographic immutability.
- `ActionSnapshot` is in-process object-integrity hardening, not a Python sandbox.
- HMAC leases remain in-process hardening, not asymmetric cross-process issuer-only authentication.
- Python private attributes, dataclass freezing, and mapping proxies are not protection from arbitrary malicious code already running inside the same interpreter.
- Windows tests are present but not executed here; Windows security validation is not verified.
- Filesystem race resistance is bounded to the existing local control-plane posture and does not prove protection against adversarial kernels/filesystems.

## Review conclusion

Within the stated standalone Phase 2.6 scope, the implementation closes the reproduced mutable-action aliasing gap: the exact authorized snapshot is the object sent to the Device Agent and verified afterward, and direct Device Agent execution of mutable caller-owned `ActionRequest` objects is rejected.
