# ZORQ MEMORY//OS Integration v1

**Status label:** DESIGNED. Production MEMORY//OS integration is NOT VERIFIED and DEFERRED.
**Canonical rule:** MEMORY//OS is the canonical memory-governance subsystem. ZORQ must not create a competing authority system.

## 1. Adapter boundary

The versioned adapter must support:

- retrieval;
- storage;
- provenance;
- policy;
- deletion;
- retention;
- sensitivity;
- conflict handling;
- memory lifecycle;
- governance decisions.

## 2. Adapter contract states

- ALLOW;
- DENY;
- HOLD;
- UNAVAILABLE;
- CONTRADICTORY;
- NOT_APPLICABLE.

If required governance is unavailable for governed operations, ZORQ fails closed.

## 3. Logical interfaces

| Interface | Input | Output |
|---|---|---|
| retrieve | owner, query, scope, privacy/egress policy | governed records, provenance, denials/holds |
| store | source record, derived memory proposal, retention/sensitivity | stored IDs or denial/hold |
| delete | scope, owner auth, propagation plan | completed/partial/unknown/retained-by-policy |
| govern_action | action/memory context | allow/deny/hold/unavailable |
| conflict_check | memory item/new claim | current/historical/conflicted/superseded state |
| export | owner scope and format | governed export manifest |

## 4. ZORQ-derived views

ZORQ may maintain timelines, relationship graphs, indexes, summaries, and embeddings as derived views. These inherit source governance and deletion obligations. They are not canonical memory authority.

## 5. Failure semantics

- Unavailable memory retrieval for ordinary answer context may degrade response with disclosure.
- Unavailable governance for governed action/storage/deletion must fail closed or hold.
- Contradictory governance must not be silently overridden.

## 6. Production claim limitation

Do not claim production MEMORY//OS integration until it exists, is independently verified, and has deletion/retention/provenance/egress behavior proven end to end.
