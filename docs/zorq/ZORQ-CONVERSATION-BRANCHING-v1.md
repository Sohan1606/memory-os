# ZORQ Phase 3C Conversation Branching v1

Phase 3C adds lightweight persistent branch support for text conversations.

## Persistence

Branches are stored in `conversation_branches`:

- `owner_id`
- `conversation_id`
- `branch_id`
- `parent_branch_id`
- `topic`
- `start_sequence`
- `created_at`
- `lifecycle_state`

The active branch is tracked in `conversation_runtime.current_branch_id`.

## Creation

Branches can be created directly:

```python
ConversationRuntime.create_branch(context, conversation_id, topic)
```

or by user command:

```text
change topic to <topic>
```

Changing topic creates a new branch whose parent is the previous active branch, updates the active
topic, and creates a checkpoint.

## Switching

Branches can be switched explicitly:

```python
ConversationRuntime.switch_branch(context, conversation_id, branch_id)
```

`GO_BACK` uses the latest checkpoint and switches to the checkpoint branch if one is present.

## Memory and source records

When user/assistant messages are retained, the runtime passes the active `branch_id` into
`PersonalContinuityEngine.record_message`. The source archive therefore preserves conversation and
branch provenance without adding a second memory API.

## Restart continuity

Branch state is stored in SQLite. A new `ConversationRuntime` instance over the same database can
call `reopen_conversation(...)` and recover:

- active branch ID
- active topic
- conversation state
- latest checkpoint

## Limits

Phase 3C branching is intentionally minimal. It does not implement speculative agent branches,
future simulation branches, browser sessions, GUI state, or autonomous task branches.
