"""Race-resistant reads for small local credential files."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import cast


class PrivateFileError(RuntimeError):
    """Raised when a credential file cannot be opened under the safety policy."""


def enforce_private_fd_permissions(descriptor: int) -> None:
    """Restrict an open file to its owner on platforms that expose POSIX modes."""

    if os.name != "posix":
        return
    fchmod = cast(Callable[[int, int], None] | None, getattr(os, "fchmod", None))
    if fchmod is None:  # pragma: no cover - defensive guard for unusual POSIX runtimes
        raise OSError("POSIX runtime does not provide fchmod")
    fchmod(descriptor, 0o600)


def read_private_text(path: Path, *, maximum_bytes: int, label: str) -> str:
    """Read one private regular file without following a swapped symlink."""

    descriptor = -1
    try:
        before = path.lstat()
        _validate_stat(before, maximum_bytes=maximum_bytes, label=label)
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        _validate_stat(opened, maximum_bytes=maximum_bytes, label=label)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PrivateFileError(f"{label} changed while it was being opened")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = -1
            raw = stream.read(maximum_bytes + 1)
    except PrivateFileError:
        raise
    except OSError as exc:
        raise PrivateFileError(f"cannot safely read {label}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    if len(raw) > maximum_bytes:
        raise PrivateFileError(f"{label} is unexpectedly large")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrivateFileError(f"{label} must be UTF-8 text") from exc


def _validate_stat(file_stat: os.stat_result, *, maximum_bytes: int, label: str) -> None:
    if stat.S_ISLNK(file_stat.st_mode):
        raise PrivateFileError(f"{label} must not be a symbolic link")
    if not stat.S_ISREG(file_stat.st_mode):
        raise PrivateFileError(f"{label} must be a regular file")
    if file_stat.st_size > maximum_bytes:
        raise PrivateFileError(f"{label} is unexpectedly large")
    if os.name == "posix" and stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise PrivateFileError(f"{label} permissions are too broad; run chmod 600 on it")
