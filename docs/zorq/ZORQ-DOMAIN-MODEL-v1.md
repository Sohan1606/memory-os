# ZORQ Domain Model v1

**Status label:** IMPLEMENTED / VERIFIED as Phase 3A executable contracts.
**Implementation:** `src/zroq/domain_contracts.py`.

## Domain model overview

Phase 3A implements stable schemas for the architectural graph:

```text
User -> OwnerIdentity -> Session -> Device
Conversation -> Message -> Response -> ResponseCursor -> Checkpoint
MemorySource -> Memory -> TimelineEvent -> Entity -> Relationship
Goal -> Project -> Decision -> Plan -> Action -> Outcome
Capability / Grant / Confirmation / Lease -> Action authority references
Evidence / Observation / Recommendation -> Intelligence support
Experiment / EvolutionProposal -> controlled future evolution contracts
```

## Ownership and tenancy

Durable records include explicit ownership fields such as `owner_id`, and when applicable `principal_id`, `source_owner_id`, `project_id`, privacy class, retention policy, lifecycle state, or provenance. Owner identity is never inferred from message text or memory content.

## Stable identifiers

Every record has a stable ID. IDs are validated and designed to remain stable across retrieval, indexing, derived memory, export, deletion, migration, and restart.

Conversation message ordering uses explicit `sequence` fields and does not depend only on wall-clock timestamps.

## Source and derived memory

`MemorySource` represents original evidence. `Memory` represents derived/structured knowledge and must reference its source through `source_id` and `Provenance`. Derived memory cannot replace source evidence.

## World model relationships

The executable `DomainIndex` validator checks cross-entity consistency:

- Memory references a valid MemorySource.
- Relationship references valid Entity records.
- Decision references a valid Project when provided.
- Outcome references the correct Action and ActionSnapshotRef.
- ResponseCursor references a valid Response and Conversation.
- TimelineEvent references valid sources and memories.

`DomainIndex` is a validation helper only, not a storage engine.

## Status labels

- Domain schemas: IMPLEMENTED.
- Contract validation tests: VERIFIED.
- Persistent store/retrieval engine: DEFERRED / NOT VERIFIED.
- Production MEMORY//OS integration: DEFERRED / NOT VERIFIED.
