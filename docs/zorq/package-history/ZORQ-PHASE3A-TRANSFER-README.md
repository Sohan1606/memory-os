# ZORQ Phase 3A Transfer README

**Package:** `ZORQ-PHASE3A-SCHEMA-CONTRACTS-VERIFIED.zip`
**Status:** Phase 3A foundational schemas + executable contracts.
**Production status:** not production-ready.

## Scope

Phase 3A implements only versioned, executable contracts in:

```text
src/zroq/domain_contracts.py
```

and tests in:

```text
tests/test_phase3a_domain_contracts.py
```

It does not implement production MEMORY//OS integration, storage, retrieval, vector indexes, voice, browser automation, GUI/computer vision, proactive daemon behavior, broad Windows control, specialist agents, Truth Engine web research, Future Simulator runtime, Optimization runtime, or Controlled Evolution runtime.

## Verify after extraction

From the extracted `zroq` directory:

```text
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Expected status on a comparable Linux/Python environment:

```text
Ran 174 tests

OK (skipped=8)
```

Durations may vary.

## Windows status

Windows tests present: YES.
Windows tests executed here: NO.
Windows security validation: NOT VERIFIED.

## Phase 2.6 preservation

Phase 2.6 remains the Action Plane authority:

```text
ActionRequest -> ActionSnapshot -> authorization -> confirmation -> lease -> Device Agent -> verification -> audit
```

Phase 3A adds only schemas/contracts and references such as `ActionSnapshotRef`.
