"""Loading and validating MAX web-session credentials."""

from __future__ import annotations

import json
import os
import tempfile
import uuid
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from max_to_telegram.config import Settings
from max_to_telegram.safe_files import PrivateFileError, read_private_text


class AuthError(ValueError):
    """Raised when local MAX credentials cannot be loaded safely."""


_MAX_SESSION_FILE_BYTES = 64 * 1024
_MAX_VIEWER_ID = 2**63 - 1


@dataclass(frozen=True, slots=True)
class MaxCredentials:
    """Credentials copied from an authorized MAX web session."""

    viewer_id: int
    token: str = field(repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.viewer_id <= _MAX_VIEWER_ID:
            raise AuthError("MAX viewerId must be a positive signed int64 integer")
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


@dataclass(frozen=True, slots=True)
class LocalMaxSession:
    credentials: MaxCredentials
    device_id: str | None = None


def load_credentials(settings: Settings) -> MaxCredentials:
    """Load credentials from direct environment values or a protected JSON file."""

    return load_local_session(settings).credentials


def load_local_session(settings: Settings) -> LocalMaxSession:
    """Load credentials plus an optional stable device ID."""

    if settings.max_viewer_id is not None and settings.max_auth_token is not None:
        return LocalMaxSession(
            credentials=MaxCredentials(settings.max_viewer_id, settings.max_auth_token),
            device_id=settings.max_device_id,
        )
    if settings.max_session_file is None:
        raise AuthError("MAX credentials are not configured")
    document = _read_session_file(settings.max_session_file)
    return LocalMaxSession(
        credentials=parse_session_document(document),
        device_id=settings.max_device_id or _parse_device_id(document),
    )


def load_session_file(path: Path) -> MaxCredentials:
    """Read an ignored local session file without following symlinks."""

    return parse_session_document(_read_session_file(path))


def _read_session_file(path: Path) -> Any:
    try:
        raw = read_private_text(
            path,
            maximum_bytes=_MAX_SESSION_FILE_BYTES,
            label="MAX session file",
        )
    except PrivateFileError as exc:
        raise AuthError(str(exc)) from exc
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AuthError("MAX session file is not valid JSON") from exc

    return document


def save_session_file(path: Path, session: LocalMaxSession) -> None:
    """Atomically persist refreshed credentials with private permissions."""

    if session.device_id is None:
        raise AuthError("cannot persist MAX session without a device ID")
    try:
        normalized_device_id = str(uuid.UUID(session.device_id))
    except ValueError as exc:
        raise AuthError("MAX device ID must be a UUID") from exc
    if path.exists():
        _read_session_file(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "viewerId": session.credentials.viewer_id,
        "token": session.credentials.token,
        "deviceId": normalized_device_id,
    }
    encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    descriptor = -1
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    except OSError as exc:
        raise AuthError(f"cannot update MAX session file: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary_path)


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


def _parse_device_id(document: Any) -> str | None:
    if not isinstance(document, Mapping):
        return None
    value = document.get("deviceId", document.get("device_id", document.get("__oneme_device_id")))
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith('"'):
            try:
                decoded = json.loads(candidate)
            except json.JSONDecodeError as exc:
                raise AuthError("MAX session contains an invalid device ID") from exc
            candidate = decoded if isinstance(decoded, str) else ""
    else:
        candidate = ""
    try:
        return str(uuid.UUID(candidate))
    except ValueError as exc:
        raise AuthError("MAX session contains an invalid device ID") from exc
