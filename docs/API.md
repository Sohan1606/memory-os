# HTTP API

Base URL `http://127.0.0.1:8000`. The frontend reaches these through a
same-origin `/api/*` rewrite. `user_id` defaults to the demo user everywhere.

## System

### `GET /api/health`
Full truthful system report.

```json
{
  "status": "ok",
  "api": "fastapi",
  "agent": { "framework": "langgraph", "graph": "compiled", "checkpointer": "sqlite" },
  "memory": { "backend": "sqlite", "path": "memory_os.sqlite3", "count": 24 },
  "vector": { "backend": "chromadb", "mode": "semantic", "count": 24, "error": null },
  "embeddings": { "model": "all-MiniLM-L6-v2 (local ONNX)", "dimension": 384, "mode": "local" },
  "provider": { "name": "demo", "model": "deterministic-demo", "available": true,
                "tool_calling": false, "detail": "LOCAL DEMO - ..." },
  "voice": { "mode": "browser", "detail": "Browser SpeechRecognition fallback ..." },
  "langmem": { "state": "NOT INSTALLED", "version": null, "detail": "..." }
}
```

`vector.mode` is `semantic` or `keyword`; `langmem.state` is `NOT INSTALLED`,
`NOT CONFIGURED` or `ACTIVE`. These are measured, not asserted.

## Agent

### `POST /api/chat`
```json
{ "message": "Remember that I deploy on Fly.io every Friday.", "thread_id": "thread-alpha" }
```
Returns `answer`, `provider`, `recalled` (retrieval results with scores and
reasons), `activity` (safe operational events only — never model reasoning), and
`thread_id`.

### `GET /api/conversations`
List of `{ id, title, created_at, updated_at }`.

### `GET /api/conversations/{thread_id}/messages`
### `DELETE /api/conversations/{thread_id}`

## Memories

### `GET /api/memories?category=&q=`
### `POST /api/memories`
```json
{ "content": "...", "category": "PREFERENCE", "importance": 0.7, "source": "manual" }
```
Returns `{ "action": "created" | "reinforced" | "updated", "memory": {...} }` —
the action reflects real duplicate/conflict detection.

### `GET /api/memories/{id}`
Memory plus `versions` and `related`.

### `PATCH /api/memories/{id}` · `DELETE /api/memories/{id}`

### `POST /api/memories/search`
```json
{ "query": "what do you remember about my projects", "top_k": 5 }
```
Returns `query`, `state` (`STRONG` | `WEAK` | `NO_STRONG_MATCH`), `mode`
(`semantic` | `keyword`), `results[]` each with `score`, `strength`, `reasons`,
and a `path[]` of retrieval steps.

### `GET /api/memories/graph`
`{ nodes: Memory[], edges: [{source, target}] }` from real relationship rows.

### `GET /api/memories/timeline`
### `POST /api/memories/consolidate` — `{ "memory_ids": ["...", "..."] }`

## Audit and data

### `GET /api/events` — append-only audit log
### `GET /api/export` · `POST /api/import` · `POST /api/reset` · `DELETE /api/memories`

## Voice

### `GET /api/voice/status`
`{ "mode": "browser" | "whisper", "detail": "..." }`

### `POST /api/voice/transcribe`
Multipart upload. Returns 503 with an honest message when faster-whisper is not
installed rather than pretending to transcribe. (`python-multipart` is a hard
requirement — FastAPI fails at *import* time without it.)

## v8 cognitive endpoints

All responses are typed (pydantic on the server, TypeScript in `frontend/lib/types.ts`).
Unmeasured values return `INSUFFICIENT EVIDENCE` / `NOT CONFIGURED` rather than a number.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/cognition/status` | Single Observatory snapshot |
| GET | `/api/cognition/events` | Activity feed (`since` for incremental polling, `kind` to filter) |
| GET | `/api/cognition/turn/{correlation_id}` | Replay one conversational turn (404 if unknown) |
| GET | `/api/cognition/why` | Object history — `subject_kind`, `subject_id` |
| GET | `/api/cognition/changes` | What changed (optional `since`) |
| GET | `/api/cognition/resume` | Returning-user briefing |
| GET | `/api/cognition/self` | Capability + limitation report |
| GET | `/api/world` | World entities, summary, due-soon (`kind`, `state`) |
| GET | `/api/world/graph` | World entity graph |
| GET | `/api/intents` | Current intent + history |
| GET | `/api/predictions` | Predictions + calibration |
| POST | `/api/predictions/{id}/resolve` | Close a prediction against reality |
| GET | `/api/causality/{node_kind}/{node_id}` | Upstream, downstream, chain, impact |
| GET | `/api/decisions` | Recorded decisions |
| POST | `/api/decisions` | Record a decision with `influenced_by` |
| GET | `/api/memories/{id}/reputation` | Evidence-based reputation (distinct from confidence) |
| POST | `/api/memories/outcome` | Attribute a real outcome to a memory |
| GET | `/api/autonomy` | Level, trust, interventions, restraint |
| POST | `/api/autonomy` | Change autonomy level (400 on unknown level) |
| GET | `/api/learning` | Policies + self-evaluation |
| POST | `/api/learning/consolidate` | Mine the event log for repeated patterns |
| POST | `/api/sandbox` | Run a counterfactual (never mutates state) |
| GET | `/api/sandbox` | Simulation history |

`POST /api/chat` additionally returns `correlation_id` and a `cognition` trace
(need, intent, world entities, recall count, arbitration, attention, autonomy).

---

## V8.2 — Cognitive Core

All routes below are **additive**. Every V8/V8.1 route above is unchanged.

`/api/provider` gained `capabilities` and `routing`; `/api/health` reports
`"version": "8.2"`.

### Capability detection and routing (§3)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/capabilities` | Five capabilities with `state` (`SUPPORTED` / `NOT_SUPPORTED` / `UNKNOWN`) and the reason for each |
| GET | `/api/capabilities/route` | Full routing table — how every task class will actually run |
| GET | `/api/capabilities/route?task=vision` | One task. 400 on an unknown task |

