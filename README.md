# MEMORY//OS

**MEMORY//OS V8.5 — Production Trust + Auth + Privacy + Multi-User + Observability.**

A continuously maintained personal cognitive environment, operated through
conversation. It remembers, learns evidence-backed Skills from meaningful
Experience, generalizes across multiple Skills into Principles, and can tell you
*why* it trusts them, *what they changed*, and *whether using them worked*.

V8.4.1 makes conversation the primary control surface. With a configured
tool-capable model, questions such as "what have you learned?" are routed to
real inspection tools; actual model selection is explicitly **NOT VERIFIED** in
the release environment because Ollama was unavailable. The focused command
"forget that skill" is also handled deterministically and retires the
abstraction without losing its audit trail. Users do not need to maintain table
rows or object ids.

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

## New in V8.5.1

V8.5.1 is a surgical reliability correction for real-model cognitive tool
routing. A real `llama3.2:3b` offered ~40 tool schemas at once picked the
wrong cognitive subsystem for natural questions ("What missions am I working
on?" → `get_attention_state`). The existing `CapabilityRouter` now narrows
the ADVERTISED tool surface to relevant capability families before the model
makes its final — still genuine — tool choice. Narrowing selects families,
never tools; fails open on ambiguity; never blocks execution; and is traced
honestly as a `TOOL_SURFACE` stage / `routing.tool_surface` event. Details:
[docs/V8.5.1.md](docs/V8.5.1.md).

## New in V8.5

V8.5 makes the system production-trustworthy without weakening the cognitive
architecture. Real authentication (`AUTH_MODE=required`): PBKDF2-hashed
passwords, hashed 256-bit session tokens, login/logout/expiry/revocation, CSRF
protection and secure cookies. Explicit authorization: tenant → users →
cognitive namespaces with least-privilege `member`/`admin`/`owner` roles and no
unrestricted admin. Proven user isolation: the namespace is resolved once from
the verified session — caller-supplied ids are ignored at every API surface and
in every conversational tool, bare-id routes answer 404 on ownership mismatch,
and cross-user thread reuse is refused. Security audit events (`auth.login`,
`authorization.denied`, `security.rate_limited`, …) live on the SAME canonical
EventBus as every cognitive event. Production observability: request
correlation ids, latency/error/auth-failure metrics with content-free labels,
structured redacted JSON logs, and honest `/api/health/live` vs
`/api/health/ready` with per-dependency `ACTIVE / DEGRADED / NOT_CONFIGURED /
BLOCKED / FAILED` truth. Bounded rate limits on auth, APIs, research,
portability and expensive cognition. A deterministic, idempotent migration
lets the workspace owner adopt the V8.4.4 single-user namespace with zero data
rewriting. The default `AUTH_MODE=disabled` preserves V8.4.4 local behavior
exactly. Details: [`docs/V8.5.md`](docs/V8.5.md) ·
[`docs/V8.5-SECURITY.md`](docs/V8.5-SECURITY.md) ·
[`docs/V8.5-VERIFICATION.md`](docs/V8.5-VERIFICATION.md).

## New in V8.4.4

V8.4.4 adds real user-owned data portability and recovery. A versioned ZIP
package contains the complete persisted cognitive surface, deterministic JSON,
relationships, provenance, per-file/object SHA-256 hashes and a human-readable
report. Sensitive credentials and runtime state are excluded. Imports are
staged as untrusted input, validated before live state is touched, conflict
analysed, dry-run planned, and applied only after explicit confirmation inside a
rollback-safe SQLite transaction. Existing state is never silently overwritten
or deleted. The Observatory now exposes real export status, package integrity,
validation, conflict details, dry-run blockers and restore history.

New structured tools: `start_export`, `inspect_export`, `validate_import`,
`dry_run_restore`, `inspect_restore_conflicts`, `restore_selected`, and
`inspect_restore_history`. New API namespace: `/api/portability/v1/*`.

Full detail: [`docs/V8.4.4.md`](docs/V8.4.4.md) · verification:
[`docs/V8.4.4-VERIFICATION.md`](docs/V8.4.4-VERIFICATION.md).

## New in V8.4.3

The system can now fetch real, explicit http(s) URLs — supplied by you or a
tool call, **never invented** — through an SSRF-defended pipeline, and turn
what it genuinely retrieves into auditable evidence and bounded-confidence
claims. There is no search engine: nothing is discovered on its own.
Corroboration counts independent domains, not repeated pages; conflicting
claims from different sources are both kept, never silently resolved.
External evidence never becomes a memory, skill, principle, or belief by
itself — it can only reach the World Model through an explicit
propose-then-confirm step, capped at a low confidence and tagged
`source: "research"` so it stays visibly distinct from what you told the
system directly. Every failed or blocked fetch is reported honestly (with
its exact reason) rather than presented as "nothing found." Six new
conversational tools (`start_research`, `fetch_research_source`,
`list_research`, `inspect_research`, `inspect_research_evidence`,
`inspect_research_claims`) and a new Observatory "Connected Research" panel
make the whole SOURCE → FETCH → EVIDENCE → CLAIM → CONFLICT → WORLD UPDATE
pipeline inspectable end to end.

Full detail: [`docs/V8.4.3.md`](docs/V8.4.3.md) · verification:
[`docs/V8.4.3-VERIFICATION.md`](docs/V8.4.3-VERIFICATION.md).

## New in V8.4.1

Meaningful observed episodes are now first-class **Experiences**. Repeated,
coherent, evidence-backed Experiences can become validated **Skills**; multiple
trusted Skills from distinct patterns can support a higher-order **Principle**.
Trusted Skills and Principles participate in scope-aware retrieval, persisted
arbitration and real decision influence; Skill preconditions are additionally
checked against explicit current-world state. Their usage
outcomes update a reputation that remains separate from confidence. Corrections
can weaken, contradict, rescope, retire or stop using an abstraction without
erasing its provenance or audit trail.

Conversation has model-selectable inspection/correction tools, while the
Observatory displays lifecycle, evidence, confidence, reputation, validation,
usage and provenance from real APIs. Background cognition may detect and
validate candidates but never promotes them.

Full detail: [`docs/V8.4.1.md`](docs/V8.4.1.md) · implementation audit:
[`docs/V8.4.1-AUDIT.md`](docs/V8.4.1-AUDIT.md) · verification:
[`docs/V8.4.1-VERIFICATION.md`](docs/V8.4.1-VERIFICATION.md).

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

- Backend full suite: **786 passed, 10 skipped** (12 `slow`-marked tests
  deselected by default); skips are explicit — real-model tests without a
  reachable Ollama server, live-network research tests report NOT VERIFIED
  only if outbound network is genuinely unavailable (it was available and
  ran for real in this build's verification)
- V8.4.3 Connected Research suite: **72/72 passed** across SSRF/security
  (36), engine integration (18), live real-network fetches (5), DB
  migration (2), and API/tools/explanation integration (11)
- V8.4.1 deterministic core/integration/scenarios: **38/38 passed**
- `npm run lint` — clean · `npm run typecheck` — clean · `npm run build` — 6/6 static pages
- Browser QA: **87/87** V8.4.1 assertions plus **25/25** Observatory
  regression assertions; desktop 1440×900 + mobile 390×844, zero console/page/HTTP errors
- Dependency audit: npm 0 vulnerabilities; Python's fixable multipart
  advisories resolved, with four no-fix Chroma server advisories documented as
  unreachable in the supported embedded architecture
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
| [`docs/V8.4.1.md`](docs/V8.4.1.md) | Evidence-backed Experience → Skill → Principle architecture and behavior |
| [`docs/V8.4.1-AUDIT.md`](docs/V8.4.1-AUDIT.md) | 38-area and scenarios A–H acceptance audit |
| [`docs/V8.4.1-VERIFICATION.md`](docs/V8.4.1-VERIFICATION.md) | Exact backend, frontend, browser, security and artifact gates |
| [`docs/V8.4.2.md`](docs/V8.4.2.md) | Auditable Explanation Engine architecture and explanation classes |
| [`docs/V8.4.2-VERIFICATION.md`](docs/V8.4.2-VERIFICATION.md) | V8.4.2 backend/frontend verification gates |
| [`docs/V8.4.3.md`](docs/V8.4.3.md) | Connected Research + External World Intelligence architecture, security, data model |
| [`docs/V8.4.3-VERIFICATION.md`](docs/V8.4.3-VERIFICATION.md) | V8.4.3 backend/frontend/security/live-network verification gates |
| [`docs/V8.4.4.md`](docs/V8.4.4.md) | Data portability format, validation, conflicts, restore safety and security |
| [`docs/V8.4.4-VERIFICATION.md`](docs/V8.4.4-VERIFICATION.md) | V8.4.4 backend/frontend/clean-room/artifact verification gates |
| [`docs/TESTING.md`](docs/TESTING.md) | Test coverage, verified results, bugs caught |
| [`docs/DESIGN.md`](docs/DESIGN.md) | Visual language, motion, the scroll sequence |
| [`PROJECT_STATUS.md`](PROJECT_STATUS.md) | Honest status matrix and limitations |

## Limitations

- `AUTH_MODE=disabled` (the default) is the single-user local demo: no login
  exists in that mode. Real multi-user auth requires `AUTH_MODE=required`
- V8.5 rate limiting and metrics are per-process; multi-instance deployments
  need shared backends
- Password auth is the only built-in identity provider (the `IdentityService`
  seam exists for OIDC/SSO); no MFA, password reset or email verification
- The demo frontend has no login page; in required mode clients authenticate
  through the API (bearer token or cookie + CSRF header)
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
