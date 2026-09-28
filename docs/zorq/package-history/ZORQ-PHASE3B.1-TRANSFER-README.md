# ZORQ Phase 3B.1 Transfer README

**Package:** `ZORQ-PHASE3B.1-TEMPORAL-RECALL-VERIFIED.zip`
**Status:** Phase 3B.1 narrow temporal-recall correctness pass verified on Linux.
**Production MEMORY//OS status:** NOT VERIFIED / BLOCKED BY ENVIRONMENT.

## Scope

Phase 3B.1 corrects date-only historical recall semantics. Canonical source timestamps remain UTC, but date-only user queries are interpreted as calendar-day concepts in the explicit query timezone, otherwise the owner calendar timezone, otherwise documented UTC fallback.

Implemented changes:

- `MemoryAccessContext.owner_calendar_timezone`;
- `ContinuityQuery.calendar_timezone` query override;
- Python `zoneinfo`-based local calendar-day bounds;
- UTC conversion for storage/query comparisons;
- timezone/DST/date-boundary regression tests.

No Phase 3C+ functionality is included.

## Verify after extraction

From the extracted `zroq` directory:

```text
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Expected status on a comparable Linux/Python environment:

```text
Ran 221 tests

OK (skipped=8)
```

Durations may vary.

## Windows status

Windows tests present: YES.
Windows tests executed here: NO.
Windows storage/security validation: NOT VERIFIED.

## Stop condition

Stopped after Phase 3B.1. Phase 3C conversational runtime is next and is not implemented here.
