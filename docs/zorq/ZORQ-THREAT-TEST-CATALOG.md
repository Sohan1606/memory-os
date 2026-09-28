# ZORQ THREAT TEST CATALOG — PHASE 2

**Status:** Implemented threat-test catalog and deferred test inventory.

## Test contract

- **Purpose:** translate Phase 1 threats into deterministic tests for the Phase 2 implementation.
- **Authority:** a failed critical test blocks the relevant capability; tests cannot approve a risk exception.
- **Inputs:** hostile provider output, malformed action/lease, paths, grants, sessions, confirmations, filesystem/process state, audit events.
- **Outputs:** pass/fail, status, evidence, audit event, actual side-effect state.
- **Invariants:** no bypass through model, provider, confirmation, retry, capability discovery, or local parameters.
- **Security boundary:** tests exercise cognition/control/device/verification/audit separately.
- **Failure behavior:** fail the suite and prevent acceptance of the affected capability.
- **Audit requirements:** real actions must produce inspectable audit records.
- **Verification requirements:** negative tests inspect actual target state, not only returned text.
- **Versioning:** catalog maps to project and contract versions.
- **Compatibility rules:** new capability or privilege requires new threat entries/tests.
- **Future extension points:** platform-specific fuzzing, property tests, red-team, package supply-chain, and provider tests.

## Executed catalog

### TT-01 — Model output pretending to be policy

A provider returns an administrator command/capability. Expected: `REPORT_UNAVAILABLE`; no action result and no filesystem effect.

**Executed by:** `test_model_output_cannot_grant_authority_or_invent_capability`.

### TT-02 — Memory governance unavailable

Default adapter returns unavailable for an R1 filesystem action. Expected: denied; no directory.

**Executed by:** `test_core_boots_and_default_memory_fails_closed_for_side_effects`.

### TT-03 — Contradictory governance

Test-only governance returns contradictory. Expected: denied; valid confirmation cannot override.

**Executed by:** `test_contradictory_memory_governance_fails_closed`, `test_confirmation_is_not_a_policy_decision`.

### TT-04 — Stale session

Session is revoked before action. Expected: denied.

**Executed by:** `test_stale_session_denies`.

### TT-05 — Path traversal

Target uses approved-root parent traversal. Expected: denied; outside path absent.

**Executed by:** `test_path_traversal_is_denied`.

### TT-06 — Symlink/reparse escape

Approved root contains a symlink to an outside directory. Expected: denied before write.

**Executed by:** `test_symlink_escape_is_denied_before_real_write`.

### TT-07 — Capability discovery is not permission

Registry has an enabled capability but owner grant is removed. Expected: report denied; no action.

**Executed by:** `test_capability_discovery_is_not_permission`.

### TT-08 — Confirmation digest substitution

Confirmation is created for one path; a different path is dispatched. Expected: authorization required/mismatch; no action.

**Executed by:** `test_confirmation_digest_mismatch_denies`.

### TT-09 — Retry target mutation

Same idempotency key is reused with different path. Expected: conflict failure; second path absent.

**Executed by:** `test_idempotency_prevents_mutating_retry`.

### TT-10 — Forged device lease

Lease has incorrect action digest. Expected: local agent refuses execution.

**Executed by:** `test_forged_device_lease_cannot_execute`.

### TT-11 — Fake successful tool response

Execution observation claims execution without creating a directory. Expected: verifier does not return verified.

**Executed by:** `test_fake_success_cannot_pass_verification`.

### TT-12 — Cancellation

Long allowlisted harmless process is cancelled. Expected: cancelled/failed/unknown, never verified.

**Executed by:** `test_long_allowlisted_command_can_be_cancelled_honestly`.

### TT-13 — Emergency stop

Stop is activated before an R1 action. Expected: stopped; no directory.

**Executed by:** `test_emergency_stop_blocks_new_dispatch`.

### TT-14 — Audit manipulation

An audit event payload is modified in memory. Expected: hash-chain verification raises integrity error.

**Executed by:** `test_audit_chain_detects_tampering`.

## Deferred catalog

The following require later capabilities and are not falsely marked passed:

- voice replay/synthesis and speaker anti-spoofing;
- multilingual/code-switching/translation security;
- browser/page/tool-output injection in a real browser;
- Windows UAC, secure desktop, accessibility, protected process, and GUI drift;
- PowerShell/parser/network-pivot/persistence isolation;
- real provider status spoofing and external outcome confidence;
- capability package signatures, runtime behavior drift, and supply-chain compromise;
- hardware attestation and cross-device handoff;
- distributed idempotency and crash recovery;
- real MEMORY//OS policy conflict/version/event behavior;
- cloud provider data-routing, retention, and deletion;
- operator/break-glass abuse;
- production canary/rollback.

## Exit rule

No deferred threat may be silently reclassified as passed when the corresponding feature is added. The feature must add an implementation, a test, evidence, and a threat-model update together.
