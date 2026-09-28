# ZORQ Provenance Model v1

**Status label:** IMPLEMENTED / VERIFIED as executable contracts.
**Implementation:** `Provenance`, `Evidence`, `MemorySource`, and provenance-bearing domain records.

## Reusable provenance structure

`Provenance` supports:

- `source_type`;
- `source_id`;
- `conversation_id`;
- `message_id`;
- parent/derived source IDs;
- `created_at`;
- `observed_at`;
- confidence;
- verification state;
- derivation method;
- supersedes;
- superseded_by.

It is immutable-by-value and can be serialized/deserialized through the generic contract system.

## Evidence contract

`Evidence` supports:

- source identifier;
- URI/reference when applicable;
- publisher/author;
- source type;
- access time;
- publication/update time;
- jurisdiction/context;
- primary/secondary classification;
- evidence strength;
- limitations;
- claim references.

No web research runtime is implemented in Phase 3A.

## Memory and external evidence

The same provenance model works for personal memory and external evidence. Source claims, derived memory, and ZORQ inference remain distinct.

## Status labels

- Provenance contracts: IMPLEMENTED / VERIFIED.
- Web/source collection runtime: DEFERRED / NOT VERIFIED.
- Production evidence store: DEFERRED / NOT VERIFIED.
