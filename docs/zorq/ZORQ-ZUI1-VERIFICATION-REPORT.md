# ZORQ Phase Z-UI.1 — Frontend Transformation Verification Report

**Branch:** `zroq/canonical-migration` @ `bcdc3df` (Z-UI.1 work uncommitted in tree — intentional, per directive stop-before-commit)
**Date:** 2026-09-28 · **Status: ALL VERIFICATION PASSED — ready for review**

---

## 1. What was built

Z-UI.1 transformed the showcase frontend into the ZORQ control surface: a
truthful, state-first interface over the real backend, with the sealed
capability registry, the action lifecycle, the audit chain, device truth and
the five-plane architecture rendered from a new GET-only backend facade.
Public identity is **ZORQ** alone. No internal full-form identity appears anywhere
public (UI, metadata, README, comments, CSS classes, a11y labels — verified by
QA forbidden-text scan and by construction).

### Work packages executed
| WP | Scope | Status |
|---|---|---|
| UI-0 | Design baseline (`ZORQ-ZUI1-DESIGN-BASELINE.md`): reconciliation map, IA, design language, state vocabularies, facade contract | ✅ |
| UI-1 | Backend state facade `backend/app/zorq_facade.py` + 5 tests→7 tests + `main.py` additive integration | ✅ |
| UI-2 | Design system CSS (z-tokens + 15 component classes) + component primitives | ✅ |
| UI-3 | System Home `/` transform (identity, live state, planes, surface directory, preserved real demos) | ✅ |
| UI-4 | Workspace transform (conversation + context rail: runtime truth, proposed-actions, authorization boundary) | ✅ |
| UI-5 | `/actions` `/capabilities` `/audit` new surfaces + Observatory ZORQ core panel | ✅ |
| UI-6 | `/devices` new + `/system` transform (five planes, live stack, never-faked) + `/memory` extend (governance header) | ✅ |
| UI-7 | Responsive (nav-desktop/nav-mobile, workbench collapse, z-grid) + a11y pass | ✅ |
| UI-8 | Retirement: ScrollSequence+60 frames, Gallery, Hero, CustomCursor, Section, frames generator, `frames` script | ✅ |

## 2. Design system

- **Tokens** (`globals.css` `:root`): `--z-accent #e3b34c` (amber), semantic
  tones ok `#4db585` / active amber / warn `#dd8a3c` / fail `#e0645c` /
  unknown `#9d8fc4` / offline `#8296ab` / off neutral, each with `-soft`/`-line`
  variants; `z-field/z-surface-1/2`, `z-line(-strong)`, `z-ink/2/3`,
  `z-radius`, `z-dur`, `z-ease`. MEMORY//OS teal `#6ee7d7` (DQ-17) is retained
  **only** inside memory surfaces via the pre-existing `--accent`.
- **State is never color-only**: `.z-state` carries a glyph, mono uppercase
  label and tone; unknown/absent states are hollow and dashed so they can
  never read as solid success; working states pulse (killed under
  `prefers-reduced-motion`).
- **Components**: `.z-panel(.z-panel-head/-title/-hint)`, `.z-readout(-key/-val)`,
  `.z-page(-kicker/-title/-lede)`, `.z-grid[data-cols]`, `.z-nav-link[aria-current]`,
  `.z-nav-group`, `.z-chip`, `.z-steps/.z-step[data-state]`, `.z-arrow`,
  `.z-condition(-label/-note)`, `.z-skeleton`, global `:focus-visible` amber
  outline, `.z-workbench` two-column layout with 1100px collapse,
  responsive nav classes. 155 z-refs in CSS; pre-existing MEMORY//OS classes untouched.

## 3. Route architecture

