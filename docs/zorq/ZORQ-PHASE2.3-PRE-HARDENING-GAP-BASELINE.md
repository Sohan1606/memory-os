# ZORQ Phase 2.3 Pre-Hardening Gap Baseline

**Status:** Captured before Phase 2.3 implementation source changes.
**Scope:** Authoritative capability + grant boundary closure only.
**Production readiness:** Not production-ready.

## Existing Phase 2.2 suite reproduction

Command run from `/home/user/zroq` before Phase 2.3 source hardening:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Result:

```text
Ran 76 tests in 0.356s

OK (skipped=8)
```

The 8 skipped tests are Windows-specific and were not executed on this non-Windows host.

## Newly added Phase 2.3 regression tests before hardening

Command:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest tests/test_phase23_authority_boundary.py -v
```

Result before hardening:

```text
Ran 16 tests in 0.027s

FAILED (failures=13, errors=3)
```

## Reproduced authority-source weaknesses

The failing tests demonstrate that the Phase 2.2 Kernel boundary trusted caller-supplied `CapabilityManifest` and `PermissionGrant` objects.

Observed pre-hardening failures included real filesystem mutations reaching `VERIFIED` through forged caller authority for:

- unregistered capability accepted with forged manifest/grant;
- forged manifest accepted;
- forged grant accepted;
- forged manifest lowering risk to R0;
- forged manifest removing confirmation;
- forged manifest disabling MEMORY//OS governance;
- forged grant substituting authority after the authoritative grant was removed;
- forged grant increasing effective call budget;
- forged capability version accepted when paired with forged manifest.

Additional pre-hardening failures showed manifest nested mappings were mutable after registry seal:

- `input_schema` mutable;
- `output_schema` mutable;
- `resource_limits` mutable;
- sealed registry returned mutable manifest internals.

## Baseline excerpt

```text
test_kernel_rejects_unregistered_capability ... FAIL: status VERIFIED
test_kernel_does_not_accept_caller_supplied_forged_manifest ... FAIL: status VERIFIED
test_kernel_does_not_accept_caller_supplied_forged_grant ... FAIL: status VERIFIED
test_forged_manifest_cannot_lower_risk ... FAIL: status VERIFIED
test_forged_manifest_cannot_remove_confirmation ... FAIL: status VERIFIED
test_forged_manifest_cannot_disable_memory_governance ... FAIL: status VERIFIED
test_forged_grant_cannot_expand_scope ... FAIL: status VERIFIED
test_forged_grant_cannot_increase_call_budget ... FAIL: status VERIFIED
test_registered_capability_version_is_authoritative ... FAIL: status VERIFIED
manifest immutability tests ... FAIL: TypeError not raised
new authoritative Kernel API tests ... ERROR: current Kernel still requires caller-supplied manifest/grant
```

## Boundary statement

This baseline is a hardening input only. It confirms that Phase 2.2 fixed earlier session/lease/failure-containment issues but had not yet closed the authoritative capability/grant source boundary. Phase 2.3 must make the trusted registry and grant authority the only source of manifest/grant authority at the Kernel boundary.
