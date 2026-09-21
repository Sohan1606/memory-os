# MEMORY//OS — Architecture

## 1. Overview

MEMORY//OS is a local-first AI agent with persistent long-term memory. It has two
processes:

```
Browser  ──►  Next.js (port 3000)  ──rewrite /api/*──►  FastAPI (port 8000)
                                                              │
                        ┌─────────────────────────────────────┼──────────────────┐
                        │                                     │                  │
                  LangGraph agent                      MemoryService        Providers
              (StateGraph + checkpointer)                     │           demo/ollama/openai
                        │                            ┌────────┴────────┐
                  LangChain tools                ChromaDB           SQLite
          search/save/update/delete/consolidate  (vectors,        (metadata, versions,
                                                  MiniLM-L6-v2)    relationships, events)
```

The browser never talks to port 8000 directly. `next.config.mjs` rewrites
`/api/*` to `BACKEND_URL`, so the frontend is same-origin and no CORS or
hardcoded localhost URL is shipped to the client.

## 2. Request lifecycle

A chat turn (`POST /api/chat`):

1. **load_context** — loads the thread's checkpointed messages and pre-searches
   long-term memory for the user's utterance.
2. **agent** — when a tool-calling model is configured, the model is given the
   five memory tools via `bind_tools` and *decides for itself* whether to call
   any. With the demo provider there is no LLM, so a deterministic planner
   produces the reply instead. Both paths are labelled truthfully in the
   activity trail (`MODEL_CALL` vs `DEMO_PLANNER`).
3. **tools** — `ToolNode` executes whichever memory tool the model requested and
   loops back to the agent. This can repeat.
4. **memory_manager** — post-turn extraction. Decides whether the user said
   something durable enough to store, and writes it.

Graph shape:

```
START → load_context → agent ⇄ tools → memory_manager → END
```

`activity` uses an `append_activity` reducer so events from every node survive
the run instead of the last node's write clobbering the rest.

## 3. Memory model

### Short-term
LangGraph checkpointing, scoped by `thread_id`, persisted with `SqliteSaver`
(falls back to `InMemorySaver` and reports `checkpointer: memory` if SQLite is
unavailable). Two threads never see each other's conversation state.

### Long-term
Scoped by `user_id`. Split across two stores that are always written together:

| Store    | Holds                                                          |
|----------|----------------------------------------------------------------|
| ChromaDB | 384-dim embeddings from `all-MiniLM-L6-v2` (local ONNX runtime) |
| SQLite   | `memories`, `memory_versions`, `memory_relationships`, `memory_events`, `conversations`, `messages` |

Deleting a memory removes it from both, and no dangling relationship rows are
left behind.

## 4. Memory lifecycle

1. **Extraction** — the deterministic policy engine classifies an utterance as
   durable or transient. Questions are never stored. `remember` only counts as a
   leading imperative.
2. **Duplicate detection** — cosine similarity ≥ 0.80 against an existing memory
   reinforces it (bumping confidence) instead of creating a near-copy.
3. **Conflict detection** — similarity ≥ 0.45, or ≥ 0.30 combined with an
   explicit contradiction signal, triggers a versioned update: the old content
   is written to `memory_versions`, the row's `version` increments, the vector is
   re-embedded, and a `MEMORY_SUPERSEDED` event is recorded.
4. **Consolidation** — several related memories can be merged into one, with
   `MEMORY_CONSOLIDATED` events and relationship rows linking the sources.
5. **Audit** — every mutation appends to `memory_events`:
   `MEMORY_CREATED`, `MEMORY_REINFORCED`, `MEMORY_RETRIEVED`, `MEMORY_UPDATED`,
   `MEMORY_CONSOLIDATED`, `MEMORY_SUPERSEDED`, `MEMORY_DELETED`.

## 5. Retrieval and ranking

Chroma returns cosine *distance*; similarity is `1 - distance`. (An earlier
`1 - distance/2` conversion squashed the range and made junk queries look like
strong matches — fixed and covered by tests.)

Final score is a weighted blend:

| Term            | Weight |
|-----------------|--------|
| semantic        | 0.45   |
| category intent | 0.20   |
| keyword overlap | 0.15   |
| importance      | 0.08   |
| recency         | 0.08   |
| confidence      | 0.04   |

Thresholds: `min_semantic` 0.18, strong 0.40, weak 0.20.

The category-intent term is what makes "what do you remember about my projects?"
return PROJECT memories rather than high-importance IDENTITY memories — solved
by modelling intent explicitly rather than by lowering thresholds.

