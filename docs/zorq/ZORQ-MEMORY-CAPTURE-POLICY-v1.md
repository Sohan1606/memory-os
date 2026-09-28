# ZORQ Memory Capture Policy v1

**Status label:** IMPLEMENTED / VERIFIED as schema only.
**Implementation:** `MemoryCapturePolicy`, `RetentionMode`, `RetentionOverride`, `DeletionBehavior`.

## Purpose

Phase 3A defines the owner-configurable policy schema that later phases will use for memory capture. It does not implement storage or production MEMORY//OS integration.

## Policy dimensions

The policy distinguishes:

- default conversation retention;
- explicit “remember this”;
- explicit “don't remember this”;
- temporary conversation;
- sensitive memory;
- project-scoped memory;
- retention period;
- deletion behavior;
- per-conversation override;
- per-message override.

## Supported retention modes

```text
DEFAULT_RETAIN
DEFAULT_DO_NOT_RETAIN
PER_CONVERSATION_OVERRIDE
PER_MESSAGE_OVERRIDE
TEMPORARY
PROJECT_SCOPED
EXPLICIT_REMEMBER
EXPLICIT_DO_NOT_REMEMBER
```

The schema supports:

```text
DEFAULT RETAIN
DEFAULT DO NOT RETAIN
PER-CONVERSATION OVERRIDE
PER-MESSAGE OVERRIDE
```

## Deletion policy

Deletion behavior is represented but not executed in Phase 3A. Later phases must propagate deletion across source records, derived memories, embeddings, indexes, graphs, caches, and backups.

## Status labels

- Capture policy schema: IMPLEMENTED / VERIFIED.
- Storage enforcement: DEFERRED / NOT VERIFIED.
- Production deletion propagation: DEFERRED / NOT VERIFIED.
