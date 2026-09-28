# ZORQ Schema Versioning v1

**Status label:** IMPLEMENTED / VERIFIED for Phase 3A contracts.
**Implementation:** `ContractRecord` base class in `src/zroq/domain_contracts.py`.

## Schema version

All Phase 3A contract records include:

```text
schema_version = "zorq.phase3a.v1"
```

## Serialization format

```text
json-compatible-dict/v1
```

Each contract supports:

- `to_dict()`;
- `from_dict()`;
- `to_json()`;
- `from_json()`.

Serialized records include `schema_type`.

## Compatibility policy

Phase 3A accepts only explicitly supported schema versions. Unknown fields, unsupported schema versions, and unknown enum values fail closed. Migrations must be explicit. Old records must not be silently reinterpreted.

## Unknown-field handling

`from_dict()` rejects unknown fields. This protects durable record semantics from silently accepting unintended future data.

## Immutability and alias prevention

Construction recursively copies and freezes mappings, lists, tuples, and sets. This prevents caller-owned mutable aliases from mutating durable contract state after validation.

## Status labels

- Versioned serialization: IMPLEMENTED / VERIFIED.
- Future migration framework: DESIGNED / DEFERRED.
