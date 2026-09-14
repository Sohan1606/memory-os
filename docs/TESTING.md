# Testing

## Backend

```bash
cd backend
python -m pytest -q
```

**Last verified run: 74 passed, 1 skipped in 62.12s** (Python 3.13.14).

The skip is `test_active_state_requires_a_model`, which is skipped by design
because the optional `langmem` package is not installed.

### Coverage by area

| Module | What it proves |
|--------|----------------|
| `test_vector_store.py` | Chroma persists across restarts; cosine distance→similarity conversion is correct |
| `test_embeddings.py` | Real 384-dim local MiniLM vectors; similar text scores higher than unrelated text |
| `test_retrieval.py` | Hybrid ranking, category intent, explainable reasons, `NO_STRONG_MATCH` on junk |
| `test_policy.py` | Durable vs transient classification; questions are never stored |
| `test_tools.py` | All five LangChain `StructuredTool`s execute and mutate real state |
| `test_langgraph.py` | Graph compiles; nodes run in order; the activity reducer preserves every node's events |
| `test_checkpoint.py` | SqliteSaver persists thread state across agent instances |
| `test_thread_isolation.py` | Thread A's conversation never leaks into thread B |
| `test_user_namespace.py` | User A's memories are unreachable as user B |
| `test_conflict.py` | Contradiction → versioned update, v2, `MEMORY_SUPERSEDED`, vector re-embedded |
| `test_consolidation.py` | Merge produces one memory, relationship rows, sources removed from the index |
| `test_events.py` | Every lifecycle operation appends the right audit event |
| `test_delete.py` | Delete clears SQLite *and* Chroma, leaving no dangling relationships |
| `test_api.py` | Every HTTP route, including honest 503s for unconfigured optional features |
| `test_langmem.py` | The honesty contract: accurate state, no fabricated output when inert, policy fallback still works |
| `test_runtime_singleton.py` | Concurrent cold starts build exactly one Runtime and retrieval stays semantic |

### Bugs these tests caught

- LangGraph nodes each returned `activity` without a reducer, so the last write
  won and `LOAD_CONTEXT`/`DEMO_PLANNER` events silently vanished. Fixed with
  `append_activity`.
- Similarity computed as `1 - distance/2` made junk queries return five "strong"
  matches.
- An operator-precedence error in the `create()` conflict branch.
- `find_conflict` only searched within one category, so a `CONTEXT`-classified
  change never met the `PREFERENCE` memory it contradicted.
- **Found by clean-install verification:** `@lru_cache` does not hold a lock
  while its factory runs, so concurrent first requests each built a `Runtime`.
  That raced Chroma's client initialisation and some workers fell back to
  keyword mode, making a fresh install report `RETRIEVAL: KEYWORD`. Fixed with a
  double-checked lock plus bounded retry in the vector store, and covered by
  `test_runtime_singleton.py`.

## Frontend

```bash
cd frontend
npm run lint       # ✔ no warnings or errors
npm run typecheck  # clean (strict, noUnusedLocals, noUnusedParameters)
npm run build      # ✓ 7/7 static pages
```

## Browser QA

Automated with Playwright (Chromium) against the production build with the real
backend. 14 checks, **all passing, zero console errors**:

| Check | Result |
|-------|--------|
| Landing hero renders | PASS |
| Hero stats come from live `/api/health` (`26`, `SEMANTIC`, `384-DIM LOCAL`, `DEMO`) | PASS |
| Canvas elements present (scroll sequence + hero core) | PASS |
| Retrieval shows real scores and match reasons | PASS |
| Memory explorer lists live memories (26 cards) | PASS |
| Inspector drawer opens with versions and provenance | PASS |
| Conflict demo performs a real mutation and reaches v02 | PASS |
| Architecture page reflects live health (LANGGRAPH, CHROMADB) | PASS |
| Agent replies over HTTP in the workspace | PASS |
| Cross-thread long-term recall | PASS |
| No horizontal overflow at 390px (landing) | PASS |
| No horizontal overflow at 390px (workspace) | PASS |
| Reduced-motion renders full content | PASS |
| Zero console/page errors | PASS |

### Manually verified over HTTP