Responses carry a `state` of `STRONG`, `WEAK` or `NO_STRONG_MATCH`, a `mode` of
`semantic` or `keyword` (honest fallback labelling if embeddings fail to load),
per-result `reasons`, and a `path` describing the retrieval steps.

## 6. Provider abstraction

| Provider | Default | Requires                                   | Tool calling |
|----------|---------|--------------------------------------------|--------------|
| `demo`   | yes     | nothing                                    | no (deterministic planner) |
| `ollama` | no      | `langchain-ollama` + running Ollama (free) | yes |
| `openai` | no      | `langchain-openai` + API key (paid)        | yes |

Each reports `available` and a human-readable `detail`. A requested-but-missing
provider degrades to demo and says so; it never silently pretends.

## 7. Optional components

- **LangMem** — `app/memory/langmem_adapter.py`. Reports `NOT INSTALLED`,
  `NOT CONFIGURED` or `ACTIVE`. It needs both the package *and* a tool-calling
  model, so it is inert in demo mode and the policy engine remains the extractor.
  The adapter returns an empty list when inert; it never fabricates output.
- **faster-whisper** — server-side transcription. Absent by default, so
  `/api/voice/status` reports `browser` and the UI uses the Web Speech API.

## 8. Frontend

Next.js App Router, React 19, TypeScript in strict mode with
`noUnusedLocals`/`noUnusedParameters`.

- `hooks/useMemoryStore.tsx` is the single source of truth. Every mutation
  refreshes memories, graph, timeline and health, so no two panels can disagree.
- `components/ScrollSequence.tsx` is the signature scroll-linked canvas image
  sequence: 60 WebP frames drawn to an `HTMLCanvasElement` (never `<img>`),
  preloaded with a progress bar, sticky over ~400vh, `requestAnimationFrame`
  redraw only when the frame index actually changes, a reduced subset below
  768px, and a single static frame under `prefers-reduced-motion`. It is used
  exactly once in the product.
- No remote assets: system font stack, no Google Fonts, no image CDN. Frames are
  generated locally by `scripts/generate-frames.mjs`.

---

## V8.2 additions

### New modules

| Module | Responsibility |
|---|---|
| `providers/capabilities.py` | Capability detection + `CapabilityRouter` (task → execution mode) |
| `agent/execution.py` | Trace stages, `ExecutionTrace`, `TraceRecorder`, `Cancellation`, `run_tool_safely` |
| `cognition/context_builder.py` | The single canonical context bundle per turn |
| `cognition/arbitration.py` | `ArbiterV2` + persisted arbitration records |
| `cognition/influence.py` | The memory → influence → outcome → reputation ledger |
| `cognition/continuity.py` | Open threads worth returning to |
| `cognition/intent_v2.py` | Probabilistic intent evolution + need detection V2 |
| `cognition/policy_engine.py` | Eight learned behavioural dimensions, with evidence |
| `cognition/trust_v2.py` | Per capability × task-class reliability |
| `cognition/focus.py` | Object permanence via stable IDs |
| `cognition/user_control.py` | Natural-language cognitive commands |

### Composition and the circular-dependency resolution

`Runtime` is the composition root and builds in a deliberate order, because the
agent needs cognition's recorder and cognition introspects the runtime:

```
1. Database, MemoryService, Provider
2. MemoryAgent            (no cognition references yet)
3. Cognition              (introspects the runtime, builds all subsystems)
4. agent.recorder / context_builder / router / policy_engine  ← assigned post-hoc
```

This keeps `agent` and `cognition` free of a circular import while still giving
the agent loop the real tracer and the real context builder.

### Turn lifecycle in V8.2

```
POST /api/chat
  │  correlation_id minted once, shared by both paths below
  ├─▶ cognition.process_turn()
  │     route → policy.apply_utterance → focus.resolve → control.handle
  │     → needs.detect → intent_evolution.observe → retrieve → arbitrate
  │     → influence (winner only) → continuity → context.build → trust.record
  └─▶ agent.run()
        load_context → [MODEL_CALL ⇄ TOOL_DECISION → TOOL_RESULT
                        → MODEL_REVISION]* → FINAL_RESPONSE
        bounded by depth cap, timeout, duplicate detection, cancellation
```

Both halves write to the same event bus under the same `correlation_id`, so
`/api/cognition/turn/{id}` and `/api/execution/{id}` describe one turn.

### Schema evolution

`Database._migrate()` runs on construction and applies the `MIGRATIONS` tuple.
Every migration is an additive `ALTER TABLE ADD COLUMN` guarded by
`PRAGMA table_info`, so an existing V8.1 database upgrades in place with no data
loss and no destructive change.

---

## V8.3 — Continuous Cognition