`UNKNOWN` never satisfies a requirement. `vision` with no vision model returns
mode `NOT_CONFIGURED` — there is no honest deterministic fallback for it.

### Execution tracing (§4)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/execution` | Recent traces |
| GET | `/api/execution/{correlation_id}` | Every recorded step of one turn. **404** if unknown — never a fabricated trace |

Stages: `MODEL_CALL`, `TOOL_DECISION`, `TOOL_RESULT`, `MODEL_REVISION`,
`FINAL_RESPONSE`, `CONTEXT_BUILD`, `PROVIDER_DEGRADED`, `CANCELLED`,
`LIMIT_REACHED`, `TOOL_FAILED`, `DUPLICATE_TOOL_CALL`.

The `correlation_id` returned by `POST /api/chat` addresses both
`/api/cognition/turn/{id}` and `/api/execution/{id}` — one id per turn.

### Arbitration (§6)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/arbitration` | Recent arbitration records |
| GET | `/api/arbitration/{id}` | One record with all candidates. 404 if unknown |

Each record carries `winner_id`, `losers`, `blocked`, `reason`, `uncertainty`
and `conflict`.

### The causal chain (§8)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/influence` | Recorded influences. `?pending=true` for unresolved |
| POST | `/api/influence/{id}/outcome` | Attach an **observed** outcome |
| GET | `/api/memories/{id}/impact` | The full causal story of one memory |

```jsonc
// POST /api/influence/{id}/outcome
{
  "verdict": "SUPPORTED",          // SUPPORTED | CONTRADICTED | NEUTRAL | INSUFFICIENT EVIDENCE
  "detail":  "The Tuesday deploy went ahead and the user confirmed it",
  "evidence": ["user said 'yes, that worked'"]
}
```

**Evidence is required.** `SUPPORTED` or `CONTRADICTED` with an empty `evidence`
array is downgraded to `INSUFFICIENT EVIDENCE` and reputation does **not** move.
`NEUTRAL` never moves reputation. An outcome can only be recorded once (409-style
`ValueError`). 404 on an unknown influence; 422 on an unknown verdict.

### Adaptive policy and trust (§15 / §17)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/cognitive-policy` | Effective values + every policy with its evidence |
| GET | `/api/cognitive-policy/{key}` | One dimension, with a plain-language explanation. 404 on unknown key |
| DELETE | `/api/cognitive-policy/{key}` | Revert a learned behaviour to default |
| GET | `/api/trust` | Per-capability reliability |

Trust below `MIN_EVIDENCE` (3) reports `label: "INSUFFICIENT EVIDENCE"` with
`reliability: null` — never an optimistic default.

### Intent and needs (§10 / §11)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/intents/transitions` | Every intent change, with `changed_because`, `uncertainty` and evidence |
| GET | `/api/intents/{id}/why` | Trajectory of one intent. Unknown ids return `INSUFFICIENT EVIDENCE`, not 404 |
| GET | `/api/needs` | Recent needs, persisted hypotheses, measured accuracy |
| POST | `/api/needs/{id}/evaluate` | `{"correct": true}` — real feedback. 404 if unknown |

`accuracy.accuracy` is `null` with `INSUFFICIENT EVIDENCE` until feedback exists.

### Continuity (§9)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/continuity` | Open threads, each with a grounded `reason` |
| POST | `/api/continuity/{id}/close` | Close one. 404 if unknown |

### Focus / object permanence (§21)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/focus` | `{subject_kind, subject_id, session_id?, label?}`. 400/422 on an unknown kind |
| GET | `/api/focus?session_id=…` | Live (unexpired) focus entries |
| DELETE | `/api/focus?session_id=…` | Clear focus |

Focus has a 30-minute TTL and is scoped per session. It is what lets
"forget **that**" resolve to a stable id — and refuse when ambiguous.

### Explanations (§23)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/cognition/why-now` | Why this surfaced *now* — `subject_kind`, `subject_id` |
| GET | `/api/memories/{id}/why-used` | Arbitration + influence evidence for one memory |

No hidden chain-of-thought is ever returned; these cite recorded evidence.

### Prediction → reality (§13)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/predictions/{id}/observe` | Offer an observation against an open prediction |

```jsonc
{ "observation": "It slipped, we missed the deadline",
  "supports": null,            // omit to let the engine judge from the text
  "evidence": [] }
```

If the observation does not clearly resolve the prediction, it stays **open**
and the response explains why. When it does resolve, `error`, `surprise` and
`learning_signal` are returned and persisted.

### User-controlled cognition (§19)

| Method | Path | Notes |
|---|---|---|
| POST | `/api/control` | Execute a natural-language cognitive command |
| GET | `/api/control/commands` | The commands the system can actually act on |

```jsonc
// POST /api/control
{ "message": "forget that", "session_id": "default" }
```

**422** when the message contains no recognisable command — ordinary
conversation belongs on `/api/chat`. A destructive command whose target cannot
be resolved returns `200` with `applied: false` and
`requires: "clarification"`: the system asks rather than deleting the wrong
memory.
