# PROJECT STATUS

Every status below was produced by a command that was actually executed. Nothing
is aspirational. Where a capability is absent it is marked
`OPTIONAL / NOT CONFIGURED` rather than given a false PASS.

Verified on Linux, Node v20.20.2 / npm 10.8.2, Python 3.13.14.

## Build and test matrix

| Item | Status | Evidence |
|---|---|---|
| V8.4.3 backend fast gate | **PASS** | `pytest -m "not slow"` → **786 passed, 10 skipped, 12 deselected** in ~213 s |
| V8.4.3 Connected Research security suite | **PASS** | `test_v843_net_security.py` → **36 passed** — SSRF (loopback/private/link-local/metadata/CGNAT/multicast/IPv4-mapped-IPv6/alt-IP-encodings/unsafe-scheme/userinfo/port-policy), DNS rebinding via a real resolver, malformed-URL handling, resource limits (oversized response, redirect loop, timeout, unsupported content-type) |
| V8.4.3 Connected Research engine suite | **PASS** | `test_v843_research_engine.py` → **18 passed** — session lifecycle, failed-fetch persistence, SSRF-blocked ledger entries, evidence provenance, single-source confidence cap, same-domain-≠-independent corroboration, both-sides-preserved conflicts, prompt-injection flagging without execution, world-update propose/confirm/apply/double-apply-rejected, cross-user isolation, source-limit enforcement |
| V8.4.3 live real-network suite | **PASS** | `test_v843_live_network.py` → **5 passed** — genuine outbound HTTPS fetch to example.com, a real research session against a live page, a real httpbin.org redirect-to-private-IP blocked end to end, a real DNS failure against a nonexistent domain, honest network-availability self-report (would report NOT VERIFIED/skip if network were unavailable — it was available here) |
| V8.4.3 DB migration test | **PASS** | `test_v843_migration.py` → **2 passed** — a real V8.4.2-shaped SQLite file (built from `SCHEMA`+`SCHEMA_V83`+`SCHEMA_V841`+`SCHEMA_V842` only, pre-populated with representative rows) opens under V8.4.3 startup, gains all 7 new `research_*` tables, and every pre-existing row survives untouched; idempotent on a second open |
| V8.4.3 API/tools/explanation suite | **PASS** | `test_v843_api_and_tools.py` → **11 passed** — full `/api/research/v2/*` lifecycle via `TestClient`, world-update confirm-required contract, cross-user 404s, SSRF block via the API layer, legacy `/api/research` contract unaffected, all 6 new cognitive tools present and functional, `ExplanationEngine.explain(subject_kind="research_claim", ...)` producing `RESEARCH_EVIDENCE` explanations with a capped-confidence decisive factor, honest `INSUFFICIENT EVIDENCE` for unknown research subjects |
| V8.4.3 frontend | **PASS** | `npm ci` clean install (322 packages, 0 vulnerabilities); `npm run typecheck` clean; `npm run lint` → `✔ No ESLint warnings or errors`; `npm run build` → 6/6 static pages including `/observatory` with the new Research panel |
| V8.4.3 live browser QA | **PASS** | Backend (uvicorn 0.0.0.0:8000) + frontend (`next start` 0.0.0.0:3000) run together; through the same-origin `/api/*` proxy: create session → real fetch to `https://example.com/` → 2 evidence excerpts / 2 claims extracted → propose world update → confirm+apply → World Model shows the new entity tagged `source: "research"` at capped confidence; separately, fetches to `169.254.169.254` and `127.0.0.1:1` both correctly returned `BLOCKED`/`PRIVATE_ADDRESS` through the live browser-facing path |
| Backend test suite | **PASS** | V8.4.1 full `python -m pytest` → **689 passed, 19 skipped** in 200.68 s; fast gate → **689 passed, 10 skipped, 9 deselected** in 198.88 s |
| V8.3.1.1 mission action suite | **PASS** | `pytest tests/test_v8311_mission_actions.py` → **28 passed** in 4.75 s |
| Browser QA — v8.3.1.1 (Playwright) | **PASS** | executed on this build: 5 routes × 1440×900 and 390×844 → **0 console errors, 0 page errors, 0 overflow, 0 HTTP ≥400, no error overlay**; five-turn create→pause→resume driven through the Workspace UI, final visible response reported the mission **active**, Observatory showed **ACTIVE**, activity trail showed `Tool selected: resume_mission` |
| Clean install from ZIP | **PASS** | V8.3.1.2 ZIP (288 files, 1.9 MB) extracted to a fresh dir: no `.git`/`.venv`/`node_modules` and no local databases in the archive; fresh venv + `pytest -m "not slow"` → **651 passed, 10 skipped, 7 deselected** in 149.2 s; `npm ci`, typecheck, lint, `next build` (8/8 pages) — all from the extracted copy, exit 0 |
| Frontend lint | **PASS** | `npm run lint` → `✔ No ESLint warnings or errors` |
| Frontend typecheck | **PASS** | `npm run typecheck` → clean (strict + `noUnusedLocals`/`noUnusedParameters`) |
| Frontend production build | **PASS** | `npm run build` → `✓ Compiled successfully`, 8/8 static pages |
| V8.4.1 browser QA | **PASS** | `tests/v841_browser_qa.py` → **87/87** against real FastAPI/Next processes; 5 routes × desktop/mobile, no overflow, console errors, page errors, HTTP ≥400 or Next error overlay |
| Observatory regression QA | **PASS** | `tests/observatory_qa.py` → **25/25**, including real API state, simulation isolation and mobile/reduced-motion checks |
| JavaScript dependency audit | **PASS** | `npm audit` and `npm audit --omit=dev` → **0 vulnerabilities** after patched PostCSS/Sharp resolution |
| Python dependency audit | **PASS WITH ACCEPTED RISK** | `python-multipart` upgraded to 0.0.32; four no-fix Chroma HTTP-server advisories remain but that server is not started/exposed by the supported embedded architecture; see `docs/V8.4.1-SECURITY.md` |
| Backend over real HTTP | **PASS** | uvicorn on `0.0.0.0:8000`; V8.4.1 lifecycle, evidence, validation, promotion, usage, outcome, focus and inspection exercised through the Next same-origin proxy |
| Frontend ↔ backend integration | **PASS** | `/api/*` rewrite verified end to end from the browser |
| Browser QA — v7 (Playwright) | **PASS** | `tests/browser_qa.py` → 14/14, **0 console errors** |
| Browser QA — v8 Observatory | **PASS** | `tests/observatory_qa.py` → **25/25**, 0 console errors |
| Browser QA — v8.2 (Playwright) | **PASS** | 5 pages × desktop 1440×900 and mobile 390×844, **0 console errors**, 0 page errors |
| V8.2 restart persistence | **PASS** | memories, influences, arbitrations, intent transitions, execution traces and focus all survive a backend restart |
| V8.2 degradation | **PASS** | unreachable Ollama → `DETERMINISTIC FALLBACK`; vision `NOT_CONFIGURED`; no model capability claimed |
| Browser QA — v8.3 (Playwright) | **PASS** | 5 routes × desktop 1440×900 and mobile 390×844 → **0 console errors, 0 page errors, 0 overflow** |
| V8.3 restart persistence | **PASS** | missions (2), observations (5), world facts (4), world changes (2), background cycles (1), suppressions (1) and full mission history all identical across a backend restart |
| V8.3 background safety | **PASS** | empty cycle recorded as empty; rate limit, deadline, cancellation and task budget all enforced; task failure contained |
| V8.3 simulation isolation | **PASS** | world and mission rows byte-identical after a projection; commit refused without explicit confirmation AND changes |
| V8.3 time machine | **PASS** | `HISTORY NOT AVAILABLE` before recorded history; state reconstructed only from `world_changes` / `mission_events` |
| V8.3 degraded path | **PASS** | unreachable Ollama → missions, background, attention, simulation and documents all still function; `RESEARCH PROVIDER NOT CONFIGURED`, PDF `METADATA ONLY` |
| Real Ollama model path | **NOT VERIFIED IN THIS ENVIRONMENT** | No Ollama server reachable. All nine slow real-model tests skipped explicitly; the two V8.4.1 tests state that learned inspection/correction tool selection is NOT VERIFIED rather than substituting demo/scripted behavior |
| Mobile layout (390 px) | **PASS** | 0 px horizontal overflow on landing and workspace |
| Reduced motion | **PASS** | full content renders; sequence pins to a static frame |
| No remote runtime assets | **PASS** | system fonts, locally generated frames, no CDN |