- `"what do you remember about my projects"` → 4 PROJECT memories (0.45/0.41/0.40/0.31)
- `"how should you explain things to me"` → COMMUNICATION_STYLE (0.57/0.52)
- Junk query → `NO_STRONG_MATCH`, empty results
- Same statement twice → `reinforced`, not duplicated
- `"I have switched to light mode."` → `updated`, v2, `MEMORY_SUPERSEDED`

### Not automated

Voice capture requires a real microphone and a Chromium user gesture; the
Web Speech API path was exercised manually. The `whisper` path is untested
because faster-whisper is not installed — health reports `browser`.

## v8 cognitive tests

Backend (`cd backend && python -m pytest`):

| Module | Covers |
|---|---|
| `test_cognition_events.py` | Closed event vocabulary, labels, correlation, subscriber isolation |
| `test_cognition_reputation.py` | Lifecycle progression, earned reputation, arbitration |
| `test_cognition_autonomy.py` | Authority vs confidence, DO NOTHING, suppressed interventions |
| `test_cognition_sandbox.py` | Simulations never mutate real state |
| `test_cognition_learning.py` | Evidence thresholds, reversible policies |
| `test_cognition_self_model.py` | Honest capability reporting, recovery |
| `test_cognition_scenarios.py` | Scenarios A–P over the real runtime |
| `test_cognition_api.py` | Typed HTTP surface |

Browser (`python tests/observatory_qa.py`) — requires both servers running.
Captures screenshots to `tests/shots/`, because DOM-presence checks alone have
previously missed a real layout bug.


## V8.1 — two suites, two honest results

The same files run in two modes. Real-LLM tests **skip themselves** unless a
model is genuinely reachable, so a fallback run can never be mistaken for a
real-agent pass.

### Deterministic (no model needed)

```bash
cd backend
MODEL_PROVIDER=demo python -m pytest -q
```

Measured: **174 tests, 0 failures, 8 skipped** (7 real-LLM tests + 1 optional
faster-whisper test).

### Real local model

```bash
cd backend
MODEL_PROVIDER=ollama \
OLLAMA_BASE_URL=http://127.0.0.1:11434 \
OLLAMA_MODEL=qwen2.5:0.5b-instruct \
python -m pytest -q
```

Measured: **174 tests, 0 failures, 2 skipped**, against
`qwen2.5:0.5b-instruct-q4_K_M` on CPU.

`MODEL_PROVIDER=demo` deliberately excludes the real-LLM suite even when an
Ollama server is running, so the deterministic run stays deterministic.

### What the real-LLM suite proves

| Test | Guarantee |
|---|---|
| `test_provider_reports_real_agent_mode` | Provider reports `REAL AGENT` with a concrete model. |
| `test_model_resolves_a_pulled_tag` | The configured name resolves to a genuinely installed tag. |
| `test_real_end_to_end_tool_call_loop` | USER → LLM → TOOL CALL → RESULT → SECOND MODEL DECISION, and no raw `<tool_call>` leaks. |
| `test_extraction_returns_validated_structures` | Model output is schema-valid or dropped. |
| `test_concurrent_turns_all_succeed` | Parallel turns are serialised; reproduced a real 500. |
| `test_extraction_yields_to_a_busy_model` | Background extraction degrades instead of queueing. |
| `test_turn_is_labelled_model_assisted` | Turns are labelled by how they were actually understood. |

### Notes on running these

* **RAM.** The suite loads Chroma and ONNX per module. Stop dev servers before a
  real-mode run: with under ~800 MiB free, Ollama refuses to load the model and
  reports it plainly rather than hanging.
* **Duration.** Real mode takes roughly 7 minutes on CPU; demo mode about 100
  seconds.

### Browser QA

`tests/browser_qa.py` (14 V7 assertions), `tests/observatory_qa.py` (25 V8
assertions) and the V8.1 pass (16 assertions covering the provider badge, memory
health panel, perception states, a live chat turn, mobile overflow and console
errors). Run them against a **built** frontend.

> `BACKEND_URL` is compiled into the Next.js build. If you move the API off port
> 8000, rebuild the frontend - otherwise every `/api/*` call returns 500.
