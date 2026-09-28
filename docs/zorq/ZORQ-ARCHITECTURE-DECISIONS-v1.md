# ZORQ Architecture Decisions v1

**Status label:** DESIGNED. This document records architectural decisions, conflict resolutions, unresolved risks, and the architecture quality gate.

## Architecture decision records

### ADR-001 — ZORQ is an integrated personal intelligence + continuity + action OS
**Decision:** define ZORQ as an integrated system, not a chatbot, browser agent, memory DB, automation tool, or agent collection.
**Status:** DESIGNED.

### ADR-002 — Phase 2.6 Action Plane remains authoritative
**Decision:** all real-world execution flows through authorized → snapshot → lease → device execution → verification → audit.
**Status:** IMPLEMENTED/VERIFIED for standalone Phase 2.6 slice; DESIGNED for future capabilities.

### ADR-003 — MEMORY//OS remains canonical memory-governance authority
**Decision:** ZORQ uses a versioned adapter boundary and does not fork or replace MEMORY//OS.
**Status:** DESIGNED; production integration NOT VERIFIED.

### ADR-004 — Source preservation is mandatory
**Decision:** raw source archive, derived memory, current interpretation, and historical view remain distinct.
**Status:** DESIGNED.

### ADR-005 — Vector search is not sufficient memory
**Decision:** exact, semantic, temporal, relational, and causal retrieval are required.
**Status:** DESIGNED.

### ADR-006 — Memory Firewall controls provider context
**Decision:** send only required, allowed, minimized memory to providers.
**Status:** DESIGNED.

### ADR-007 — ResponseCursor enables interruption continuity
**Decision:** response position and semantic state are explicit runtime objects.
**Status:** DESIGNED.

### ADR-008 — Barge-in STOP uses local high-priority path
**Decision:** STOP/PAUSE must not require full LLM reasoning.
**Status:** DESIGNED; implementation DEFERRED.

### ADR-009 — Controlled Evolution replaces auto-upgrading brain
**Decision:** knowledge may update, but behavioral/security changes require proposal, approval, canary, monitoring, rollback.
**Status:** DESIGNED.

### ADR-010 — No universal computer-control tool
**Decision:** all future OS/browser/app abilities are bounded capabilities with grants, policy, verification, and audit.
**Status:** DESIGNED; universal tool FORBIDDEN.

## Architecture quality gate audit

| Potential conflict | Resolution |
|---|---|
| MEMORY//OS governance vs ZORQ retrieval | Retrieval is governed by MEMORY//OS adapter; ZORQ indexes are derived views and inherit governance. |
| Proactive suggestion vs owner authority | Suggestions are clearly separated from authorized actions. |
| Voice convenience vs high-assurance authentication | Voice may help interaction; sensitive authority still requires policy-defined auth/confirmation. |
| Historical truth vs current truth | Temporal validity windows and states preserve both. |
| Memory retention vs deletion | Deletion propagation covers raw/derived/index/cache/backups and reports partial/unknown/retained states. |
| Model intelligence vs deterministic authority | Models propose; Action Plane authorizes and verifies. |
| Continuous evolution vs controlled security changes | Evolution cannot silently modify identity, grants, emergency stop, audit, Action Kernel, or secrets. |
| Memory retrieval vs current permission | Retrieved preference/history never creates current authorization. |
| Stop speaking vs stop action | Interaction Plane stops speech; Action Plane handles action cancellation. |
| External source vs truth | Source claims remain separate from ZORQ inferences and verification. |

## Contradiction / unresolved-risk register

| Risk | Status | Mitigation / future work |
|---|---|---|
| Production MEMORY//OS behavior unavailable in this repo | NOT VERIFIED | build adapter in Phase 3B and independently verify. |
| Long-term deletion across backups is hard | DESIGNED/NOT VERIFIED | explicit deletion propagation and retained-by-policy reporting. |
| Cross-device trust not implemented | DEFERRED | device enrollment/revocation/key architecture before use. |
| Voice barge-in latency not proven | DEFERRED | implement local path and test latency in Phase 3F. |
| Browser/GUI prompt injection remains complex | DEFERRED | structured APIs first, DOM/accessibility layer, visual fallback only with confirmation. |
| Evolution poisoning | DESIGNED/NOT VERIFIED | canary/rollback/audit and no direct security mutation. |
| Exact lifetime recall may conflict with privacy/deletion | DESIGNED | retention policy and deletion controls override convenience. |
| External verification unavailable for some actions | DESIGNED | return UNKNOWN/PARTIAL, never fabricated VERIFIED. |
| Platform-specific Windows semantics | NOT VERIFIED | actual Windows host validation required. |

## Final principle

ZORQ should become more intelligent without becoming less governable; remember more without losing user control; become proactive without silently acquiring authority; act more powerfully without collapsing thought, authorization, execution, and verified outcome; adapt without uncontrolled self-modification; preserve authorized history without confusing historical truth with current truth; and remain interruptible without losing continuity.
