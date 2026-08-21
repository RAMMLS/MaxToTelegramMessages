"""Loading and validating MAX web-session credentials."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from max_to_telegram.config import Settings


class AuthError(ValueError):
    """Raised when local MAX credentials cannot be loaded safely."""


_MAX_SESSION_FILE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class MaxCredentials:
    """Credentials copied from an authorized MAX web session."""

    viewer_id: int
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.viewer_id <= 0:
            raise AuthError("MAX viewerId must be a positive integer")
        if not 16 <= len(self.token) <= 4096:
            raise AuthError("MAX token has an unexpected length")
        if self.token != self.token.strip() or any(ord(char) < 32 for char in self.token):
            raise AuthError("MAX token contains whitespace or control characters")

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MaxCredentials:
        """Parse the logical ``__oneme_auth`` object."""

        viewer_raw = value.get("viewerId", value.get("viewer_id"))
        token_raw = value.get("token")
        if isinstance(viewer_raw, bool):
            raise AuthError("MAX viewerId must be a positive integer")
        try:
            viewer_id = int(viewer_raw)
        except (TypeError, ValueError) as exc:
            raise AuthError("MAX session is missing a valid viewerId") from exc
        if not isinstance(token_raw, str):
            raise AuthError("MAX session is missing a token")
        return cls(viewer_id=viewer_id, token=token_raw)


def load_credentials(settings: Settings) -> MaxCredentials:
    """Load credentials from direct environment values or a protected JSON file."""

    if settings.max_viewer_id is not None and settings.max_auth_token is not None:
        return MaxCredentials(settings.max_viewer_id, settings.max_auth_token)
    if settings.max_session_file is None:
        raise AuthError("MAX credentials are not configured")
    return load_session_file(settings.max_session_file)


def load_session_file(path: Path) -> MaxCredentials:
    """Read an ignored local session file without following symlinks."""

    try:
        file_stat = path.lstat()
    except OSError as exc:
        raise AuthError(f"cannot access MAX session file: {path}") from exc
    if stat.S_ISLNK(file_stat.st_mode):
        raise AuthError("MAX session file must not be a symbolic link")
    if not stat.S_ISREG(file_stat.st_mode):
        raise AuthError("MAX session path must point to a regular file")
    if file_stat.st_size > _MAX_SESSION_FILE_BYTES:
        raise AuthError("MAX session file is unexpectedly large")
    if os.name == "posix" and stat.S_IMODE(file_stat.st_mode) & 0o077:
        raise AuthError("MAX session file permissions are too broad; run chmod 600 on it")

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AuthError(f"cannot read MAX session file: {path}") from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError("MAX session file is not valid JSON") from exc

    return parse_session_document(document)


def parse_session_document(document: Any) -> MaxCredentials:
    """Accept direct and common browser-export representations."""

    if not isinstance(document, Mapping):
        raise AuthError("MAX session JSON must be an object")

    candidate: Any = document.get("__oneme_auth", document)
    if isinstance(candidate, str):
        try:
            candidate = json.loads(candidate)
        except json.JSONDecodeError as exc:
            raise AuthError("__oneme_auth contains invalid JSON") from exc
    if not isinstance(candidate, Mapping):
        raise AuthError("__oneme_auth must be a JSON object")
    return MaxCredentials.from_mapping(candidate)