## V8.4.3 feature matrix

| Feature | Status | Notes |
|---|---|---|
| Real http(s) fetch pipeline | **PASS** | `net_security.py`; user/tool-supplied URLs only, never invented |
| SSRF defense (pre-DNS + post-DNS + per-redirect) | **PASS** | connection pinning defeats DNS rebinding; verified against a real resolver and a real redirecting server (httpbin.org) |
| Resource limits | **PASS** | timeouts, 2 MB response cap, 5-redirect cap, content-type allowlist — all independently tested |
| Prompt-injection defense | **PASS** | fetched content flagged, never executed as instructions |
| Deterministic evidence/claim extraction | **PASS, HONESTLY LABELED** | sentence-splitting + lexical similarity; documented as non-semantic |
| Source-aware corroboration | **PASS** | same-domain repeats do not inflate `independent_domain_count` |
| Conflict preservation | **PASS** | both conflicting claims kept, `research_conflicts` row created |
| Bounded World Model integration | **PASS** | propose → `confirm=True` → apply via existing `WorldStateV2.reconcile()`; double-apply rejected |
| No auto-promotion to memory/skill/principle | **PASS** | verified nothing in the pipeline writes to those tables |
| New `/api/research/v2/*` routes | **PASS** | 14 routes, user-scoped, cross-user 404 verified |
| Legacy `/api/research` contract | **PASS, UNCHANGED** | still returns `BLOCKED` / `RESEARCH PROVIDER NOT CONFIGURED` |
| 6 new cognitive tools | **PASS** | `start_research`, `fetch_research_source`, `list_research`, `inspect_research`, `inspect_research_evidence`, `inspect_research_claims` |
| Explanation engine integration | **PASS** | `RESEARCH_EVIDENCE`/`RESEARCH_CONFLICT` classes added to the existing `ExplanationEngine` |
| Additive DB migration | **PASS** | proven against a real pre-V8.4.3 database file |
| Observatory Research panel | **PASS** | source/evidence/claim/world-update visually distinct; failures never look like "no information found" |
| Live outbound network test suite | **PASS (network available in this build)** | would report NOT VERIFIED/skip honestly if network were unreachable |

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

