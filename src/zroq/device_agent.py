"""Lease-bound local Device Agent for the narrow Phase 2.6 capability set."""

from __future__ import annotations

from collections.abc import Callable, Iterable
import os
import platform
import stat
import threading

from .contracts import (
    ActionSnapshot,
    ExecutionLease,
    ExecutionObservation,
    utc_now,
)
from .leases import LeaseVerifier
from .security import (
    DeviceSecurityPolicy,
    SecurityViolation,
)


class LocalDeviceAgent:
    def __init__(
        self,
        device_id: str,
        owner_id: str,
        policy: DeviceSecurityPolicy,
        lease_verifier: LeaseVerifier,
        current_security_epoch: Callable[[], int],
        policy_version: str,
        execution_capabilities: Iterable[tuple[str, str, str]],
        agent_version: str = "0.2.6",
    ) -> None:
        self.device_id = device_id
        self.owner_id = owner_id
        self.policy = policy
        self.lease_verifier = lease_verifier
        self.current_security_epoch = current_security_epoch
        self.policy_version = policy_version
        self.execution_capabilities = frozenset(
            execution_capabilities
        )
        self.agent_version = agent_version
        self._stop_event = threading.Event()

    def clear_stop(self) -> None:
        self._stop_event.clear()

    def stop(self) -> None:
        self._stop_event.set()

    def is_stopped(self) -> bool:
        return self._stop_event.is_set()

    def cancel(self, action_id: str) -> bool:
        # Phase 2.6 has no generic process capability. Filesystem operations are
        # cancellable before commit through the shared cancellation event.
        return False

    def execute(
        self,
        action: ActionSnapshot,
        cancel_event: threading.Event,
        lease: ExecutionLease,
    ) -> ExecutionObservation:
        if self.is_stopped() or cancel_event.is_set():
            return ExecutionObservation(
                getattr(action, "action_id", "unknown"),
                False,
                False,
                error="cancelled_before_execution",
                cancelled=True,
            )

        if not isinstance(action, ActionSnapshot):
            return ExecutionObservation(
                getattr(action, "action_id", "unknown"),
                False,
                False,
                error="lease_requires_immutable_action_snapshot",
            )

        verification = self.lease_verifier.verify_and_consume(
            lease,
            action,
            policy_version=self.policy_version,
            security_epoch=self.current_security_epoch(),
        )

        if not verification.valid:
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error=(
                    "invalid_or_unissued_device_lease:"
                    f"{verification.reason}"
                ),
            )

        if (
            action.device_id != self.device_id
            or action.owner_id != self.owner_id
        ):
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="device_or_owner_mismatch",
            )

        if (
            action.capability_id,
            action.capability_version,
            action.operation,
        ) not in self.execution_capabilities:
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error=(
                    "operation_not_in_trusted_device_capability_table"
                ),
            )

        if self.is_stopped() or cancel_event.is_set():
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="cancelled_after_lease_verification",
                cancelled=True,
            )

        try:
            if action.operation == "read_current_time":
                return self._time(action)

            if action.operation == "inspect_device_metadata":
                return self._metadata(action)

            if action.operation == "inspect_directory":
                return self._inspect_directory(action)

            if action.operation == "create_directory":
                return self._create_directory(
                    action,
                    cancel_event,
                    lease,
                )

            if action.operation == "create_text_file":
                return self._create_text_file(
                    action,
                    cancel_event,
                    lease,
                )

            if action.operation == "read_text_file":
                return self._read_text_file(action)

            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="operation_not_supported",
            )

        except (
            SecurityViolation,
            OSError,
            UnicodeError,
            ValueError,
        ) as exc:
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error=str(exc),
            )

    def _check_cancel(
        self,
        cancel_event: threading.Event,
    ) -> bool:
        return (
            self.is_stopped()
            or cancel_event.is_set()
        )

    def _commit_barrier(
        self,
        action: ActionSnapshot,
        cancel_event: threading.Event,
        lease: ExecutionLease,
    ) -> ExecutionObservation | None:
        """Final pre-commit stop/epoch validation."""

        if self._check_cancel(cancel_event):
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="execution_barrier_stopped_before_commit",
                cancelled=True,
            )

        if lease.security_epoch != self.current_security_epoch():
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="execution_barrier_epoch_invalid",
            )

        return None

    def _time(
        self,
        action: ActionSnapshot,
    ) -> ExecutionObservation:
        now = utc_now()

        return ExecutionObservation(
            action.action_id,
            True,
            False,
            {
                "iso_time": now.isoformat(),
                "timezone": "UTC",
            },
        )

    def _metadata(
        self,
        action: ActionSnapshot,
    ) -> ExecutionObservation:
        data = {
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "agent_version": self.agent_version,
            "approved_root_count": len(
                self.policy.canonical_roots()
            ),
            "filesystem_posture": self.policy.posture.value,
            "filesystem_posture_reason": self.policy.posture_reason,
        }

        return ExecutionObservation(
            action.action_id,
            True,
            False,
            data,
        )

    def _inspect_directory(
        self,
        action: ActionSnapshot,
    ) -> ExecutionObservation:
        path = self.policy.canonical_path(
            str(action.parameters.get("path", ""))
        )

        if not path.exists() or not path.is_dir():
            raise SecurityViolation(
                "approved_directory_not_found"
            )

        max_entries = self.policy.limits.max_directory_entries

        if max_entries < 0:
            raise SecurityViolation(
                "directory_entry_limit"
            )

        children = []

        for index, entry in enumerate(
            path.iterdir()
        ):
            if index >= max_entries:
                raise SecurityViolation(
                    "directory_entry_limit"
                )

            children.append(entry)

        entries = []

        for entry in sorted(
            children,
            key=lambda p: p.name,
        ):
            entry_stat = entry.lstat()
            mode = getattr(
                entry_stat,
                "st_mode",
                0,
            )

            is_symlink = stat.S_ISLNK(mode)

            entries.append(
                {
                    "name": entry.name,
                    "is_dir": stat.S_ISDIR(mode),
                    "is_file": stat.S_ISREG(mode),
                    "is_symlink": is_symlink,
                    "size": (
                        entry_stat.st_size
                        if stat.S_ISREG(mode)
                        else None
                    ),
                }
            )

        return ExecutionObservation(
            action.action_id,
            True,
            False,
            {
                "path": str(path),
                "entries": entries,
            },
        )

    def _create_directory(
        self,
        action: ActionSnapshot,
        cancel_event: threading.Event,
        lease: ExecutionLease,
    ) -> ExecutionObservation:
        self.policy.assert_mutation_supported()

        if self._check_cancel(cancel_event):
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="cancelled_before_commit",
                cancelled=True,
            )

        path = self.policy.canonical_path(
            str(action.parameters.get("path", ""))
        )

        parent = self.policy.require_existing_parent(path)

        if path.exists():
            raise SecurityViolation(
                "directory_already_exists"
            )

        if self._check_cancel(cancel_event):
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="cancelled_before_commit",
                cancelled=True,
            )

        parent_fd = self.policy.open_parent_fd(parent)

        try:
            barrier = self._commit_barrier(
                action,
                cancel_event,
                lease,
            )

            if barrier is not None:
                return barrier

            if os.name == "nt":
                os.mkdir(
                    str(path),
                    mode=0o700,
                )
            else:
                os.mkdir(
                    path.name,
                    mode=0o700,
                    dir_fd=parent_fd,
                )

        finally:
            os.close(parent_fd)

        return ExecutionObservation(
            action.action_id,
            True,
            True,
            {
                "path": str(path),
                "created": True,
            },
        )

    def _create_text_file(
        self,
        action: ActionSnapshot,
        cancel_event: threading.Event,
        lease: ExecutionLease,
    ) -> ExecutionObservation:
        self.policy.assert_mutation_supported()

        if self._check_cancel(cancel_event):
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="cancelled_before_commit",
                cancelled=True,
            )

        path = self.policy.canonical_path(
            str(action.parameters.get("path", ""))
        )

        parent = self.policy.require_existing_parent(path)

        content = str(
            action.parameters.get(
                "content",
                "",
            )
        )

        encoded = content.encode("utf-8")

        if len(encoded) > self.policy.limits.max_file_bytes:
            raise SecurityViolation(
                "content_size_limit"
            )

        if path.exists():
            raise SecurityViolation(
                "file_already_exists"
            )

        if self._check_cancel(cancel_event):
            return ExecutionObservation(
                action.action_id,
                False,
                False,
                error="cancelled_before_commit",
                cancelled=True,
            )

        parent_fd = self.policy.open_parent_fd(parent)
        fd: int | None = None

        try:
            barrier = self._commit_barrier(
                action,
                cancel_event,
                lease,
            )

            if barrier is not None:
                return barrier

            if os.name == "nt":
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(
                        os,
                        "O_BINARY",
                        0,
                    )
                )

                fd = os.open(
                    str(path),
                    flags,
                    0o600,
                )
            else:
                flags = (
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_NOFOLLOW
                )

                fd = os.open(
                    path.name,
                    flags,
                    0o600,
                    dir_fd=parent_fd,
                )

            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
                newline="",
            ) as handle:
                fd = None
                handle.write(content)

        finally:
            if fd is not None:
                os.close(fd)

            os.close(parent_fd)

        return ExecutionObservation(
            action.action_id,
            True,
            True,
            {
                "path": str(path),
                "bytes": len(encoded),
            },
        )

    def _read_text_file(
        self,
        action: ActionSnapshot,
    ) -> ExecutionObservation:
        path = self.policy.assert_existing_text_file(
            str(action.parameters.get("path", ""))
        )

        size = path.stat().st_size
        self.policy.assert_output_fits(size)

        content = path.read_text(
            encoding="utf-8"
        )

        encoded_len = len(
            content.encode("utf-8")
        )

        self.policy.assert_output_fits(
            encoded_len
        )

        return ExecutionObservation(
            action.action_id,
            True,
            False,
            {
                "path": str(path),
                "content": content,
                "bytes": encoded_len,
            },
        )