```
                    ┌──────────────── conversation (primary surface)
                    │
   perception ──► understanding ──► intent / need ──► context ──► memory
                                                          │
                          ┌───────────────────────────────┤
                          ▼                               ▼
                   world state V2                    missions
                   · change history                  · bounded steps
                   · staleness                       · transition log
                   · reconciliation                  · resume brief
                          │                               │
                          └───────────────┬───────────────┘
                                          ▼
                                    observations
                                 (canonical evidence)
                                          │
              ┌───────────────┬───────────┼───────────┬──────────────┐
              ▼               ▼           ▼           ▼              ▼
         prediction      attention V2  simulation  time machine  background
         · windows       · ladder      · isolated  · real        · bounded
         · UNRESOLVED    · silence     · SIMULATED   history     · cancellable
                                                                 · honest
```

**Composition root.** All eleven V8.3 subsystems are constructed in
`cognition/orchestrator.Cognition.__init__` alongside the V8.2 ones, and reached
as `runtime.cognition.<name>`. They take the existing `db` and `bus`, so there
is exactly one event log and one database.

**Schema separation.** V8.3 DDL lives in `SCHEMA_V83`, executed after the
untouched V8/V8.1 `SCHEMA`. Column additions go through the existing additive
`MIGRATIONS` mechanism, guarded by `PRAGMA table_info`, so an existing database
upgrades in place without data loss.

**Surface follows the thought (§33).** Conversation stays primary. The
Observatory gains three panels — Missions, Continuous state, Background
cognition — and they are strictly inspection surfaces over real recorded data.
Nothing in the UI holds parallel state, and nothing there fabricates activity:
the Background panel shows empty cycles as empty.


---

## V8.4.1 — evidence-backed abstractions

V8.4.1 adds an abstraction layer without adding a parallel memory, event,
reputation, arbitration or causal system.

```text
canonical ObservationLog
          │ owned evidence
          ▼
      ExperienceStore ── experience_transitions
          │ pattern + observed action/outcome
          ▼
      KnowledgeService ── knowledge_items / evidence / validations
          │                     │
          ├── ReputationStore ◄─┤ observed usage outcomes
          ├── ArbiterV2 ◄───────┤ retrieval candidates
          ├── CausalGraph ◄─────┤ provenance / influence / outcome edges
          └── EventBus ◄────────┘ canonical lifecycle events
                    │
                    ▼
           ContextBuilder → agent / DecisionLog
                    │
                    ▼
           knowledge_usages → outcome → refinement
```

### Composition

`Cognition.__init__` builds V8.4.1 in dependency order:

```text
observations + canonical causal graph
    → ExperienceStore
    → existing ReputationStore + ArbiterV2
    → KnowledgeService
    → existing BackgroundCognition / ContextBuilder / agent collaborators
```

`DecisionLog` receives Experience and Knowledge collaborators after construction
to preserve the existing composition shape and avoid circular imports.

### Storage boundary

`SCHEMA_V841` is executed after the existing V8.3 schema. It creates nine
additive tables for episodes, evidence links, validation, reputation, usage and
transitions. Existing tables and migrations are unchanged. Every ownership-
sensitive table carries `user_id`; every service query and evidence-link check
uses it.

Foreign-key-like links to memories, observations, Skills, Principles and causal
nodes intentionally use stable text ids because several canonical sources live
in separate tables. Ownership and type are verified in service code before a
link is written.

### Lifecycle and promotion boundary

Candidate creation, validation and promotion are separate operations. The
background task calls only creation and deterministic validation. `promote()`
requires a persisted latest PASS and is never called from background cognition.
This boundary prevents frequency, an LLM proposal, or an unattended cycle from
creating trusted guidance.

### Retrieval boundary

`KnowledgeService.retrieve()` first restricts by user and live lifecycle. It
then computes explicit precondition/current-world checks. `ArbiterV2` applies
scope as a hard gate and scores the eligible set. The same persisted arbitration
ledger used for memories records the winner, blocked candidates and factors.

A winner is not an influence until it enters bounded turn context or is
explicitly attached to a decision. Only then does `record_use()` create a usage
row and causal edge.

### Conversation boundary

The four V8.4.1 cognitive tools are ordinary LangChain structured tools added at
the existing `_tools_for()` composition point. On the genuine model path the
model selects them. The deterministic demo planner is still separately labelled
and no learned-object keyword router was introduced.

### Frontend boundary

`ExperienceSkillPrinciplePanel` owns no learning state. It loads typed API
responses, filters them for display, and sends selected stable ids to the
existing focus API. Confidence and reputation are rendered from separate
fields. Missing data is displayed as insufficient evidence rather than filled
with client defaults.

