# ZORQ Phase 3B.2 — Production MEMORY//OS Adapter Implementation

**Status:** IMPLEMENTED and verified (see [`ZORQ-PHASE3B2-VERIFICATION.md`](ZORQ-PHASE3B2-VERIFICATION.md)).
**Specification:** [`ZORQ-MASTER-IMPLEMENTATION-SPECIFICATION-v1.md`](ZORQ-MASTER-IMPLEMENTATION-SPECIFICATION-v1.md) §11 (Rev 2, locked).
**Baseline:** branch `zroq/canonical-migration`, commit `262159d` (canonical migration; zero tracked-file modifications outside the new files listed below).
**Package version:** 0.3.2 (DQ-12 resolution applied).

## 1. What was built

Phase 3B.2 wires the existing, unchanged ZORQ memory boundary to the real,
in-repository MEMORY//OS v10.2.0 backend at the service layer, in process:

- Canonical raw sources → backend `messages` table (owner-scoped by `user_id`,
  full ZORQ metadata JSON round-trip).
- Canonical governed memories → backend `MemoryService.create` under the
  backend's own duplicate/conflict governance (reinforce / supersede / create),
  with ZORQ source linkage via `memories.source = "zorq:<source_id>"` and
  thread linkage via `memories.thread_id`.
- Canonical conversation registry → backend `conversations` table (idempotent,
  owner-checked registration).
- Canonical retrieval → backend `messages` query (EXACT/TEMPORAL), real
  `MemoryService.search` (LEXICAL/SEMANTIC, semantic via Chroma/ONNX when
  available, truthful keyword fallback otherwise).
- ZORQ local `PersonalContinuityStore` demoted to a **derived view** (DQ-7):
  parallel write, governance inherited, never the production authority;
  serves only the modes the canonical store cannot (ZORQ project/entity/goal/
  decision relational refs, causal history, timeline enrichment) and is
  always labelled `derived-view` in `mode_status`.
- Deletion propagation to canonical store (messages, memories via
  `MemoryService.delete` — versions, relationships, embeddings, event log —
  and conversation registry) plus derived views, with truthful
  PARTIAL/UNKNOWN reporting for unverifiable targets (R-6).

## 2. Files added (complete list)

