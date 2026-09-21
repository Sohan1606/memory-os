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

---

## V8.3 — Continuous Cognition

Full suite: **552 passed, 10 skipped** (`python -m pytest`, ~169 s). The V8.2
baseline of 412/10 is fully preserved; V8.3 adds **140** tests.

| Suite | Tests | Covers |
|---|---|---|
| `test_v83_missions.py` | 15 | Lifecycle, bounded planning, derived progress, replanning triggers, resume brief, persistence across registry instances |
| `test_v83_world_observations.py` | 24 | Change provenance, per-class staleness, all six reconciliation verdicts, observation/memory separation, promotion rules |
| `test_v83_background_attention.py` | 29 | Empty-cycle honesty, rate limiting, deadlines, cancellation, contained failure, non-destructiveness, attention ladder, learned silence |
| `test_v83_simulation_history.py` | 36 | Simulation isolation and commit guards, time-machine availability, prediction windows, document parsing honesty, connectors, research, maintenance |
| `test_v83_api.py` | 27 | Every V8.3 route plus V8.2 compatibility checks |
| `test_v83_scenarios.py` | 9 | §38 end-to-end living-system scenarios A–G, epistemic separation, full restart persistence |

### What the tests deliberately assert *cannot* happen

Much of this suite is negative testing — the honesty guarantees are only real
if something fails when they are violated:

- a background cycle that finds nothing **cannot** report activity
- a simulation **cannot** alter world, mission or memory rows
  (asserted by byte-comparing table contents before and after)
- an `INFERRED` or `SIMULATED` observation **cannot** be promoted to memory
- retrieval alone **cannot** reinforce a memory
- silence **cannot** become an accepted intervention or a resolved prediction
- the time machine **cannot** answer for a moment before recorded history
- an evenly-matched world conflict **cannot** be silently resolved
- a PDF **cannot** acquire content it was never parsed for

### Verified outside the suite

- **Browser QA** — Playwright, 5 routes × desktop 1440×900 and mobile 390×844:
  0 console errors, 0 page errors, 0 horizontal overflow.
- **Restart persistence** — counts and mission history identical across a real
  uvicorn restart.
- **Degraded path** — with Ollama unreachable, every V8.3 subsystem still
  functions and no capability is claimed.
- **Real Ollama path (V8.3.1) — VERIFIED.** `llama3.2:3b` under a real Ollama
  server: `tests/test_v831_real_model.py` → **4 passed in 7308.99 s
  (2:01:48)**. With no scripting, the model chose `list_missions` itself for
  "What missions am I currently working on?", answered from the real registry
  ("Finish the quarterly report"), and when asked for the next step of a
  mission that has none, it did not invent one — and answering wrote no state.

  Environment note: this machine has ~2 GB RAM and no GPU, so the model runs on
  CPU with 6 GB of swap at roughly 6.6 tok/s prompt and as low as 0.03 tok/s
  generation on long contexts. That is why a four-test file takes two hours and
  why the fixture raises `llm_timeout_s` to 900 s. The **product defaults stay
  at 120 s / 180 s**; at the default the real model legitimately timed out and
  the system degraded to the deterministic planner, exactly as designed. These
  tests carry the `slow` marker — run `pytest -m "not slow"` to skip them.

  The tests probe for the server once and skip with an explicit reason when it
  is absent. A skip is never a pass.

### V8.3.1 suite

`tests/test_v831_conversational_cognition.py` — **47 tests** against the real
runtime (real registry, real world model, real focus tracker, real SQLite):

| Group | Covers |
|---|---|
| End-to-end scenario | the required 7 steps: create → list → next step → pause that → resume it → why → what changed |
| Next-step truthfulness | absent step reported, recorded step quoted, empty registry stated, blockers real |
| Object permanence | focus persists across calls, ambiguity refused, single mission resolves, `mission` is focusable, "that mission" resolves |
| Creation discipline | explicit creation only, rubbish titles rejected |
| Mission updates | completion, partial progress, invalid state, reason recorded |
| Subsystem routing | projects from world state, goal ≠ mission, honest emptiness |
| Explanation | real world changes, no subject → ask, nothing changed stated |
| Epistemic labelling | RECORDED / SIMULATED / PREDICTED, simulation mutates nothing, history refused |
| Attention | no manufactured findings, direct queries answered |
| Mission-first context | missions in the bundle and prompt, survive unrelated turns, absent when none |
| Agent integration | cognitive tools bound, memory tools preserved, model-chosen call traced |
| Deterministic fallback | reads real missions, labels itself, keeps goals distinct |
| Prior behaviour | V8.2 preference correction and the five memory tools intact |
| Persistence | missions and steps survive a real restart |

