from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import max_to_telegram.safe_files as safe_files
from max_to_telegram.safe_files import (
    PrivateFileError,
    enforce_private_fd_permissions,
    read_private_text,
)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions are required")
def test_enforce_private_fd_permissions_restricts_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "state.lock"
    path.write_text("", encoding="utf-8")
    path.chmod(0o666)

    descriptor = os.open(path, os.O_RDWR)
    try:
        enforce_private_fd_permissions(descriptor)
    finally:
        os.close(descriptor)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_reads_private_file_through_validated_descriptor(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    path.write_text("value", encoding="utf-8")
    path.chmod(0o600)

    assert read_private_text(path, maximum_bytes=16, label="secret") == "value"


def test_rejects_path_replaced_between_inspection_and_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "secret"
    replacement = tmp_path / "replacement"
    path.write_text("first", encoding="utf-8")
    replacement.write_text("second", encoding="utf-8")
    path.chmod(0o600)
    replacement.chmod(0o600)
    original_open = os.open

    def swapping_open(raw_path: os.PathLike[str] | str, flags: int) -> int:
        replacement.replace(path)
        return original_open(raw_path, flags)

    monkeypatch.setattr(safe_files.os, "open", swapping_open)

    with pytest.raises(PrivateFileError, match="changed while"):
        read_private_text(path, maximum_bytes=16, label="secret")
