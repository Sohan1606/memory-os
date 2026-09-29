# ZORQ Z-UI.1 — Design Baseline & Architectural Reconciliation (WP-UI-0)

**Status:** WP-UI-0 record. Visual references: none confirmed (per owner directive —
the ZORQ visual language is derived from the product architecture and the quality
bar, not from an external reference set). Locked spec: §12 (Rev 2). Base: `bcdc3df`.

## 1. Reconciliation map (current → ZORQ target)

| Current route | Current components | Current functionality (real) | ZORQ target | Disposition | Data source | Security constraint |
|---|---|---|---|---|---|---|
| `/` | Hero, Section×N (problem/layers/retrieval/voice), ScrollSequence, LayerSwitcher, RetrievalDemo, VoiceDemo, Gallery | Cinematic MEMORY//OS showcase; live health/stats; real retrieval + speech→memory demos | **System Home** — ZORQ identity + live system state + surface directory; real-data demos preserved | TRANSFORM (marketing narrative + ScrollSequence + Gallery retired per §12.4.3 criteria) | `/api/health`, `/api/stats` if exists, facade `/api/zorq/status` | No fake state; identity = "ZORQ" alone (ZUI-27) |
| `/workspace` | Chat, CognitiveSurface (+ threads) | Real `/api/chat` turns, recalled-memory display, inline cognitive surface, threads | **ZORQ Workspace** — primary intelligence surface: conversation + contextual memory with provenance + active-context disclosure + response-control affordances + action-proposal presentation (authorization-aware) | TRANSFORM (Chat/CognitiveSurface preserved + extended) | `/api/chat`, `/api/v9/surface/turns`, `/api/conversations*`, facade actions state | Assistant text never fabricated client-side; interruption affordances reflect real runtime state only |
| `/memory` | MemoryGraph, MemoryTimeline, MemoryInspector, ConflictDemo, LayerSwitcher, store | Live memory store, filters, graph, audit timeline, versions/related, real consolidation + conflict resolution | **Memory & Context** — MEMORY//OS governance window; add ZORQ contextual-memory, historical-vs-current validity, source-vs-derived provenance, governance status | PRESERVE + TRANSFORM | `/api/memories*`, `/api/graph`, `/api/timeline` | MEMORY//OS authoritative; retrieval ≠ governance; provenance always visible |
| `/observatory` | 22 panels + primitives | Deep inspection with developer mode, honest Empty/error states | **Observatory** — preserved deep inspection; extended with ZORQ panels (action plane, capability registry, audit/verification, security epoch/sessions, memory-governance integration, voice readiness) | PRESERVE + EXTEND | existing panel APIs + facade endpoints | Panels render UNKNOWN/NOT CONFIGURED truthfully (existing discipline) |
| `/architecture` | Architecture.tsx | Static layer explainer + live health states + "Never faked" panel | **System** — ZORQ planes truth view with live per-plane status + runtime/provider detail; absorbs the architecture narrative | TRANSFORM | `/api/health`, `/api/provider`, `/api/cognition/status`, facade | Per-plane status truthful; no capability claims beyond backend |
| *(new)* `/actions` | — | — | **Actions & Verification** — lifecycle model, live action state (truthful empty until the action runtime is exposed), execution-origin labels, verification semantics (COMPLETED ≠ VERIFIED) | NEW (facade-gated) | facade `/api/zorq/actions` | GET-only facade; no execution path from UI; authorization boundaries explicit |
| *(new)* `/capabilities` | — | — | **Capabilities** — sealed registry contents with the VISIBLE→AVAILABLE→AUTHORIZED→EXECUTABLE ladder, risk, confirmation mode; visibility ≠ permission labeling | NEW (facade-gated) | facade `/api/zorq/capabilities` | No capability item executable without the full Action Plane flow |
| *(new)* `/audit` | — | — | **Audit** — real hash-chained ZORQ audit tail + MEMORY//OS events cross-link | NEW (facade-gated) | facade `/api/zorq/audit`, `/api/events` | Read-only; integrity status shown; no fabricated audit records |
| *(new)* `/devices` | — | — | **Devices & Connectivity** — current device, connection LOCAL, sync NOT-CONFIGURED, device trust ≠ authorization distinctions | NEW (facade-gated) | facade `/api/zorq/devices` | No fake device lists/sync; discovery ≠ permission |

**Infrastructure preserved unchanged:** `lib/api.ts` + `lib/types.ts` (extended, never
bypassed), `hooks/useMemoryStore` (subordinate read-model), `useMediaQuery`,
`useReducedMotion`, `useSpeech` (retained until the 3F runtime replaces it),
observatory primitives + all 22 panels, MemoryGraph/Timeline/Inspector/ConflictDemo,
LayerSwitcher, RetrievalDemo + VoiceDemo (retained until ZORQ-native equivalents
pass verification — §12.4.3), same-origin proxy config, build/typecheck/lint pipeline,
Playwright browser-QA discipline.

**Retired in WP-UI-8 (all four §12.4.3 criteria enforced there):** ScrollSequence +
`public/frames/` (cinematic marketing sequence), Gallery (showcase renditions),
CustomCursor (a11y conflict), `/` marketing narrative sections (replaced by the
truthful ZORQ identity surface).

## 2. Information architecture

Navigation is grouped by user intent, not backend modules:

```text
ZORQ [wordmark → home]
  PRIMARY    Workspace · Memory
  SYSTEM     Actions · Capabilities · Audit
  RUNTIME    Devices · System · Observatory
[header status strip: connection · provider · memory authority · epoch]
```

