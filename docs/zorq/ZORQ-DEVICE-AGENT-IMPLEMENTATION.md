# ZORQ DEVICE AGENT IMPLEMENTATION — PHASE 2.6

**Status:** bounded local executor with runtime action-snapshot requirement, verifier-only lease access, trusted execution capability table, pre-commit execution barrier, and enforced device resource ceilings; not production-ready.

## Purpose

`zroq.device_agent.LocalDeviceAgent` performs the narrow local operations permitted by the trusted Phase 2.6 control plane. It does not authorize user intent, create grants, install capabilities, call shells, launch arbitrary applications, or interpret model output.

## Authority boundary

The Device Agent receives:

- device identity and owner binding;
- configured `DeviceSecurityPolicy`;
- verifier-only lease function from `LeaseIssuer`;
- current security epoch function;
- policy version;
- immutable execution capability table derived from sealed trusted composition;
- an `ActionSnapshot` from the Kernel, not caller-owned mutable `ActionRequest` input.

It does **not** receive the lease issuer object or public lease minting authority.

## Phase 2.6 snapshot contract

The Device Agent execution API is intentionally narrow:

```text
execute(ActionSnapshot, cancel_event, lease)
```

Runtime behavior:

- if emergency stop/cancellation is already active, return a cancelled observation;
- otherwise, reject non-`ActionSnapshot` inputs before any effect starts;
- verify and consume the lease against the same snapshot digest/material fields;
- execute only table-supported bounded operations.

This direct-call rule matters because Python type annotations alone are not enforcement. Direct calls with caller-owned mutable `ActionRequest` objects fail closed and cannot execute side effects.

## Defense-in-depth checks

Before any operation, the agent checks:

1. emergency-stop state is clear;
2. the action argument is an immutable-by-value `ActionSnapshot`;
3. lease exists, verifies, matches snapshot digest/material fields, is unexpired, one-use, and in current security epoch;
4. snapshot device and owner match this local agent;
5. `(capability_id, capability_version, operation)` exists in the immutable trusted execution table;
6. operation is one of the explicitly implemented local bounded handlers;
7. filesystem target is under an approved root and passes root/posture checks;
8. resource/output/path/directory/file-size limits are not exceeded.

For filesystem mutation operations, it additionally performs a final pre-commit barrier immediately before the irreversible effect:

- stop/cancel active => blocked as cancelled before commit;
- lease epoch not equal current security epoch => blocked;
- otherwise commit.

The Device Agent is not the full authorization authority; the Kernel and Authority Engine must authorize before lease issuance.

## Implemented operations

- `local.time/read_current_time`
- `device.metadata/inspect_device_metadata`
- `filesystem.approved/inspect_directory`
- `filesystem.approved/create_directory`
- `filesystem.approved/create_text_file`
- `filesystem.approved/read_text_file`

No generic command execution, PowerShell, shell, subprocess fallback, interpreter execution, arbitrary application launch, browser automation, or network provider call exists.

## Filesystem posture

`DeviceSecurityPolicy` records explicit posture:

- `SUPPORTED`: approved-root identity, `dir_fd` `os.open`/`os.mkdir`, `O_NOFOLLOW`, and `O_DIRECTORY` are available.
- `DEGRADED`: mutation primitives are incomplete, so mutation operations fail closed.
- `UNAVAILABLE`: required root identity primitives are unavailable and policy construction fails closed.

Approved roots must already exist at startup. Root replacement and parent directory substitution are detected where platform primitives support it.

## Directory inspection boundary

`inspect_directory` is non-recursive. Child entry classification uses non-following metadata (`lstat` mode bits). Symlink targets are not traversed, and outside target type is not revealed through `is_dir`/`is_file`.

Directory entry limits are enforced with bounded enumeration:

```text
enumerate at most max_directory_entries + 1
if extra entry exists: fail closed with directory_entry_limit
sort only after the directory is proven within limit
```

The capability does not silently truncate and report `VERIFIED`.

## Resource limits

The policy enforces configured hard ceilings combined with trusted manifest ceilings:

- `max_file_bytes`: enforced for create_text_file content and read_text_file source size.
- `max_output_bytes`: enforced before read_text_file content is returned.
- `max_directory_entries`: enforced fail-closed with bounded enumeration.
- `max_path_length`: enforced before canonical path resolution.
- `max_operation_seconds`: consumed by the Kernel when deriving lease expiration; Device Agent enforces the resulting lease expiry.

## Cancellation and emergency stop

Phase 2.6 operations are bounded and synchronous. Cancellation/stop is checked before dispatch and again at the final pre-commit barrier for mutation operations. Phase 2.6 does not claim cancellation of an OS syscall that has already committed. Post-commit evidence is reported truthfully.

## Failure reporting

Device Agent returns `ExecutionObservation` with truthful fields:

- `executed` records whether handler execution completed;
- `effect_started` records whether side-effect risk started;
- `cancelled` records pre-effect cancellation;
- `error` records refusal/failure reason;
- `evidence` contains bounded operation evidence.

Unexpected exceptions are contained at the Kernel boundary and never produce automatic `VERIFIED`.

## Limitations

- The agent runs in the same process as the prototype control plane.
- HMAC verification is an in-process hardening mechanism, not independent hardware attestation or asymmetric remote authorization.
- Python private fields, dataclass freezing, and mapping proxies are not a sandbox against arbitrary code in the same interpreter.
- Phase 2.6 does not claim cryptographic immutability; it hardens in-process object integrity against caller-owned mutable aliasing.
- Windows validation tests are present but not executed on this non-Windows host.
- Phase 2.6 does not prove perfect protection against adversarial kernels/filesystems or untested reparse/junction behavior.
