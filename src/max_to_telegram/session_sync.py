"""Securely refresh a local MAX session from the private login portal."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

from max_to_telegram.auth import (
    AuthError,
    LocalMaxSession,
    MaxCredentials,
    load_session_file,
    save_session_file,
)
from max_to_telegram.config import Settings

logger = logging.getLogger(__name__)

_MAX_RESPONSE_BYTES = 64 * 1024
_REQUEST_TIMEOUT_SECONDS = 20


class SessionSyncError(RuntimeError):
    """Raised when the portal cannot provide a valid MAX session."""


async def refresh_session_from_portal(
    settings: Settings,
    *,
    session_factory: Any = aiohttp.ClientSession,
) -> bool:
    """Fetch and atomically install a portal session.

    Returns ``False`` only when a temporary portal failure can safely fall back
    to an already valid local session file.
    """

    url = settings.max_session_sync_url
    token = settings.max_session_sync_token
    path = settings.max_session_file
    if url is None or token is None:
        return False
    if path is None:
        raise SessionSyncError("remote MAX session sync requires MAX_SESSION_FILE")

    timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_SECONDS)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": "MAX-to-Telegram/0.1",
    }
    if settings.max_session_site_access_token:
        headers["OAI-Sites-Authorization"] = f"Bearer {settings.max_session_site_access_token}"
    try:
        async with (
            session_factory(timeout=timeout) as session,
            session.get(
                url,
                headers=headers,
                allow_redirects=False,
            ) as response,
        ):
            if response.status == 200:
                raw = await response.content.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise SessionSyncError("portal session response is too large")
                local_session = parse_portal_session(raw)
                save_session_file(path, local_session)
                logger.info("MAX session refreshed from the protected portal")
                return True
            if response.status in {401, 403}:
                raise SessionSyncError("portal rejected the bridge session credential")
            if response.status == 404 and _has_valid_local_session(path):
                logger.info("Portal has no newer MAX session; using the protected local file")
                return False
            raise SessionSyncError(f"portal session request failed with HTTP {response.status}")
    except SessionSyncError:
        raise
    except (aiohttp.ClientError, TimeoutError, OSError) as exc:
        if _has_valid_local_session(path):
            logger.warning(
                "MAX session portal is temporarily unavailable; "
                "using the protected local file (%s)",
                type(exc).__name__,
            )
            return False
        raise SessionSyncError(
            "MAX session portal is unavailable and no local fallback exists"
        ) from exc


def parse_portal_session(raw: bytes) -> LocalMaxSession:
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SessionSyncError("portal session response is not valid JSON") from exc
    if not isinstance(document, dict):
        raise SessionSyncError("portal session response must be an object")
    device_id = document.get("deviceId")
    try:
        credentials = MaxCredentials.from_mapping(document)
        session = LocalMaxSession(credentials=credentials, device_id=device_id)
        if session.device_id is None:
            raise AuthError("portal session is missing a device ID")
        return session
    except AuthError as exc:
        raise SessionSyncError("portal returned an invalid MAX session") from exc


def _has_valid_local_session(path: Path) -> bool:
    try:
        load_session_file(path)
        return True
    except (AuthError, OSError):
        return False