### What the V8.3.1 tests assert cannot happen

- a mission with no recorded next step **cannot** be reported as having one
- an empty mission registry **cannot** produce a list of missions
- `"pause that"` with several open missions **cannot** pause an arbitrary one
- a goal **cannot** be returned as a mission
- a simulation **cannot** change mission state
- answering a question **cannot** write mission state
- the deterministic fallback **cannot** present itself as the real agent

### V8.3.1.1 suite — mission action reliability

`tests/test_v8311_mission_actions.py` — **28 tests**, 4.75 s, against the real
runtime and the real `MissionRegistry`.

| Area | What is asserted |
|---|---|
| Direct resume | explicit `mission_id`, real `paused → active`, `previous_state` reported |
| Focus resume | `resume_mission()` with no arguments resolves through conversational focus |
| Five-turn scenario | the exact reported conversation: create → list → next step → pause → resume |
| Terminal missions | completed / failed / abandoned return `TERMINAL_STATE` and are not reopened |
| Ambiguity | two paused missions and no focus → `AMBIGUOUS_REFERENCE`, nothing mutated |
| No-ops | resuming an active mission → `NO_CHANGE` / `ALREADY_ACTIVE`, **no history row** |
| Invalid transitions | resuming a *blocked* mission → `INVALID_TRANSITION` |
| Tracing | `TOOL_DECISION resume_mission` followed by `TOOL_RESULT resume_mission` |
| Events | the registry emits a real `mission.resumed`, not a synthesised one |
| Fallback | the demo planner runs the whole scenario and stays labelled DETERMINISTIC |
| Fallback safety | a *question* about missions creates nothing |

**Real-model regression.** `TestRealModelMissionActions` in
`tests/test_v831_real_model.py` (marked `slow`) drives an actual Ollama
`llama3.2:3b`: it creates and pauses a mission, sets focus, sends "Resume it.",
then asserts the provider is not `demo`, that the model itself selected
`resume_mission`, that the registry is back to `active`, and that
`mission.resumed` reached the bus. A companion test asserts the model does not
resurrect a completed mission. Timeouts are raised in the fixture only; product
defaults are untouched, and a legitimate timeout degrades and reports rather
than being hidden.

### What the V8.3.1.1 tests assert cannot happen

- a **terminal** mission **cannot** be resumed back into life
- a no-op **cannot** write a history row or claim a transition
- an **ambiguous** "resume it" **cannot** resume an arbitrary mission
- a mission state change **cannot** be narrated unless the tool result confirms it
- the fallback **cannot** create a mission from a question about missions

### V8.3.1.2 suite — null tolerance and focus-driven selection

`tests/test_v8312_tool_routing.py` — **24 tests**.

| Area | What is asserted |
|---|---|
| Null arguments | `open_only` true / false / null / omitted; null == omitted exactly |
| Null safety | a null-argument listing creates and mutates nothing |
| Null on actions | `{"mission_id": null, "reason": null}` still resumes via focus |
| Focus lifecycle | focused paused→resume, active→pause, active→complete, →abandon |
| Focus identity | with two open missions, focus alone picks the right one and the other is untouched |
| Step vs mission | `complete_mission_step` does not complete the mission; the tools are distinct |
| Canonical events | `mission.resumed` / `mission.paused` emitted by the registry |
| No-op honesty | an already-active mission emits no `mission.resumed` |
| Focus in prompt | the focused mission's id, state and valid actions appear; terminal offers none; no focus claims nothing; a deleted focused mission is not advertised |
| Architecture | no `"resume" in ...` keyword routing on the real-model path |

**Real-model release gate.** The gate for V8.3.1.2 is an actual `llama3.2:3b`
run in which the model itself selects `resume_mission` for "Resume it." against
a focused paused mission, ending `active`. Result recorded in
`PROJECT_STATUS.md` with raw output in `docs/v8312-real-model-gate-evidence.txt`.
A deterministic test passing is explicitly NOT sufficient for that claim.


---

## V8.4.1 verification suites

| Suite | Purpose |
|---|---|
| `test_v841_learning_core.py` | Experience lifecycle/provenance, Skill/Principle evidence and validation, scope/world checks, arbitration, reputation, correction, isolation and background maintenance |
| `test_v841_integration.py` | HTTP APIs, context and decision integration, canonical events, conversational tools, focus and evidence-backed explanations |
| `test_v841_scenarios.py` | Eight end-to-end acceptance scenarios A–H, including restart persistence |
| `test_v841_real_model.py` | Slow real-Ollama gate; the actual model must select learned inspection/correction tools and mutate/read canonical state |