| Route | Disposition | Content |
|---|---|---|
| `/` | TRANSFORM | ZORQ identity, live system state (runtime/memory/control plane), five planes, surface directory, MEMORY//OS live demos (RetrievalDemo, LayerSwitcher, VoiceDemo — real pathways) |
| `/workspace` | TRANSFORM | Conversation (Chat/CognitiveSurface preserved) + context rail: runtime truth, proposed-actions (truthful empty), authorization boundary |
| `/memory` | EXTEND | Governance header (canonical authority, adapter status, retrieval≠governance) + all prior functionality (explorer, consolidation, graph, conflict, timeline) |
| `/actions` | NEW | Lifecycle stepper + phase meanings, verification semantics (COMPLETED≠VERIFIED), execution origins, status vocabulary, authority invariants, truthful no-records |
| `/capabilities` | NEW | Sealed registry cards with ladder chips (VISIBLE/AVAILABLE/AUTHORIZED-SESSION-BOUND/EXECUTABLE), risk, confirmation, governance requirement |
| `/audit` | NEW | Real hash-chained event tail, integrity token, persistence, scope note, cross-link to memory timeline |
| `/devices` | NEW | Current device identity, truthful empty authorized/available lists, distinctions, full state vocabularies, Z-LD.1/Z-DIST.1 not-implemented panels |
| `/system` | TRANSFORM (from `/architecture`) | Runtime health, ZORQ core, five planes with boundaries, preserved live stack explainer, never-faked principle |
| `/architecture` | RETIRED → 307 redirect to `/system` (component preserved) |
| `/observatory` | EXTEND | ZORQ core panel (full status readout + planes) + all 20 existing panels |

## 4. Backend facade (WP-UI-1)

`backend/app/zorq_facade.py` (421 LOC) — `APIRouter(prefix="/api/zorq")`,
GET-only, fail-closed:

- 5 endpoints: `/status` `/capabilities` `/actions` `/audit` `/devices`.
  POST on any → **405** (verified live).
- `_compose_core()` lazily composes a restricted ZorqCore: per-boot
  `secrets.token_urlsafe(32)` owner secret, approved root
  `settings.data_dir/zorq-facade-root`, persistent audit
  `settings.data_dir/zorq_facade_audit.jsonl`.
- Real sealed registry: `core.registry.all_manifests()`, combined digest =
  `sha256_digest({"manifests": sorted(manifest_digest(m) ...)})`.
- Truthful statuses: Intelligence/Evolution DESIGNED; Continuity/Interaction
  PARTIALLY-IMPLEMENTED with notes; Action IMPLEMENTED (2.6 slice, not
  HTTP-exposed); connection LOCAL; sync NOT-CONFIGURED; voice/multimodal
  NOT-IMPLEMENTED with phase attribution; capabilities ladder
  visible/available true, authorized "SESSION-BOUND", executable false.
- `main.py` integration additive (import + include_router, 5 lines).

## 5. Frontend architecture

- `frontend/components/zorq/` — `StateToken.tsx` (tone rules + a11y text),
  `Primitives.tsx` (ZPanel, Readout, PageHeader, LoadingPanel, ErrorPanel,
  UnavailablePanel, NotImplementedPanel, LadderChips, LifecycleStepper),
  `useFacade.ts` (10–30s polling, visibility-paused, typed condition:
  LOADING/READY/ERROR/UNAVAILABLE).
- `lib/types.ts` +174: §12.1.1 vocabularies as
  `ConnectionState/ExecutionOrigin/SyncState/CapabilityLevel/SystemCondition`
  + full response types. `lib/api.ts` +16: `zorqApi` (extends the typed
  client; the UI never bypasses it).
