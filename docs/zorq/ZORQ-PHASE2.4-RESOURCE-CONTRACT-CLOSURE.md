# ZORQ PHASE 2.4 — RESOURCE CEILING + CONTRACT INTEGRITY CLOSURE

**Status:** Phase 2.4 resource-ceiling and contract-integrity closure; hardened standalone control-plane slice; not production-ready.
**Scope:** final control-plane hardening pass before independent review. No Phase 3 functionality.

## Closed findings

Independent review identified three Phase 2.3 issues:

1. trusted capability timeout was not enforced against caller-supplied `ActionRequest.timeout_seconds`;
2. manifest nested mappings were only shallowly frozen despite documentation describing immutable manifest state;
3. several Phase 2.3 authority tests proved rejection mainly by invoking the removed legacy 5-argument Kernel API and catching `TypeError`.

These findings were reproduced before source changes in `ZORQ-PHASE2.4-PRE-HARDENING-GAP-BASELINE.md` and closed in Phase 2.4.

## Timeout authority model

`ActionRequest.timeout_seconds` is caller input. It is not policy authority.

Phase 2.4 distinguishes:

- **caller-requested timeout:** untrusted requested runtime window; may narrow execution;
- **trusted maximum timeout:** authoritative ceiling derived from capability manifest, trusted device policy, and remaining validated session lifetime.

The Kernel computes:

```text
authoritative_ceiling = min(
    authoritative_manifest.timeout_seconds,
    device_policy.max_operation_seconds,
    session_remaining_time
)

effective_timeout = min(
    caller_requested_timeout,
    authoritative_ceiling
)
```

If no separate device policy exists, the manifest/session ceiling would still apply. This implementation includes `ResourceLimits.max_operation_seconds` as trusted device policy.

Lease expiry uses `effective_timeout` directly. The previous caller-controlled `timeout_seconds + 5` lease window is removed.

## Deep manifest immutability

`CapabilityRegistry.register` now recursively freezes manifest-owned structures:

- mappings become `MappingProxyType` containing recursively frozen values;
- lists/tuples become tuples containing recursively frozen values;
- sets become frozensets;
- original caller-owned nested structures are not aliased into the stored manifest.

This is in-process object-integrity hardening only. It is not cryptographic immutability and not a sandbox against arbitrary malicious code already executing inside the Python interpreter.

## Current Kernel API proof

`ActionKernel.execute` normal API remains:

```python
execute(action, session, confirmation=None)
```

Phase 2.4 tests inspect the signature directly and assert there are no `manifest` or `grant` parameters. Authority tests now use hostile `ActionRequest` objects and current 3-argument Kernel calls instead of treating `TypeError` from the removed legacy API as the security result.

## ActionRequest field-integrity review

| Field | Classification | Phase 2.4 handling |
|---|---|---|
| `capability_id` | untrusted selector | resolved through sealed registry; unknown fails closed |
| `capability_version` | untrusted selector | validated against authoritative manifest/grant version |
| `operation` | untrusted selector | resolved through sealed registry and Device Agent trusted table |
| `parameters` | untrusted input | bounded by grant scope and Device Agent filesystem policy |
| `purpose` | untrusted purpose label | enforced when authoritative grant binds purpose |
| `risk` | untrusted caller value | must match authoritative manifest risk |
| `expected_effect` | untrusted caller value | must match authoritative manifest description |
| `verification_requirement` | untrusted caller value | must match authoritative manifest verification profile |
| `timeout_seconds` | untrusted caller value | can only narrow effective timeout; cannot expand trusted ceiling |

## Resource limit review

| Limit | Source | Enforcement layer | Expansion prevention |
|---|---|---|---|
| `max_file_bytes` | trusted manifest + device policy min | DeviceSecurityPolicy / DeviceAgent create/read | caller parameters cannot raise policy |
| `max_output_bytes` | trusted manifest + device policy min | DeviceSecurityPolicy read output check | fail-closed before returning oversized content |
| `max_directory_entries` | trusted manifest + device policy min | DeviceAgent directory inspection | fail-closed if complete listing would exceed ceiling |
| `timeout_seconds` | trusted manifest + device policy + session remaining | Kernel lease expiry and idempotency wait ceiling | caller timeout cannot extend lease |
| `max_path_length` | device policy | DeviceSecurityPolicy canonical path check | caller path rejected if too long |

No generic-command timeout metadata remains; generic command/application execution remains absent.

## Direct-call adversarial posture

| Attack | Defense classification |
|---|---|
| forged `ActionRequest.risk` | deterministic validation against authoritative manifest |
| forged `ActionRequest.timeout_seconds` | authoritative timeout ceiling derived by Kernel |
| forged capability version | authoritative lookup + grant binding |
| unknown capability | authoritative registry lookup fails closed |
| unsupported operation | authoritative registry + Device Agent trusted table |
| old/revoked session | authoritative session store + security epoch validation |
| forged/stale lease | issuer-bound lease verifier + one-use lease registry |
| old confirmation | policy version / digest / security epoch validation |
| emergency stop | security epoch bump, session revocation, lease revocation |
| registry registration after seal | sealed registry object boundary |
| grant installation after seal | sealed grant authority object boundary |

These are trusted-process controls. They do not claim sandbox protection from malicious code with arbitrary access to Python internals.

## Implemented / verified / deferred status

### IMPLEMENTED

- authoritative registry and grant resolution;
- caller timeout ceiling enforcement;
- deep recursive manifest freezing;
- ActionRequest field-integrity validation for risk/effect/verification/version/operation;
- resource ceiling enforcement for file size, output size, directory entries, path length, and leases;
- current API-shape tests;
- direct hostile `ActionRequest` tests;
- sealed registry/grant post-seal denial tests.

### VERIFIED

- 115 unittest cases discovered;
- 107 passed on this host;
- 8 Windows-specific tests skipped;
- 0 failures;
- 0 errors;
- fresh extraction reproduction passes during packaging.

### DESIGNED

- production MEMORY//OS adapter contract remains external and canonical;
- identity/recovery model remains a local test/developer abstraction;
- HMAC leases remain in-process issuer-authentication hardening.

### DEFERRED

- production MEMORY//OS integration;
- production identity and durable grants;
- hardware/OS attestation;
- durable/distributed idempotency and grant-call accounting;
- package marketplace/supply-chain review.

### FORBIDDEN / ABSENT

- generic shell/PowerShell/interpreter execution;
- generic command capability;
- generic application launch;
- browser automation;
- GUI/computer vision control;
- voice authorization;
- proactive daemon;
- self-improvement;
- cloud integrations;
- broad OS control.

### NOT VERIFIED

- Windows security validation on an actual Windows host;
- production MEMORY//OS behavior;
- production identity assurance;
- protection from arbitrary malicious code in the trusted Python process.

### PLATFORM-SPECIFIC

Filesystem mutation support depends on root identity and directory-file-descriptor primitives. Where unavailable, mutation posture is degraded/fail-closed rather than treated as equivalently secure.
