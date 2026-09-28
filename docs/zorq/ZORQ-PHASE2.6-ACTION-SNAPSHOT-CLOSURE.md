# ZORQ Phase 2.6 — Action Snapshot + Execution Integrity Closure

**Package version:** 0.2.6
**Status:** Phase 2.6 action-snapshot and execution-integrity closure
**Readiness:** hardened standalone control-plane slice; **not production-ready**
**Windows validation:** tests present YES; executed here NO; security validation NOT VERIFIED

## Trust-boundary invariant

> Once an action crosses the authorization boundary, ZORQ executes an immutable snapshot of the authorized action. Caller-owned mutable objects are never authoritative for execution.

Normative flow:

```text
UNTRUSTED ActionRequest
  -> canonicalize + deep-freeze
  -> ActionSnapshot
  -> digest / idempotency / capability / grant / governance / confirmation / authorization / timeout / lease / Device Agent / verification / audit
```

The original `ActionRequest` is treated as untrusted caller-owned input after snapshot creation. The exact authorized snapshot is the exact state that reaches the execution boundary.

## Security-relevant snapshot fields

`ActionSnapshot` captures every security-relevant `ActionRequest` field:

- `action_id`
- `task_id`
- `owner_id`
- `principal_id`
- `device_id`
- `capability_id`
- `capability_version`
- `operation`
- `parameters`
- `purpose`
- `risk`
- `expected_effect`
- `verification_requirement`
- `idempotency_key`
- `created_at`
- `timeout_seconds`

## Implementation summary

### Contracts

`src/zroq/contracts.py` now defines:

- `ActionSnapshot`
- `freeze_action_value()`
- `action_snapshot()`
- a shared deterministic action digest helper used by `ActionRequest` and `ActionSnapshot`

Nested action values are recursively copied and frozen:

- dictionaries and mappings become read-only mapping proxies containing frozen values;
- lists and tuples become tuples containing frozen values;
- sets and frozensets become frozensets containing frozen values;
- scalar values are copied/retained only where supported as stable action values;
- unsupported values fail snapshot creation and therefore fail closed before authorization/execution.

### Action Kernel

`src/zroq/action_kernel.py` snapshots incoming actions at the Kernel entry point. The rebound internal value is the snapshot and is used throughout:

- digest computation;
- idempotency conflict/replay checks;
- capability registry lookup;
- grant lookup/accounting;
- memory governance;
- confirmation binding;
- policy/authority authorization;
- timeout ceiling calculation;
- lease issuance;
- Device Agent dispatch;
- postcondition verification;
- audit finalization.

If snapshot creation fails, the Kernel returns a failed action result and does not dispatch.

### Device Agent

`src/zroq/device_agent.py` now requires `ActionSnapshot` at runtime. Direct calls with caller-owned mutable `ActionRequest` objects are rejected with no side effect. Emergency-stop/cancellation state is still honored first and returns a cancelled observation.

This makes the Device Agent execution contract explicit: execution receives immutable snapshots, not mutable caller-owned requests.

### Confirmation and idempotency

Confirmations validate against snapshot digests, not mutable caller state. A confirmation for action A does not silently approve mutated action B.

Idempotency now binds to the snapshot digest:

- same logical snapshot + same idempotency key can replay according to policy;
- changed snapshot + same idempotency key is rejected as an idempotency conflict before dispatch.

## Explicit non-claims

Phase 2.6 does **not** claim:

- cryptographic immutability;
- cross-process issuer-only authentication beyond the existing in-process lease proof model;
- a Python sandbox against arbitrary code inside the same interpreter;
- production readiness;
- Windows security verification;
- MEMORY//OS production integration;
- persistent memory, voice, browser automation, GUI/computer vision, cloud integrations, proactive daemons, self-improvement, or generic shell/PowerShell/interpreter execution.

## Boundary of closure

The closed gap is the high-priority in-process aliasing flaw where nested mutable action parameters could change after authorization and before execution. Phase 2.6 ensures that the object authorized by ZORQ is the same immutable-by-value object passed into the Device Agent and verified afterward.
