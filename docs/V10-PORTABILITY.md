# MEMORY//OS V10 Portability

V10 participates in the existing `PortabilityService`; it does not create a second export format or recovery engine.

## Export participation

The existing allowlist and domain map include:

- `cognitive_debt`
- `contradiction_records`
- `unknown_records`
- `model_error_records`
- `maintenance_proposals`
- `cognitive_health_snapshots`
- `maintenance_runs`

V10 records are included in the existing `maintenance` domain and in the dependent `semantic_state`/event selection where applicable. The canonical `cognitive_events` rows already included by the existing dependency graph carry V10 event references and correlation IDs. Canonical V9 cognitive object IDs and evidence references remain unchanged.

Exports are deterministic JSON members inside the existing bounded ZIP package. The existing manifest, SHA-256 integrity file, package limits, sensitive-key redaction, and user-owned path are unchanged.

## Import and restore

V10 records use the same staged, validated, dry-run, confirmed restore lifecycle as V9. The existing importer only accepts allowlisted tables and user-scoped rows; live state is not changed during staging or validation. Restore is performed through the existing rollback-safe transaction and emits the existing portability events.

The V10 core pass does not add a special alternate importer. This is intentional: adding a second importer would violate the canonical portability boundary. The existing importer now performs deterministic V10 linked-reference and tenant diagnostics before restore.

## Schema compatibility

The package schema remains `9.0` so existing V9 export/import compatibility and regression contracts are preserved. V10 tables are additive members of that package and are not a claim that a V9 package contains V10 state. V10 rows are absent from older packages and remain absent rather than being fabricated.

## V10.0.1 hardening status

The previous follow-up is now closed for the implemented boundary. **IMPLEMENTED** checks include:

- `maintenance_schema_version: 10.0.1` and tenant scope in the manifest;
- V10 rows filtered by the authenticated owner and tenant;
- linked debt object/evidence IDs, contradiction sides, unknown references, model-error references, and proposal targets resolve in-package or in the same canonical owner namespace;
- missing, duplicate/divergent, cross-user, and cross-tenant references are rejected before restore;
- a V9-shaped database can be reopened, receive additive V10 tables, export maintenance rows, validate them, and restore them through the existing transactional importer.

The package top-level `schema_version` remains `9.0` for established V9 compatibility. This is not a claim that older V9 packages contain V10 state: absent V10 members stay absent. V10-capable packages identify their additive payload with `maintenance_schema_version`.