## V8.2 — Cognitive Core

| Capability | Status | Evidence |
|---|---|---|
| Capability detection + routing (§3) | **PASS** | `test_v82_capabilities.py` — 19 tests; `UNKNOWN` never satisfies a requirement |
| Model-driven agent loop + tracing (§4) | **PASS** | `test_v82_agent_loop.py` — 17 tests; depth cap, timeout, duplicates, cancellation |
| Canonical context builder (§5) | **PASS** | `test_v82_context_focus.py`; bounded, ranked, declared truncation |
| Memory arbitration V2 (§6) | **PASS** | `test_v82_arbitration.py` — 18 tests, 9 weighted factors, persisted records |
| Retrieval quality (§7) | **PASS** | quarantined/contradicted memories excluded **and reported** |
| Causal chain (§8) | **PASS** | `test_v82_influence.py` — retrieval ≠ influence; no evidence ⇒ no reputation change |
| Continuity engine (§9) | **PASS** | 9 grounded reasons, 45-day dormancy |
| Intent evolution V2 (§10) | **PASS** | questions never create intent; emerging ≠ confirmed |
| Need detection V2 (§11) | **PASS** | always a hypothesis; accuracy is `INSUFFICIENT EVIDENCE` without feedback |
| Prediction → learning (§13) | **PASS** | `test_v82_prediction_regret.py`; unresolved observations leave predictions open |
| Consequence / regret (§14) | **PASS** | evidence-based or `INSUFFICIENT EVIDENCE`; never a fabricated number |
| Adaptive policy (§15) | **PASS** | 8 dimensions, evidence-carrying, revertible |
| Context fabric (§16) | **PARTIAL BY DESIGN** | permission-aware shape, **no connectors**; `NOT CONNECTED` is a valid end state |
| Per-capability trust (§17) | **PASS** | `reliability: null` below `MIN_EVIDENCE=3` |
| User-controlled cognition (§19) | **PASS** | `test_v82_user_control.py` — 21 tests; refuses to guess destructive targets |
| Autonomy governor V2 (§20) | **PASS** | ACT / ASK / WAIT / DO_NOTHING / BLOCKED, `decision` kept compatible |
| Object permanence (§21) | **PASS** | stable IDs, 30-min TTL, ambiguity refused |
| Observatory surfaces | **PASS** | 4 new panels; 0 console errors desktop + mobile |
| Explanation endpoints (§23) | **PASS** | evidence-cited; no chain-of-thought exposed |

### Honest limitations in V8.2

- The real-model path **cannot be verified in this environment** (no Ollama).
  It is covered by scripted-model tests; the real-model tests skip loudly.
- No external connectors exist. Calendar/email/files are `NOT CONNECTED`.
- No vision without a vision model — `NOT_CONFIGURED`, never a guess.
- No cross-user learning; everything is per-user-namespace.
- Regret is scored from evidence the caller supplies; the system does not
  independently investigate.


## V8.3 capability table

Every row states what the system genuinely does, including where it does
nothing.

