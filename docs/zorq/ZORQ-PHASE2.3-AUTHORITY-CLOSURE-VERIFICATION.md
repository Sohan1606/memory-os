# ZORQ PHASE 2.3 AUTHORITY CLOSURE VERIFICATION REPORT

**Status:** Phase 2.3 authoritative capability and grant boundary closure; hardened standalone control-plane slice; not production-ready.
**Date:** 2026-09-27

## 1. Pre-hardening baseline

Before Phase 2.3 source hardening, the Phase 2.2 suite was reproduced:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
Ran 76 tests
OK (skipped=8)
```

The new Phase 2.3 regression tests then reproduced the authority-source weakness:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests/test_phase23_authority_boundary.py -v
Ran 16 tests
FAILED (failures=13, errors=3)
```

The pre-hardening baseline is recorded in `ZORQ-PHASE2.3-PRE-HARDENING-GAP-BASELINE.md`.

## 2. Phase 2.3 hardening implemented

- Action Kernel API now accepts untrusted `ActionRequest` plus optional `Confirmation`; it no longer accepts caller-supplied `CapabilityManifest` or `PermissionGrant` authority objects.
- Kernel resolves authoritative manifests internally from sealed `CapabilityRegistry`.
- Kernel resolves authoritative grants internally from sealed `GrantAuthority`.
- Grants bind capability ID, capability version, manifest digest, policy version, scope, risk ceiling, confirmation mode, max calls, expiry, and active state.
- Capability manifest top-level mapping fields were frozen in Phase 2.3; independent Phase 2.4 review later found nested structures were only shallowly frozen and corrected this with recursive deep freezing.
- Registry is sealed after trusted composition and runtime registration remains unavailable.
- Device Agent has an immutable execution capability table derived from trusted composition and rejects leases for operations not in that table.
- Effective device resource limits are the minimum of trusted manifest ceilings and configured device policy ceilings for implemented file/directory limits.
- Caller-provided risk, confirmation mode, memory-governance flag, and resource limits are ignored for authority; authoritative registry/grant state is used instead.

## 3. Verification command

From `/home/user/zroq`:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

## 4. Result

- Tests discovered by unittest: **97**
- Passed: **89**
- Skipped: **8** Windows-specific tests skipped on non-Windows host
- Failed: **0**
- Errors: **0**
- Network required: **No**
- Hosted provider required: **No**
- Production MEMORY//OS implementation added: **No**
- Phase 3 functionality added: **No**

## 5. Phase 2.3 regression coverage

New Phase 2.3 tests include:

- `test_kernel_rejects_unregistered_capability`
- `test_kernel_does_not_accept_caller_supplied_forged_manifest`
- `test_kernel_does_not_accept_caller_supplied_forged_grant`
- `test_forged_manifest_cannot_lower_risk`
- `test_forged_manifest_cannot_remove_confirmation`
- `test_forged_manifest_cannot_disable_memory_governance`
- `test_forged_grant_cannot_expand_scope`
- `test_forged_grant_cannot_increase_call_budget`
- `test_registered_capability_version_is_authoritative`
- `test_kernel_resolves_manifest_from_authoritative_registry`
- `test_manifest_nested_mapping_is_immutable`
- `test_sealed_registry_manifest_cannot_be_modified`
- `test_resource_limits_cannot_be_changed_after_seal`
- `test_schema_cannot_be_changed_after_seal`
- `test_authoritative_grant_version_mismatch_fails`
- `test_inactive_authoritative_grant_fails`
- `test_expired_authoritative_grant_fails`
- `test_authoritative_grant_manifest_digest_mismatch_fails`
- `test_device_agent_rejects_unsupported_capability_table_entry`
- `test_effective_resource_limit_does_not_exceed_device_ceiling`
- `test_audit_records_authority_denial_and_chain_remains_valid`

These tests include direct Kernel adversarial calls and assert no real filesystem side effect occurs for hostile forged authority inputs.

## 6. Retained guarantees

Phase 2.1 and 2.2 tests remain in the suite and continue to cover:

- authoritative session validation;
- epoch-bound sessions;
- authenticated emergency-stop recovery;
- stale session denial;
- stale/forged/wrong-device lease denial;
- one-use lease registry;
- concurrent idempotency;
- grant max-calls accounting;
- confirmation assurance/policy/epoch;
- exception containment;
- fail-closed read output limits;
- sealed registry runtime-installation denial;
- no generic shell/process execution;
- no generic application launch;
- filesystem containment controls;
- audit integrity.

## 7. Windows status

Windows validation tests are present but skipped on this non-Windows host. Windows security validation remains **NOT VERIFIED** in this run.

## 8. Known limitations

- Not production-ready.
- Python private attributes are not a sandbox against arbitrary malicious code already executing inside the trusted process.
- HMAC is in-process hardening, not asymmetric issuer-only authentication across a process boundary.
- Grant accounting and idempotency are process-local, not durable/distributed.
- Local secret authentication is not production identity.
- Real MEMORY//OS integration remains unimplemented.
- Windows tests require a real Windows host before Windows validation can be claimed.
- Audit is hash-chained but not remote WORM storage.

## 9. Deferred / forbidden

Still deferred/forbidden: production MEMORY//OS integration, voice, multilingual runtime, browser/GUI/vision control, proactive daemon, self-improvement, cloud integrations, arbitrary shell/PowerShell/interpreter execution, generic command execution, generic application launch, broader OS control, credentials/password actions, payments, account-security operations, and production deployment.
