from __future__ import annotations

import os
import uuid

import pytest
from websockets.asyncio.client import connect

from max_to_telegram.protocol import Frame, FrameCodec


@pytest.mark.live
@pytest.mark.skipif(
    os.getenv("RUN_LIVE_MAX_RESEARCH") != "1",
    reason="set RUN_LIVE_MAX_RESEARCH=1 to contact the public MAX WebSocket",
)
async def test_anonymous_max_handshake() -> None:
    codec = FrameCodec()
    user_agent = "Mozilla/5.0 MAX-bridge-live-test/0.1"
    payload = {
        "userAgent": {
            "deviceType": "WEB",
            "pushDeviceType": "WEBPUSH",
            "locale": "ru",
            "deviceLocale": "ru",
            "osVersion": "Test",
            "deviceName": "MAX bridge live test",
            "headerUserAgent": user_agent,
            "isPwa": False,
            "appVersion": "26.8.8",
            "screen": "0x0 1.0x",
            "timezone": "Europe/Moscow",
        },
        "deviceId": str(uuid.uuid4()),
    }

    async with connect(
        "wss://api.oneme.ru/websocket",
        origin="https://web.max.ru",
        user_agent_header=user_agent,
        open_timeout=15,
    ) as websocket:
        await websocket.send(codec.encode(Frame(cmd=0, seq=0, opcode=6, payload=payload)))
        decoded = codec.decode(await websocket.recv())

    assert decoded.version == 10
    assert decoded.cmd == 1
    assert decoded.seq == 0
    assert decoded.opcode == 6
    assert isinstance(decoded.payload, dict)
    assert "phone-auth-enabled" in decoded.payload
