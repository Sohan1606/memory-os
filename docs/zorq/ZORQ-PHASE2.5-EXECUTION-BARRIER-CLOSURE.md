# ZORQ PHASE 2.5 — EXECUTION BARRIER + FAILURE CONTAINMENT + FILESYSTEM BOUNDARY CLOSURE

**Status:** Phase 2.5 execution-barrier, failure-containment, and filesystem-boundary closure; hardened standalone control-plane slice; not production-ready.
**Scope:** final narrow control-plane hardening pass before independent architecture/security review. No Phase 3 functionality.

## Closed findings

Independent review identified five concrete Phase 2.5 issues:

1. emergency stop was not fully atomic with filesystem commit;
2. unexpected exceptions outside Device Agent execution could strand idempotency state;
3. `inspect_directory` followed symlinks when classifying child entries;
4. directory-entry limits were enforced after full enumeration/sort;
5. filesystem mutation posture did not require `O_NOFOLLOW` availability before advertising `SUPPORTED`.

The pre-hardening state is recorded in `ZORQ-PHASE2.5-PRE-HARDENING-GAP-BASELINE.md`.

## Execution barrier model

Phase 2.5 establishes the mutation path invariant:

```text
authorized
   ↓
lease valid and consumed
   ↓
execution barrier acquired
   ↓
final stop/cancel/epoch validation
   ↓
commit effect
```

The Device Agent performs a final pre-commit barrier check immediately before local filesystem mutation:

- if emergency stop/cancel is active before commit, the effect is blocked;
- if the lease security epoch no longer matches the current security epoch, the effect is blocked;
- old/pre-stop authorizations do not silently resume after recovery.

Truthful semantics remain:

```text
before commit = blockable
after commit = effect may have occurred; report truthfully
```

Phase 2.5 does not claim that it can cancel an OS syscall that has already committed.

## Failure containment model

Phase 2.5 extends exception containment across the complete reserved Kernel lifecycle:

- capability resolution;
- grant resolution;
- MEMORY//OS governance;
- authority evaluation;
- timeout calculation;
- grant accounting;
- lease issuance;
- Device Agent dispatch;
- audit append paths where feasible;
- verification.

Unexpected internal exceptions now:

- finalize the reserved idempotency record;
- wake waiting callers;
- preserve grant accounting conservatively if dispatch authority was consumed;
- revoke an unused lease when possible;
- return `FAILED` when absence of effect is established;
- return `UNKNOWN` when effect state cannot be established;
- never return `VERIFIED` because of an exception;
- record truthful audit/security evidence on a best-effort basis.

This is not intended to hide programmer errors: the returned evidence records exception type/message and the audit/security event records the internal failure where the audit path itself is available.

## Directory inspection symlink boundary

`inspect_directory` now uses non-following metadata (`lstat` / mode bits) for child classification. For a symlink child:

```text
name = child
is_symlink = true
is_file = false
is_dir = false
target type = NOT FOLLOWED
```

The capability remains non-recursive. It does not traverse symlink targets and does not reveal outside target type through `is_dir()` / `is_file()` follow behavior.

## Directory entry resource boundary

Directory inspection now enumerates at most:

```text
max_directory_entries + 1
```

If the extra entry exists, it fails closed with `directory_entry_limit`. Sorting is performed only after the listing is proven within the configured limit. The implementation no longer materializes/sorts a huge directory before rejecting it, and it does not silently truncate and call the result verified.

## Filesystem posture requirements

Mutation posture is `SUPPORTED` only when all primitives required by the implementation are available:

- approved-root identity primitives (`st_dev`, `st_ino`);
- `os.mkdir` with `dir_fd` support;
- `os.open` with `dir_fd` support;
- `O_NOFOLLOW`;
- `O_DIRECTORY`.

If required mutation primitives are incomplete, posture is `DEGRADED` and mutation operations fail closed. If root identity primitives are unavailable, policy construction fails closed.

## Filesystem race review

Phase 2.5 improves deterministic trusted-process barriers but does not claim complete race-free OS containment.

Reviewed windows:

- approved-root replacement: detected using root device/inode fingerprint where supported;
- parent replacement: parent is resolved and opened through directory-fd primitives where supported;
- symlink insertion: final mutation uses `O_NOFOLLOW` / directory-fd primitives where available, and posture is not `SUPPORTED` without them;
- path validation versus mutation: still platform-sensitive; unsupported primitives fail closed/degraded;
- postcondition replacement: verification reads postcondition after execution but cannot prove remote WORM state or kernel compromise resistance;
- Windows junction/reparse behavior: tests are present but not executed on this host; Windows validation remains NOT VERIFIED.

## Direct-call adversarial posture

| Attack | Defense classification |
|---|---|
| stop during lease verification | deterministic validation + object-bound stop state |
| stop immediately before commit | execution barrier / deterministic final check |
| old epoch before commit | execution barrier / security epoch validation |
| governance exception | failure containment + idempotency finalization |
| authority exception | failure containment + idempotency finalization |
| lease issue exception | failure containment + conservative grant accounting |
| audit failure | best-effort audit/security evidence; result still finalized where possible |
| symlink inspection | non-following metadata boundary |
| huge directory inspection | bounded enumeration resource boundary |
| unsupported filesystem primitive simulation | platform-dependent posture, fail-closed mutation |

These are trusted-process controls. They do not claim sandbox protection from malicious code with arbitrary access to Python internals.

## Implemented / verified / deferred status

### IMPLEMENTED

- final pre-commit mutation barrier;
- stop/cancel/epoch validation immediately before irreversible filesystem effects;
- lifecycle-wide internal failure containment;
- idempotency finalization and waiter wakeup on internal exceptions;
- non-following child classification for directory inspection;
- bounded directory enumeration;
- mutation posture requiring `O_NOFOLLOW` and `O_DIRECTORY`;
- Phase 2.5 direct adversarial tests.

### VERIFIED

- 135 unittest cases discovered;
- 127 passed on this host;
- 8 Windows-specific tests skipped;
- 0 failures;
- 0 errors.

### DESIGNED

- production MEMORY//OS adapter contract remains external and canonical;
- identity/recovery model remains a local test/developer abstraction;
- HMAC leases remain in-process issuer-authentication hardening.

### DEFERRED

- production MEMORY//OS integration;
- production identity and durable grants;
- hardware/OS attestation;
- durable/distributed idempotency and grant-call accounting;
- package marketplace/supply-chain review;
- Windows validation on a real Windows host.

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
- protection from arbitrary malicious code in the trusted Python process;
- perfect kernel/filesystem race freedom.

### PLATFORM-SPECIFIC

Filesystem mutation support depends on root identity, directory-file-descriptor primitives, `O_NOFOLLOW`, and `O_DIRECTORY`. Where unavailable, mutation posture is degraded/fail-closed rather than treated as equivalently secure.
