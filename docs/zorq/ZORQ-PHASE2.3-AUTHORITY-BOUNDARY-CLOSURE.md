# ZORQ PHASE 2.3 — AUTHORITATIVE CAPABILITY + GRANT BOUNDARY CLOSURE

**Scope:** control-plane closure only.
**Production status:** implementation hardening slice, not production security approval.

## Closed weakness

Phase 2.2 still allowed direct callers of `ActionKernel.execute` to provide a `CapabilityManifest` and `PermissionGrant` as trusted inputs. An adversarial in-process caller could build an unregistered low-risk/no-confirmation/no-governance manifest and a forged grant, then cause real filesystem mutation.

Phase 2.3 closes this normal API weakness by making caller-supplied manifests/grants non-authoritative.

## New authority flow

```text
MODEL/PROVIDER -> proposal only
PLANNER        -> plan only
AUTHORITY      -> decision using authoritative policy
ACTION KERNEL  -> resolves manifest + grant internally, issues lease
DEVICE AGENT   -> verifies lease + trusted execution table, performs bounded execution
VERIFIER       -> checks postcondition/evidence
AUDIT          -> records append-only hash chain
```

The Kernel normal execution API is:

```python
kernel.execute(action, session, confirmation=None)
```

It does not accept manifest or grant arguments.

## Authoritative capability registry

- Capability manifests are registered only during trusted composition.
- Registry is sealed after startup.
- Runtime capability registration is denied.
- Registry resolution fails closed for unknown, disabled, retired, or unavailable capabilities/operations.
- `CapabilityManifest` nested mapping fields are copied into immutable mapping proxies during registration.
- Returned manifest mappings cannot be modified through normal Python mapping operations.

## Authoritative grant authority

`GrantAuthority` is the Kernel's grant source. Installed grants bind:

- grant ID;
- owner ID;
- principal ID;
- capability ID;
- operation;
- capability version;
- authoritative manifest digest;
- policy version;
- allowed filesystem roots;
- purpose;
- risk ceiling;
- confirmation mode;
- max calls;
- expiry;
- active state.

The Kernel resolves a grant using the authenticated session principal plus action capability/operation and the authoritative manifest version. A caller-created substitute grant cannot expand scope or reset max calls because it is never read by the Kernel.

## Authoritative policy inputs

The following fields are not trusted from caller-supplied objects:

- risk;
- confirmation requirement;
- memory-governance requirement;
- resource limits;
- grant call budget;
- capability version.

The effective values come from sealed registry, sealed grant authority, and device policy ceilings.

## Device Agent defense-in-depth

The Device Agent is still not the full authorization authority. It receives a one-use lease from the Kernel and verifies it before execution. Phase 2.3 adds an immutable execution capability table derived from trusted startup composition. The agent rejects execution for a capability/version/operation not present in that table, even if a caller somehow constructs an action object.

## Resource limits

Effective filesystem/device limits are computed from trusted manifest resource limits and configured device policy hard ceilings. The device policy never expands beyond configured hard ceilings, and read output remains fail-closed when the result exceeds the effective ceiling.

## What is not claimed

- This does not protect against arbitrary malicious code already executing with access to trusted Python internals.
- Python private attributes and mapping proxies are not a sandbox.
- HMAC remains issuer-authenticated only within the trusted in-process control plane; it is not asymmetric issuer-only authentication across an independent process boundary.
- Windows validation remains present but not independently executed on a real Windows host in this run.
- Passing tests do not make this production-ready.
