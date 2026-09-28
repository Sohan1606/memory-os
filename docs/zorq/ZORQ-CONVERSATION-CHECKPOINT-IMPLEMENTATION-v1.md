# ZORQ Phase 3C Conversation Checkpoint Implementation v1

Phase 3C persists conversation checkpoints using the existing domain contract
`ConversationCheckpoint`.

## Stored data

Checkpoint records include:

- `checkpoint_id`
- `conversation_id`
- `owner_id`
- `created_at`
- `branch_id`
- `topic`
- `unresolved_questions`
- referenced memory IDs
- current `ResponseCursor` when applicable
- historical action-state references when supplied
- referenced security epoch when supplied

Checkpoints are serialized via the existing contract `to_dict()`/`from_dict()` path and stored in
`conversation_checkpoints.checkpoint_json`.

## When checkpoints are created

The runtime checkpoints after:

- conversation start
- response completion
- STOP/interruption
- PAUSE
- CANCEL
- SKIP
- REPEAT
- runtime command completion
- topic/branch change
- explicit `checkpoint` command
- explicit historical authority-reference checkpoint creation

## Authority safety

Conversation checkpoints may preserve historical references for explanation or continuity, but they
must not restore active executable authority.

Allowed historical references:

- `referenced_session_ids`
- `referenced_confirmation_ids`
- `referenced_grant_ids`
- `referenced_lease_ids`
- `referenced_security_epoch`
- inert `historical_action_state`

Forbidden active authority fields:

- `active_session_id`
- `active_confirmation_id`
- `active_grant_id`
- `active_lease_id`
- `pending_authorization_id`

The existing `ConversationCheckpoint` contract raises `ContractValidationError` if active authority
fields are populated.

## Restart recovery

`ConversationRuntime.reopen_conversation(context, conversation_id)` reads the persisted runtime row
and latest checkpoint for the owner/conversation pair. Owner isolation is enforced before any
checkpoint is returned.

## Regression coverage

Phase 3C tests verify:

- checkpoint creation on branch/topic change
- recovery after runtime restart
- checkpoint storage of response cursor after interruption
- historical authority references are allowed
- active authority restoration is rejected by contract validation
