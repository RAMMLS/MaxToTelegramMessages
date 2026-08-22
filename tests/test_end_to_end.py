from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from typing import Any

import pytest

from max_to_telegram.auth import MaxCredentials
from max_to_telegram.bridge import Bridge
from max_to_telegram.config import Settings
from max_to_telegram.dedupe import DedupeStore
from max_to_telegram.max_client import OPCODE_INIT, OPCODE_LOGIN, OPCODE_NEW_MESSAGE, MaxClient
from max_to_telegram.parser import ChatPolicy, MessageParser
from max_to_telegram.protocol import Frame, FrameCodec
from max_to_telegram.telegram import TelegramSender

BOT_TOKEN = "1234567890:AA_TEST_token_1234567890abcdefghijk"


class FakeWebSocket:
    def __init__(self, incoming: list[bytes]) -> None:
        self.incoming = deque(incoming)
        self.sent: list[bytes] = []

    async def send(self, message: bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes:
        return self.incoming.popleft()

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self

    async def __anext__(self) -> bytes:
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.popleft()


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *args: object) -> None:
        return None


class OneConnectionClient(MaxClient):
    """Exercise one complete connection without the production reconnect loop."""

    async def events(self) -> AsyncIterator[Frame]:
        async for frame in self._connected_events():
            yield frame


class FakeResponse:
    status = 200

    def __init__(self, message_id: int) -> None:
        self.message_id = message_id

    async def json(self, *, content_type: None = None) -> dict[str, Any]:
        return {"ok": True, "result": {"message_id": self.message_id}}

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeTelegramSession:
    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, kwargs))
        return FakeResponse(9000 + len(self.requests))

    async def close(self) -> None:
        return None


def _push(codec: FrameCodec, *, seq: int, chat_id: int, text: str) -> bytes:
    return codec.encode(
        Frame(
            cmd=0,
            seq=seq,
            opcode=OPCODE_NEW_MESSAGE,
            payload={
                "chatId": chat_id,
                "chat": {"title": f"Room {chat_id}"},
                "sender": {"displayName": "Synthetic sender"},
                "message": {
                    "id": seq * 100,
                    "sender": 456,
                    "text": text,
                    "time": 1_777_000_000_000 + seq,
                },
            },
        )
    )


@pytest.mark.asyncio
async def test_binary_max_pipeline_forwards_only_allowlisted_chat(tmp_path) -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket(
        [
            codec.encode(Frame(cmd=1, seq=0, opcode=OPCODE_INIT, payload={})),
            codec.encode(
                Frame(
                    cmd=1,
                    seq=1,
                    opcode=OPCODE_LOGIN,
                    payload={"profile": {"contact": {"id": 123}}},
                )
            ),
            _push(codec, seq=20, chat_id=99, text="must stay in MAX"),
            _push(codec, seq=21, chat_id=42, text="selected notification"),
        ]
    )
    settings = Settings.from_env(
        {
            "MAX_VIEWER_ID": "123",
            "MAX_AUTH_TOKEN": "max-test-token-1234567890123456",
            "MAX_CHAT_IDS": "42",
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "TELEGRAM_CHAT_ID": "-100123",
            "BRIDGE_STATE_DB": str(tmp_path / "state.db"),
        }
    )
    source = OneConnectionClient(
        settings,
        MaxCredentials(123, "max-test-token-1234567890123456"),
        connector=lambda *_args, **_kwargs: FakeConnection(websocket),
        codec=codec,
        keepalive_interval=3600,
    )
    telegram_session = FakeTelegramSession()
    sender = TelegramSender(
        BOT_TOKEN,
        "-100123",
        session=telegram_session,
    )
    store = DedupeStore(settings.state_db).open()
    runtime = Bridge(
        source=source,
        parser=MessageParser(viewer_id=123),
        policy=ChatPolicy(settings.max_chat_ids),
        discovery_mode=False,
        queue_size=2,
        store=store,
        sender=sender,
    )
    source.set_before_message_ack(runtime.persist_before_ack)

    try:
        await runtime.run()

        outbound_frames = [codec.decode(raw) for raw in websocket.sent]
        message_acks = [
            frame
            for frame in outbound_frames
            if frame.cmd == 1 and frame.opcode == OPCODE_NEW_MESSAGE
        ]
        assert [frame.payload for frame in message_acks] == [
            {"chatId": 99, "messageId": 2000},
            {"chatId": 42, "messageId": 2100},
        ]
        assert len(telegram_session.requests) == 1
        request_url, request = telegram_session.requests[0]
        assert request_url.endswith("/sendMessage")
        assert request["json"]["chat_id"] == "-100123"
        assert "selected notification" in request["json"]["text"]
        assert "must stay in MAX" not in request["json"]["text"]
        assert runtime.stats.delivered_messages == 1
        assert runtime.stats.rejected_messages == 1
        assert store.stats().pending == 0
        assert store.stats().delivered == 1
        assert store.pending_messages() == ()
    finally:
        await runtime.close()
        store.close()
