# ZORQ Personal Continuity Implementation v1

**Status:** IMPLEMENTED / VERIFIED locally for text continuity.

## Source archive

The durable source archive is SQLite-backed and stores original retained messages, not only summaries.

Stored source fields include:

- conversation ID;
- message ID;
- source ID;
- owner;
- role;
- content;
- sequence;
- event time;
- ingest time;
- privacy class;
- retention mode;
- provenance;
- lifecycle state;
- project/branch/entity/goal/decision references;
- timezone/local display metadata.

Message order uses sequence plus event time. It does not rely on timestamps alone.

## Conversation lifecycle

Implemented:

- session/conversation start;
- session/conversation end;
- branch metadata;
- title/topic metadata;
- participant;
- owner;
- project;
- retention;
- deletion lifecycle state.

## Capture policy

`MemoryCapturePolicyEngine` implements Phase 3A capture policy behavior for:

- default conversation retention;
- explicit remember;
- explicit do-not-remember;
- temporary conversation/message;
- sensitive memory policy;
- project-scoped memory;
- per-conversation/per-message override.

The Phase 3B tests use an owner policy equivalent to `DEFAULT_RETAIN`. Retention is not silently forced if policy denies storage.

## Derived memory

Derived memory is separate from source and always source-backed.

Supported derived kinds:

- `PERSONAL_FACT`
- `PREFERENCE`
- `GOAL`
- `PROJECT_FACT`
- `DECISION`
- `EVENT`
- `RELATIONSHIP`
- `LESSON`
- `OUTCOME`

Derived memory never replaces original source records.

## Current vs historical

Supersession preserves old records. A newer memory can mark an older one `SUPERSEDED` while the old source remains historical evidence.

## Conflict handling

Conflicting derived memories preserve both source-backed claims and mark uncertainty with `CONFLICTED` lifecycle state.

## Restart safety

SQLite persistence is verified by restarting the store/adapter/engine over the same database and retrieving source-backed history.
