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

---

# V8.3 — Continuous Cognition

All routes are same-origin under `/api`. Every one accepts an optional
`user_id`; omitted, it resolves to the demo user.

## Missions (§10–§12)

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/missions` | List missions. `?open_only=true`, `?state=` |
| `POST` | `/api/missions` | Create a mission (starts in `draft`) |
| `GET` | `/api/missions/brief` | Resume briefing: active / blocked / waiting / gone quiet |
| `GET` | `/api/missions/{id}` | One mission with its steps and links |
| `GET` | `/api/missions/{id}/history` | Every transition with reason and evidence |
| `POST` | `/api/missions/{id}/state` | Move state. `reason` is **required** |
| `POST` | `/api/missions/{id}/steps` | Add a step (refused past 7 open steps) |
| `POST` | `/api/missions/steps/{id}/complete` | Complete a step; recomputes progress |

An unknown state returns **400**; an unknown mission returns **404**.

## Observations (§17)

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/observations` | Evidence log plus status/source breakdown |
| `POST` | `/api/observations` | Record evidence (`origin` required) |
| `GET` | `/api/observations/evidence/{kind}/{id}` | All evidence on a subject, split by epistemic status |
| `POST` | `/api/observations/{id}/promote` | Explicitly promote to memory — `OBSERVED` only |

Promoting `INFERRED`/`SIMULATED` evidence returns `{"promoted": false}` with
the reason. No observation ever becomes a memory automatically.

## World state (§7–§9)

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/world/snapshot` | Facts with per-fact freshness |
| `GET` | `/api/world/changes` | Change provenance. `?entity_id=` |
| `GET` | `/api/world/stale` | Facts due re-confirmation (stale ≠ false) |
| `POST` | `/api/world/reconcile` | Offer a claim; returns a verdict |

Verdicts: `keep`, `supersede`, `merge`, `flag`, `downgrade`, `ignore`.

## Time machine (§21)

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/history/coverage` | The window history can answer for |
| `GET` | `/api/history/world?at=` | World state at a moment |
| `GET` | `/api/history/missions?at=` | Mission state at a moment |
| `GET` | `/api/history/diff?start=&end=` | What changed between two moments |

Outside recorded history: `{"available": false, "reason": "HISTORY NOT
AVAILABLE — …"}`.

## Background cognition (§13–§14)

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/background` | State, limits and real recent cycles |
| `POST` | `/api/background/control` | `enabled` / `paused` / `disabled` |
| `POST` | `/api/background/run` | Run one bounded cycle |

A cycle returns its true result, including `findings: []` and
`changes_made: 0`. Rate-limited to one cycle per 60 s unless `force`.

## Attention (§15–§16)

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/attention/evaluate` | Returns a level on the full ladder |
| `GET` | `/api/attention/policy` | Learned silence policy |
| `GET` | `/api/attention/suppressions` | What was not raised, and why |
| `POST` | `/api/attention/{id}/reaction` | Record a real reaction |

Levels: `IGNORE`, `MONITOR`, `PREPARE`, `MENTION`, `ASK`, `ACT`, `DO_NOTHING`.

## Simulation (§20)

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/api/simulation` | Counterfactual over a frozen snapshot |
| `POST` | `/api/simulation/{id}/commit` | Apply — needs `confirm` **and** `changes` |

Results carry `epistemic_status: "SIMULATED"`.

## Documents (§6)

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/documents` | Tracked documents plus parser capabilities |
| `POST` | `/api/documents` | Upload (multipart `file`) |

`understanding` is one of `FULL`, `PARTIAL`, `STRUCTURE ONLY`,
`METADATA ONLY`, `NOT AVAILABLE`.

