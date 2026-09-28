# ZORQ Phase 3A Schema Contracts

**Status label:** IMPLEMENTED / VERIFIED for executable schemas and contract tests.
**Scope:** foundational contracts only. No production MEMORY//OS adapter, storage engine, retrieval engine, vector index, voice, browser automation, GUI/computer vision, proactive daemon, specialist agents, Truth Engine runtime, Simulation Engine runtime, Optimization runtime, or Controlled Evolution runtime is implemented.

## Purpose

Phase 3A turns the Master Architecture v1 domain model into executable, versioned, testable contracts while preserving the Phase 2.6 Action Plane authority.

```text
ARCHITECTURE
  -> EXACT CONTRACTS
  -> VALIDATED DOMAIN MODEL
  -> ONLY THEN REAL MEMORY / CONTINUITY IMPLEMENTATION
```

## Implemented source

- `src/zroq/domain_contracts.py`
- `tests/test_phase3a_domain_contracts.py`

## Contract families

`domain_contracts.py` implements typed frozen dataclasses for:

- Identity/session: `User`, `OwnerIdentity`, `Session`, `Device`.
- Conversation: `Conversation`, `ConversationBranch`, `Message`, `Response`, `ResponseCursor`, `ConversationCheckpoint`, `InteractionControlCommand`.
- Continuity: `MemorySource`, `Memory`, `TimelineEvent`, `Entity`, `Relationship`.
- Intelligence/world model: `Goal`, `Project`, `Decision`, `Plan`, `Observation`, `Evidence`, `Recommendation`.
- Action/outcome references: `Action`, `ActionSnapshotRef`, `Outcome`.
- Governance references: `Capability`, `Grant`, `Confirmation`, `Lease`.
- Memory policy/retrieval/deletion/firewall/MEMORY//OS boundary: `MemoryCapturePolicy`, `MemoryRetrievalRequest`, `MemoryRetrievalHit`, `MemoryRetrievalResult`, `DeletionRequest`, `DeletionResult`, `MemoryGovernanceRequest`, `MemoryGovernanceDecision`, `MemoryStorageRequest`, `DerivedViewReference`, `MemoryFirewallRequest`, `MemoryFirewallResult`, `MemoryOSAdapterContract`.
- Evolution contracts: `Experiment`, `EvolutionProposal`.
- Cross-entity helper: `DomainIndex`.

## What is deliberately not implemented

- No storage engine.
- No persistent database.
- No retrieval engine.
- No vector index.
- No production MEMORY//OS adapter.
- No provider/model integration.
- No voice/TTS/STT/barge-in runtime.
- No web research.
- No browser/GUI automation.
- No broad Windows/device control.
- No specialist-agent runtime.
- No autonomous background operation.

## Phase 2.6 preservation

Phase 2.6 remains the Action Plane authority:

```text
ActionRequest -> ActionSnapshot -> authorization -> confirmation -> lease -> Device Agent -> verification -> audit
```

Phase 3A adds only references such as `ActionSnapshotRef`; it does not replace or weaken the Phase 2.6 kernel.

## Verification summary

Latest full suite after Phase 3A implementation:

```text
Ran 174 tests in 0.724s

OK (skipped=8)
```

Phase 3A new tests: 26.
Phase 2.6 retained tests: 148.
Windows tests present: YES.
Windows tests executed here: NO.
Windows security validation: NOT VERIFIED.
