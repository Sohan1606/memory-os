"""Capability-specific postcondition verification."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .contracts import ActionRequest, ActionSnapshot, ExecutionObservation, VerificationResult, VerificationStatus
from .security import DeviceSecurityPolicy, SecurityViolation


class VerificationSubsystem:
    def __init__(self, device_policy: DeviceSecurityPolicy):
        self.device_policy = device_policy

    def verify(self, action: ActionRequest | ActionSnapshot, execution: ExecutionObservation) -> VerificationResult:
        if execution.cancelled:
            return VerificationResult(VerificationStatus.UNKNOWN, {"operation_id": execution.operation_id}, "execution was cancelled or timed out", "E1")
        if not execution.executed:
            return VerificationResult(VerificationStatus.FAILED, {"operation_id": execution.operation_id}, "execution did not complete", "E0")
        try:
            if action.operation == "create_directory":
                path = self.device_policy.canonical_path(str(action.parameters.get("path", "")))
                ok = path.exists() and path.is_dir()
                return VerificationResult(
                    VerificationStatus.VERIFIED if ok else VerificationStatus.FAILED,
                    {"path": str(path), "exists": path.exists(), "is_dir": path.is_dir() if path.exists() else False},
                    "directory postcondition read back" if ok else "directory postcondition failed",
                    "E3",
                )
            if action.operation == "create_text_file":
                path = self.device_policy.assert_existing_text_file(str(action.parameters.get("path", "")))
                content = path.read_text(encoding="utf-8")
                expected = str(action.parameters.get("content", ""))
                ok = content == expected
                return VerificationResult(
                    VerificationStatus.VERIFIED if ok else VerificationStatus.FAILED,
                    {"path": str(path), "content_matches": ok, "bytes": len(content.encode("utf-8"))},
                    "text file content read back" if ok else "text file content mismatch",
                    "E3",
                )
            if action.operation == "read_text_file":
                return VerificationResult(VerificationStatus.VERIFIED, {"readback": True}, "read completed from approved file", "E3")
            if action.operation in {"read_current_time", "inspect_device_metadata", "inspect_directory"}:
                return VerificationResult(VerificationStatus.VERIFIED, {"local_observation": True}, "local observation returned", "E3")
            return VerificationResult(VerificationStatus.UNKNOWN, {}, "no verification profile for operation", "E0")
        except (SecurityViolation, OSError, UnicodeError) as exc:
            return VerificationResult(VerificationStatus.UNKNOWN, {"error": str(exc)}, "verification could not establish postcondition", "E1")