## Connectors, research, maintenance, predictions

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/connectors` | Declared interfaces — all `NOT CONNECTED` |
| `GET` | `/api/connectors/{name}/fetch` | `items: null` (no source ≠ empty source) |
| `GET` | `/api/research` | `RESEARCH PROVIDER NOT CONFIGURED` (legacy V8.3 contract, unchanged) |
| `POST` | `/api/research` | Track a question; generates no findings (legacy V8.3 contract, unchanged) |
| `GET` | `/api/maintenance/review` | Read-only diagnosis, `changes: 0` |
| `GET` | `/api/maintenance/remedies` | The non-destructive remedy set |
| `GET` | `/api/predictions/due` | Elapsed evaluation windows |
| `POST` | `/api/predictions/{id}/unresolved` | Close honestly as `UNRESOLVED` |

## Connected Research (V8.4.3)

A **distinct, additive namespace** from `/api/research` above. These routes
drive the real `ResearchEngine`: explicit http(s) URLs are fetched through
an SSRF-defended pipeline (see [`docs/V8.4.3.md`](V8.4.3.md)). All routes
are scoped by `user_id` (query param on `GET`s, body field on `POST`s) and
404 on another user's session/update id.

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/api/research/v2/status` | Engine availability, limits, honest "no search engine" statement |
| `POST` | `/api/research/v2` | Create a session: `{"question": str, "user_id"?: str}` |
| `GET` | `/api/research/v2` | List sessions (`?user_id=&limit=`) |
| `GET` | `/api/research/v2/{session_id}` | Get one session |
| `POST` | `/api/research/v2/{session_id}/fetch` | Fetch one explicit URL: `{"url": str, "user_id"?: str}` |
| `POST` | `/api/research/v2/{session_id}/finish` | Mark COMPLETED/FAILED from real fetch outcomes |
| `GET` | `/api/research/v2/{session_id}/sources` | List discovered sources |
| `GET` | `/api/research/v2/{session_id}/fetches` | Full fetch ledger, including failures |
| `GET` | `/api/research/v2/{session_id}/evidence` | List raw evidence excerpts |
| `GET` | `/api/research/v2/{session_id}/claims` | List capped-confidence claims |
| `GET` | `/api/research/v2/{session_id}/conflicts` | List detected conflicts (both sides preserved) |
| `GET` | `/api/research/v2/{session_id}/world-updates` | List proposed/applied world updates |
| `POST` | `/api/research/v2/{session_id}/world-updates/propose` | Propose: `{"claim_id": str, "kind": str, "label": str, "user_id"?: str}` |
| `POST` | `/api/research/v2/world-updates/{update_id}/apply` | Apply: `{"confirm": true, "user_id"?: str}` — `confirm=false` → `400` |

`fetch` returns `{"status": "COMPLETED"|"FETCH_FAILED"|"BLOCKED"|"TIMEOUT", "error_code"?, "detail": str, ...}`
for every attempt — including blocked SSRF attempts and network failures,
which are never converted into an empty/silent result.

---

## V8.4.1 — Experience → Skill → Principle

All routes are additive. Every route resolves `user_id` through the existing
namespace mechanism; an id owned by another namespace returns the same 404 as an
unknown id.

