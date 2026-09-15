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

---

## V8.2 test suite

Ten new files, **241 tests** (233 passing, 2 skipped for the absent real model,
plus 6 that overlap with other modules when run as a set).

| File | Tests | Covers |
|---|---|---|
| `test_v82_capabilities.py` | 19 | §3 capability detection, provider veto, `UNKNOWN`, routing table, vision `NOT_CONFIGURED` |
| `test_v82_agent_loop.py` | 17 | §4 trace stages, depth cap, timeout, cancellation, duplicate blocking, tool-failure recovery |
| `test_v82_arbitration.py` | 18 | §6/§7 nine factors, blocked/superseded, contradiction penalty, excluded quarantined memories |
| `test_v82_influence.py` | 15 | §8 the causal chain, and every way reputation must *not* move |
| `test_v82_intent_needs.py` | 19 | §10/§11 questions never assert intent, emerging vs confirmed, need accuracy |
| `test_v82_context_focus.py` | 20 | §5/§16/§21 bounded ranked context, declared truncation, NOT CONNECTED, focus ambiguity |
| `test_v82_prediction_regret.py` | 25 | §13/§14 resolution guard, error/surprise/learning, evidence-based regret |
| `test_v82_user_control.py` | 21 | §19 command parsing, false-positive resistance, refusal to guess |
| `test_v82_api.py` | 39 | The HTTP surface, plus V8.1 backwards-compatibility assertions |
| `test_v82_real_intelligence.py` | 17 | Fallback honesty, scripted-model path, real-Ollama tests (skipped with a reason) |

### Real intelligence vs deterministic fallback

`test_v82_real_intelligence.py` is split deliberately:

* **Part A — fallback.** Runs everywhere. Asserts the system *declares* itself
  deterministic, claims no model capabilities, and still performs genuine
  retrieval, arbitration and evidence-keeping.
* **Part B — model path.** A `ScriptedModel` stands in for a tool-calling LLM,
  so the loop's real behaviour is exercised deterministically: the **model's**
  tool choice is honoured (not a keyword rule), the tool hits real memory data,
  revisions are traced, runaway loops are bounded, and retrieved memory is
  proven to actually reach the prompt.
* **Part C — real Ollama.** Probes for a live server; skips with an explicit
  reason when absent. It never silently passes.

### Bugs these tests caught

Written to be capable of failing — and five did:

1. **Empty reply on depth limit.** Hitting `MAX_TOOL_DEPTH` mid tool-call
   returned `""`. Now it explains why it stopped.
2. **Emerging intent overwrote the confirmed one.** Stored as `emerging`,
   reported as `current_intent`. Now surfaced as `emerging_intent`.
3. **"back to X" was discarded.** An explicit resume signal without a
   purpose-phrase fell through. Added `_try_resume`, which still refuses when
   the target is ambiguous.
4. **REMEMBER false positive.** *"what should I remember for the meeting"*
   triggered a memory write. The pattern is now imperative-anchored.
5. **Test isolation.** A shared LangGraph thread leaked messages between tests.

### Full run

```
$ cd backend && .venv/bin/python -m pytest
412 passed, 10 skipped in 116.61s
```

The V8.1 baseline (**179 passed, 8 skipped**) is fully preserved.
