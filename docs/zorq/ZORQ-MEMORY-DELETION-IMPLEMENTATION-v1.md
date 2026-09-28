# ZORQ Memory Deletion Implementation v1

**Status:** IMPLEMENTED / VERIFIED for implemented local representations.

## Supported scopes

Implemented deletion scopes:

- message/source IDs;
- conversation;
- project;
- date range;
- derived memory only.

## Propagation targets implemented

Deletion/tombstoning propagates to:

- raw source record content;
- derived memory content;
- SQLite FTS indexes;
- local semantic/concept index rows;
- timeline descriptions;
- relationship graph rows;
- deletion audit metadata.

## Status semantics

Reports use:

- `COMPLETED`
- `PARTIAL`
- `UNKNOWN`
- `FAILED`
- `RETAINED_BY_POLICY`

Backups are not implemented. If backup deletion is requested, the result must not be `COMPLETED`; it is partial/unknown.

## Audit safety

Deletion audit records include requester, scope, policy/outcome, propagation targets, and status. Deleted personal content is not stored in the audit record.

## Source vs derived deletion

Derived-memory-only deletion can remove/tombstone derived views while preserving source records, when requested.