### Experiences

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/experiences` | List episodes; optional `lifecycle`, `pattern_key`, `limit` |
| POST | `/api/experiences` | Create an observed episode from owned observation evidence |
| GET | `/api/experiences/{id}` | Full episode and evidence |
| POST | `/api/experiences/{id}/lifecycle` | Enrich, validate, activate or archive with an audited reason |
| GET | `/api/experiences/{id}/provenance` | Evidence origins, provenance and transition history |

Creation requires `situation` and a non-empty `evidence_ids` array. Each id must
refer to a canonical observation in the same user namespace. `success` requires
an `outcome`.

```jsonc
POST /api/experiences
{
  "situation": "deployment health check failed",
  "evidence_ids": ["obs_..."],
  "action": "inspect deployment logs before retrying",
  "outcome": "the timeout was identified",
  "success": true,
  "pattern_key": "inspect_deployment_logs",
  "scope_kind": "project",
  "scope_value": "Atlas",
  "source": "decision-outcome",
  "provenance": {"decision_id": "decision_..."},
  "user_id": "demo-user"
}
```

### Skills and Principles

The two kinds have parallel operational routes, but validation thresholds differ.

| Method | Skill path | Principle path |
|---|---|---|
| List | `GET /api/skills` | `GET /api/principles` |
| Create candidate | `POST /api/skills/candidates` | `POST /api/principles/candidates` |
| Retrieve/arbitrate | `POST /api/skills/retrieve` | `POST /api/principles/retrieve` |
| Inspect | `GET /api/skills/{id}` | `GET /api/principles/{id}` |
| Explain | `GET /api/skills/{id}/explanation` | `GET /api/principles/{id}/explanation` |
| Validate | `POST /api/skills/{id}/validate` | `POST /api/principles/{id}/validate` |
| Promote | `POST /api/skills/{id}/promote` | `POST /api/principles/{id}/promote` |
| Record use | `POST /api/skills/{id}/use` | `POST /api/principles/{id}/use` |
| Correct | `POST /api/skills/{id}/correction` | `POST /api/principles/{id}/correction` |

A validation PASS does not promote. Promotion is a separate operation and
requires the latest persisted validation to pass.

```jsonc
POST /api/skills/retrieve
{
  "query": "The Atlas deployment failed",
  "scope": {"project": "Atlas"},
  "current_world": ["Kubernetes cluster is reachable"],
  "limit": 5,
  "user_id": "demo-user"
}
```

The response carries `winner`, ranked `candidates`, `blocked` candidates and a
persisted `arbitration` explanation. Retired, contradicted, outdated,
out-of-scope or world-ineligible items cannot win.

```jsonc
POST /api/skills/{id}/correction
{
  "action": "rescope", // weaken | contradict | outdated | retire | forget | stop_using | rescope
  "reason": "Only valid in Atlas production",
  "scope_kind": "environment",
  "scope_value": "Atlas production",
  "evidence": ["explicit user correction"],
  "user_id": "demo-user"
}
```

`forget` and `stop_using` retire the item rather than erasing its evidence or
audit trail.

### Usage and outcomes

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/learning/usages` | Usage ledger; optional `item_id`, `pending`, `limit` |
| POST | `/api/learning/usages/{usage_id}/outcome` | Resolve a real use and update reputation/lifecycle |
| POST | `/api/decisions/{decision_id}/outcome` | Resolve a decision, create an Experience, and resolve learned influences |

```jsonc
POST /api/learning/usages/{usage_id}/outcome
{
  "verdict": "SUPPORTED", // SUPPORTED | CONTRADICTED | NEUTRAL | INSUFFICIENT EVIDENCE
  "detail": "Logs exposed the timeout before another deployment",
  "evidence": ["incident report ATLAS-41"],
  "user_id": "demo-user"
}
```

`SUPPORTED` or `CONTRADICTED` with no evidence is downgraded to
`INSUFFICIENT EVIDENCE`; reputation does not move.

### Learning maintenance

`POST /api/learning/consolidate` retains the existing policy mining result and
adds `experience_learning`. Maintenance may create and validate Skill/Principle
candidates but reports `promoted: 0`; promotion is never automatic.

`GET /api/learning` adds `experience_count` and aggregate `knowledge` lifecycle
statistics.

---

## Explanation Engine (V8.4.2)

Auditable reasoning graphs assembled from real persisted state and canonical events.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/explanations` | List persisted explanation snapshots (optional `subject_kind`, `subject_id`, `limit`) |
| GET | `/api/explanations/{id}` | Retrieve a persisted explanation snapshot by id |
| GET | `/api/explanations/subject/{subject_kind}/{subject_id}` | Generate or retrieve an explanation graph for a subject (`intent`: `why`, `why_not`, `why_now`, `what_changed`, `what_evidence`, `what_alternatives`, `what_caused_change`) |
| GET | `/api/explanations/decision/{decision_id}` | Explain decision influence, alternatives, and outcomes |
| GET | `/api/explanations/event/{event_id}` | Explain triggers and correlation behind a canonical event |
| POST | `/api/explanations/query` | Ad-hoc explanation query with specific intent, depth, and subject |

```jsonc
POST /api/explanations/query
{
  "subject_kind": "skill",
  "subject_id": "sk_12345",
  "query_intent": "what_changed",
  "depth": 2,
  "persist": true,
  "user_id": "demo-user"
}
```