Full lifecycle, thresholds, correction semantics and limitations are documented
in [V8.4.1.md](V8.4.1.md).

## V8.4.3 — Connected Research + External World Intelligence

V8.4.3 adds a real external-fetch capability without adding a parallel
event bus, explanation engine, or world-state writer. The V8.3
`ResearchMode` / `ConnectorRegistry` "NOT CONFIGURED" state machine in
`connectors.py` is untouched; this is an additive, always-available
capability that answers a different question — "fetch this real URL and
tell me honestly what you found."

```text
 explicit URL (user or tool call, never invented)
          │
          ▼
   net_security.fetch()  — SSRF defense (pre-DNS + post-DNS + per-redirect),
          │                 connection pinning, timeouts, byte/redirect limits
          ▼
   content_safety.scan() — flags prompt-injection patterns, never executes them
          │
          ▼
   ResearchEngine (app/cognition/research.py)
          │
          ├── research_sources / research_fetches  (audit ledger — failures included)
          ├── research_evidence                    (deterministic excerpts)
          ├── research_claims / research_conflicts (capped confidence, both sides kept)
          └── research_world_updates ──► WorldStateV2.reconcile()  (explicit confirm=True only)
                                              │
                                              ▼
                                   existing World Model (tagged source="research")
```

### Composition

`Cognition.__init__` constructs `self.research_engine = ResearchEngine(db,
self.bus, self.world_v2)` alongside (not instead of) the existing
`self.research = ResearchMode(...)`. The engine receives the *same*
`EventBus` and `WorldStateV2` instances used by every other subsystem — no
second bus, no second world-state writer.

### Storage boundary

`SCHEMA_V843` is executed after `SCHEMA_V842`. It creates seven additive
tables (`research_sessions_v2` — deliberately distinct from the existing
V8.3 `research_sessions` — plus `research_sources`, `research_fetches`,
`research_evidence`, `research_claims`, `research_conflicts`,
`research_world_updates`). A dedicated migration test
(`tests/test_v843_migration.py`) proves a real pre-V8.4.3 database survives
this addition with all existing rows intact.

### Security boundary

Every fetch — with no exception — goes through
`app/cognition/net_security.py`. Structural validation happens before any
DNS resolution; IP-class validation happens after DNS resolution; the
actual TCP connection is pinned to the exact validated IP (defeating DNS
rebinding); and every redirect hop repeats the full validation from
scratch. This is the single choke point for all outbound research traffic
— `ResearchEngine` never opens a socket itself.

### Confidence boundary

`research_evidence` and `research_claims` intentionatlly keep
`source_quality`, `evidence_strength`, `freshness`, `corroboration_count`,
`independent_domain_count` and `claim_confidence` as separate fields.
`claim_confidence` is capped (`SINGLE_SOURCE_CONFIDENCE_CAP=0.55`,
`CORROBORATED_CONFIDENCE_CAP=0.80`) and a further, lower
`WORLD_UPDATE_CONFIDENCE_CAP=0.60` bounds anything reaching the World
Model — evidence is never silently promoted to certainty.

### World Model boundary

`propose_world_update()` only ever creates a `PROPOSED` row; the World
Model itself is unmodified until `apply_world_update(..., confirm=True)`
is called explicitly, which then calls the *existing*
`WorldStateV2.reconcile()` — the identical verdict machinery
(`keep`/`supersede`/`merge`/`flag`/`downgrade`/`ignore`) every other
subsystem already uses. A double-apply raises `ValueError` rather than
silently no-opping.

### Conversation boundary

Six new cognitive tools are ordinary `StructuredTool.from_function`
registrations at the existing `build_cognitive_tools()` composition point,
resolved through the same conversational-focus mechanism as `missions`.
No keyword-only router was introduced — the model decides when to call
`start_research`/`fetch_research_source` based on the system prompt's
"CONNECTED RESEARCH" guidance.

### Explanation boundary

`ExplanationEngine` gained `RESEARCH_EVIDENCE`/`RESEARCH_CONFLICT` classes
and `explain_research_*` methods dispatched from its existing single
`explain()` entrypoint — not a second explanation system.

### Frontend boundary

`ResearchPanel.tsx` owns no research state of its own; it loads typed API
responses from `/api/research/v2/*` and renders sources, evidence, claims,
conflicts and world updates in four visually distinct registers so a raw
fetched page is never confused with a claim, and a claim is never confused
with an applied World Model fact.

Full lifecycle, data model, security details and known limitations are
documented in [V8.4.3.md](V8.4.3.md).