| File | Content |
|---|---|
| `src/zroq/adapters/__init__.py` | Adapter package (DQ-2 placement note + re-exports) |
| `src/zroq/adapters/memoryos_v10.py` | `OwnerIdentityMapping`, `MemoryOSV10Composition`, `ProductionMemoryOSAdapter`, `ProductionMemoryOSContractAdapter`, `compose_production_memoryos` |
| `tests/test_phase3b2_memoryos_adapter.py` | 20 contract tests (spec §11.6's 18 named tests + 2 supplementary) |
| `docs/zorq/ZORQ-G0-CANONICAL-GATE-VERIFICATION.md` | G-0 gate report |
| `docs/zorq/ZORQ-PHASE3B2-IMPLEMENTATION.md` | This document |
| `docs/zorq/ZORQ-PHASE3B2-VERIFICATION.md` | G-1…G-5 verification report |

Modified (documentation/version only): `pyproject.toml` (0.2.6 → 0.3.2),
`README.md` + `tests/README.md` (DQ-14 documentation drift),
`docs/zorq/ZORQ-MEMORYOS-ADAPTER-IMPLEMENTATION-v1.md` (stale
"BLOCKED BY ENVIRONMENT" status superseded — DQ-8 truthfulness),
`.gitignore` (internal-record ignore block from the Rev 2 phase),
`docs/zorq/ZORQ-MASTER-IMPLEMENTATION-SPECIFICATION-v1.md` (the locked Rev 2
spec itself, delivered earlier in this phase sequence).

**Not modified:** any `backend/` file, any `frontend/` file, any existing
`src/zroq` module (engine, kernel, contracts, action plane — all byte-identical
to `262159d`, enforced by `test_no_action_plane_changes_regression`).

## 3. Architecture

```text
PersonalContinuityEngine (unchanged)
        │  engine boundary (govern / store_source / retrieve / delete / export)
        ▼
ProductionMemoryOSAdapter (src/zroq/adapters/memoryos_v10.py)
   ├─ govern: owner capture policy (ZORQ) ∧ backend policy (MEMORY//OS),
   │          fail-closed mapping, egress/owner-isolation denials first
   ├─ store_source: MemoryService.create (durable, real policy)
   │                + canonical messages row (always, metadata round-trip)
   │                + local derived-view write (parallel, DQ-7)
   ├─ retrieve: canonical EXACT/TEMPORAL/LEXICAL/SEMANTIC
   │            + derived-view RELATIONAL/CAUSAL (labelled)
   ├─ delete: canonical + derived propagation, truthful statuses
   └─ export: canonical export + derived export, origin-labelled

ProductionMemoryOSContractAdapter (same module)
   └─ Phase 3A MemoryOSAdapterContract protocol (govern/retrieve/store/delete
      over domain-contract records), composed pair delegating to the above

MemoryOSV10Composition
   └─ lazy import of backend/app (sys.path), Database + VectorStore +
      MemoryService + policy module, read-only composition verification
```

### Why a composed pair for the two protocols

The Phase 3A `MemoryOSAdapterContract` protocol and the Phase 3B engine
boundary declare methods with identical names (`govern`, `retrieve`, `store`,
`delete`) but incompatible signatures. A single class cannot implement both;
`ProductionMemoryOSContractAdapter` therefore delegates to
`ProductionMemoryOSAdapter`. This is an implementation reality of the existing
contracts (both unchanged per §11.3), not a new API.

## 4. Governance mapping table (DQ-4)

Evaluation order (first match wins; every step fail-closed):

| # | Condition | Outcome | Reason |
|---|---|---|---|
| 1 | Backend unavailable / composition error / backend exception | `UNAVAILABLE` | real MEMORY//OS backend unavailable; governed operation fails closed |
| 2 | Non-local provider under `NO_EGRESS` | `DENY` | provider egress denied by local-only/no-egress policy |
| 3 | Owner not in allowed owner set | `DENY` | owner isolation denied |
| 4 | `store_conversation` / `store_source` / `store` + owner capture policy retains | `ALLOW` (+`retention_mode` constraint) | owner capture policy retained; canonical eligibility governed by the real MEMORY//OS policy at store time |
| 5 | same operations + owner capture policy does not retain (do-not-remember, temporary, policy block) | `DENY` | owner-policy reason verbatim |
| 6 | `retrieve` / `activate` / `delete` / `export` / `derive` / `timeline` | `ALLOW` | owner-scoped governed operation against the canonical store |
| 7 | unknown operation | `NOT_APPLICABLE` | operation is not governed by this adapter |

Additional fail-closed rules inside `store_source`:

- Backend `policy.evaluate` exception → `UNAVAILABLE` (nothing stored).
- `MemoryService.create` returns an unknown action → `UNAVAILABLE` with
  best-effort compensating delete (contradictory backend state never becomes
  ALLOW; nothing stored).
- Canonical write exception → `UNAVAILABLE` (raw-source row is written last;
  a created memory is compensated).
- `HOLD` is unreachable under the v1 deterministic policy and is never emitted;
  it is never treated as ALLOW (enforced by test).

### Capture policy mapping (DQ-5)

Owner commands (ZORQ `MemoryCapturePolicyEngine`, unchanged) decide
retention; the real MEMORY//OS policy engine decides canonical memory
creation (durability, category, importance, confidence):

| Input | Owner policy | MEMORY//OS policy | Canonical result |
|---|---|---|---|
| default message | retain | durable | message row + governed memory |
| default message | retain | transient | message row only (archive-every-turn, mirrors backend chat flow) |
| `explicit_remember` | retain (EXPLICIT_REMEMBER) | any | message row + governed memory (owner command channel ≡ backend "remember that" text channel) |
| `explicit_do_not_remember` | do not retain | — | DENY; nothing canonical, nothing derived |
| `temporary` | do not retain | — | DENY |
| sensitive (SECRET/…) | per owner `sensitive_memory` policy (default retain) | durable/transient | privacy class preserved in canonical metadata and on retrieval; Memory Firewall governs egress/activation unchanged |
| project-scoped | retain (PROJECT_SCOPED) | durable/transient | `project_id` preserved in canonical metadata; relational retrieval via derived view |

## 5. Identity mapping (DQ-3)

`OwnerIdentityMapping` pairs one ZORQ owner with one MEMORY//OS `user_id`
(explicit, no implicit user creation — the backend's auth-disabled local
configuration treats `user_id` as a scoping key). `device_scope` is recorded
so Z-DIST.1 can scope per device without contract change. Every canonical
query is `user_id`-scoped; cross-owner conversation-registry collisions fail
closed (`PermissionError` → `UNAVAILABLE`).

## 6. Local-first constraints (Rev 2 §11.2 item 7)

Composition targets a local backend root and local SQLite/Chroma paths only;
no network is assumed (tests run with `disable_embeddings=True`, exercising
the truthful keyword fallback; semantic mode activates automatically when the
local ONNX model is available — G-0 evidence). All records reuse
UTC-canonical timestamps, the event/ingest-time distinction, and versioned
contracts, so Z-LD.1/Z-DIST.1 extend rather than redesign.

## 7. Reported backend gaps (reported, not patched — §11.2)

1. **No per-message privacy/retention columns** in the backend `messages`
   table. Resolution: ZORQ privacy class and retention mode round-trip through
   the `metadata` JSON column (lossless for ZORQ-origin rows); ZORQ-side
   governance (firewall, egress, activation privacy blocks) remains enforced
   at the ZORQ boundary. Backend-native rows reconstruct with `PERSONAL` /
   `DEFAULT_RETAIN` defaults and `origin: memoryos-chat` provenance.
2. **No ZORQ relational references** (project/entity/goal/decision ids) in
   the canonical schema. Resolution: preserved in message metadata; RELATIONAL
   and CAUSAL retrieval modes served from the derived view, explicitly
   labelled `AVAILABLE:derived-view(...)` — never presented as canonical.
3. **No conversation title channel** through the engine adapter boundary.
   Resolution: canonical registry records the conversation id as the title;
   ZORQ titles live in the derived view.
4. **Backend messages use physical DELETE** while the ZORQ store tombstones.
   Resolution: deletion reports describe both stores truthfully; canonical
   absence is verified by direct query in tests.
5. **`temporal_start/temporal_end` deletion scopes** compare against the
   canonical row timestamp, which for ZORQ-origin rows is the event time
   (stored in `created_at` + `metadata.event_time`); documented, covered by
   the date-range deletion test.

## 8. Boundaries preserved (§11.5)

- MEMORY//OS is the canonical memory/governance authority; no competing
  authority was created; the harness is relabeled fallback (composition-level
  truthfulness via `integration_status` labels:
  `AVAILABLE_VERIFIED` / `AVAILABLE_UNVERIFIED` / `UNAVAILABLE`).
- `real_memoryos_verified` becomes `True` only via `mark_verified(...)`,
  which requires all gates G-1…G-5 and a report path (no fake status).
- Zero Action Plane changes (static scan + git-diff enforced by test).
- Memory never authorizes action (`MEMORY RETRIEVAL ≠ MEMORY GOVERNANCE ≠
  AUTHORIZATION`); emergency stop halts the kernel while memory governance
  continues truthfully without authorizing anything (DQ-8, enforced by test).
- `MEMORY RETRIEVAL ≠ MEMORY GOVERNANCE` preserved: retrieval ALLOW never
  implies activation, mutation, or authorization; governed operations are
  separately decided.
