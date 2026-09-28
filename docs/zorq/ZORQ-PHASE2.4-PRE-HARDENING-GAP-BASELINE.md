# ZORQ Phase 2.4 Pre-Hardening Gap Baseline

**Status:** Captured before Phase 2.4 source changes.
**Scope:** resource-ceiling and contract-integrity closure only.
**Production status:** not production-ready; not a security approval.

## Existing Phase 2.3 baseline

Command run from `/home/user/zroq` before Phase 2.4 source changes:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed result:

```text
Ran 97 tests in 0.399s

OK (skipped=8)
```

The 8 skipped tests are Windows-specific and were not executed on this non-Windows host.

## Finding A — manifest timeout ceiling mismatch

Reproduction used a valid `filesystem.approved/create_directory` action and replaced `ActionRequest.timeout_seconds` with `3600` while the authoritative filesystem manifest declared `timeout_seconds=5`.

Observed before hardening:

```text
result_status=VERIFIED
manifest_timeout_seconds=5
caller_timeout_seconds=3600
issued_lease_lifetime_seconds=899.999
exceeds_manifest_ceiling=True
target_exists=True
```

The lease was bounded by the remaining session lifetime, but it still exceeded the trusted manifest timeout ceiling. This confirmed that caller-supplied `ActionRequest.timeout_seconds` could extend execution/lease lifetime beyond authoritative capability policy.

## Finding B — shallow nested manifest mutability

A test manifest was registered with nested dictionaries/lists in `input_schema`, `output_schema`, and `resource_limits`. The top-level mapping was a `mappingproxy`, but nested values remained mutable and aliased.

Observed before hardening:

```text
top_level_mapping_type=mappingproxy
stored.input_schema.outer.inner=mutated-after-register
stored.input_schema.items=['a', {'deep': 'safe'}, 'mutated-list']
stored.resource_limits.nested.max=999
source_alias_mutates_stored_seq_inner=777
nested_mutability_reproduced=True
```

This confirmed that Phase 2.3 manifest immutability was shallow, despite docs describing immutable manifest state. Recursive/deep freezing is required for manifest-owned security-relevant structures.

## Finding C — Phase 2.3 authority tests relied on removed legacy API shape

`ActionKernel.execute` currently has the correct signature:

```text
(action: ActionRequest, session: Session, confirmation: Confirmation | None = None) -> ActionResult
```

However, the Phase 2.3 helper `attack_with_caller_authority` attempted the removed 5-argument API:

```python
core.kernel.execute(action, session, manifest, grant, confirmation)
```

and caught `TypeError`. The following tests used that helper:

- `test_kernel_rejects_unregistered_capability`
- `test_kernel_does_not_accept_caller_supplied_forged_manifest`
- `test_kernel_does_not_accept_caller_supplied_forged_grant`
- `test_forged_manifest_cannot_lower_risk`
- `test_forged_manifest_cannot_remove_confirmation`
- `test_forged_manifest_cannot_disable_memory_governance`
- `test_forged_grant_cannot_expand_scope`
- `test_forged_grant_cannot_increase_call_budget`
- `test_registered_capability_version_is_authoritative`

This is a test-quality/contract-integrity issue rather than a new execution vulnerability: the removed API shape is good, but tests must also prove the current authority model directly through 3-argument Kernel calls and hostile `ActionRequest` objects.

## Required closure

Phase 2.4 must:

1. compute trusted effective timeouts from authoritative manifest/device/session ceilings, not caller timeout;
2. implement recursive immutable copies for manifest-owned mappings/sequences/sets;
3. rewrite authority-boundary tests to prove current behavior directly instead of treating `TypeError` as the security result.
