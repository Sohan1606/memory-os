# MEMORY//OS

**MEMORY//OS V8.3.1 — Conversational Cognition.**

A continuously maintained personal cognitive environment, operated through
conversation. It remembers, and it can tell you *why* it believed something,
*what that belief changed*, *whether it turned out to be right* — and what it
has been quietly keeping track of since you last spoke.

As of V8.3.1 you reach all of that by simply talking. Ask "what am I working
on?" and the mission registry answers; say "pause that" and the right mission
pauses. You never need to know an endpoint, a table or a panel name.
A local-first agent with persistent long-term memory — LangGraph, LangChain
tools, ChromaDB and local embeddings — presented through a cinematic dark
product interface.

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

## New in V8.3.1

The cognitive subsystems became **first-class participants in conversation**.
Fifteen cognitive tools (missions, world state, predictions, attention,
continuity, history, simulation, explanation) call the real subsystems, and the
model — not a keyword table — decides when to use them. Missions are now
first-class context, `"that"` and `"it"` resolve to real objects across turns,
and the assistant distinguishes what is **recorded** from what it is merely
**proposing**: a mission with no next step is reported as having none, never
given an invented one.

Full detail: [`docs/V8.3.1.md`](docs/V8.3.1.md) · audit:
[`docs/V8.3.1-AUDIT.md`](docs/V8.3.1-AUDIT.md).

## New in V8.3

Long-running **missions** that span conversations · a **world model with
history**, per-fact staleness and evidence-based reconciliation · **bounded,
cancellable background cognition** that records empty cycles honestly · a
**canonical observation log** kept separate from memory · **attention V2** with
learned silence · a **time machine** over real recorded history · a strictly
isolated **counterfactual sandbox**.

Full detail: [`docs/V8.3.md`](docs/V8.3.md). The pre-work audit that shaped it:
[`docs/V8.3-AUDIT.md`](docs/V8.3-AUDIT.md).

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

- Backend tests: **412 passed, 10 skipped** (V8.1 baseline of 179/8 fully preserved)
- `npm run lint` — clean · `npx tsc --noEmit` — clean · `npm run build` — 6/6 pages
- Browser QA: desktop 1440×900 + mobile 390×844, 5 pages each, zero console errors
- Restart persistence: memories, influences, arbitrations, intent transitions,
  execution traces and focus all survive a backend restart
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
| [`docs/V8.2.md`](docs/V8.2.md) | V8.1 → V8.2: the cognitive core — routing, execution tracing, the causal chain |
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


## v8.2 — Cognitive Core

V8.2 does not add features on top of V8.1; it makes the cognition V8.1
*described* actually run, be recorded, and be inspectable — and it makes the
system refuse to claim anything it has not observed.

**What v8.2 adds**

- **Real capability detection and routing.** Five capabilities resolved from the
  provider and a known-model table, each with the reason for its state. `UNKNOWN`
  never satisfies a requirement — an unrecognised model is not assumed to work.
  Vision with no vision model is `NOT_CONFIGURED`, not quietly downgraded.
- **A genuinely model-driven agent loop**, traced through eleven stages with a
  depth cap, timeout, duplicate-call detection, tool-failure recovery and
  cancellation. When it stops early it says why instead of returning nothing.
- **One canonical context bundle** per turn — bounded, relevance-ranked, and
  carrying source, confidence and permission for every item. Truncation is
  declared; unconnected sources are declared and contribute nothing.
- **Memory arbitration V2** — nine weighted factors, persisted records,
  contradicted and quarantined memories excluded and reported.
- **The causal chain: memory → influence → outcome → reputation.** Retrieval is
  *not* influence. An outcome with no evidence is downgraded to
  `INSUFFICIENT EVIDENCE` and reputation does not move.
- **Prediction → reality → learning.** A prediction is scored only when reality
  actually resolved it; error, surprise and a learning signal are persisted.
- **Evidence-based regret.** A bad outcome with no recorded expectation and no
  cited evidence is `INSUFFICIENT EVIDENCE`, not a number.
- **Intent evolution V2** — probabilistic, with uncertainty and stated reasons.
  A question never creates an intent fact; an unconfirmed second goal is
  `emerging` and does not displace the one you confirmed.
- **Autonomy governor V2** — ACT / ASK / WAIT / DO_NOTHING / BLOCKED.
- **User-controlled cognition in plain language** — "forget that", "that's
  wrong", "why do you believe that?". Strict phrasing only, and it refuses to
  guess which memory you meant rather than deleting the wrong one.
- **Per-capability trust** that reads `INSUFFICIENT EVIDENCE` with no number
  attached until there is real evidence.

Full detail, including the bugs these tests caught and what is still
NOT CONFIGURED: [`docs/V8.2.md`](docs/V8.2.md).