| Capability | State | Honest limitation |
|---|---|---|
| Missions | **ACTIVE** | Progress derives only from completed steps; planning capped at 7 open steps |
| World change history | **ACTIVE** | Only from V8.3 onward — earlier state is reported `UNKNOWN`, never assumed |
| World staleness | **ACTIVE** | Per fact-class horizons. Stale means "re-check", never "false" |
| World reconciliation | **ACTIVE** | Evenly-matched conflicts are flagged for the user, not resolved automatically |
| Observations | **ACTIVE** | Evidence only. Promotion to memory is explicit and `OBSERVED`-only |
| Background cognition | **ACTIVE** | Explicitly invoked, not scheduled; no timer thread in this build |
| Attention V2 / learned silence | **ACTIVE** | Needs ≥4 recorded reactions; silence is never read as consent |
| Prediction windows | **ACTIVE** | Elapsed window with no evidence → `UNRESOLVED`, excluded from accuracy |
| Time machine | **ACTIVE** | Bounded by recorded history; otherwise `HISTORY NOT AVAILABLE` |
| Counterfactual sandbox | **ACTIVE** | Rule-based projection; isolation is structural; commit needs double confirmation |
| Memory maintenance V2 | **ACTIVE** | Review is read-only; reinforcement requires a recorded outcome, never retrieval |
| Document intelligence | **PARTIAL** | Text `FULL`; CSV/JSON `STRUCTURE ONLY`; PDF/DOCX/XLSX `NOT CONFIGURED`, `METADATA ONLY` |
| Vision | **NOT CONFIGURED** | No vision model bundled; images are metadata only, never described |
| Connectors | **NOT CONNECTED** | Calendar, email, files, tasks are interfaces only — no data is read |
| Research mode | **NOT CONFIGURED** | `RESEARCH PROVIDER NOT CONFIGURED`; no web access, no findings generated |
| Authentication | **ABSENT BY DESIGN** | Single-user local-first. No login, no session auth, no multi-tenant isolation. Documented, not faked |

## V8.3.1.1 — Conversational Mission Action Reliability

A reliability refinement discovered during real-model verification of V8.3.1,
not a new feature. With a real `llama3.2:3b`, "Resume it." after pausing a
mission produced a reply claiming the state was unknown.

The registry was never at fault: `MissionRegistry.set_state()` performed
`paused → active` correctly when called directly. The defect was in the tool
surface. The only conversational route to a state change was the generic
`update_mission_state`, which required a 3B model to pick a state enum, write a
reason and know to omit `mission_id` — too many simultaneous decisions.

**Fix:** four intention-named tools — `pause_mission`, `resume_mission`,
`complete_mission`, `abandon_mission` — each with two optional arguments
(`mission_id`, `reason`), all delegating to one `_transition` helper that calls
the canonical `set_state`. The model chooses the verb; the tool resolves the
object through existing conversational focus. Toolset 15 → 19, registered
through the existing `_tools_for()`.

This is **not** keyword routing: `graph.py` still contains no natural-language
command dispatch on the real-model path. No new HTTP endpoints, no UI redesign,
no change to the mission API.

Truthfulness behaviours: no-ops return `NO_CHANGE` and write no history row;
terminal missions return `TERMINAL_STATE` and are never resurrected; resuming a
blocked mission returns `INVALID_TRANSITION`; ambiguous references return
`AMBIGUOUS_REFERENCE` and mutate nothing.

Also fixed, found during this round's browser QA: a pre-existing React
hydration mismatch in `components/Gallery.tsx`, where SSR'd SVG geometry
computed from `Math.sin` emitted unrounded floats whose final digit differed
between server and client. Rounded to 3 decimal places; no visual change.

See `docs/V8.3.1.md` and `docs/V8.3.1-AUDIT.md`.

### Real-model verification — V8.3.1.2 release gate

Run against a real Ollama `llama3.2:3b` (`MODEL_PROVIDER=ollama`,
`OLLAMA_BASE_URL=http://127.0.0.1:11434`, `LLM_NUM_CTX=4096`), not mocked and
not the deterministic fallback.

**Gate: focused PAUSED mission + "Resume it."**

```
provider: ollama | before: paused
elapsed: 2237s
provider used: ollama
TOOL TRACE:
    TOOL_DECISION resume_mission
    TOOL_RESULT   resume_mission
after: active
ANSWER: The mission "Ship the billing migration" has been resumed.
        It is now active and ready to proceed.
```

The model selected `resume_mission` **on its own**, with no arguments, and did
NOT call `get_mission` or `get_world_state` first. The mission ended `active`.
`provider` is `ollama` with no `(degraded)` suffix, so this was a real model
call. **Release gate: PASSED.** Raw output: `docs/v8312-real-model-gate-evidence.txt`.

