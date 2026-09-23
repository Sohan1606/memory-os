# MEMORY//OS V10 — Cognitive Self-Maintenance Architecture

## Status

This document describes the V10.0.1 core hardening implemented on the `feature/v10-cognitive-self-maintenance` branch. Domain persistence, canonical events, services, API integration, portability participation, and adversarial tests are implemented. No autonomous timer or major frontend redesign was added. Status vocabulary in this document is deliberate: IMPLEMENTED means exercised by tests; PARTIAL means only canonical evidence-backed cases are implemented; NOT IMPLEMENTED means the required canonical source does not exist; NOT CONFIGURED means an optional capability is absent; NOT VERIFIED means the environment prevented a claim.

## Audit and extension map

| Existing canonical V9/V8 system | V10 extension point | V10 responsibility |
|---|---|---|
| `MeaningKernel` and `schemas.semantic` | `Cognition.personal_state` | V10 reads typed objects, modality, provenance, evidence and temporal scope; it does not create a second semantic layer. |
| `PersonalStateService` and immutable state versions | `cognition.v10` | Detects stale assumptions, compares active objects, creates canonical `QUESTION` objects for unknowns, and applies confirmed retirement through `PersonalStateService`. |
| `EventBus` / `cognitive_events` | `V10_MAINTENANCE` event family | Persists audit, finding, classification, proposal, health and application events on the existing stream. V10 does not create an event table. |
| V8/V9 `predictions` and `PredictionEngine` | `ModelErrorService` | Reads evaluated prediction/outcome rows and adds classification/bookkeeping. It does not evaluate or replace predictions. |
| V8 `ObservationLog` and V8 causality/learning records | model-error evidence references | V10 records evidence references and learning candidates; it does not promote unsupported learning or duplicate causality. |
| `AutonomyGovernor` | `MaintenanceProposalService.confirm` | Material maintenance stays proposed until explicit confirmation; governor disposition is recorded and BLOCKED is honored. |
| V8.5 `uid`, `Principal`, permission mapping and middleware | every `/api/v10` route | Reads/writes use the verified namespace and the existing cognition permissions. Direct IDs are queried with user and tenant scope. |
| `PortabilityService` | existing table allowlist/domain map | V10 tables and the canonical event stream participate in the existing deterministic package export/import path. |
| V9 `SurfaceLifecycle` | V10 surface vocabulary | The lifecycle vocabulary includes real maintenance stages for later runtime use; the first core pass does not invent a background trace. |

## Domain services

`backend/app/cognition/v10.py` contains the following services:

- `CognitiveDebtService`: evidence-backed debt detection and `OPEN → ACKNOWLEDGED → RESOLVED` / `DEFERRED` lifecycle. IMPLEMENTED types are stale assumptions, unvalidated predictions, stale world dependencies, unresolved decisions, and missing decision outcomes. Other spec categories remain NOT IMPLEMENTED where V9 has no canonical evidence.
- `ContradictionEngine`: canonical proposition comparison across subject, object, predicate, polarity, scope, time, context, correction/supersession, modality, and uncertainty. Shared-token/polarity heuristics cannot produce `TRUE_CONTRADICTION`; the engine returns `INSUFFICIENT_CONTEXT` when identity or evidence is incomplete and never mutates either compared object.
- `UnknownService`: persists an explicit unknown while linking it to a canonical V9 `QUESTION` object.
- `ModelErrorService`: compares a recorded prediction with an actual observation and distinguishes execution, observation, information, timing, causal, world, random, user-model, and unresolved classes.
- `MaintenanceProposalService`: stores bounded target/current/proposed state and applies only supported confirmed transitions.
- `CognitiveHealthService`: calculates explainable dimensions without an aggregate human score. Empty/sparse core evidence returns `INSUFFICIENT_EVIDENCE`; otherwise the summary aggregates only evaluable dimensions and exposes unevaluable dimensions explicitly. `MODEL_STABILITY` requires a sufficient evaluated prediction sample and never treats model-error count as a penalty.
- `SelfMaintenanceOrchestrator`: explicit `OBSERVE → AUDIT → DETECT → CLASSIFY → PROPOSE → UPDATE → RE-AUDIT` invocation. It is not a timer thread.

## Persistence