- Desktop: persistent slim header with grouped links + live status readout.
- Mobile: wordmark + status dot + menu button → full-screen grouped drawer with
  large touch targets; status strip collapses to a single live chip.
- Progressive disclosure: PRIMARY surfaces are task-oriented; SYSTEM surfaces
  explain governance; RUNTIME surfaces expose deeper truth (Observatory = advanced).

## 3. Design language — "the instrument"

Derived from what ZORQ is: a control surface over a live intelligence system.
Principles: calm dark field, typographic hierarchy, hairline structure, one
signal accent, monospaced instrument readouts, motion only as state feedback.

- **Field:** near-black warm-neutral foundation retained from the existing system
  (`#050506` → `#0a0a0d` → `#101014` surface steps). No glassmorphism, no
  decorative gradients, no particles.
- **ZORQ accent:** signal amber (`#e3b34c` family) — identity, interactive focus,
  active/working states. **MEMORY//OS subsystem accent:** the existing teal
  (`#6ee7d7`) retained inside memory surfaces — MEMORY//OS stays visible and
  credited as the memory authority (DQ-17). Two accents, two meanings, never mixed.
- **Semantic state colors** (always paired with a glyph + mono label — never
  color-only): VERIFIED/READY green; ACTIVE/EXECUTING/SYNCING amber (animated
  pulse, static under reduced motion); DEGRADED/WARN orange; FAILED/DENIED red;
  UNKNOWN violet (hollow marker); UNAVAILABLE/NOT-CONFIGURED neutral (hollow,
  dashed); OFFLINE slate; PENDING hollow amber. COMPLETED renders neutral-green
  but is ALWAYS visually distinct from VERIFIED (check-glyph + border weight).
- **Typography:** existing self-hosted system stacks retained (no remote fonts —
  performance + privacy). Refined scale: `.display` statements on home only;
  12/13px mono micro-labels (letter-spaced uppercase) for instrument readouts;
  14–15px body; 600-weight page titles; tabular numerics for metrics.
- **Structure:** 4/8px spacing grid; panels = hairline border + flat surface +
  6–10px radius; elevation only for overlays (dialogs/drawer); hairline rules
  between sections; 1px focus rings (amber, 2px offset) everywhere.
- **Motion:** 150–200ms ease-out micro-transitions; 250ms surface reveals;
  2s status pulse; skeleton shimmer for loading; all disabled under
  `prefers-reduced-motion` (global kill retained). Motion never implies success
  or authority the backend did not report.

## 4. State vocabularies (typed frontend contracts — §12.1.1)

`ConnectionState`, `ExecutionOrigin`, `SyncState`, `DeviceTrustState`,
`CapabilityLevel` (VISIBLE / AVAILABLE / AUTHORIZED / EXECUTABLE / VERIFIED-OUTCOME),
`ActionPhase` (PROPOSED → AUTHORIZATION → SNAPSHOT → LEASE → EXECUTION →
VERIFICATION → AUDIT), `SystemCondition` (LOADING/READY/ERROR/UNKNOWN/UNAVAILABLE/
NOT-CONFIGURED/NOT-IMPLEMENTED/OFFLINE/DEGRADED). Today's truthful values render
from the facade: connection LOCAL, sync NOT-CONFIGURED, current device only,
voice NOT-IMPLEMENTED (Phase 3F), multimodal NOT-IMPLEMENTED.

## 5. Facade contract (WP-UI-1, DQ-16 — GET-only, fail-closed)

| Endpoint | Exposes | Notes |
|---|---|---|
| `GET /api/zorq/status` | ZORQ package identity/version, in-process core boot state, security epoch, filesystem posture, session count, memory-governance integration (canonical authority, production-adapter contract version + verified state), runtime truth block (connection/execution/sync/voice/multimodal), capability summary, audit integrity | Read-only; no secrets; fail-closed to truthful UNAVAILABLE when zroq-core is not importable |
| `GET /api/zorq/capabilities` | Sealed registry manifests (id, version, operations, permissions, risk, confirmation mode, verification, cancellation, governance requirement, description) + registry digest + per-capability ladder (VISIBLE/AVAILABLE true; AUTHORIZED = session-bound, unknown via facade; EXECUTABLE false via facade) + visibility≠permission notice | No execution path |
| `GET /api/zorq/actions` | Action lifecycle model, live action records (truthfully empty until the action runtime is exposed), execution-origin vocabulary, one-click-action prohibition note | GET-only per DQ-16 |
| `GET /api/zorq/audit` | Real hash-chained audit tail of the in-process core (boot/lifecycle events) + chain integrity status + persistence scope note | Read-only; real events only |
| `GET /api/zorq/devices` | Current device identity (real), connection LOCAL, sync NOT-CONFIGURED, authorized/available device lists (truthfully empty), trust/capability vs authorization distinction labels | No fabricated devices |

All five endpoints: 200 + `available: true/false` shape; backend tests in
`backend/tests/test_zorq_facade.py`; browser verification in the Z-UI.1 QA suite.

## 6. Conflicts pre-declared (§12.9 classes)

No external reference set exists, so per-class conflict assessment is N/A; the
pre-declared classes remain enforced against this design itself (no one-click
irreversible actions, no fake voice/offline/sync/multimodal, UNKNOWN never styled
as success, client never authoritative, MEMORY//OS credited, motion/a11y rules).

## 7. Deviation register (from the directive's implied direction)

None yet — the visual language is original (no reference to deviate from). Any
self-identified deviation from the quality bar is recorded in the final report.
