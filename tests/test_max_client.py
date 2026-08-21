from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

import pytest

from max_to_telegram.auth import MaxCredentials
from max_to_telegram.config import Settings
from max_to_telegram.max_client import (
    OPCODE_INIT,
    OPCODE_KEEPALIVE,
    OPCODE_LOGIN,
    OPCODE_NEW_MESSAGE,
    MaxAuthenticationError,
    MaxClient,
    MaxClientError,
)
from max_to_telegram.protocol import Frame, FrameCodec, ProtocolError


class FakeWebSocket:
    def __init__(self, incoming: list[bytes | str]) -> None:
        self.incoming = deque(incoming)
        self.sent: list[bytes] = []

    async def send(self, message: bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        if not self.incoming:
            raise MaxClientError("test socket exhausted")
        return self.incoming.popleft()

    def __aiter__(self) -> AsyncIterator[bytes | str]:
        return self

    async def __anext__(self) -> bytes | str:
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.popleft()


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket
        self.kwargs: dict[str, Any] = {}

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeConnector:
    def __init__(self, *websockets: FakeWebSocket) -> None:
        self.connections = deque(websockets)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, url: str, **kwargs: Any) -> FakeConnection:
        self.calls.append((url, kwargs))
        return FakeConnection(self.connections.popleft())


def settings() -> Settings:
    return Settings.from_env(
        {
            "MAX_VIEWER_ID": "123",
            "MAX_AUTH_TOKEN": "a" * 32,
            "MAX_DEVICE_ID": "123e4567-e89b-12d3-a456-426614174000",
            "MAX_DISCOVERY_MODE": "true",
        }
    )


def encoded(codec: FrameCodec, frame: Frame) -> bytes:
    return codec.encode(frame)


def login_success(codec: FrameCodec) -> list[bytes]:
    return [
        encoded(codec, Frame(cmd=1, seq=0, opcode=OPCODE_INIT, payload={"lang": "ru"})),
        encoded(
            codec,
            Frame(
                cmd=1,
                seq=1,
                opcode=OPCODE_LOGIN,
                payload={"profile": {"contact": {"id": 123}}, "time": 1000},
            ),
        ),
    ]


@pytest.mark.asyncio
async def test_handshake_acknowledges_ping_and_message_before_yield() -> None:
    codec = FrameCodec()
    message_payload = {
        "chatId": 42,
        "message": {"id": 777, "sender": 456, "text": "hello", "time": 1000},
    }
    websocket = FakeWebSocket(
        [
            *login_success(codec),
            encoded(codec, Frame(cmd=0, seq=20, opcode=OPCODE_KEEPALIVE)),
            encoded(
                codec, Frame(cmd=0, seq=21, opcode=OPCODE_NEW_MESSAGE, payload=message_payload)
            ),
        ]
    )
    connector = FakeConnector(websocket)
    client = MaxClient(settings(), MaxCredentials(123, "a" * 32), connector=connector)

    received = [frame async for frame in client._connected_events()]
    sent = [codec.decode(item) for item in websocket.sent]

    assert [frame.opcode for frame in received] == [OPCODE_NEW_MESSAGE]
    assert [frame.opcode for frame in sent] == [
        OPCODE_INIT,
        OPCODE_LOGIN,
        OPCODE_KEEPALIVE,
        OPCODE_NEW_MESSAGE,
    ]
    assert sent[-1].cmd == 1
    assert sent[-1].seq == 21
    assert sent[-1].payload == {"chatId": 42, "messageId": 777}
    assert sent[1].payload["token"] == "a" * 32


@pytest.mark.asyncio
async def test_connection_uses_tls_origin_and_disables_websocket_ping() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket(login_success(codec))
    connector = FakeConnector(websocket)
    client = MaxClient(settings(), MaxCredentials(123, "a" * 32), connector=connector)

    assert [frame async for frame in client._connected_events()] == []

    url, kwargs = connector.calls[0]
    assert url == "wss://api.oneme.ru/websocket"
    assert kwargs["origin"] == "https://web.max.ru"
    assert kwargs["ping_interval"] is None
    assert kwargs["max_size"] == 16 * 1024 * 1024


@pytest.mark.asyncio
async def test_login_error_is_classified_as_authentication_failure() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket(
        [
            encoded(codec, Frame(cmd=1, seq=0, opcode=OPCODE_INIT, payload={})),
            encoded(
                codec,
                Frame(
                    cmd=3,
                    seq=1,
                    opcode=OPCODE_LOGIN,
                    payload={"error": "login.token", "localizedMessage": "expired"},
                ),
            ),
        ]
    )
    client = MaxClient(
        settings(), MaxCredentials(123, "a" * 32), connector=FakeConnector(websocket)
    )

    with pytest.raises(MaxAuthenticationError, match=r"login\.token"):
        [frame async for frame in client._connected_events()]


