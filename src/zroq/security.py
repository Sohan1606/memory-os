"""Deterministic security helpers used by the control plane and device agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import ctypes
import os
from pathlib import Path
from typing import Iterable, Mapping


class SecurityViolation(Exception):
    """A request crossed a declared security boundary."""


class FileSystemPosture(str, Enum):
    SUPPORTED = "SUPPORTED"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class ResourceLimits:
    max_file_bytes: int = 1_048_576
    max_directory_entries: int = 256
    max_output_bytes: int = 16_384
    max_operation_seconds: float = 5.0
    max_path_length: int = 512


@dataclass(frozen=True)
class RootFingerprint:
    path: Path
    device: int
    inode: int


@dataclass(frozen=True)
class DeviceSecurityPolicy:
    approved_roots: tuple[Path, ...]
    limits: ResourceLimits = ResourceLimits()
    _canonical_roots: tuple[Path, ...] = field(init=False, repr=False)
    _root_fingerprints: tuple[RootFingerprint, ...] = field(init=False, repr=False)
    posture: FileSystemPosture = field(init=False)
    posture_reason: str = field(init=False)

    def __post_init__(self) -> None:
        canonical: list[Path] = []
        fingerprints: list[RootFingerprint] = []

        if not self.approved_roots:
            raise SecurityViolation("approved_roots_required")

        for root in self.approved_roots:
            resolved = Path(root).expanduser().resolve(strict=True)

            if not resolved.is_dir():
                raise SecurityViolation("approved_root_not_directory")

            stat = resolved.stat()
            device = getattr(stat, "st_dev", None)
            inode = getattr(stat, "st_ino", None)

            if device is None or inode is None:
                raise SecurityViolation("filesystem_identity_primitives_unavailable")

            canonical.append(resolved)
            fingerprints.append(
                RootFingerprint(
                    resolved,
                    int(device),
                    int(inode),
                )
            )

        object.__setattr__(self, "_canonical_roots", tuple(canonical))
        object.__setattr__(self, "_root_fingerprints", tuple(fingerprints))

        mutation_supported = (
            _supports_dir_fd_mutation()
            or _supports_windows_mutation()
        )

        if mutation_supported:
            object.__setattr__(
                self,
                "posture",
                FileSystemPosture.SUPPORTED,
            )

            if os.name == "nt" and not _supports_dir_fd_mutation():
                object.__setattr__(
                    self,
                    "posture_reason",
                    "windows_handle_mutation_supported",
                )
            else:
                object.__setattr__(
                    self,
                    "posture_reason",
                    "root_identity_and_dir_fd_mutation_supported",
                )
        else:
            object.__setattr__(
                self,
                "posture",
                FileSystemPosture.DEGRADED,
            )
            object.__setattr__(
                self,
                "posture_reason",
                "dir_fd_mutation_unavailable; mutation operations fail closed",
            )

    def canonical_roots(self) -> tuple[Path, ...]:
        self._assert_roots_unchanged()
        return self._canonical_roots

    def canonical_path(self, raw_path: str | Path) -> Path:
        raw = str(raw_path)

        if not raw or len(raw) > self.limits.max_path_length:
            raise SecurityViolation("path_missing_or_too_long")

        self._assert_roots_unchanged()

        if os.name == "nt":
            lexical = Path(os.path.abspath(os.path.expanduser(raw)))

            matching_root = None
            for root in self._canonical_roots:
                if _lexically_within(lexical, root):
                    matching_root = root
                    break

            if matching_root is None:
                raise SecurityViolation("path_outside_approved_roots")

            _assert_no_windows_reparse_components(
                lexical,
                matching_root,
            )

            candidate = lexical.resolve(strict=False)
        else:
            candidate = Path(raw).expanduser().resolve(strict=False)

        if not any(
            is_within(candidate, root)
            for root in self._canonical_roots
        ):
            raise SecurityViolation("path_outside_approved_roots")

        return candidate

    def assert_existing_text_file(
        self,
        raw_path: str | Path,
    ) -> Path:
        candidate = self.canonical_path(raw_path)

        if not candidate.exists() or not candidate.is_file():
            raise SecurityViolation("text_file_not_found")

        if candidate.is_symlink():
            raise SecurityViolation("text_file_symlink_not_allowed")

        if os.name == "nt" and _windows_reparse_point(candidate):
            raise SecurityViolation("text_file_reparse_point_not_allowed")

        if candidate.stat().st_size > self.limits.max_file_bytes:
            raise SecurityViolation("file_size_limit")

        return candidate

    def assert_output_fits(self, byte_count: int) -> None:
        if byte_count > self.limits.max_output_bytes:
            raise SecurityViolation("read_output_limit_exceeded")

    def assert_mutation_supported(self) -> None:
        if self.posture != FileSystemPosture.SUPPORTED:
            raise SecurityViolation(
                "filesystem_mutation_posture_unavailable"
            )

    def require_existing_parent(self, candidate: Path) -> Path:
        self._assert_roots_unchanged()

        parent = candidate.parent.resolve(strict=True)

        if not parent.is_dir():
            raise SecurityViolation("parent_not_directory")

        if parent.is_symlink():
            raise SecurityViolation("parent_symlink_not_allowed")

        if os.name == "nt":
            for root in self._canonical_roots:
                if is_within(parent, root):
                    _assert_no_windows_reparse_components(parent, root)
                    break

        if not any(
            is_within(parent, root)
            for root in self._canonical_roots
        ):
            raise SecurityViolation("parent_outside_approved_roots")

        return parent

    def open_parent_fd(self, parent: Path) -> int:
        self.assert_mutation_supported()

        if os.name == "nt":
            return _windows_open_parent_fd(
                self,
                parent,
            )

        flags = (
            os.O_RDONLY
            | os.O_DIRECTORY
            | os.O_NOFOLLOW
        )

        return os.open(parent, flags)

    def _assert_roots_unchanged(self) -> None:
        for fingerprint in self._root_fingerprints:
            try:
                stat = fingerprint.path.stat()
            except OSError as exc:
                raise SecurityViolation(
                    "approved_root_unavailable"
                ) from exc

            if (
                int(getattr(stat, "st_dev", -1))
                != fingerprint.device
                or int(getattr(stat, "st_ino", -1))
                != fingerprint.inode
            ):
                raise SecurityViolation("approved_root_replaced")


def _supports_dir_fd_mutation() -> bool:
    supports_dir_fd = getattr(
        os,
        "supports_dir_fd",
        set(),
    )

    required_dir_fd = (
        os.mkdir in supports_dir_fd
        and os.open in supports_dir_fd
    )

    required_flags = (
        getattr(os, "O_NOFOLLOW", None) is not None
        and getattr(os, "O_DIRECTORY", None) is not None
    )

    return bool(
        required_dir_fd
        and required_flags
    )


def _supports_windows_mutation() -> bool:
    if os.name != "nt":
        return False

    try:
        ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        )
        import msvcrt  # noqa: F401
    except (ImportError, OSError):
        return False

    return hasattr(
        ctypes,
        "WinDLL",
    )


def _windows_reparse_point(path: Path) -> bool:
    if os.name != "nt":
        return False

    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise SecurityViolation(
            "path_inspection_failed"
        ) from exc

    reparse_flag = getattr(
        __import__("stat"),
        "FILE_ATTRIBUTE_REPARSE_POINT",
        0,
    )

    attributes = int(
        getattr(
            info,
            "st_file_attributes",
            0,
        )
    )

    if reparse_flag and (
        attributes & reparse_flag
    ):
        return True

    if path.is_symlink():
        return True

    is_junction = getattr(
        path,
        "is_junction",
        None,
    )

    if is_junction is not None and is_junction():
        return True

    return False


def _lexically_within(
    candidate: Path,
    root: Path,
) -> bool:
    candidate_norm = os.path.normcase(
        os.path.abspath(str(candidate))
    )

    root_norm = os.path.normcase(
        os.path.abspath(str(root))
    )

    return (
        candidate_norm == root_norm
        or candidate_norm.startswith(
            root_norm.rstrip("\\/")
            + os.sep
        )
    )


def _assert_no_windows_reparse_components(
    candidate: Path,
    root: Path,
) -> None:
    if os.name != "nt":
        return

    candidate = Path(
        os.path.abspath(str(candidate))
    )
    root = Path(
        os.path.abspath(str(root))
    )

    if not _lexically_within(candidate, root):
        raise SecurityViolation(
            "path_outside_approved_roots"
        )

    current = root

    if _windows_reparse_point(current):
        raise SecurityViolation(
            "approved_root_reparse_point"
        )

    relative = os.path.relpath(
        str(candidate),
        str(root),
    )

    if relative == os.curdir:
        return

    for component in Path(relative).parts:
        if component in ("", "."):
            continue

        if component == os.pardir:
            raise SecurityViolation(
                "path_outside_approved_roots"
            )

        current = current / component

        if _windows_reparse_point(current):
            raise SecurityViolation(
                "reparse_point_not_allowed"
            )


def _windows_open_parent_fd(
    policy: DeviceSecurityPolicy,
    parent: Path,
) -> int:
    """Open a Windows directory handle that forbids delete/rename sharing."""

    if os.name != "nt":
        raise SecurityViolation(
            "windows_mutation_called_on_non_windows"
        )

    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL(
        "kernel32",
        use_last_error=True,
    )

    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = ctypes.c_void_p

    GENERIC_READ = 0x80000000
    FILE_SHARE_READ = 0x00000001
    FILE_SHARE_WRITE = 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    policy._assert_roots_unchanged()

    matching_root = next(
        (
            root
            for root in policy._canonical_roots
            if is_within(parent, root)
        ),
        None,
    )

    if matching_root is None:
        raise SecurityViolation(
            "parent_outside_approved_roots"
        )

    _assert_no_windows_reparse_components(
        parent,
        matching_root,
    )

    before = parent.stat()

    handle = create_file(
        str(parent),
        0,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS
        | FILE_FLAG_OPEN_REPARSE_POINT,
        None,
    )

    if handle == INVALID_HANDLE_VALUE:
        error = ctypes.get_last_error()
        raise OSError(
            error,
            f"CreateFileW failed for approved parent: {parent}",
        )

    try:
        fd = msvcrt.open_osfhandle(
            handle,
            os.O_RDONLY,
        )
    except OSError:
        kernel32.CloseHandle(handle)
        raise

    try:
        after = os.fstat(fd)

        if (
            int(getattr(after, "st_dev", -1))
            != int(getattr(before, "st_dev", -2))
            or int(getattr(after, "st_ino", -1))
            != int(getattr(before, "st_ino", -2))
        ):
            raise SecurityViolation(
                "approved_parent_replaced"
            )

        parent_attributes = int(
            getattr(
                after,
                "st_file_attributes",
                0,
            )
        )

        reparse_flag = getattr(
            __import__("stat"),
            "FILE_ATTRIBUTE_REPARSE_POINT",
            0,
        )

        if reparse_flag and (
            parent_attributes & reparse_flag
        ):
            raise SecurityViolation(
                "parent_reparse_point_not_allowed"
            )

        # A root replacement between the initial check and this handle
        # acquisition must still be detected.
        policy._assert_roots_unchanged()

        return fd

    except BaseException:
        os.close(fd)
        raise


def is_within(
    candidate: Path,
    root: Path,
) -> bool:
    try:
        candidate.resolve(
            strict=False
        ).relative_to(
            root.resolve(
                strict=False
            )
        )
        return True
    except ValueError:
        return False


def sanitize_mapping(
    values: Mapping[str, object],
    allowed: Iterable[str],
) -> dict[str, object]:
    allowed_set = set(allowed)
    return {
        key: value
        for key, value in values.items()
        if key in allowed_set
    }