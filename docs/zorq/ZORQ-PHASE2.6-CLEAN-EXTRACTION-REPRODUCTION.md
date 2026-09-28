# ZORQ Phase 2.6 Clean Extraction Reproduction

**Archive tested:** `/home/user/ZORQ-PHASE2.6-EXECUTION-INTEGRITY-VERIFIED.zip`
**Version:** 0.2.6
**Status:** clean extraction reproduction for the standalone Phase 2.6 package

## Procedure

A clean extraction directory was created outside the working tree:

```text
/tmp/zroq_phase26_clean
```

Commands:

```text
rm -rf /tmp/zroq_phase26_clean
mkdir -p /tmp/zroq_phase26_clean
unzip -q /home/user/ZORQ-PHASE2.6-EXECUTION-INTEGRITY-VERIFIED.zip -d /tmp/zroq_phase26_clean
cd /tmp/zroq_phase26_clean/zroq
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

## Observed result

```text
Ran 148 tests

OK (skipped=8)
```

The exact duration is environment-dependent; clean extraction runs in this environment completed in approximately half a second.

Interpretation:

- Compile check: passed
- Tests discovered: 148
- Passed on this Linux host: 140
- Skipped on this Linux host: 8
- Failures: 0
- Errors: 0

## Windows status

Windows tests present: YES.
Windows tests executed here: NO.
Windows security validation: NOT VERIFIED.

## Limitations

This reproduction verifies the archive can be extracted and the Linux-host test suite passes in this environment. It does not prove production readiness, Windows behavior, cryptographic immutability, or protection against arbitrary malicious code inside the same Python interpreter.
