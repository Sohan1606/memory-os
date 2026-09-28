# ZORQ Memory Activation Contract v1

**Status label:** IMPLEMENTED / VERIFIED for executable contracts.
**Implementation:** `src/zroq/domain_contracts.py`.
**Tests:** `tests/test_phase3a1_contextual_memory_activation.py`.

## Implemented contract types

Phase 3A.1 adds:

- `CurrentContextFrame`
- `MemoryRelevanceSignal`
- `MemoryActivationThresholdPolicy`
- `MemoryActivationRequest`
- `MemoryActivationCandidate`
- `MemoryActivationDecision`
- `MemoryContextSelection`

## Enums / policy concepts

- `MemoryActivationTrigger`
  - `USER_REQUESTED_SEARCH`
  - `SYSTEM_DETECTED_CONTEXTUAL_ACTIVATION`
- `MemoryRelevanceSignalType`
  - semantic, temporal, entity, project, goal, decision, causal, pattern, explicit prior reference, current-state compatibility.
- `MemoryRelevanceLevel`
  - `HIGH`, `MEDIUM`, `LOW`, `NONE`, `UNCERTAIN`.
- `MemoryActivationDecisionState`
  - `NOT_RELEVANT`, `RELEVANT`, `ACTIVATED`, `BLOCKED_BY_POLICY`, `BLOCKED_BY_PRIVACY`, `CONFLICTED`, `STALE`, `UNCERTAIN`.
- `MemoryActivationPolicyDecision`
  - `SHOW`, `USE_INTERNAL_ONLY`, `REDACT`, `BLOCK`.
- `MemoryValidity`
  - historically valid, currently valid, historically relevant/currently invalid, superseded, conflicted, unknown.

## Request contract

`MemoryActivationRequest` carries:

- activation ID;
- owner/principal;
- creation time;
- conversation ID;
- current context;
- purpose;
- task scope;
- requested memory scope;
- privacy class;
- provider trust class;
- trigger;
- temporal context.

It validates owner and conversation consistency between request and `CurrentContextFrame`.

## Candidate contract

`MemoryActivationCandidate` carries:

- memory/source ID;
- relevance level;
- relevance reasons;
- semantic relation;
- temporal relation;
- entity/project/goal/decision relations;
- historical/current validity;
- current lifecycle/conflict state;
- sensitivity/privacy;
- provenance;
- relevance signals;
- confidence;
- governance status.

It requires source-backed provenance and owner consistency.

## Decision contract

`MemoryActivationDecision` explicitly distinguishes:

```text
NOT_RELEVANT
RELEVANT
ACTIVATED
BLOCKED_BY_POLICY
BLOCKED_BY_PRIVACY
CONFLICTED
STALE
UNCERTAIN
```

Validation rules include:

- ACTIVATED requires governance `ALLOW`.
- ACTIVATED cannot use `BLOCK` policy.
- LOW/NONE relevance cannot be automatically activated.
- Activated memories must be usable internally or visibly.
- Blocked memories cannot be activated or mentioned.
- User-visible mention requires `SHOW` policy.

## Context selection contract

`MemoryContextSelection` gathers decisions into:

- selected candidates;
- internal-only candidates;
- user-visible candidates;
- redacted candidates;
- blocked candidates;
- minimized reasoning context;
- source-backed explanation.

It validates that selected candidates are activated, user-visible candidates are mention-allowed, and blocked candidates are actually blocked.

## Activation explanation

Activation contracts retain source ID, provenance, temporal state, confidence, and governance state so ZORQ can answer “Why did you bring that up?” without exposing restricted details.

Example:

```text
It was related to the architecture decision we discussed in September 2026.
```

## Non-claims

These contracts do not implement candidate retrieval, vector similarity, embeddings, production MEMORY//OS integration, provider routing, database storage, background activation, or natural-language extraction.
