# ZORQ Phase 3C Conversational Runtime Implementation v1

Status: **implemented and verified in the local standalone ZORQ project**.
Scope: **text-first conversation runtime only**.

This document describes the Phase 3C runtime added in `src/zroq/conversation_runtime.py`.
It does **not** implement voice, STT, TTS, wake word, microphone access, browser automation,
GUI/computer vision automation, daemon behavior, autonomous agents, Truth Engine, Future
Simulator, Optimization Engine, Controlled Evolution, broad OS control, production MEMORY//OS,
or Phase 3C+ functionality.

## Runtime entry points

Main classes:

- `ConversationRuntime`
- `ConversationRuntimeStore`
- `ConversationModelProvider` protocol
- `ConversationProviderRequest`
- `ResponseStreamEvent`
- `ResponseStreamEventType`
- `DeterministicConversationProvider` test double
- `UnavailableConversationProvider`

The runtime uses the existing Phase 3B `PersonalContinuityEngine` and `PersonalContinuityStore`.
All retained user and assistant text is routed through `PersonalContinuityEngine.record_message`;
Phase 3C does not introduce a second canonical memory store. Runtime-only response state,
branches, checkpoints, cursors, and stream events are persisted in new SQLite tables in the same
database file unless another `db_path` is supplied.

## Text turn flow

Normal message flow:

1. Validate owner-scoped `MemoryAccessContext` and conversation identity.
2. Classify whether the text is a response-control command, memory-control command, explicit
   historical recall request, or normal user message.
3. For a normal message, build contextual activation through
   `PersonalContinuityEngine.activate_contextual_memory` before storing the current user text as
   candidate historical memory. This avoids self-activation of the just-arrived message.
4. Persist the user message through the Personal Continuity Engine unless the user selected a
   do-not-remember path and policy decides not to retain.
5. Build a provider-neutral `ConversationProviderRequest` containing:
   - conversation ID and branch ID
   - latest source-backed conversation messages
   - active topic and response state
   - current user message, bounded/redacted for provider context
   - contextual memory activation result IDs
   - retrieved source IDs and timeline references
   - project/entity/goal/decision references from the existing activation frame
   - bounded staged-retrieval counts
   - Memory Firewall metadata indicating minimized reasoning-context injection
   - provider egress posture (`NO_EGRESS` for local-only contexts)
6. Invoke the configured `ConversationModelProvider`.
7. Stream provider events into persisted runtime response state.
8. Persist assistant output through the Personal Continuity Engine when a response is completed,
   interrupted, canceled, skipped, or repeated, according to the command semantics.
9. Create a `ConversationCheckpoint` with the current response cursor after important state
   transitions.

## Provider abstraction

`ConversationModelProvider` is a protocol. A provider must expose:

```python
provider_id: str
is_test_provider: bool
stream_response(request: ConversationProviderRequest, cancellation_signal: CancellationSignal)
```

The provider must emit provider-neutral `ResponseStreamEvent` objects. The required event types
are:

- `RESPONSE_STARTED`
- `TEXT_DELTA`
- `MEMORY_REFERENCE`
- `EVIDENCE_REFERENCE`
- `RESPONSE_PAUSED`
- `RESPONSE_INTERRUPTED`
- `RESPONSE_COMPLETED`
- `RESPONSE_FAILED`
- `RESPONSE_CANCELED`

Implemented providers:

- `DeterministicConversationProvider`: deterministic test provider only. It is not represented as
  intelligent and is used by tests to exercise streaming, interruption, resume, references, and
  failure paths.
- `UnavailableConversationProvider`: truthfully reports model unavailability/degraded status and
  does not fabricate an answer.

No hosted/cloud provider is included in Phase 3C. The interface is intentionally provider-neutral
for future integration, but the runtime remains offline-first and supports local-only/no-egress
contexts.

## Context and memory behavior

Phase 3C distinguishes:

- **Automatic contextual activation**: triggered for normal turns through the existing contextual
  activation path. It produces activation telemetry and minimized reasoning context.
- **Explicit recall**: user historical questions such as “What did we discuss on 27 September
  2026?” route to `PersonalContinuityEngine.answer_historical_query` and return source-backed
  answers with evidence source IDs.

The runtime does not invent citations. Response records store `referenced_memory_ids_json` and
`referenced_evidence_ids_json` only from activation/retrieval/provider reference events.

Memory commands implemented:

- `REMEMBER_THIS`
- `DO_NOT_REMEMBER`
- `FORGET_THIS`
- `FORGET_CONVERSATION`
- source-backed memory display (`SHOW_MEMORY`)
- exact historical recall (`EXPLICIT_RECALL`)

Conversation deletion uses the existing Personal Continuity deletion path and also scrubs runtime
response text caches for the conversation. Phase 3C does not claim deletion completion for unknown
external systems; the existing deletion report describes implemented local representations.

## Action-plane separation

Conversation state remains distinct from Action Plane state.

- `ConversationRuntime.action_plane_available_from_conversation` returns `False`.
- The runtime does not import or instantiate `ActionKernel`, `DeviceAgent`, or `LeaseIssuer`.
- Text `STOP`, `PAUSE`, `CANCEL`, and `SKIP` affect response generation only.
- Conversational `STOP`/`CANCEL` do **not** cancel unrelated real-world actions.
- Explicit action cancellation remains the responsibility of the existing Phase 2.6 Action Plane.

Conversation checkpoints may store historical authority references using existing
`ConversationCheckpoint` fields, but contract validation prevents restoring active executable
authority (`active_session_id`, `active_confirmation_id`, `active_grant_id`, `active_lease_id`,
`pending_authorization_id`).

## Persistence tables

`ConversationRuntimeStore` creates these runtime tables:

- `conversation_runtime`
- `conversation_branches`
- `conversation_responses`
- `conversation_checkpoints`
- `conversation_runtime_events`

These tables hold runtime state only. Canonical personal history remains the Phase 3B source
archive (`messages`, derived memory tables, timeline, deletion audit, indexes).

## Verification

Focused Phase 3C tests live in:

- `tests/test_phase3c_conversation_runtime.py`

They cover normal turns, provider events, interruption, resume, pause, cancel, skip, provider
unavailability, provider exceptions, explicit recall, automatic contextual activation, provenance,
memory commands, deletion, owner isolation, branches, checkpoints, restart continuity, timezone
recall contract, and no-action-authority regression.
