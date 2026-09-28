# ZORQ Phase 3A.1 Transfer README

**Package:** `ZORQ-PHASE3A.1-CONTEXTUAL-MEMORY-VERIFIED.zip`
**Status:** Phase 3A.1 contextual memory activation contract extension.
**Production status:** not production-ready. Contract/architecture only.

## Scope

Phase 3A.1 adds executable typed contracts for contextual memory activation in:

```text
src/zroq/domain_contracts.py
```

and regression coverage in:

```text
tests/test_phase3a1_contextual_memory_activation.py
```

Primary documentation:

```text
docs/ZORQ-CONTEXTUAL-MEMORY-ACTIVATION-v1.md
docs/ZORQ-MEMORY-ACTIVATION-CONTRACT-v1.md
docs/ZORQ-PHASE3A.1-VERIFICATION.md
```

## Boundary

This phase defines how a future ZORQ runtime can distinguish:

```text
memory storage != retrieval != relevance != activation != mention != reasoning context
```

It preserves:

```text
memory relevance != memory truth != memory authority != action authority
```

## What is not included

This package does not implement Phase 3B, production MEMORY//OS, persistent database, vector index/search, embeddings, retrieval engine, storage engine, background daemon behavior, autonomous memory processing, voice, STT/TTS, browser automation, GUI/computer vision, specialist agents, Truth Engine runtime/web research, Future/Simulation Engine runtime, Optimization Engine runtime, Controlled Evolution runtime, runtime capability/grant installation, cloud/network provider integration, or autonomous execution.

## Verify after extraction

From the extracted `zroq` directory:

```text
python -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Expected status on a comparable Linux/Python environment:

```text
Ran 190 tests

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

Phase 3A.1 does not weaken, bypass, or rewrite the control plane.
