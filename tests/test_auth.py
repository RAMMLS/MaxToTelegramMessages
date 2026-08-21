from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from max_to_telegram.auth import (
    AuthError,
    MaxCredentials,
    load_credentials,
    load_session_file,
    parse_session_document,
)
from max_to_telegram.config import Settings


def write_private_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(0o600)


def test_loads_direct_credentials_from_settings() -> None:
    settings = Settings.from_env(
        {
            "MAX_VIEWER_ID": "123",
            "MAX_AUTH_TOKEN": "a" * 32,
            "MAX_DISCOVERY_MODE": "true",
        }
    )

    credentials = load_credentials(settings)

    assert credentials.viewer_id == 123
    assert credentials.token == "a" * 32


def test_loads_private_session_file(tmp_path: Path) -> None:
    session_file = tmp_path / ".max-session.json"
    write_private_json(session_file, {"viewerId": "123", "token": "b" * 32})

    credentials = load_session_file(session_file)

    assert credentials == MaxCredentials(123, "b" * 32)


def test_parses_browser_export_with_serialized_value() -> None:
    document = {"__oneme_auth": json.dumps({"viewerId": "456", "token": "c" * 32})}

    credentials = parse_session_document(document)

    assert credentials.viewer_id == 456
    assert credentials.token == "c" * 32


def test_supports_snake_case_viewer_id() -> None:
    credentials = parse_session_document({"viewer_id": 789, "token": "d" * 32})

    assert credentials.viewer_id == 789


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_rejects_session_file_readable_by_other_users(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    write_private_json(session_file, {"viewerId": 123, "token": "e" * 32})
    session_file.chmod(0o644)

    with pytest.raises(AuthError, match="chmod 600"):
        load_session_file(session_file)


def test_rejects_symlinked_session_file(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    link = tmp_path / "session.json"
    write_private_json(target, {"viewerId": 123, "token": "f" * 32})
    link.symlink_to(target)

    with pytest.raises(AuthError, match="symbolic link"):
        load_session_file(link)


def test_rejects_oversized_session_file(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    session_file.write_text("x" * (65 * 1024), encoding="utf-8")
    session_file.chmod(0o600)

    with pytest.raises(AuthError, match="unexpectedly large"):
        load_session_file(session_file)


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ([], "must be an object"),
        ({"__oneme_auth": "not-json"}, "invalid JSON"),
        ({"__oneme_auth": []}, "must be a JSON object"),
        ({"token": "g" * 32}, "viewerId"),
        ({"viewerId": 123}, "token"),
    ],
)
def test_rejects_malformed_session_documents(document: object, message: str) -> None:
    with pytest.raises(AuthError, match=message):
        parse_session_document(document)


@pytest.mark.parametrize("viewer_id", [0, -1, True, "not-an-id"])
def test_rejects_invalid_viewer_id(viewer_id: object) -> None:
    with pytest.raises(AuthError, match="viewerId"):
        parse_session_document({"viewerId": viewer_id, "token": "h" * 32})


@pytest.mark.parametrize("token", ["short", " leading" + "i" * 20, "j" * 20 + "\n"])
def test_rejects_suspicious_tokens(token: str) -> None:
    with pytest.raises(AuthError, match="token"):
        MaxCredentials(123, token)


def test_repr_and_errors_do_not_include_token() -> None:
    token = "super-secret-token-value-123456"
    credentials = MaxCredentials(123, token)

    assert token not in repr(credentials)
    with pytest.raises(AuthError) as raised:
        parse_session_document({"viewerId": 123, "token": token + "\n"})
    assert token not in str(raised.value)
