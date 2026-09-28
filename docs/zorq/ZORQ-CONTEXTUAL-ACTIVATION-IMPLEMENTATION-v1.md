# ZORQ Contextual Activation Implementation v1

**Status:** IMPLEMENTED / VERIFIED locally.

## Runtime path

Implemented flow:

```text
current user message
→ CurrentContextFrame
→ MemoryActivationRequest
→ staged retrieval candidates
→ relevance signals
→ adapter governance
→ temporal/current consistency check
→ MemoryActivationDecision
→ Memory Firewall
→ minimized reasoning context
```

## Automatic historical relevance

The runtime can activate memory without explicit historical search. The deterministic integration test stores a 2026 ZORQ continuity/architecture discussion and later activates it from a 2035 architecture redesign message.

## Relevance signals

Implemented signals include:

- semantic/concept relation;
- project overlap;
- entity overlap;
- goal overlap;
- decision overlap;
- causal/historical relation;
- temporal relevance;
- explicit prior reference when present.

The engine requires multi-dimensional support for activation and avoids surfacing memory from semantic similarity alone.

## State separation

The runtime distinguishes:

- `RETRIEVED`
- `RELEVANT`
- `ACTIVATED`
- `USED_IN_REASONING`
- `MENTIONED_TO_USER`

These are represented in `ActivationRuntimeResult` and Phase 3A.1 contract objects.

## Internal vs visible use

Policy decisions are preserved:

- `SHOW`
- `USE_INTERNAL_ONLY`
- `REDACT`
- `BLOCK`

Sensitive memory can be redacted or blocked. Secret/system/security regulated memory is blocked from activation/surfacing.

## “Why did you bring that up?”

The activation result includes privacy-respecting explanation text, such as a source-backed relation to a project/month without exposing blocked details.
