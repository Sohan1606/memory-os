# ZORQ Phase 3C.1 Response Control Protocol v1

This document defines the durable response-control protocol added in Phase 3C.1.

## Out-of-band control API

Conversation controls no longer depend only on literal chat text such as `"stop"`.

The runtime exposes explicit methods:

- `interrupt_response(context, conversation_id, response_id=None, reason=...)`
- `pause_response(context, conversation_id, response_id=None, reason=...)`
- `cancel_response(context, conversation_id, response_id=None, reason=...)`
- `skip_response(context, conversation_id, response_id=None, reason=...)`
- `resume_response(context, conversation_id, response_id=None, reason=...)`
- `continue_response(context, conversation_id, response_id=None, reason=...)`

Text commands still work, but they route to the explicit control API:

- `"stop"` -> `interrupt_response(...)`
- `"pause"` -> `pause_response(...)`
- `"cancel"` -> `cancel_response(...)`
- `"skip"` -> `skip_response(...)`
- `"continue"` / `"resume"` / “What were you saying?” -> `resume_response(...)`

Out-of-band interruption does not create a new ordinary user source message. A textual resume prompt
may still be retained as a user message because it is an ordinary conversational utterance.

## Persistent control state

`conversation_responses` now persists response-control metadata:

- `control_state`
- `control_epoch`
- `control_updated_at`
- `control_reason`
- `control_requested_by`
- `terminal_state`
- `resumable`

Control states:

- `ACTIVE`
- `STOP_REQUESTED`
- `PAUSE_REQUESTED`
- `CANCEL_REQUESTED`
- `RESUME_REQUESTED`
- `TERMINAL`

Every authoritative control decision increments `control_epoch`. Active stream producers capture
the epoch they started under. Any producer with an old epoch is stale.

## Terminal precedence

Phase 3C.1 uses deterministic precedence:

```text
CANCEL_REQUESTED
  > STOP_REQUESTED
  > PAUSE_REQUESTED
  > provider completion/failure under normal ACTIVE generation
```

Semantics:

- STOP: response becomes `INTERRUPTED`, resumable, prefix preserved.
- PAUSE: response becomes `PAUSED`, resumable, prefix preserved.
- CANCEL: response becomes `CANCELED`, non-resumable, prefix preserved but no future continuation.
- COMPLETED: allowed only when no newer control epoch invalidated the stream.

`CANCEL` wins a STOP/CANCEL race regardless of thread scheduling. A lower-precedence control request
cannot downgrade a stronger existing control state.

## Stale stream rejection

Before committing provider output, the stream verifies:

```text
stream.control_epoch == persisted.control_epoch
persisted.control_state == ACTIVE
```

If either check fails, the event is ignored/rejected as stale.

This applies to:

- late `TEXT_DELTA`;
- late `MEMORY_REFERENCE`;
- late `EVIDENCE_REFERENCE`;
- late `RESPONSE_COMPLETED`;
- late `RESPONSE_FAILED`;
- late `RESPONSE_CANCELED`.

A stale producer may physically continue yielding events, but it loses authority to mutate durable
conversation state.

## Atomicity

Where practical, Phase 3C.1 uses SQLite `BEGIN IMMEDIATE` transactions for:

- control epoch increment;
- durable control state update;
- response generation-state transition;
- conversation-state transition.

Producer writes use conditional transactions that verify the expected epoch and `ACTIVE` state before
mutating response rows.

## Source-record consistency

Interrupted/canceled prefixes are retained through the existing Personal Continuity governance path
only for the actual committed prefix. Late stale output does not create completed assistant source
records.

A valid resume stores only the continuation delta as a new assistant source, preserving the Phase 3C
design:

```text
interrupted source:   "Alpha "
valid continuation:   "Beta Gamma"
combined response row: "Alpha Beta Gamma"
```

## Restart semantics

The `_active_streams` map is only an optimization/reference to live in-process producers. It is not
the source of truth.

On reopen, the runtime inspects persisted response state. If a row is still `GENERATING`/`ACTIVE`
but no in-memory producer exists in the new runtime, the response is treated as a recoverable
generating response. `resume_response(...)` can continue from the authoritative persisted prefix.

## Authorization scope

Phase 3C.1 control requests remain owner/context scoped through `MemoryAccessContext` and existing
owner isolation. This is not a production identity or voice-identity model. The runtime does not
invent speaker verification or production voice authentication.

## Action Plane separation

Conversational STOP/PAUSE/CANCEL/SKIP affect only response generation. They do not cancel unrelated
real-world actions and do not call `ActionKernel`, `DeviceAgent`, or `LeaseIssuer`. Explicit action
cancellation remains an Action Plane responsibility.

## Stream event terminal rule

Normal stream shape:

```text
RESPONSE_STARTED
→ TEXT_DELTA* / MEMORY_REFERENCE* / EVIDENCE_REFERENCE*
→ one terminal event:
   RESPONSE_COMPLETED
   RESPONSE_INTERRUPTED
   RESPONSE_PAUSED
   RESPONSE_CANCELED
   RESPONSE_FAILED
```

After a terminal control state is durably committed, late provider events are ignored as stale and
must not emit contradictory terminal completions to consumers.