@pytest.mark.asyncio
async def test_rejects_login_for_different_viewer() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket(
        [
            encoded(codec, Frame(cmd=1, seq=0, opcode=OPCODE_INIT, payload={})),
            encoded(
                codec,
                Frame(
                    cmd=1,
                    seq=1,
                    opcode=OPCODE_LOGIN,
                    payload={"profile": {"contact": {"id": 999}}},
                ),
            ),
        ]
    )
    client = MaxClient(
        settings(), MaxCredentials(123, "a" * 32), connector=FakeConnector(websocket)
    )

    with pytest.raises(MaxAuthenticationError, match="different viewer"):
        [frame async for frame in client._connected_events()]


@pytest.mark.asyncio
async def test_rejects_text_websocket_frame() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket([*login_success(codec), "unexpected text"])
    client = MaxClient(
        settings(), MaxCredentials(123, "a" * 32), connector=FakeConnector(websocket)
    )

    with pytest.raises(ProtocolError, match="text WebSocket"):
        [frame async for frame in client._connected_events()]


@pytest.mark.asyncio
async def test_rejects_malformed_message_push_without_ack() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket(
        [
            *login_success(codec),
            encoded(codec, Frame(cmd=0, seq=2, opcode=OPCODE_NEW_MESSAGE, payload={})),
        ]
    )
    client = MaxClient(
        settings(), MaxCredentials(123, "a" * 32), connector=FakeConnector(websocket)
    )

    with pytest.raises(ProtocolError, match="missing"):
        [frame async for frame in client._connected_events()]

    assert [codec.decode(item).opcode for item in websocket.sent] == [OPCODE_INIT, OPCODE_LOGIN]


@pytest.mark.asyncio
async def test_updates_refreshed_token_in_memory() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket(
        [
            encoded(codec, Frame(cmd=1, seq=0, opcode=OPCODE_INIT, payload={})),
            encoded(
                codec,
                Frame(
                    cmd=1,
                    seq=1,
                    opcode=OPCODE_LOGIN,
                    payload={
                        "profile": {"contact": {"id": 123}},
                        "token": "b" * 32,
                    },
                ),
            ),
        ]
    )
    client = MaxClient(
        settings(), MaxCredentials(123, "a" * 32), connector=FakeConnector(websocket)
    )

    assert [frame async for frame in client._connected_events()] == []
    assert client.credentials.token == "b" * 32


def test_reconnect_delay_is_capped_and_jittered() -> None:
    client = MaxClient(
        settings(),
        MaxCredentials(123, "a" * 32),
        random_uniform=lambda low, high: high,
    )

    assert client._reconnect_delay(0) == 0.5
    assert client._reconnect_delay(4) == 8.0
    assert client._reconnect_delay(10) == 10.0


@pytest.mark.asyncio
async def test_keepalive_sends_application_ping() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket([])
    client = MaxClient(
        settings(),
        MaxCredentials(123, "a" * 32),
        keepalive_interval=0.001,
    )

    task = asyncio.create_task(client._keepalive(websocket, asyncio.Lock()))
    await asyncio.sleep(0.005)
    client.stop()
    await task

    sent = [codec.decode(item) for item in websocket.sent]
    assert any(frame.opcode == OPCODE_KEEPALIVE and frame.cmd == 0 for frame in sent)


@pytest.mark.asyncio
async def test_events_reconnects_after_clean_socket_close() -> None:
    codec = FrameCodec()
    message = encoded(
        codec,
        Frame(
            cmd=0,
            seq=3,
            opcode=OPCODE_NEW_MESSAGE,
            payload={"chatId": 42, "message": {"id": 7, "sender": 5, "time": 1000}},
        ),
    )
    connector = FakeConnector(
        FakeWebSocket(login_success(codec)),
        FakeWebSocket([*login_success(codec), message]),
    )
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = MaxClient(
        settings(),
        MaxCredentials(123, "a" * 32),
        connector=connector,
        sleep=fake_sleep,
        random_uniform=lambda low, high: high,
    )
    iterator = client.events()

    received = await anext(iterator)
    client.stop()
    await iterator.aclose()

    assert received.opcode == OPCODE_NEW_MESSAGE
    assert len(connector.calls) == 2
    assert sleeps == [0.5]
