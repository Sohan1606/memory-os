# ZORQ Temporal Model v1

**Status label:** IMPLEMENTED / VERIFIED as executable contracts.
**Implementation:** `TemporalExtent` plus UTC validation across Phase 3A contracts.

## Canonical time

UTC is canonical for executable contracts. Datetime fields must be timezone-aware UTC. Non-UTC aware datetimes fail validation.

Where local display matters, contracts preserve:

- `local_display_time`;
- `timezone_name`;
- `utc_offset_minutes`.

## Time-sensitive fields

Contracts distinguish, where applicable:

- `event_time`;
- `event_start` / `event_end`;
- `created_at`;
- `observed_at`;
- `ingested_at`;
- `updated_at`;
- `valid_from`;
- `valid_until`.

## Historical vs current validity

`TemporalExtent` supports:

- exact timestamp;
- date/range representation via start/end;
- before/after validation;
- current validity (`is_current_at`);
- historical inclusion (`includes`).

This is the Phase 3A contract foundation for:

```text
27-09-2026 conversation
-> retained source/provenance
-> 30-07-2035 exact historical recall query
```

## Validation

Temporal ranges fail closed when end precedes start. This applies to validity windows, event ranges, retrieval time ranges, deletion time ranges, sessions, confirmations, leases, and experiments.

## Status labels

- Temporal contracts: IMPLEMENTED / VERIFIED.
- Natural-language temporal parsing: DEFERRED / NOT VERIFIED.
- Persistent temporal indexes: DEFERRED / NOT VERIFIED.
