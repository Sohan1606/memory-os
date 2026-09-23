# MEMORY//OS V9 — Conversational Cognitive OS Architecture

## V9.0.2 local-model response boundary

The V9.0.2 path keeps the existing provider, capability router, model lock,
Meaning Compiler and Meaning Kernel. When the installed `langchain-ollama`
model exposes `with_structured_output`, semantic extraction uses Ollama's native
JSON-schema mode with `SemanticRepresentation` and `include_raw=True`.

The final boundary remains `MeaningCompiler.validate_model_output()`. It accepts
only a direct object/string, a complete JSON fence, one documented text/JSON
content block, or the documented LangChain structured wrapper. It never scans
prose for an embedded object. All normalized payloads still pass strict
Pydantic validation and semantic policy before deterministic materiality and
the Meaning Kernel.

Missing temporal scope, empty candidates, input/content mismatch, invented
paraphrases, wrong source/provenance, more than three candidates, confidence
above `0.85`, or `FACT` proposals fail closed to deterministic semantics.

## V9.0.1 live surface and semantic extraction

### Live surface

The browser first calls `POST /api/v9/surface/turns` to mint a server-owned,
user/thread-scoped correlation id. `POST /api/chat` receives that id only after
the backend verifies the canonical `surface.turn_started` event belongs to the
verified namespace and thread. While the synchronous V8 cognition/agent path
runs, real boundaries append `surface.activity` events:

```text
surface.turn_started
  → LISTENING COMPLETED
  → UNDERSTANDING ACTIVE / COMPLETED|FAILED
  → CHECKING_WORLD_STATE ACTIVE / COMPLETED|FAILED
  → CHECKING_MEMORY ACTIVE / COMPLETED|DEGRADED
  → CHECKING_PERSONAL_CONTEXT ACTIVE / COMPLETED|DEGRADED
  → EVALUATING_CONSEQUENCES ACTIVE / COMPLETED
  → FORMING_RESPONSE ACTIVE / COMPLETED|FAILED
  → WAITING_FOR_USER ACTIVE
```

`GET /api/v9/surface/turns/{correlation_id}` reconstructs latest state and full
transition history from `cognitive_events` using both `user_id` and
`correlation_id`. The frontend polls this persisted projection while `/api/chat`
is outstanding. Polling is a transport fallback, not a fake timer or a second
trace. The final `CognitiveSurface` reads the same lifecycle and remains
inspectable after the turn.

Stages such as external verification are absent unless corresponding tool work
actually occurred. No private reasoning is stored or returned.

### Model-assisted meaning

`ModelMeaningCompiler` uses the existing provider, shared local-model lock and
`CapabilityRouter` structured-output capability. It runs only for an available
local Ollama provider; V9.0.1 does not initiate paid external semantic calls.
The model can propose JSON but cannot write state.

```text
Ollama proposal
  → MeaningCompiler.validate_model_output (strict Pydantic, extra=forbid)
  → semantic policy (authorized input match, grounding, ≤3 candidates,
                     MODEL_HYPOTHESIS, confidence ≤0.85, no direct FACT)
  → deterministic materiality policy
  → Meaning Kernel
```

Unavailable, busy, timed-out, invalid, ungrounded or policy-rejected model
output is persisted as an inspectable fallback reason and uses the deterministic
compiler. Model hypotheses never silently become user-stated facts.

## Contract

V9 evolves the V8.5.1 runtime; it does not replace it. Conversation is the
primary control plane and the backend remains the source of truth.

```text
text / browser speech recognition
  → existing perception + conversation route
  → MeaningKernel
  → schema-validated SemanticRepresentation
  → Cognitive Objects + relationships
  → immutable Personal State version
  → existing bounded ContextBuilder
  → V8.5.1 family-level tool-surface routing
  → genuine model tool choice (or labelled deterministic fallback)
  → existing V8 cognitive systems
  → CognitiveSurface protocol
  → text + optional browser speech synthesis
```

## Semantic core

