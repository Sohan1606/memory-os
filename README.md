# MEMORY//OS

**An AI assistant that actually remembers.** A local-first agent with persistent
long-term memory — LangGraph, LangChain tools, ChromaDB and local embeddings —
presented through a cinematic dark product interface.

Runs with **zero API keys and zero paid services.**

```
frontend/   Next.js App Router · React 19 · TypeScript (strict)
backend/    FastAPI · LangGraph · LangChain · ChromaDB · SQLite
docs/       Architecture, memory design, API, setup, testing, design notes
```

## Quick start

```bash
# Terminal 1
cd backend && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8000

# Terminal 2
cd frontend && npm ci && npm run dev
```

Open <http://localhost:3000>. Windows instructions: [`docs/SETUP.md`](docs/SETUP.md).

First run downloads the ~80 MB MiniLM ONNX embedding model once and caches it.

## What is real

Every claim below is exercised by an automated test or verified over HTTP.

| Capability | Status | Evidence |
|---|---|---|
| LangGraph `StateGraph` agent with checkpointing | **PASS** | `START → load_context → agent ⇄ tools → memory_manager → END`, compiled and run in `test_langgraph.py` |
| LangChain structured tools | **PASS** | `search_memory`, `save_memory`, `update_memory`, `delete_memory`, `consolidate_memory` — executed in `test_tools.py` |
| Agentic tool-calling loop | **PASS (needs a model)** | `bind_tools` + `ToolNode` loop; the model chooses. Demo mode has no LLM and uses a deterministic planner, labelled as such |
| ChromaDB persistent vectors | **PASS** | survives restart, `test_vector_store.py` |
| Local embeddings | **PASS** | `all-MiniLM-L6-v2` ONNX, 384-dim, no network at query time |
| Semantic retrieval + hybrid ranking | **PASS** | explainable reasons, honest `semantic` / `keyword` labelling |
| SQLite metadata, versions, relationships, audit | **PASS** | six tables, WAL |
| Short-term memory (thread-scoped) | **PASS** | `SqliteSaver`, `test_checkpoint.py`, `test_thread_isolation.py` |
| Long-term memory (user-scoped) | **PASS** | `test_user_namespace.py` |
| Duplicate detection / reinforcement | **PASS** | ≥ 0.80 similarity reinforces |
| Conflict detection + versioned update | **PASS** | supersede → v2 + `MEMORY_SUPERSEDED` |
| Consolidation | **PASS** | `test_consolidation.py` |
| Audit events | **PASS** | 7 event types, `test_events.py` |
| Voice input | **PASS (browser)** | Web Speech API into the same pipeline |
| Scroll-linked canvas sequence | **PASS** | 60 local WebP frames on canvas, used once |
| **LangMem** | **OPTIONAL / NOT CONFIGURED** | Adapter present and tested; needs the package *and* a tool-calling model. Never mislabelled |
| **faster-whisper** | **OPTIONAL / NOT CONFIGURED** | Not installed; health reports `browser` |
| **Local LLM agent (Ollama)** | **ACTIVE WHEN INSTALLED** | Real multi-step tool calling. Auto-detected; see `docs/LOCAL_LLM.md` |
| **Model-assisted extraction** | **ACTIVE WITH A MODEL** | Schema-validated; deterministic engine always runs underneath |
| **Memory health engine** | **ACTIVE** | Evidence-based findings, non-destructive remedies |
| **Multimodal perception** | **PARTIAL** | Text/documents read; images METADATA ONLY; video NOT CONFIGURED |
| **OpenAI provider** | **OPTIONAL / NOT CONFIGURED** | Abstraction present; no key required or bundled |

Default demo mode is `MODEL_PROVIDER=demo`: no LLM, so replies come from a
deterministic planner. **Memory, embeddings, retrieval, versioning and the audit
log are fully real in demo mode** — only free-form model tool choice is absent.

## Verified results

- Backend tests: **74 passed, 1 skipped** (the skip is the optional LangMem case)
- `npm run lint` — clean · `npm run typecheck` — clean · `npm run build` — 7/7 pages
- Browser QA: 14/14 Playwright checks, zero console errors
- `"what do you remember about my projects"` → 4 PROJECT memories (0.45/0.41/0.40/0.31)
- Junk query → `NO_STRONG_MATCH` (it declines rather than inventing a match)
- `"I have switched to light mode."` → existing dark-mode memory updated to v2

## The interface

- **Landing** — the problem, the signature scroll sequence, live memory layers,
  an interactive retrieval demo, voice capture, product surfaces
- **/memory** — the full live store, network graph, a real conflict-resolution
  run, and the audit timeline
- **/architecture** — seven layers, annotated with live health
- **/workspace** — talk to the agent, switch threads, watch memory form

Every number, node and score is fetched from the backend. There are no
hardcoded datasets and no fake buttons.

## Documentation

| Document | Contents |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System shape, request lifecycle, graph, providers |
| [`docs/MEMORY_DESIGN.md`](docs/MEMORY_DESIGN.md) | Schema, lifecycle, duplicate vs conflict, isolation |
| [`docs/API.md`](docs/API.md) | Every HTTP route with payloads |
| [`docs/SETUP.md`](docs/SETUP.md) | Windows + Unix setup, config, troubleshooting |
| [`docs/LOCAL_LLM.md`](docs/LOCAL_LLM.md) | Ollama install, model choice, health check, troubleshooting |
| [`docs/V8.1.md`](docs/V8.1.md) | V8 → V8.1: real agent, extraction, perception, memory health |
| [`docs/TESTING.md`](docs/TESTING.md) | Test coverage, verified results, bugs caught |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Visual language, motion, the scroll sequence |
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | Honest status matrix and limitations |

## Limitations

- Single-user demo namespace by default; there is no authentication
- Demo provider is deterministic, not generative
- SQLite and local Chroma suit a single node, not horizontal scale
- Voice depends on browser support (Chromium/Safari; Firefox lacks SpeechRecognition)
- Consolidation is user-triggered, not automatic

## v8 — Causal Cognitive layer

On top of the v7 memory system, MEMORY//OS now understands, records and explains
its own reasoning. Visit **/observatory** for the unified inspection environment.

**What v8 adds**

- A canonical **cognitive event bus** (69 validated event types) that is the
  single source of truth for Activity, Timeline, Why, replay and history.
- A **living world model** built only from what you actually said — questions
  never create entities.
- **Probabilistic intent + need detection** with intent evolution; a superseded
  intent becomes `historical` rather than being deleted.
- **Memory biology**: candidate → validating → trusted → reinforced, advancing on
  evidence only, plus **reputation** that is strictly distinct from confidence.
- **Memory arbitration** that resolves competing memories and explains why the
  others lost.
- **Predictions** with Brier calibration and automatic **surprise detection**.
- A **causal graph** linking memory → decision → outcome, traversable both ways.
- An **autonomy governor** where authority is separate from confidence:
  irreversible actions always ask, and demonstrated failures withdraw authority.
- An **attention engine** that can conclude *do nothing* — and logs the
  interventions it suppressed.
- A **counterfactual sandbox** that never mutates real state.
- A **self model** that reports its own limitations honestly.

**Honesty contract.** This layer never invents a number. Where something has not
been measured it returns `INSUFFICIENT EVIDENCE`, `NOT CONFIGURED`,
`NOT CONNECTED` or `DEGRADED`. See `docs/COGNITION.md`, including the explicit
*"What is NOT implemented"* section.

