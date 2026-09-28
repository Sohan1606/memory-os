# ZORQ Interaction Control Contract v1

**Status label:** IMPLEMENTED / VERIFIED as executable schema.
**Implementation:** `InteractionControlCommand`, `InteractionCommandType`, `InteractionTarget`, `ResponseCursor`.

## STOP semantics

Phase 3A encodes the distinction between speech STOP and action STOP.

### Speech STOP

```text
SPEAKING -> STOP(target=SPEECH) -> immediate speech interruption -> preserve ResponseCursor
```

### Action STOP

```text
STOP(target=ACTION) -> Action Plane cancellation semantics
```

A generic STOP with unknown target fails validation. Conversational STOP must not automatically cancel an unrelated external action.

## Supported interaction commands

- STOP;
- PAUSE;
- CONTINUE;
- CANCEL;
- REPEAT;
- GO_BACK;
- SKIP;
- CHANGE_TOPIC.

## Supported targets

- SPEECH;
- RESPONSE_GENERATION;
- ACTION;
- CONVERSATION;
- MEMORY_OPERATION;
- UNKNOWN.

STOP requires an explicit non-UNKNOWN target.

## Status labels

- Interaction-control schema: IMPLEMENTED / VERIFIED.
- Actual speech stop runtime: DEFERRED / NOT VERIFIED.
- Actual Action Kernel cancellation beyond existing Phase 2.6 slice: DEFERRED / NOT VERIFIED.
