# ZORQ Phase 3C Conversation State Machine v1

Phase 3C reuses existing domain contract enum `ConversationState`:

- `IDLE`
- `LISTENING`
- `THINKING`
- `SPEAKING`
- `INTERRUPTED`
- `PAUSED`
- `WAITING`
- `RESUMING`
- `CANCELED`
- `COMPLETED`

The runtime is text-first, so `SPEAKING` means “assistant response generation/streaming is active”
for text. It does not imply TTS/audio.

## Normal turn transitions

```text
IDLE/COMPLETED/WAITING
  -> THINKING       build context and activate memory
  -> SPEAKING       invoke provider and stream events
  -> COMPLETED      provider emits RESPONSE_COMPLETED and assistant text is retained
```

If the provider is unavailable or raises an exception:

```text
THINKING/SPEAKING -> CANCELED
```

A failed provider turn stores the user message, records runtime failure telemetry, and does not
fabricate assistant text.

## Control transitions

### STOP

```text
SPEAKING -> INTERRUPTED
```

Effects:

- stops further response generation
- preserves the generated prefix
- persists the prefix as an interrupted assistant source if non-empty
- persists a `ResponseCursor`
- creates a checkpoint
- keeps the conversation ready for follow-up/resume
- does not cancel unrelated Action Plane work

### PAUSE

```text
SPEAKING -> PAUSED
```

Effects:

- stops the current stream
- preserves response cursor and generated prefix
- marks response resumable
- creates checkpoint
- does not cancel unrelated Action Plane work

### CONTINUE / RESUME / “What were you saying?”

```text
INTERRUPTED/PAUSED -> RESUMING -> SPEAKING -> COMPLETED
```

Effects:

- resumes using preserved prefix/cursor metadata
- passes `generated_prefix` and `resume_from_position` to the provider request
- records a `prefix-preserved-provider-continuation` strategy
- if the provider cannot token-level continue, the deterministic Phase 3C strategy stores the
  preserved prefix and asks the provider to emit continuation from that prefix rather than claiming
  exact hidden model-state continuation

### CANCEL

```text
SPEAKING/PAUSED/INTERRUPTED -> CANCELED
```

Effects:

- marks response non-resumable
- persists the generated prefix as canceled if non-empty
- creates checkpoint
- does not cancel unrelated Action Plane work

### SKIP

```text
SPEAKING -> WAITING
```

Effects:

- skips remaining response generation
- marks the response row canceled/non-resumable
- stores any already generated prefix as skipped if non-empty
- remains ready for a new user message
- does not cancel unrelated Action Plane work

### REPEAT

```text
COMPLETED/INTERRUPTED/PAUSED/CANCELED -> COMPLETED
```

Effects:

- repeats the last stored runtime response in a new runtime response record
- records the repeated assistant message through Personal Continuity governance

### CHANGE_TOPIC

```text
any idle-like conversation state -> IDLE on a new branch
```

Effects:

- creates a new `conversation_branches` row
- switches current branch
- updates active topic
- checkpoints the branch/topic change

### GO_BACK

```text
any idle-like conversation state -> IDLE at checkpoint branch
```

Effects:

- loads latest checkpoint
- switches to checkpoint branch when available
- does not restore executable authority

## Memory command transitions

Memory commands are handled as runtime commands rather than normal provider generation:

- `REMEMBER_THIS` -> `COMPLETED` after acknowledgement
- `DO_NOT_REMEMBER` -> `COMPLETED` after acknowledgement and marks next message as do-not-retain
- `FORGET_THIS` -> `IDLE` after deletion/tombstone of selected source
- `FORGET_CONVERSATION` -> `CANCELED` and runtime content scrubbed
- explicit historical recall -> `COMPLETED` with source-backed answer
- show memory -> `COMPLETED` with source-backed retrieval summary

## Action-state boundary

This state machine is conversation-only. No transition grants leases, confirms actions, revokes
Action Plane sessions, or cancels device execution. Action cancellation remains explicit Phase 2.6
Action Plane behavior.