**Problem A on the real model: "What missions am I currently working on?"**

```
elapsed 2151s | provider: ollama
    TOOL_DECISION get_current_focus
    TOOL_RESULT   get_current_focus
ANSWER: You are currently working on "Migrate the billing database to
        Postgres". It is an active mission with a progress of 0%.
```

No `TOOL_FAILED`, no ValidationError. The model chose a legitimate
mission-reading tool and surfaced the real recorded mission.

**Honest performance note.** This machine has ~2 GB RAM, no GPU, and runs the
model on CPU with swap: ~5-9 tok/s prompt, as low as 0.02 tok/s generation. A
single tool-calling turn takes 30-40 minutes. Product timeouts were NOT changed
to make these pass — the probes raise `llm_timeout_s` in their own config only;
`app/config.py` still ships 120 s / 180 s, and a genuine timeout still degrades
and reports itself rather than being hidden.

### Real-model regression status for V8.3.1.1

`TestRealModelMissionActions` (marked `slow`) was started against a real
`llama3.2:3b` during the V8.3.1.1 round but was superseded before it finished:
V8.3.1.2 changed the prompt and tool schemas it exercises. The equivalent
verification for the current build is the V8.3.1.2 release gate recorded above,
which passed. On this machine (~2 GB RAM, no GPU, CPU inference with swap) the V8.3.1
real-model file of four tests took 2 h 01 m, so a six-test file running for
hours is expected behaviour, not a hang.

What **is** verified for this build is stated above: the full fast suite
(627 passed), the frontend checks, and browser QA executed against real dev
servers. The equivalent conversational path was additionally exercised
end-to-end through the browser UI, where create → pause → resume produced a
final visible response reporting the mission as **active**, backed by the real
registry.


## V8.4.1 — Experience → Skill → Principle

| Capability | Status | Evidence / honest limitation |
|---|---|---|
| Experience persistence and lifecycle | **PASS** | Observation-backed creation, provenance and audited observed→enriched→validated→active/archive transitions |
| Skill candidate detection | **PASS** | Three coherent successful Experiences with distinct origins; background never promotes |
| Skill validation and promotion | **PASS** | Deterministic evidence/quality/consistency/structure/scope checks; separate explicit promotion |
| Skill future influence | **PASS** | Scope/world-aware retrieval, persisted arbitration, context/decision usage, outcome and reputation loop |
| Principle generalization | **PASS** | At least two trusted Skills, two patterns and four Experiences; one-Skill case rejected |
| Principle future influence | **PASS** | Same retrieval/arbitration/use/outcome loop with stronger validation |
| Confidence vs reputation | **PASS** | Separate persistence, scoring, API fields, model context and Observatory display |
| Correction/refinement | **PASS** | Weaken, contradict, outdate, rescope, retire, forget and stop-use; audit retained |
| Automatic failure retirement | **PASS** | Three attributed contradictions with no success → weakened → contradicted → retired |
| Current-world/precondition check | **PASS** | Missing recorded precondition blocks before arbitration/use |
| User isolation | **PASS** | Read, evidence-link, explanation, correction and causal traversal tests |
| Causal provenance | **PASS** | Experience→Skill, Skill/Experience→Principle, abstraction→decision, outcome→abstraction |
| Canonical event bus | **PASS** | Candidate, validation, promotion, use, outcome and lifecycle events in existing event vocabulary |
| Conversation tools and focus | **PASS deterministically** | Real subsystem tools and stable-id focus tested; actual model selection **NOT VERIFIED** without Ollama |
| Observatory | **PASS** | Real API data, empty/loading state, counts, lifecycle, confidence, reputation, evidence, validation, usage, provenance and focus |
| Scenarios A–H | **PASS** | `test_v841_scenarios.py` → 8/8 |
| V8.4.1 deterministic suites | **PASS** | core 17 + integration 13 + scenarios 8 = **38/38** |
| Real-model V8.4.1 suite | **NOT VERIFIED** | 2/2 skipped with `ConnectError`: no Ollama server at `http://localhost:11434` |
| Security static analysis | **PASS** | Bandit baseline and current both 0 high / 3 medium / 14 low; no net-new finding |
| Browser gate | **PASS** | 87/87 V8.4.1 + 25/25 Observatory regression |

Detailed architecture: `docs/V8.4.1.md` · acceptance audit:
`docs/V8.4.1-AUDIT.md` · security: `docs/V8.4.1-SECURITY.md` · exact gates:
`docs/V8.4.1-VERIFICATION.md`.