The real-model suite probes `/api/tags` before test execution and skips with an
explicit **NOT VERIFIED** reason if Ollama or the requested model is absent. It
does not substitute a scripted model, demo planner or direct tool call.

The browser gate must use real frontend and backend processes, real API data and
both desktop and mobile viewports. It checks the new Observatory panel, loading
and empty states, lifecycle/evidence/confidence/reputation content, stable focus,
no horizontal overflow, no HTTP errors, no console/page errors and no Next.js
error overlay.

Exact results for the release candidate are recorded in
[V8.4.1-VERIFICATION.md](V8.4.1-VERIFICATION.md).

---

## V8.4.3 verification suites

| Suite | Purpose |
|---|---|
| `test_v843_net_security.py` (36 tests) | Adversarial SSRF suite: loopback/private-IPv4/private-IPv6/link-local+metadata/IPv4-mapped-IPv6/alt-IP-encodings (decimal/hex/octal/partial-dotted)/unsafe-schemes/userinfo-rejection/invalid-URL/port-policy/multicast/CGNAT/unspecified-address, all blocked; DNS-rebinding end-to-end via a real resolver; malformed-URL variants; `fetch()` never raises; resource limits (oversized response, redirect loop, unsupported content-type, timeout) via a local `ThreadingHTTPServer` fixture with a narrowly-scoped `allow_loopback` fixture that only widens policy enough to test fetch *mechanics*, never weakening real SSRF policy |
| `test_v843_research_engine.py` (18 tests) | `ResearchEngine` integration against a local HTTP server: session lifecycle, failed-fetch persistence (404 → `UNREACHABLE`/`FETCH_FAILED`, session `FAILED`), empty-page → zero fabricated evidence, SSRF-blocked URL recorded in the ledger, evidence provenance fields, single-source confidence cap, same-domain corroboration correctly NOT counted as independent, conflicting claims both preserved + conflict record created, prompt-injection flagged but never executed, world-update propose/apply requiring `confirm=True` with capped confidence and rejected double-apply, cross-user isolation across every accessor, source-limit enforcement, honest `status()` limitation statement |
| `test_v843_live_network.py` (5 tests) | **Real outbound network calls**, no mocking: a genuine HTTPS fetch to `example.com`, a full research session against a real live page, a real `httpbin.org` redirect-to-private-IP correctly blocked end to end, a real DNS failure against a nonexistent domain reported honestly, and a self-check that reports the whole suite as `NOT VERIFIED`/skipped (never silently passed) if outbound network is unavailable in the running environment |
| `test_v843_migration.py` (2 tests) | Builds a real SQLite file using only the pre-V8.4.3 schema strings, populates representative V8/V8.1–V8.4.2 rows, then opens it via the real `Database.__init__` startup path and proves every pre-existing row survives untouched while all 7 new `research_*` tables are created; a second test proves re-opening is idempotent |
| `test_v843_api_and_tools.py` (11 tests) | Full `/api/research/v2/*` HTTP lifecycle via `TestClient`, world-update apply requiring `confirm=True` (400 otherwise), cross-user 404, SSRF block surfaced through the API layer, legacy `/api/research` contract proven unchanged, all 6 new cognitive tools present with honest descriptions ("never invent a URL", "no search engine"), and `ExplanationEngine` producing `RESEARCH_EVIDENCE` explanations with a capped-confidence decisive factor plus an honest `INSUFFICIENT EVIDENCE` fallback for unknown subjects |

All five V8.4.3 suites (72 tests) pass together with zero regression to the
full pre-existing suite (`pytest -m "not slow"` → **786 passed, 10 skipped,
12 deselected**). The live-network suite is deliberately isolated from the
adversarial SSRF suite: the former performs genuine internet calls and
self-reports honestly if network access is unavailable; the latter uses a
local, loopback-only fixture server so the SSRF policy itself is never
weakened just to make a test pass.

Exact results for the release candidate are recorded in
[V8.4.3-VERIFICATION.md](V8.4.3-VERIFICATION.md).

---

## V8.4.4 portability and recovery tests

`backend/tests/test_v844_portability.py` covers deterministic serialization,
manifest/object hashes, package inspection, corruption and manifest tampering,
same-id divergent conflicts, selective restore, dependencies, transaction
repeatability, owner scoping, path traversal, and EventBus lifecycle events.

The release gate also checks API multipart staging, validation-before-restore,
dry-run blockers, explicit confirmation, restore history, the structured
portability tools, frontend typecheck/lint/build, clean extraction, and release
ZIP contents/hash. A blocked or failed restore must never be represented as a
successful UI operation.
