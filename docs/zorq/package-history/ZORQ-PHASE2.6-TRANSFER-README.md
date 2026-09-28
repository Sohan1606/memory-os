# ZORQ Phase 2.6 Transfer README

**Package:** `ZORQ-PHASE2.6-EXECUTION-INTEGRITY-VERIFIED.zip`
**Version:** 0.2.6
**Status:** Phase 2.6 action-snapshot and execution-integrity closure; hardened standalone control-plane slice; **not production-ready**.

## What this package contains

This package contains the standalone `/zroq` project implementing Phase 2.6 hardening:

```text
UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot
```

Invariant:

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

## How to verify after extraction

From the extracted `zroq` directory:

```text
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Expected result in this environment:

```text
Ran 148 tests in 0.634s

OK (skipped=8)
```

The runtime may show a different duration, but the test count/status should match on a comparable Linux/Python environment.

## Windows status

Windows tests present: YES.
Windows tests executed here: NO.
Windows security validation: NOT VERIFIED.

Do not claim Windows security support from this package alone.

## Not included / not claimed

This package does not include or claim:

- production MEMORY//OS integration;
- Phase 3 features;
- persistent memory;
- voice/multilingual runtime;
- browser automation;
- GUI/computer vision;
- proactive daemon;
- self-improvement;
- cloud integrations;
- arbitrary shell/PowerShell/interpreter execution;
- generic application launch;
- broader OS control;
- credentials/password/account-security actions;
- payments;
- administrator privileges;
- production readiness.

Phase 2.6 does not claim cryptographic immutability. It is in-process object-integrity hardening and not a Python sandbox against arbitrary malicious code already running inside the same interpreter.

## Key documents

- `README.md`
- `docs/ZORQ-PHASE2.6-ACTION-SNAPSHOT-CLOSURE.md`
- `docs/ZORQ-PHASE2.6-VERIFICATION.md`
- `docs/ZORQ-PHASE2.6-SECURITY-REVIEW.md`
- `docs/ZORQ-PHASE2-VERIFICATION.md`
- `docs/ZORQ-IMPLEMENTATION-ARCHITECTURE.md`
- `docs/ZORQ-SECURITY-TEST-MATRIX.md`