`app/schemas/semantic.py` defines the deterministic validation boundary:

- 26 distinct `CognitiveType` values;
- modality, confidence and temporal scope;
- the six provenance categories required by V9;
- evidence and relationship references;
- typed create/update/compile request models.

`app/cognition/meaning.py` compiles an utterance without silently upgrading
uncertainty. For example, “I think … next year” is a tentative `BELIEF`, not a
`GOAL`. Questions and generic claims are compiled for the turn but are not
automatically entered into long-term personal state. Ambiguous deictic
corrections are preserved and not materialized until resolved. Model-produced
JSON can enter only through `validate_model_output`; invalid output fails
closed.

Every compilation is inspectable in `meaning_compilations`. This is not an
event log: material transitions also emit onto the existing canonical
`cognitive_events` EventBus.

## Cognitive objects and V8 bridges

`PersonalStateService` persists canonical semantic objects in
`cognitive_objects`, preserving type, modality, confidence, provenance,
source, evidence, temporal scope, status and supersession. These records do
not claim to be operational missions, verified world facts, executed actions,
or validated V8.4.1 knowledge. Existing V8 subsystems remain authoritative for
those operational meanings.

The V9 ContextBuilder section adds only relevance-ranked active semantic
objects and retains the existing global character cap, truncation and degraded
state. Existing mission, world, memory, intent, prediction, research,
explanation, attention, autonomy and learning sections remain intact.

Relationships use an explicit vocabulary (`supports`, `contradicts`,
`refines`, `supersedes`, `derived_from`, `caused_by`, `depends_on`,
`related_to`, `motivates`, `constrains`, `predicts`, `resulted_in`). Both ends
must exist in the same verified user namespace.

## Versioned personal state

Every material object or relationship transition creates an immutable snapshot
in `personal_state_versions`. Snapshots carry a SHA-256 hash, reason,
correlation id, changed object ids and timestamp. Reconstruction can target a
version or time. Diff deterministically reports added, removed/retired,
changed, and relationship changes; an AI interpretation is not substituted for
recorded differences.

Object-local revisions are also retained in `cognitive_object_versions`.
History is appended rather than rewritten.

## Cognitive Surface Protocol

`app/cognition/surface.py` projects completed backend work into safe visible
activity. Each activity has a stage, status, source reference and timestamp.
The projection can show memory/context/world checks only when corresponding
trace data exists. It exposes evidence, object references, bounded insights and
uncertainty—not private reasoning.

The workspace renders this protocol directly. It does not animate invented
stages. Observatory adds a semantic-state inspector while retaining all V8
advanced inspection.

## Voice

Browser speech recognition populates the same chat input and sends
`interaction_mode=voice` to `POST /api/chat`. It therefore uses the same
MeaningKernel, context, routing, cognition and surface path as text. Supported
browsers may synthesize the returned response. Unsupported browsers display an
honest text fallback. Voice session start/end events describe real voice turns.

## Authorization and portability

All routes call the established `uid()` boundary. In authenticated mode,
caller-supplied `user_id` values are ignored and object queries include the
verified namespace. A foreign object id returns 404.

The existing portability service now includes the five V9 tables in the
`semantic_state` domain and emits its existing export/import/restore events.
Exports use schema `9.0`; prior schema versions remain accepted. Provenance and
state snapshots survive a verified restore.

## API

- `POST /api/v9/meaning/compile`
- `GET /api/v9/meaning/compilations`
- `GET /api/v9/meaning/compilations/{id}`
- `GET|POST /api/v9/cognitive-objects`
- `GET|PATCH /api/v9/cognitive-objects/{id}`
- `POST /api/v9/cognitive-objects/{id}/retire`
- `GET|POST /api/v9/relationships`
- `GET /api/v9/personal-state`
- `GET /api/v9/personal-state/versions/{version}`
- `GET /api/v9/personal-state/reconstruct?at=...`
- `GET /api/v9/personal-state/diff?from_version=...&to_version=...`

`POST /api/chat` remains the primary control plane and now returns `surface`.
