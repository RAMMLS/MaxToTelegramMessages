from __future__ import annotations

import time
import uuid
from typing import Any

import pytest

from max_to_telegram.protocol import Frame, FrameCodec
from max_to_telegram.qr_relay import QrRelayError, QrSessionManager, extract_session


class FakeSocket:
    def __init__(self, *, login_available: bool = False) -> None:
        self.codec = FrameCodec()
        self.login_available = login_available
        self.responses: list[bytes] = []
        self.closed = False
        self.sent_opcodes: list[int] = []

    async def send(self, message: bytes) -> None:
        request = self.codec.decode(message)
        self.sent_opcodes.append(request.opcode)
        payload: Any = None
        if request.opcode == 288:
            payload = {
                "trackId": "track-secret",
                "qrLink": "https://max.ru/qr/example",
                "expiresAt": int(time.time() * 1000) + 120_000,
                "pollingInterval": 5_000,
            }
        elif request.opcode == 289:
            payload = {
                "status": {
                    "loginAvailable": self.login_available,
                    "expiresAt": int(time.time() * 1000) + 110_000,
                }
            }
        elif request.opcode == 291:
            payload = {
                "tokenAttrs": {"LOGIN": {"token": "session-token"}},
                "profile": {"contact": {"id": 42}},
            }
        self.responses.append(
            self.codec.encode(Frame(cmd=1, seq=request.seq, opcode=request.opcode, payload=payload))
        )

    async def recv(self) -> bytes:
        return self.responses.pop(0)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_relay_keeps_one_max_socket_across_http_operations() -> None:
    socket = FakeSocket()
    connector_calls = 0

    async def connector(*args: Any, **kwargs: Any) -> FakeSocket:
        nonlocal connector_calls
        connector_calls += 1
        return socket

    manager = QrSessionManager(connector=connector)
    started = await manager.start()

    assert started["type"] == "qr"
    assert started["qrLink"].startswith("https://")
    assert "trackId" not in started
    assert connector_calls == 1

    waiting = await manager.poll(started["sessionId"])
    assert waiting["type"] == "waiting"
    assert connector_calls == 1
    assert socket.sent_opcodes == [6, 288, 289]

    await manager.cancel(started["sessionId"])
    assert socket.closed is True


@pytest.mark.asyncio
async def test_relay_returns_completed_credentials_only_after_login() -> None:
    socket = FakeSocket(login_available=True)

    async def connector(*args: Any, **kwargs: Any) -> FakeSocket:
        return socket

    manager = QrSessionManager(connector=connector)
    started = await manager.start()
    completed = await manager.poll(started["sessionId"])

    assert completed["type"] == "completed"
    assert completed["session"]["viewerId"] == "42"
    assert completed["session"]["token"] == "session-token"
    uuid.UUID(completed["session"]["deviceId"])
    assert socket.sent_opcodes == [6, 288, 289, 291]
    assert socket.closed is True
    assert manager.sessions == {}


def test_extract_session_rejects_incomplete_private_payload() -> None:
    with pytest.raises(QrRelayError, match="invalid_login_result"):
        extract_session({"profile": {"contact": {"id": 42}}}, "device")
