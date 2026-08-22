"""Credential-free probe for the public MAX WebSocket init command."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

from max_to_telegram.config import Settings
from max_to_telegram.max_client import (
    MAX_WEB_USER_AGENT,
    OPCODE_INIT,
    Connector,
    build_init_payload,
)
from max_to_telegram.protocol import Frame, FrameCodec, ProtocolError


class PublicProbeError(RuntimeError):
    """Raised when the credential-free MAX init handshake is unavailable."""


@dataclass(frozen=True, slots=True)
class PublicProbeResult:
    endpoint_host: str
    protocol_version: int
    init_opcode: int
    compressed_response: bool
    public_config_key_count: int


async def probe_max_public(
    settings: Settings,
    *,
    connector: Connector = connect,
    codec: FrameCodec | None = None,
    timeout: float = 20.0,
) -> PublicProbeResult:
    """Send only opcode 6 and return content-free protocol diagnostics."""

    active_codec = codec or FrameCodec()
    device_id = settings.max_device_id or str(uuid.uuid4())
    try:
        async with connector(
            settings.max_ws_url,
            origin="https://web.max.ru",
            user_agent_header=MAX_WEB_USER_AGENT,
            open_timeout=timeout,
            close_timeout=5,
            ping_interval=None,
            max_size=16 * 1024 * 1024,
        ) as websocket:
            await websocket.send(
                active_codec.encode(
                    Frame(
                        cmd=0,
                        seq=0,
                        opcode=OPCODE_INIT,
                        payload=build_init_payload(
                            settings,
                            device_id,
                            MAX_WEB_USER_AGENT,
                        ),
                    )
                )
            )
            raw = await asyncio.wait_for(websocket.recv(), timeout=timeout)
    except (OSError, asyncio.TimeoutError, TimeoutError, WebSocketException) as exc:
        raise PublicProbeError("public MAX WebSocket init probe failed") from exc

    if isinstance(raw, str):
        raise PublicProbeError("public MAX WebSocket returned a text frame")
    try:
        response = active_codec.decode(raw)
    except ProtocolError as exc:
        raise PublicProbeError("public MAX WebSocket returned an invalid binary frame") from exc
    if response.cmd != 1 or response.seq != 0 or response.opcode != OPCODE_INIT:
        raise PublicProbeError("public MAX WebSocket returned an unexpected init response")
    if not isinstance(response.payload, dict):
        raise PublicProbeError("public MAX init response has no configuration object")

    host = urlparse(settings.max_ws_url).hostname or ""
    return PublicProbeResult(
        endpoint_host=host,
        protocol_version=response.version,
        init_opcode=response.opcode,
        compressed_response=bool(response.compression),
        public_config_key_count=len(response.payload),
    )
