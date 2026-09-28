# ZORQ Phase 3A Clean Extraction Reproduction

**Status label:** VERIFIED for package-candidate extraction in this Linux environment; final ZIP integrity is reported externally after final archive creation.
**Scope:** clean extraction compile/test reproduction for Phase 3A package contents.

## Procedure

Commands used on a clean extraction directory:

```text
rm -rf /tmp/zroq_phase3a_candidate_extract
mkdir -p /tmp/zroq_phase3a_candidate_extract
unzip -q /home/user/ZORQ-PHASE3A-SCHEMA-CONTRACTS-VERIFIED.zip -d /tmp/zroq_phase3a_candidate_extract
cd /tmp/zroq_phase3a_candidate_extract/zroq
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

## Observed result

```text
Ran 174 tests in 0.635s

OK (skipped=8)
```

Interpretation:

- Compile check: passed
- Total tests: 174
- Passed/executed on this Linux host: 166
- Skipped: 8
- Failures: 0
- Errors: 0
- Phase 3A new tests: 26
- Phase 2.6 retained tests: 148

## Truthfulness

This verifies package-candidate extraction on this Linux host. It does not verify Windows behavior, production MEMORY//OS integration, persistent storage, voice, browser automation, or Phase 3B+ functionality.
