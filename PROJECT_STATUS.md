# PROJECT STATUS

Every status below was produced by a command that was actually executed. Nothing
is aspirational. Where a capability is absent it is marked
`OPTIONAL / NOT CONFIGURED` rather than given a false PASS.

Verified on Linux, Node v20.20.2 / npm 10.8.2, Python 3.13.14.

## Build and test matrix

| Item | Status | Evidence |
|---|---|---|
| Backend test suite | **PASS** | `python -m pytest` → **139 tests, 0 failures, 0 errors, 1 skipped** (v7 74 + v8 65) |
| Clean install from ZIP | **PASS** | extracted to a fresh dir: `npm ci` (330 packages), lint, typecheck, build, fresh venv + pytest, browser QA — all from the extracted copy |
| Frontend lint | **PASS** | `npm run lint` → `✔ No ESLint warnings or errors` |
| Frontend typecheck | **PASS** | `npm run typecheck` → clean (strict + `noUnusedLocals`/`noUnusedParameters`) |
| Frontend production build | **PASS** | `npm run build` → `✓ Compiled successfully`, 8/8 static pages (adds `/observatory`) |
| Backend over real HTTP | **PASS** | uvicorn on `0.0.0.0:8000`; health, chat, search, CRUD exercised with curl |
| Frontend ↔ backend integration | **PASS** | `/api/*` rewrite verified end to end from the browser |
| Browser QA — v7 (Playwright) | **PASS** | `tests/browser_qa.py` → 14/14, **0 console errors** |
| Browser QA — v8 Observatory | **PASS** | `tests/observatory_qa.py` → **25/25**, 0 console errors |
| Mobile layout (390 px) | **PASS** | 0 px horizontal overflow on landing and workspace |
| Reduced motion | **PASS** | full content renders; sequence pins to a static frame |
| No remote runtime assets | **PASS** | system fonts, locally generated frames, no CDN |

## Feature matrix

| Feature | Status | Notes |
|---|---|---|
| LangGraph `StateGraph` | **PASS** | compiled graph, `append_activity` reducer |
| LangGraph checkpointing | **PASS** | `SqliteSaver`; health reports `checkpointer: sqlite` |
| Thread isolation | **PASS** | tested |
| User namespace isolation | **PASS** | tested |
| LangChain structured tools (5) | **PASS** | search / save / update / delete / consolidate |
| Agentic tool-calling loop | **PASS (requires a model)** | `bind_tools` + `ToolNode`; demo mode has no LLM → deterministic planner, labelled honestly |
| ChromaDB persistence | **PASS** | `PersistentClient`, cosine, survives restart |
| Local embeddings | **PASS** | `all-MiniLM-L6-v2` ONNX, **384-dim**, verified |
| Semantic retrieval | **PASS** | health reports `vector.mode: semantic` |
| Keyword fallback labelling | **PASS** | reports `keyword` when embeddings are unavailable |
| Hybrid ranking + match reasons | **PASS** | 6 weighted terms, per-result reasons |
| `NO_STRONG_MATCH` honesty | **PASS** | junk query returns no results |
| Policy-based extraction | **PASS** | questions are never stored |
| Duplicate detection / reinforcement | **PASS** | ≥ 0.80 → reinforce |
| Conflict detection + versioning | **PASS** | dark→light mode → v2 + `MEMORY_SUPERSEDED` |
| Consolidation | **PASS** | merge + relationships + sources de-indexed |
| Audit events (7 types) | **PASS** | append-only log |
| Delete consistency | **PASS** | SQLite + Chroma, no dangling edges |
| Voice input | **PASS (browser)** | Web Speech API → same pipeline |
| Scroll-linked canvas sequence | **PASS** | 60 WebP frames, canvas only, used once |
| Frontend from real APIs | **PASS** | no hardcoded datasets |
| **LangMem** | **OPTIONAL / NOT CONFIGURED** | adapter implemented + tested; reports `NOT INSTALLED`. Requires the package **and** a tool-calling model |
| **faster-whisper** | **OPTIONAL / NOT CONFIGURED** | not installed; health reports `voice.mode: browser` |
| **Ollama provider** | **OPTIONAL / NOT CONFIGURED** | `langchain-ollama` not installed |
| **OpenAI provider** | **OPTIONAL / NOT CONFIGURED** | `langchain-openai` not installed; paid, excluded from the zero-cost demo |

