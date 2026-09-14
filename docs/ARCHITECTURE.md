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
