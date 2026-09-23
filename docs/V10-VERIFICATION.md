# MEMORY//OS V10.0.1 Core Hardening Verification

Verified in the local workspace on 2026-09-23, branch `feature/v10-cognitive-self-maintenance`. The hardening pass changed backend services, additive persistence/migration, EventBus vocabulary, portability validation, focused tests, and documentation only. No UI redesign or autonomous scheduler was added.

## Status vocabulary

- **IMPLEMENTED** — exercised by a passing deterministic or integration test.
- **PARTIAL** — only the canonical evidence-backed subset is implemented.
- **NOT IMPLEMENTED** — the current canonical V9 data model does not support the category without fabrication.
- **NOT CONFIGURED** — an optional capability is absent in this workspace.
- **NOT VERIFIED** — the environment prevented a valid verification claim.

## Focused V10 gate

```text
PYTHONPATH=backend pytest -q backend/tests/test_v10_cognitive_maintenance.py
23 passed
```

The focused tests use the real `Runtime`, `Database`, `Cognition`, V9 `PersonalStateService`, V9 prediction/decision/world tables, canonical EventBus, Autonomy Governor, V10 persistence, orchestrator, and existing `PortabilityService`.

Coverage includes:

- additive V10 schema and a V9-shaped reopen/migration preserving legacy rows;
- proposition-aware contradiction comparison for subject, predicate, object, polarity, scope, time, context, correction/supersession, modality and uncertainty;
- adversarial coffee/tea, saving/vacation spending, work-from-home/team-office, explicit correction, evolution, hypothetical, uncertain, subject/object/time/scope, negation, shared-token, and competing-interpretation cases;
- stale-assumption, unvalidated-prediction, stale-world-dependency, unresolved-decision, and missing-outcome debt where canonical V9 evidence exists;
- explicit documentation/tests for unsupported debt categories rather than fabricated detections;
- direct-ID/user-substitution and proposal target ownership checks;
- non-`UNRESOLVED` model-error evidence requirements and preserved error-class distinctions;
- `INSUFFICIENT_CONTEXT`/UNKNOWN behavior and explainable health dimensions;
- model errors treated as learning evidence rather than automatic model/person instability;
- health summary rule: no canonical evidence or insufficient core evidence yields `INSUFFICIENT_EVIDENCE`; otherwise only evaluable dimensions contribute to the aggregate, while `evaluable_dimensions` and `unevaluable_dimensions` are exposed explicitly;
- correction/supersession pair semantics: `corrects_object_id`, `supersedes_object_id`, and `superseded_by` must identify one of the two compared objects; explicit value evolution requires same-type objects; `temporary_exception` metadata is honored only for a matching opposite-polarity proposition pair; `PREDICTIVE_TRACKING` counts only `correct`/`incorrect` evaluated outcomes and excludes `expired`/`cancelled` records;
- finding-only contradiction resolution and bounded `RESOLVE_CONTRADICTION` confirmation;
- canonical typed correlated events with tenant payloads and idempotent maintenance runs;
- V9→V10 export, tenant/reference validation, duplicate/reference safety, restore, and historical-state preservation.

## Regression gates

```text
PYTHONPATH=backend pytest -q
987 passed, 10 skipped, 13 deselected, 4 warnings
```

The full backend command exited 0. The 23 skips are environment-dependent real-model/Ollama tests. The four warnings are existing `PytestUnknownMarkWarning` entries for repository `slow` markers; no failure was hidden or weakened.

The relevant V9/V8.5 suites and portability tests were also run during hardening and passed. The full gate above is the final regression result and includes the focused V10 additions.

## Frontend and browser

Frontend verification previously passed without frontend changes:

```text
cd frontend && npm ci && npm run typecheck && npm run lint && npm run build
# typecheck clean; lint clean; production build generated 8/8 static pages
```

Browser QA was attempted with Playwright and Chromium. Chromium could not launch because the sandbox lacks `libnspr4.so`. Browser status is **NOT VERIFIED**, not PASS. No browser claim is substituted with static build results.

## Portability

**IMPLEMENTED:** V10 tables are in the existing allowlist/domain map; exports identify `maintenance_schema_version: 10.0.1` and tenant scope; validation checks V10 linked IDs, user/tenant scope, package integrity, and restore references; tests exercise a V9-shaped reopen, V10 export, validation, and restore. The top-level package schema remains `9.0` for V9 compatibility, and older packages do not gain fabricated V10 rows.

## Real-model status

**NOT CONNECTED / NOT VERIFIED.** No real model was connected for V10. Deterministic V10 tests are not reported as real-model evidence. Existing Ollama-dependent tests remain skipped under their explicit environment gate.

## Intentional limitations

- Cognitive Debt is **PARTIAL**: overdue commitments, contradictory goals/preferences, unresolved corrections, outdated principles, weakened claims, and material ambiguity remain **NOT IMPLEMENTED** without a canonical V9 lifecycle/evidence source.
- Autonomous scheduling is **NOT CONFIGURED**; audits are explicit API/service calls.
- Model-assisted explanation is **NOT CONNECTED**; deterministic authority remains the safe path.
- No frontend dashboard, futuristic visualization, mock functionality, or unrelated product feature was added.
