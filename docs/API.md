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