`Database` runs `SCHEMA_V10` additively after the existing V8/V9 schema scripts. The migration uses `CREATE TABLE IF NOT EXISTS` and never rewrites historical V9 rows. Tables are:

- `cognitive_debt`
- `contradiction_records`
- `unknown_records`
- `model_error_records`
- `maintenance_proposals`
- `cognitive_health_snapshots`
- `maintenance_runs`

Every V10 row stores `user_id`, `tenant_id`, timestamps, provenance/evidence references or explanation fields, lifecycle state where applicable, and correlation identifiers where the operation is event-driven. Contradiction records also preserve the proposition-analysis projection used for classification. Finding resolution is separate from any personal-state mutation; `RESOLVE_CONTRADICTION` closes only the finding unless a different explicitly authorized proposal reaches `PersonalStateService`.

Unknown records deliberately reference a V9 `QUESTION` object instead of copying a semantic object. Cognitive objects and their immutable history remain canonical.

## Deterministic boundary

The following are deterministic and authoritative:

- scope and ownership checks;
- lifecycle transitions;
- object/evidence references;
- stale-assumption, unvalidated-prediction, stale-world-dependency, unresolved-decision, and missing-outcome debt rules;
- proposition-aware contradiction scope/temporal/polarity/modality/uncertainty checks;
- explicit `UNKNOWN` results when context is insufficient;
- model-error classification flags;
- proposal authority and canonical mutation;
- health dimension aggregation;
- event and persistence writes.

No model output is used by V10 to authorize, mutate, promote facts, escalate certainty, or delete history. Model-assisted natural-language explanation is not connected to a second V10 authority path in this core pass.

## API surface

The implemented routes are:

- `GET /api/v10/cognitive-debt`
- `GET /api/v10/cognitive-debt/{id}`
- `POST /api/v10/cognitive-debt/{id}/acknowledge|resolve|defer`
- `GET /api/v10/contradictions`
- `GET /api/v10/contradictions/{id}`
- `POST /api/v10/contradictions/{id}/resolve|dismiss`
- `GET /api/v10/unknowns`
- `GET /api/v10/unknowns/{id}`
- `POST /api/v10/unknowns/{id}/resolve`
- `GET|POST /api/v10/model-errors`
- `GET /api/v10/model-errors/{id}`
- `GET /api/v10/cognitive-health`
- `GET /api/v10/cognitive-health/explain`
- `POST /api/v10/maintenance/audit`
- `GET /api/v10/maintenance/runs/{correlation_id}`
- `GET /api/v10/maintenance/proposals[/{id}]`
- `POST /api/v10/maintenance/proposals/{id}/confirm|reject|defer`

## Non-goals of this pass

There is no V10 scheduler, no hidden background intelligence claim, no frontend dashboard, no psychological/medical diagnosis, no opaque single score, and no automatic destructive cleanup. Unsupported proposal types remain blocked rather than being presented as applied.

## V10.0.1 hardening status

| Area | Status | Boundary |
|---|---|---|
| Proposition-aware contradiction matrix | **IMPLEMENTED** | Deterministic canonical comparison, explicit correction/evolution/scope/time/modality handling, and adversarial coffee/tea, spending, work context, negation, shared-token, and competing-interpretation coverage. |
| Evidence-backed Cognitive Debt | **PARTIAL** | Only categories backed by current canonical V9 rows are detected. Overdue commitments, contradictory goals/preferences, outdated principles, weakened claims, and material ambiguity are **NOT IMPLEMENTED** as debt rules without canonical lifecycle evidence. |
| Tenant-aware canonical lookups | **IMPLEMENTED** | V10 rows use user plus tenant; V9 predictions, decisions, world entities, and state-version lookups are checked through `auth_users.namespace → tenant_id`; legacy no-identity databases use the documented migration fallback. |
| Health explanations | **IMPLEMENTED** | Dimensions carry scores, explanations, finding references, and evidence sufficiency. `MODEL_STABILITY` is `INSUFFICIENT_EVIDENCE` rather than treating model errors as human or model instability. |
| Portability references | **IMPLEMENTED** | V10 export includes a maintenance schema marker and tenant scope; validation checks derived linked IDs, duplicate/reference integrity, user/tenant scope, and restore safety using the existing importer. |
| Autonomous scheduling, real model, new UI | **NOT CONFIGURED / NOT CONNECTED** | No scheduler, model-assisted authority path, or frontend redesign was added. |
