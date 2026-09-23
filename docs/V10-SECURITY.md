# MEMORY//OS V10 Security and Authorization

## Security model

V10 reuses the V8.5 production-trust boundary. The FastAPI middleware authenticates the request and binds a verified `Principal`; V10 route handlers call the existing cognition read/write permissions and resolve the namespace through `uid()`. In `AUTH_MODE=required`, caller-supplied `user_id` values are ignored.

Every V10 persistence lookup is constrained by the verified `user_id` and the tenant resolved from the canonical `auth_users.namespace` mapping. A guessed V10 ID returns not-found outside its scope. V10 events remain on `cognitive_events`; their payload includes the tenant scope for V10 audit correlation.

## Protected operations

- Read routes require `cognition.read`.
- Lifecycle, audit, model-error recording, and proposal routes require `cognition.write`.
- Proposal confirmation requires an explicit confirmation body.
- `AutonomyGovernor` is consulted before proposal application. An irreversible or BLOCKED action is not silently applied.
- A proposal is not an application: `PROPOSED`, `CONFIRMED`, `APPLIED`, `REJECTED`, `DEFERRED`, and `BLOCKED` are distinct states.
- Repeated confirmation of an already terminal proposal does not apply it a second time.
- Confirmed stale-assumption maintenance retires the canonical assumption through `PersonalStateService`; it never deletes the earlier object or its versions and never retires the later evidence object.

## Evidence and privacy

V10 records object IDs and bounded evidence references rather than creating a shadow evidence store. Provenance distinguishes `USER_STATED`, `USER_CONFIRMED`, `SYSTEM_OBSERVED`, and `SYSTEM_DERIVED` where they are used by the canonical records. Missing evidence becomes `UNKNOWN` or `INSUFFICIENT_CONTEXT`; it does not become a confident inference.

Health is a description of model structure, not a judgement of the person. V10 does not diagnose health, personality, intelligence, or mental state. Event summaries avoid private reasoning and do not expose hidden chain-of-thought.

## Coverage

The focused V10 suite exercises additive schema creation, stale-debt ownership, proposal confirmation, unknown handling, model-error boundaries, correlated event payloads, and portability. Existing V8.5 authorization/isolation suites remain green on this branch, including direct-ID, user-substitution, cross-tenant, session and permission checks.

Real-model status for V10 is **NOT CONNECTED**: the V10 authority path is deterministic and no real model was connected or claimed as evidence.

## V10.0.1 hardening status

**IMPLEMENTED:** direct-ID reads, user substitution, proposal target ownership, confirmation replay, evidence references, and V9 canonical prediction/decision/world lookups are scoped by verified user and resolved tenant. Legacy V9 tables without a tenant column are bounded by the canonical `auth_users.namespace` mapping; V10 rows additionally require their tenant column. Cross-tenant references are rejected before proposal application or portability restore.

**IMPLEMENTED:** model-error classes remain distinct (`USER_MODEL_ERROR`, `WORLD_MODEL_ERROR`, `TIMING_ERROR`, `CAUSAL_MODEL_ERROR`, `EXECUTION_ERROR`, `OBSERVATION_ERROR`, `MISSING_INFORMATION`, `RANDOM_OUTCOME`, and `UNRESOLVED`). Non-`UNRESOLVED` classifications require an evidence reference or explicit evidence flag. This is evidence bookkeeping, not a judgement of the person.

**IMPLEMENTED:** `contradiction.finding_resolved` explicitly records that only the finding changed. Underlying personal state remains historical and unchanged until a separate authorized transition reaches `PersonalStateService`.

**NOT VERIFIED:** browser security/UI behavior could not be exercised in this sandbox because Chromium cannot load `libnspr4.so`. **NOT CONNECTED:** no real model was used as V10 authority evidence.

## Final tenant-scope consistency correction

**IMPLEMENTED:** every `maintenance_proposals` mutation now includes `id AND user_id AND tenant_id`, including expiry updates and final `APPLIED` updates. The tenant is resolved from the canonical V8.5 identity mapping through the V10 service context; caller-supplied tenant values are not accepted as authority.

The final audit also verified tenant predicates for all V10 finding updates and lifecycle mutations: `cognitive_debt`, `contradiction_records`, `unknown_records`, `model_error_records`, `maintenance_proposals`, `cognitive_health_snapshots` reads, and `maintenance_runs`. Existing canonical prediction, decision, world, and personal-state access continues through the verified user/tenant boundary or the canonical V9 service.

The regression suite proves cross-tenant direct-ID access, expiry mutation, confirmation, and application attempts are rejected while same-tenant proposal defer/confirm/application remains functional.
