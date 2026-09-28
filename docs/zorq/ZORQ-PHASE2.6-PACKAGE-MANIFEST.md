# ZORQ Phase 2.6 Package Manifest

**Target archive:** `/home/user/ZORQ-PHASE2.6-EXECUTION-INTEGRITY-VERIFIED.zip`
**Version:** 0.2.6
**Status:** hardened standalone control-plane slice; **not production-ready**

## Included categories

- `pyproject.toml`
- `README.md`
- `ZORQ-PHASE2.6-TRANSFER-README.md`
- `src/zroq/*.py`
- `tests/*.py`
- `docs/*.md`

## Excluded categories

The package excludes:

- `.git`
- `.venv`
- `node_modules`
- `__pycache__`
- build/dist artifacts
- caches
- logs
- runtime databases/DBs
- downloaded models
- secrets
- private keys

## Verification state at packaging

Latest local verification command:

```text
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Latest observed result:

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

## Phase 2.6 invariant

```text
UNTRUSTED ActionRequest -> canonicalize + deep-freeze -> ActionSnapshot -> all authorization/execution uses snapshot
```

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

## Non-claims

This package is not production-ready. It does not claim cryptographic immutability, production MEMORY//OS integration, Windows verification, broad OS control, arbitrary shell/application launch, credentials/payment/account-security authority, or protection against arbitrary malicious code already running inside the same Python interpreter.
