# ZORQ Phase 3C Response Cursor Implementation v1

Phase 3C uses the existing domain contract `ResponseCursor` from `domain_contracts.py`.
The cursor is persisted indirectly in the `conversation_responses` table and embedded in
`ConversationCheckpoint` records.

## Cursor fields used

- `cursor_id`
- `response_id`
- `conversation_id`
- `branch_id`
- `owner_id`
- `created_at`
- `updated_at`
- `generation_state`
- `speech_state`
- `text_position`
- `semantic_position`
- `last_spoken_boundary`
- `interruption_reason`
- `resume_policy`
- `referenced_evidence_ids`
- `referenced_memory_ids`

Because Phase 3C is text-first, `speech_state` is reused as the existing cross-modal contract field
for response lifecycle. It does not mean audio/TTS was produced. `last_spoken_boundary` is stored as
`text-only:no-audio-boundary`.

## Persisted response row

`conversation_responses` stores cursor-relevant state:

- `generation_state`
- `speech_state`
- `generated_text`
- `text_position`
- `semantic_position`
- `resume_policy`
- `referenced_memory_ids_json`
- `referenced_evidence_ids_json`
- `generation_config_json`
- `source_message_ids_json`
- `resumable`

`ConversationRuntimeStore.upsert_response(...)` reconstructs a `ResponseCursor` every time response
state is changed.

## Interrupt semantics

When `STOP` is received while generation is active:

1. The active stream is canceled locally.
2. The response row is updated to `GenerationState.INTERRUPTED` / `SpeechState.INTERRUPTED`.
3. `generated_text` remains the exact text prefix already streamed.
4. `text_position == len(generated_text)`.
5. `semantic_position == "char:<text_position>"`.
6. `resume_policy == SUMMARIZE_THEN_RESUME` for deterministic/provider-neutral prefix continuation.
7. A checkpoint stores the `ResponseCursor`.
8. The interrupted prefix is retained through Personal Continuity governance as an assistant source
   record when non-empty.

The original partial response is not discarded.

## Resume strategy

Phase 3C does not claim hidden model-state/token-state continuation unless a future provider can
actually guarantee it. The implemented strategy is **prefix-preserved provider continuation**:

- request field `generated_prefix` contains the exact already-streamed prefix
- request field `resume_from_position` contains the preserved cursor text position
- `generation_config["resume_strategy"] == "prefix-preserved-provider-continuation"`
- deterministic provider emits only the remaining chunks when the script begins with the prefix
- if a provider cannot align with the prefix, it must truthfully continue from the prefix rather than
  pretending exact hidden continuation

When a resumed response completes, the response row contains the combined text, while the canonical
source archive stores the previously interrupted prefix and the new continuation delta separately to
avoid duplicating the prefix as a second assistant source message.

## Cancel and skip

`CANCEL` and `SKIP` mark a response non-resumable:

- `generation_state == CANCELED`
- `speech_state == CANCELED`
- `resume_policy == DO_NOT_RESUME`
- `resumable == 0`

`CANCEL` sets the conversation state to `CANCELED`; `SKIP` sets it to `WAITING` for the next user
message.
