# ZORQ Conversation Checkpoint Contract v1

**Status label:** IMPLEMENTED / VERIFIED as executable schema.
**Implementation:** `ConversationCheckpoint`, `ResponseCursor`.

## Purpose

Conversation checkpoints preserve continuity without reviving old security authority.

A checkpoint may contain:

- topic;
- branch;
- unresolved questions;
- decisions;
- referenced memory IDs;
- active task;
- response cursor;
- historical action state;
- historical references to sessions, confirmations, grants, leases, and security epoch.

## Security rule

A checkpoint MUST NOT restore executable authority. The schema rejects:

- `active_session_id`;
- `active_confirmation_id`;
- `active_grant_id`;
- `active_lease_id`;
- `pending_authorization_id`.

Old authority IDs may be referenced historically, but fresh execution requires fresh Phase 2.6 authorization.

## Response cursor

`ResponseCursor` tracks:

- response ID;
- conversation ID;
- branch ID;
- generation state;
- speech state;
- text position;
- semantic position;
- last spoken boundary;
- interruption reason;
- resume policy;
- referenced evidence IDs;
- referenced memory IDs;
- timestamps.

Transitions are represented by immutable methods: interrupt, pause, resume, cancel, complete.

## Status labels

- Checkpoint schema: IMPLEMENTED / VERIFIED.
- Text conversation runtime: DEFERRED / NOT VERIFIED.
- Voice/TTS/STT/barge-in runtime: DEFERRED / NOT VERIFIED.
