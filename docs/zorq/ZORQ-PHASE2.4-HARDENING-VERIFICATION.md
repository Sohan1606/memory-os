# ZORQ PHASE 2.4 HARDENING VERIFICATION REPORT

**Status:** Phase 2.4 resource-ceiling and contract-integrity closure; hardened standalone control-plane slice; not production-ready.
**Date:** 2026-09-27

## 1. Pre-hardening baseline

Before Phase 2.4 source changes, current Phase 2.3 baseline was reproduced:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
Ran 97 tests in 0.399s
OK (skipped=8)
```

The three Phase 2.4 findings were explicitly reproduced and recorded in `ZORQ-PHASE2.4-PRE-HARDENING-GAP-BASELINE.md`:

1. large caller timeout produced a lease lifetime exceeding the 5-second manifest ceiling;
2. nested manifest structures remained mutable/aliased after registration;
3. nine Phase 2.3 authority tests relied on the removed 5-argument API and caught `TypeError`.

## 2. Phase 2.4 changes implemented

- Kernel computes trusted effective timeout from caller request, authoritative manifest timeout, device policy timeout, and remaining session lifetime.
- Lease expiry uses the trusted effective timeout directly.
- Idempotency waiting no longer uses caller timeout as a standalone authority source.
- Manifest freezing is recursive for mappings, lists/tuples, and sets.
- Original manifest input objects are not aliased into stored registry manifests.
- Authority validates `expected_effect` and `verification_requirement` against authoritative manifest policy.
- Phase 2.3 authority tests were rewritten to use current 3-argument Kernel API and hostile `ActionRequest` objects.
- New Phase 2.4 tests cover timeout ceilings, deep immutability, ActionRequest field integrity, resource limits, current API shape, and sealed grant installation denial.
- Directory inspection now fails closed when the complete listing exceeds `max_directory_entries`; it does not silently truncate and call that verified.
- Generic command timeout metadata was removed; generic command/application execution remains absent.

## 3. Final verification command

From `/home/user/zroq`:

```text
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Observed result:

```text
Ran 115 tests in 0.424s

OK (skipped=8)
```

## 4. Exact final counts

- Total unittest cases discovered: **115**
- Passed on this host: **107**
- Skipped: **8**
- Failures: **0**
- Errors: **0**
- Windows tests present: **YES**
- Windows tests executed here: **NO**
- Windows tests skipped here: **8**
- Windows security validation: **NOT VERIFIED**
- Source module count: **19**

## 5. Phase 2.3 retained tests

Phase 2.3 authority-boundary tests remain in `tests/test_phase23_authority_boundary.py` and now directly test the current authority model. They no longer depend on an `attack_with_caller_authority` helper or `except TypeError` for security assertions.

Retained Phase 2.3 coverage includes:

- unregistered capability denial;
- forged manifest/risk/confirmation/governance denial;
- missing/forged grant denial;
- grant scope and max-call denial;
- authoritative capability version enforcement;
- authoritative registry resolution;
- manifest mapping immutability;
- grant version/inactive/expired/digest mismatch denial;
- Device Agent unsupported capability-table refusal;
- device resource ceiling checks;
- audit chain after denial.

## 6. Phase 2.4 new tests

New tests in `tests/test_phase24_contract_integrity.py`: **18**

### Resource-ceiling tests

- `test_action_timeout_cannot_exceed_manifest_timeout`
- `test_lease_expiry_uses_authoritative_timeout_ceiling`
- `test_shorter_caller_timeout_is_not_expanded`
- `test_large_caller_timeout_cannot_extend_session_or_manifest_bound`
- `test_directory_entry_limit_fails_closed`
- `test_max_path_length_is_enforced`
- `test_create_text_file_max_file_bytes_is_enforced`

### Deep-immutability tests

- `test_manifest_deep_mapping_is_immutable`
- `test_manifest_nested_sequence_is_immutable`
- `test_registered_manifest_has_no_mutable_nested_alias`
- `test_original_manifest_input_cannot_mutate_registered_copy`
- `test_nested_resource_limit_cannot_be_changed`

### Direct-authority / field-integrity tests

- `test_current_kernel_execute_api_shape`
- `test_authority_objects_cannot_be_caller_selected_by_normal_api`
- `test_hostile_action_fields_are_validated_against_authoritative_policy`
- `test_unknown_capability_hostile_request_is_denied_without_side_effect`
- `test_forged_purpose_is_denied_when_grant_binds_purpose`
- `test_direct_grant_installation_after_seal_is_denied`

## 7. Resource ceiling enforcement matrix

| Limit | Enforcement proof |
|---|---|
| `max_file_bytes` | create/read text file tests; Device Agent/DeviceSecurityPolicy failure before verified side effect |
| `max_output_bytes` | existing Phase 2.2/2.3 output-limit tests; read fails closed if complete output exceeds ceiling |
| `max_directory_entries` | new Phase 2.4 directory-entry limit test; fail-closed, no verified truncation |
| `timeout_seconds` | new Phase 2.4 lease lifetime tests; caller 3600s cannot exceed manifest/session/device ceiling |
| `max_path_length` | new Phase 2.4 path-length test; canonical path check denies too-long caller path |

## 8. Static security review

Static source scans are run over `src tests` for:

- subprocess / shell / PowerShell / cmd.exe / os.system;
- arbitrary interpreter execution / `eval` / `exec`;
- dynamic imports;
- network clients;
- browser automation;
- hosted-provider/cloud client patterns;
- generated/cache/dependency directories.

Observed source/test scan result during Phase 2.4 review:

- no subprocess/shell/PowerShell/cmd.exe/os.system matches;
- no arbitrary interpreter `eval`/`exec` matches;
- no dynamic import matches;
- no network/browser/client automation matches;
- registry/grant/lease `register`/`install` methods exist only in trusted composition or sealed stores and are covered by post-seal denial tests;
- filesystem API matches are limited to audit file append/read and bounded Device Agent/security-policy primitives (`os.open`, `os.mkdir`) under approved-root policy;
- no hidden persistence/runtime database/daemon startup pattern was found.

No generic command/application capability was added.

## 9. Windows status

```text
Windows tests present: YES
Windows tests executed here: NO
Windows security validation: NOT VERIFIED
```

Skipped Windows tests are not Windows verification.

## 10. Remaining known risks

- Not production-ready.
- Passing tests are not a production security approval.
- Python private attributes, mapping proxies, and dataclass immutability are not a sandbox against malicious code inside the same interpreter.
- HMAC is in-process issuer authentication only, not asymmetric cross-process authentication or hardware attestation.
- Grant accounting and idempotency remain process-local and non-durable.
- Local secret authentication is a development abstraction, not production identity.
- Production MEMORY//OS integration remains absent.
- Windows validation requires execution on a real Windows host.
- Audit remains local hash-chain evidence, not remote WORM storage.

## 11. Stop condition

Phase 2.4 stops after this verification and packaging. Do not begin Phase 3, production MEMORY//OS integration, voice, multilingual runtime, browser/GUI automation, proactive operation, learning/evolution, or broad OS control without a new independent review.