- `Navigation.tsx`: grouped IA (Primary/System/Runtime), live status strip
  (LOCAL + provider truth + MEMORY//OS), mobile full-screen dialog,
  `aria-current`, 3.6rem fixed header.
- `layout.tsx`: ZORQ metadata, `--nav-h`, skip-link preserved.

## 6. Verification results

| Check | Result |
|---|---|
| Backend suite (`backend/tests`, 1094 tests incl. 7 facade) | **PASS** (exit 0; skips = slow/real-model markers) |
| `test_zorq_facade.py` | 7/7 PASS (truthful statuses, GET-only 405, no-secret-keys walk) |
| Frontend `tsc --noEmit` | **PASS** 0 errors |
| Frontend ESLint | **PASS** 0 warnings/errors |
| Frontend production build | **PASS** 10 routes, 12/12 static pages |
| Browser QA (`tests/zui1_browser_qa.py`) | **PASS** — verdict printed |
| Desktop 1440×900, 9 routes | 0 console errors, 0 page errors; all truthful-state assertions (LOCAL, NOT-CONFIGURED, PROPOSED, No action records, None enrolled, DEVICE TRUST ≠ USER AUTHORIZATION, SESSION-BOUND…) |
| Mobile 390×844 | 0px horizontal overflow ×4 routes, 0 console errors, nav dialog opens with all 8 links |
| `/architecture` | 307 → `/system` |
| A11y | skip-link first focus; exactly 1 `<h1>` per route ×9; landmarks 1 header/1 nav/1 main; `lang=en`; 0 images missing alt; desktop nav visible at 1440px with menu button hidden |
| GET-only (live) | POST ×5 endpoints → 405 |
| MEMORY//OS intact (E2E) | Chat turn "I prefer full browser verification…" → policy → `save_memory` → "Stored" reply → memory visible on `/memory`; 0 console errors |
| Confidentiality | forbidden-text scan across 9 routes: clean |

Evidence: `docs/zorq/qa/zui1-browser-evidence/` — 16 screenshots
(desktop ×9, mobile ×5 incl. nav dialog, functional ×2) + `qa-results.json`.

## 7. Performance observations

- First Load JS: `/` 121 kB (was ~127 kB with ScrollSequence+Gallery),
  `/observatory` 130 kB (heaviest, unchanged panel set), new routes 109–119 kB.
- Home page weight drops by the retired 961 kB of WebP frames.
- Facade polling: 10–30s per surface, paused when the tab is hidden.
- No layout shift from state tokens (fixed-size chips, mono labels).

## 8. Known limitations (truthful)

1. **Action records are empty by truth** — the 2.6 action plane is
   implemented and test-verified in zroq-core but not exposed over HTTP;
   `/actions` renders the lifecycle contract and a first-class empty state.
2. **authorized "SESSION-BOUND"** — the ladder renders authorization as a
   partial state because no session exists in the facade; the UI never
   claims AUTHORIZED.
3. **Voice demo uses the browser Web Speech fallback** and is labeled as
   such; the real voice runtime is Phase 3F.
4. **The demo planner** answers without a model when no provider is
   configured; every surface labels PROVIDER truthfully (DEMO vs REAL).
5. QA screenshot set covers desktop+mobile; per-route reduced-motion and
   keyboard-walkthrough were verified structurally (focus-visible, dialog
   focus trap not formally asserted — noted, not claimed).

## 9. Deviations

None against the Rev 2 spec. Baseline deviations: none registered.
Two QA-harness fixes during verification (wait-for-readout instead of a
1.2s sleep; placeholder assertions via DOM content) — harness-only, no
product change. One real bug found and fixed by QA: `nav-desktop` class was
referenced but undefined, hiding the desktop nav (now defined in CSS with
1080px breakpoint + regression assertions in the QA script).

## 10. Diff stat (uncommitted, on bcdc3df)

```
 14 files changed, 1012 insertions(+), 313 deletions(-)   # tracked, code+config
 60 frames + manifest + generator retired (-222 LOC, -961 KB assets)
 new (untracked): zorq_facade.py (421) · test_zorq_facade.py (191) ·
   ZORQ-ZUI1-DESIGN-BASELINE.md · 5 route pages (601 LOC) ·
   components/zorq/ (323) · ZorqCorePanel (80) · zui1_browser_qa.py (206) ·
   qa evidence (16 png + json)
```

`src/zroq/` untouched (0 changes). **Not committed — awaiting review.**

---

## 11. Refinement pass (post-review, uncommitted)

Focused fixes after independent review — no architecture restart, no MEMORY//OS
redesign. All verification re-run from clean environments after the changes.

| # | Fix | Where |
|---|---|---|
| 1 | Provider truth: central `providerPosture()` (lib/provider.ts) — REAL AGENT only for mode === "REAL AGENT"; DETERMINISTIC FALLBACK renders as such; unreachable → PROVIDER · UNKNOWN, never defaulted | Navigation, Workspace, System, Home; regression incl. unreachable-provider simulation |
| 2 | COMPLETED ≠ VERIFIED: dedicated `complete` tone (hollow amber ◆) vs VERIFIED (solid green ✓) | StateToken + CSS; regression asserts distinct data-tone |
| 3 | Conversational identity: assistant turns labeled ZORQ (amber); MEMORY//OS remains subsystem attribution | Chat.tsx |
| 4 | Metadata vs state: new `.z-meta` MetaBadge; CANONICAL/READ-ONLY/SEALED/provider mode no longer StateTokens | Primitives, Memory, Actions, Capabilities, Navigation, Home, Workspace |
| 5 | LOCAL no longer pulses (steady connection state); pulse reserved for genuinely ongoing operations | StateToken rules |
| 6 | Navigation IA: Work / Control / Runtime (duplicate "System" group removed, not hidden) | Navigation.tsx |
| 7 | Public metadata: package name `zorq-frontend` (json + lock), ZORQ description; README "UI has not started" corrected | package.json, package-lock.json, README.md |
| 8 | Automated chat QA: real input → real send control → response visible → rail evidence → durable memory → zero console errors | tests/zui1_browser_qa.py |
| — | Signature pattern: IntelligenceRail (OBSERVE→UNDERSTAND→ANALYZE→PROPOSE ∥AUTHORIZE→ACT→VERIFY), evidence-gated, authorization gate drawn | components/zorq/IntelligenceRail.tsx, Workspace |
| — | Home hierarchy: workspace band first screen; planes/surfaces/demos descend progressively | app/page.tsx |
| — | Mobile: fixed-header clearance, touch targets ≥40px verified, rail wraps, input reachable | QA assertions |

Refinement verification: typecheck ✅ · lint ✅ · build 12/12 ✅ · backend suite exit 0 ✅ ·
zroq-core 268/8/0 ✅ · browser QA **PASS** (9 desktop + 9 mobile routes, 0 console
errors, 0 overflow; all regressions green) · DOM visual audit: 0 clipped elements.
Evidence: `docs/zorq/qa/zui1-browser-evidence/` (21 screenshots + qa-results.json +
visual-audit.json). Pre-refinement screenshots preserved inside
`ZORQ-ZUI1-CURRENT-REVIEW.zip` for before/after comparison.

---

## 12. Final semantic refinement — intelligence rail (post-review, uncommitted)

**Correction:** the rail no longer treats MODEL_CALL / MODEL_REVISION / DEMO_PLANNER /
SAVE_MEMORY / MEMORY_MANAGER / ordinary response generation as PROPOSE evidence.

**Model:** two separated flows — INTELLIGENCE (OBSERVE → UNDERSTAND → ANALYZE →
DECIDE) and ACTION GATE (PROPOSE ∥ AUTHORIZE → ACT → VERIFY), each stage evidenced
only by an explicit, corresponding backend event in the CURRENT turn:

- DECIDE — evidenced only by an explicit decision record (TOOL_DECISION). A
  generated response alone never evidences it (proven: ordinary turn → dormant).
- PROPOSE — evidenced only by an actual action-proposal record
  (ACTION_PROPOSED…). No chat activity type represents one today → honestly
  dormant in every real conversation. A memory save is a memory operation, not
  an external action proposal.
- AUTHORIZE / ACT / VERIFY — only authorization / execution / verification
  records; none exist in chat activity → visibly closed.

**Bug found and fixed by the new regression:** /api/chat returns the thread's
ACCUMULATED activity (LangGraph checkpoint state), so events from earlier
turns (e.g. a previous TOOL_DECISION) falsely evidenced stages of the current
turn. Chat now passes the rail only the current-turn segment (from the last
LOAD_CONTEXT marker) — `currentTurnActivity()` in Chat.tsx. The per-turn
activity detail lists inside the conversation still show the accumulated list
(pre-existing MEMORY//OS-era display behavior; recorded as technical debt).

**Regressions (all in tests/zui1_browser_qa.py, all PASS):** ordinary response
≠ PROPOSE · SAVE_MEMORY ≠ PROPOSE · MODEL_CALL ≠ PROPOSE · MEMORY_MANAGER ≠
PROPOSE (cognition-only fixture) · ACTION_PROPOSED fixture → PROPOSE evidenced
· AUTHORIZE/ACT/VERIFY dormant in all four scenarios · ordinary turn → DECIDE
dormant · durable turn → DECIDE evidenced by its own TOOL_DECISION · chat flow
+ durable memory round trip intact · zero console errors. Rail groups render
as INTELLIGENCE / ACTION GATE with the ∥ authority gate between PROPOSE and
AUTHORIZE. 23 evidence screenshots.

## 13. Eleventh directive — system intelligence console (uncommitted)

The interface was transformed into a SYSTEM INTELLIGENCE CONSOLE: near-black
foundation, deep burgundy structural surfaces, vermilion for active/primary
signals, cyan for informational/telemetry, cool light-gray for structure.
No rainbow, no consumer chatbot styling, no marketing language.

**Visual system (frontend/app/globals.css).** Design tokens remapped at the
root: `--black #070506`, graphite ramp → burgundy ramp, `--accent` → cyan
`#45d4e0`, ZORQ tokens `--z-accent` vermilion `#e5533f`, `--z-ok` cyan,
`--z-warn #d9a03f`, `--z-fail #ff5c4d`, radius 3px. Panels gained corner-tick
brackets (`.z-panel` ::before/::after); page titles are compact mono
uppercase with a vermilion kicker; the body carries a fixed burgundy radial
with a faint 1px grid. A new instrument section styles buttons, fields,
chips, the ASK ZORQ console, rail flow semantics (cognition = cyan,
action = vermilion), `::selection`, the workspace console band, telemetry
strips, and the core visualization. Legacy hard-coded amber/red/cream/teal
literals in observatory panels (Portability, Research, WhyInspector) and the
MemoryCore canvas were converged onto the same tokens.

**Central intelligence visualization (components/zorq/CoreVisualization.tsx,
new).** Reusable radial SVG instrument (props: zorq, health, size, busy):
five plane arc segments styled by real plane status (implemented = solid
cyan, partially implemented = dashed, designed = gray dashed), a runtime
ring with real CONN/SYNC/VOICE/M-MODAL states, a rotating guide ring
(prefers-reduced-motion respected), four orbit nodes (MEMORY//OS canonical
authority, provider posture, audit integrity, capability count), and a core
disc with the connection state. Every value is real backend state; nothing
invented; neutral labels when data is unavailable. Rotation/pulse are tied
to real connection state, not decorative.

**Home (app/page.tsx, rewritten).** ZORQ wordmark + SYSTEM INTELLIGENCE
kicker/title + lede; CoreVisualization; IntelligenceRail (dormant contract
for turn=null); the OBSERVE/UNDERSTAND/ANALYZE/DECIDE contract strip via the
rail; OPEN THE WORKSPACE band; a real-value telemetry strip (CONNECTION,
MEMORY//OS, PROVIDER, SYSTEM, CAPABILITIES); three-panel live system state
(runtime, memory authority, control plane); five-planes grid; surface
directory grouped by WORK/CONTROL/RUNTIME; the three MEMORY//OS demos are
preserved.

**Navigation (components/Navigation.tsx).** WORK (Home, Workspace, Memory,
Observatory) / CONTROL (Actions, Capabilities, Audit, System) / RUNTIME
(Devices). Header restyled as a burgundy glass instrument bar with wordmark
+ SYSTEM INTELLIGENCE suffix; status strip unchanged in semantics
(connection, provider posture, MEMORY//OS authority).

**Command console (components/Chat.tsx).** The input is a technical console:
ASK ZORQ label, mono input (Enter submits), compact ◇ MIC voice control with
truthful unavailable state, ⏎ ENTER submit — no oversized consumer Send
button. Turn labels are mono YOU/ZORQ. Provider/model/thread/retrieval moved
into a telemetry strip above the transcript. The current-turn activity
slicing fix (`currentTurnActivity()`) is intact. Mobile fix: `size={1}` on
the input removes the intrinsic 171px min-content that caused a 63px
viewport overflow.

**Conversational surface (backend/app/agent/graph.py, demo planner else
branch).** Removed the repeated "Using what I remember…", "LOCAL DEMO
mode…", and "(Applied preference: …)" narration. COMMUNICATION_STYLE memory
now applies silently (concise/brief/short/terse/minimal → short refusal
variant). A personal-fact question with a strongly recalled record answers
"On record: {content}." — attributed to memory, never fabricated. Any other
general question without a provider gets the truthful refusal: "I do not
have a model provider configured, so I will not guess at that. You can ask
me what I have on record, or connect a provider for general questions."
Preserved unchanged: DETERMINISTIC FALLBACK contract on mission replies,
asks_recall branch ("Here is what I remember:"), durable-fact storage
("Stored that as a…"), explain/why branch, tool routing, grounding, and the
prohibition on the recalled-memory-as-generic-answer patch.

**Verification (all rerun after the transformation).**
- Backend: `python -m unittest discover -s tests` → 276 tests OK,
  8 skipped (known privilege-related; user's Windows baseline is 276 OK /
  7 skipped — the Linux sandbox has one additional environment skip).
  Backend pytest suite → 1087 passed, 24 skipped, exit 0.
- Frontend: tsc --noEmit clean, ESLint clean, next build 12/12 static pages.
- Browser QA (tests/zui1_browser_qa.py): VERDICT PASS — desktop 9/9 routes
  (truth strings + 0 console errors), mobile 9/9 routes (0px overflow,
  0 console errors), a11y intact (skip-link first focus, one h1 per route,
  0 images missing alt, all 9 nav links), forbidden-content scan empty.
- New conversational regressions: ordinary query reply contains NO
  "Using what I remember" / "LOCAL DEMO mode" / "Applied preference" and
  DOES contain the truthful no-provider refusal; explicit "what do you
  remember about ZORQ verification" → "Here is what I remember:" with the
  stored records; durable turn still stores and evidences DECIDE via its
  own TOOL_DECISION; ordinary turn leaves DECIDE + all action stages
  dormant; unreachable provider renders PROVIDER · UNKNOWN in the header.
- 24 evidence screenshots in docs/zorq/qa/zui1-browser-evidence/.

**Truthfulness preserved.** No authorization, capability, execution,
verification, device-trust, provider, or telemetry value is invented
anywhere in the new visuals; the core visualization and telemetry strips
render only authoritative backend values and stay neutral otherwise. The
confidential internal record remains absent from the UI, tests, docs, and
metadata. Provider truth (REAL AGENT / DETERMINISTIC FALLBACK / UNKNOWN)
is unchanged and lives in telemetry, not in conversation replies.

## 14. Windows QA fix pass — determinism + structural semantics (uncommitted)

First Windows browser QA run (same `tests/zui1_browser_qa.py`) returned FAIL
with three real defect classes; all were fixed in the implementation, with no
QA assertion weakened and no semantic check removed.

**Defect 1 — React hydration mismatch on `/` (real).** CoreVisualization
generated SVG path geometry from raw `Math.cos/sin` floats interpolated
directly into attributes. Node's V8 (SSR) and Chrome's V8 can differ by one
ULP in transcendental results, so server and client produced slightly
different path strings (e.g. `255.1815450763502` vs `255.18154507635023`) —
a hydration mismatch that only manifests when the two engines disagree (the
Linux sandbox pair happened to agree, which is why sandbox QA had passed).
FIX: every generated coordinate/stroke/offset is rounded to fixed 2-decimal
precision through a single `q()` helper before it is written into any SVG
attribute. No `Date.now()`, `Math.random()`, browser-only branches, or
layout-dependent values are used during render; state-dependent styling
remains separate from geometry. Verified: SSR HTML and hydrated DOM are
byte-identical on the geometry, zero console errors on `/` (desktop and
mobile). The fix is engine-independent — it holds for ANY Node/Chromium
pair, including Windows.

**Defect 2 — facade-dependent structural semantics.** Six surfaces gated
their semantic vocabulary behind live facade data. When the backend process
cannot compose zroq-core (`pip install -e .` at the repo root missing from
the backend virtualenv — the Windows condition), every facade surface
returns `available: false` and those panels vanished: /actions lost
PROPOSED, "No action records", EXECUTION ≠ VERIFIED OUTCOME, the
COMPLETED/VERIFIED chips and the READ-ONLY badge; /memory lost CANONICAL;
/capabilities lost SEALED, the ladder (VISIBLE/AUTHORIZED/EXECUTABLE) and
"Visibility is not permission"; /audit lost VERIFIED; /devices lost
NOT-CONFIGURED, "None enrolled" and DEVICE TRUST ≠ USER AUTHORIZATION;
/observatory lost Planes. FIX: structural semantics now render regardless
of facade availability — READ-ONLY/CANONICAL/SEALED `.z-meta` badges, the
lifecycle stepper, the status vocabulary with its COMPLETED (hollow amber,
tone `complete`) vs VERIFIED (solid check, tone `ok`) distinction, authority
invariants, the capability-ladder contract, devices' structural
empty-states/distinctions/vocabularies, audit verification states, and the
five-plane architecture (statuses still render from the facade; when it is
unreachable they render honestly as UNAVAILABLE — never invented). Genuinely
live values (current device identity, registry contents + digest, audit
events, runtime readouts) remain data-gated with honest
loading/error/unavailable states. README now documents the `pip install -e ..`
step that enables the facade in the backend virtualenv.

**Defect 3 — metadata/registration regressions (consequence of Defect 2).**
CANONICAL / READ-ONLY / SEALED must be `.z-meta` metadata, never state
chips; they were absent entirely under facade-down because the badges were
data-gated. Now always rendered through MetaBadge (`.z-meta`); the QA
metadata distinction (metadata ≠ runtime state) passes under both
conditions.

**Verification after the fixes (all rerun):** backend unittest 276 OK /
8 skipped (Linux; Windows baseline 276 OK / 7 skipped — known privilege
skips); `npm run typecheck` clean; `npm run lint` clean; `npm run build`
12/12; browser QA VERDICT PASS — 9/9 desktop routes 0 console errors with
all truths, 9/9 mobile routes 0px overflow and 0 console errors. A dedicated
facade-down simulation (all `/api/zorq/*` routes failing, `/api/health`
healthy — exactly the Windows condition) passes every previously failing
truth, the COMPLETED/VERIFIED tone distinction, and all three `.z-meta`
metadata checks, with zero console errors. graph.py was not modified in
this pass: contextual memory stays silent, explicit memory questions stay
source-backed, no-provider/no-grounding behavior stays truthful, and the
DETERMINISTIC FALLBACK contracts are intact (re-proven by QA).