## Verified runtime observations

```
GET /api/health
  agent.framework   langgraph        agent.checkpointer  sqlite
  vector.backend    chromadb         vector.mode         semantic
  embeddings        all-MiniLM-L6-v2 (local ONNX), 384-dim
  provider          demo (tool_calling: false)
  voice.mode        browser
  langmem.state     NOT INSTALLED
```

- `"what do you remember about my projects"` → 4 PROJECT memories, 0.45 / 0.41 / 0.40 / 0.31
- `"how should you explain things to me"` → COMMUNICATION_STYLE, 0.57 / 0.52
- `"zzz quantum llama parade"` → `NO_STRONG_MATCH`, 0 results
- Same statement twice → `reinforced` (no duplicate row)
- `"I have switched to light mode."` → `updated`, version 2, `MEMORY_SUPERSEDED`
- Statement in `thread-alpha` recalled in `thread-beta` (long-term crosses threads)

## Known limitations

1. **Demo replies are deterministic, not generative.** No LLM ships with the
   demo. Memory, retrieval and versioning are real; free-form language is not.
2. **The agentic loop needs a model to be observable.** The `bind_tools`/`ToolNode`
   loop is implemented and runs, but only a real tool-calling provider makes the
   model's own tool choices visible. See `docs/SETUP.md` for the free Ollama path.
3. **LangMem is not active.** Implemented, tested and honestly reported — not running.
4. **No authentication.** Single demo user namespace.
5. **Single-node storage.** SQLite + local Chroma.
6. **Voice depends on the browser.** Firefox lacks SpeechRecognition; server-side
   Whisper is not installed.
7. **Consolidation is manual**, triggered from the memory explorer.
8. **Voice capture is not automated in QA** — it needs a real microphone and a
   user gesture; it was exercised manually.
9. **First run needs network** to fetch the ~80 MB embedding model. Everything
   afterwards is offline.

## v8 cognitive layer

| Capability | Status | Evidence |
|---|---|---|
| Cognitive event bus | **ACTIVE** | 69 validated types; unknown types raise `ValueError` (test-enforced) |
| World model | **ACTIVE** | Entities created from real utterances; questions create none |
| Intent + need engine | **ACTIVE** | 11 need classes; superseded intents become `historical` |
| Memory reputation + lifecycle | **ACTIVE** | Earned from recorded outcomes; starts `INSUFFICIENT EVIDENCE` |
| Memory arbitration | **ACTIVE** | Structured winner + why the alternatives lost |
| Prediction + surprise | **ACTIVE** | Brier calibration; confident-and-wrong emits `surprise.detected` |
| Causal graph + decisions | **ACTIVE** | Bidirectional traversal; regret computed, never invented |
| Autonomy governor | **ACTIVE** | Irreversible work always asks; failures withdraw authority |
| Attention engine | **ACTIVE** | Can conclude DO NOTHING; suppressed interventions logged |
| Counterfactual sandbox | **ACTIVE** | World state asserted unchanged before/after (test + browser QA) |
| Learning + self-evaluation | **ACTIVE** | Evidence-gated promotion; reversible policies |
| Self model | **ACTIVE** | Reports its own limitations |
| Calendar / email / files | **NOT CONNECTED** | No connector code exists |
| Actions outside memory store | **NOT CONFIGURED** | Not implemented |
| ROI (time saved / cost avoided) | **INSUFFICIENT EVIDENCE** | Not measurable here; never estimated |
| Model tool calling | **DEGRADED** | No tool-calling model configured; deterministic planner active |
| LangMem / Whisper | **NOT CONFIGURED** | Not installed; honest fallbacks active |
