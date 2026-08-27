from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from max_to_telegram.auth import load_session_file
from max_to_telegram.config import Settings
from max_to_telegram.session_sync import (
    SessionSyncError,
    parse_portal_session,
    refresh_session_from_portal,
)


class FakeContent:
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def read(self, n: int = -1) -> bytes:
        return self.body if n < 0 else self.body[:n]


class FakeResponse:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status = status
        self.content = FakeContent(body)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None


class FakeSession:
    def __init__(self, response: FakeResponse, **_: Any) -> None:
        self.response = response
        self.request: tuple[str, dict[str, Any]] | None = None

    async def __aenter__(self) -> FakeSession:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.request = (url, kwargs)
        return self.response


def sync_settings(path: Path) -> Settings:
    return Settings.from_env(
        {
            "MAX_SESSION_FILE": str(path),
            "MAX_SESSION_SYNC_URL": "https://portal.example/api/bridge/session",
            "MAX_SESSION_SYNC_TOKEN": "s" * 48,
            "MAX_DISCOVERY_MODE": "true",
        }
    )


def private_site_sync_settings(path: Path) -> Settings:
    return Settings.from_env(
        {
            "MAX_SESSION_FILE": str(path),
            "MAX_SESSION_SYNC_URL": "https://portal.example/api/bridge/session",
            "MAX_SESSION_SYNC_TOKEN": "s" * 48,
            "MAX_SESSION_SITE_ACCESS_TOKEN": "p" * 48,
            "MAX_DISCOVERY_MODE": "true",
        }
    )


def test_parse_portal_session_rejects_missing_device_id() -> None:
    raw = json.dumps({"viewerId": 123, "token": "t" * 32}).encode()
    with pytest.raises(SessionSyncError, match="invalid MAX session"):
        parse_portal_session(raw)


async def test_refresh_installs_private_session_file(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    body = json.dumps(
        {
            "viewerId": "123",
            "token": "t" * 32,
            "deviceId": "123e4567-e89b-12d3-a456-426614174000",
        }
    ).encode()
    fake = FakeSession(FakeResponse(200, body))

    changed = await refresh_session_from_portal(
        sync_settings(path), session_factory=lambda **_: fake
    )

    assert changed is True
    assert load_session_file(path).viewer_id == 123
    if os.name == "posix":
        assert path.stat().st_mode & 0o077 == 0
    assert fake.request is not None
    assert fake.request[1]["allow_redirects"] is False
    assert fake.request[1]["headers"]["Authorization"] == "Bearer " + "s" * 48


async def test_refresh_adds_private_sites_dispatch_credential(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    body = json.dumps(
        {
            "viewerId": "123",
            "token": "t" * 32,
            "deviceId": "123e4567-e89b-12d3-a456-426614174000",
        }
    ).encode()
    fake = FakeSession(FakeResponse(200, body))

    await refresh_session_from_portal(
        private_site_sync_settings(path), session_factory=lambda **_: fake
    )

    assert fake.request is not None
    assert fake.request[1]["headers"]["OAI-Sites-Authorization"] == "Bearer " + "p" * 48


async def test_refresh_rejects_auth_failure_even_with_local_file(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(
        json.dumps(
            {
                "viewerId": 123,
                "token": "l" * 32,
                "deviceId": "123e4567-e89b-12d3-a456-426614174000",
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    fake = FakeSession(FakeResponse(401))

    with pytest.raises(SessionSyncError, match="rejected"):
        await refresh_session_from_portal(sync_settings(path), session_factory=lambda **_: fake)


async def test_refresh_uses_existing_file_when_portal_has_no_session(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"viewerId": 123, "token": "l" * 32}), encoding="utf-8")
    path.chmod(0o600)
    fake = FakeSession(FakeResponse(404))

    assert (
        await refresh_session_from_portal(sync_settings(path), session_factory=lambda **_: fake)
        is False
    )
