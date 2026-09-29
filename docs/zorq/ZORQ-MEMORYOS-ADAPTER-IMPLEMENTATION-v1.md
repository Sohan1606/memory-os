# ZORQ MEMORY//OS Adapter Implementation v1

**Status:** SUPERSEDED by Phase 3B.2 — see [`ZORQ-PHASE3B2-IMPLEMENTATION.md`](ZORQ-PHASE3B2-IMPLEMENTATION.md).
**Real MEMORY//OS v10.2.0 source/API:** FOUND in the canonical repository (`backend/`); production adapter implemented and verified (Phase 3B.2).
**Production integration:** IMPLEMENTED (`src/zroq/adapters/memoryos_v10.py`) — verification in [`ZORQ-PHASE3B2-VERIFICATION.md`](ZORQ-PHASE3B2-VERIFICATION.md).

> Historical note (preserved for provenance): at the time this document was
> written, the real MEMORY//OS backend was not present in the working
> environment, so only the documented-contract harness below could be
> implemented. Phase 3B.2 replaced the harness as the production authority;
> `DocumentedMemoryOSAdapter` is retained explicitly relabeled as the
> fallback/local-harness mode.

## Environment search result

Accessible search locations included `/home/user`, the ZORQ workspace, and top-level filesystem candidate paths. Only ZORQ documents/contracts and the existing Phase 2.x test double were found. No mounted production MEMORY//OS implementation, integration package, repository, or verified v10.2.0 API surface was available.

## Implemented adapter

`DocumentedMemoryOSAdapter` implements the documented boundary in `src/zroq/personal_continuity.py`.

Outcomes:

- `ALLOW`
- `DENY`
- `HOLD`
- `UNAVAILABLE`
- `CONTRADICTORY`
- `NOT_APPLICABLE`

For governed operations, `UNAVAILABLE` and `CONTRADICTORY` never become `ALLOW`.

## Operations

Implemented through the adapter boundary:

- `store_conversation`
- `store_source`
- `retrieve`
- `derive`
- `timeline`
- `activate`
- `delete`
- `export`

The adapter applies owner context, purpose, egress policy, provider trust class, and capture policy before local storage/retrieval/deletion proceeds.

## Truthful status values

- `integration_status = DOCUMENTED_CONTRACT_HARNESS_NOT_REAL_MEMORYOS` when local harness is available but real MEMORY//OS is not verified.
- `integration_status = UNAVAILABLE` when governance is unavailable; writes fail closed.
- `real_memoryos_verified = False` in this environment.

## Security rule

A caller does not gain authority by constructing `MemoryActivationDecision(governance_status=ALLOW)` or any other object. Runtime persistence/retrieval/activation/deletion must cross the adapter and derive the decision from the adapter outcome.

## Non-claims

This is not a fork, rewrite, or replacement for MEMORY//OS. It is a local integration harness plus durable ZORQ derived-view store for Phase 3B verification where canonical MEMORY//OS is unavailable.
