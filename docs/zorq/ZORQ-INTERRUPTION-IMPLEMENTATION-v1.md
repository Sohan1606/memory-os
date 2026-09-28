# ZORQ Phase 3C Interruption Implementation v1

Phase 3C implements text-only interruption for response generation. It deliberately does not
interrupt, cancel, or control unrelated real-world actions.

## Implemented controls

- `STOP`
- `PAUSE`
- `CONTINUE`
- `RESUME`
- natural resume phrase: “What were you saying?”
- `CANCEL`
- `REPEAT`
- `GO_BACK`
- `SKIP`
- `CHANGE_TOPIC`
- `REMEMBER_THIS`
- `DO_NOT_REMEMBER`
- `FORGET_THIS`
- `FORGET_CONVERSATION`

## STOP

`STOP` applies only when a response stream is active or a resumable response exists.

Behavior:

- cancels the local provider stream signal
- does not call Action Plane objects
- updates persisted response state to `INTERRUPTED`
- preserves generated prefix in `conversation_responses.generated_text`
- stores cursor position and references
- retains the interrupted prefix as an assistant source record through Personal Continuity governance
- creates a checkpoint
- leaves the conversation ready for follow-up

## PAUSE

`PAUSE` preserves response prefix/cursor and marks the response resumable, but does not retain a
separate assistant source message until a later completion/cancellation path requires it.

## RESUME / CONTINUE

If an active stream exists, `CONTINUE` continues consuming stream events. If no stream exists but the
latest response is resumable, the runtime starts a new provider request with:

- `generated_prefix`
- `resume_from_position`
- `runtime_context.response_state == RESUMING`
- `generation_config.resume_strategy == prefix-preserved-provider-continuation`

The runtime does not claim exact token-level continuation for providers that cannot supply it.

## CANCEL

`CANCEL` marks the response non-resumable, persists any generated prefix as canceled when non-empty,
and checkpoints the cursor. It is conversation-only and does not cancel unrelated actions.

## SKIP

`SKIP` marks the current response as canceled/non-resumable, stores any generated prefix as skipped
when non-empty, and returns the conversation to `WAITING`. It is a response-generation skip, not an
Action Plane cancellation.

## Provider failure

Provider unavailability or exceptions produce `RESPONSE_FAILED` events and a canceled response row.
No assistant text is fabricated. If the user message was retainable, it remains source-backed in the
Personal Continuity archive.

## Regression coverage

`tests/test_phase3c_conversation_runtime.py` verifies:

- STOP preservation of prefix and cursor
- resume from preserved prefix
- no duplicated prefix source on resume
- PAUSE/CONTINUE behavior
- CANCEL non-resumability
- SKIP behavior
- provider unavailability/exception handling
- static no-action-authority boundary
