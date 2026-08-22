from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from max_to_telegram.config import Settings
from max_to_telegram.protocol import Frame, FrameCodec
from max_to_telegram.public_probe import PublicProbeError, probe_max_public


class FakeWebSocket:
    def __init__(self, incoming: bytes | str) -> None:
        self.incoming = incoming
        self.sent: list[bytes] = []

    async def send(self, message: bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> bytes | str:
        return self.incoming

    def __aiter__(self) -> AsyncIterator[bytes | str]:
        return self

    async def __anext__(self) -> bytes | str:
        raise StopAsyncIteration


class FakeConnection:
    def __init__(self, websocket: FakeWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> FakeWebSocket:
        return self.websocket

    async def __aexit__(self, *args: object) -> None:
        return None


def public_settings() -> Settings:
    return Settings.from_env({}, purpose="max_public")


@pytest.mark.asyncio
async def test_public_probe_sends_only_init_and_returns_content_free_result() -> None:
    codec = FrameCodec()
    websocket = FakeWebSocket(
        codec.encode(
            Frame(
                cmd=1,
                seq=0,
                opcode=6,
                payload={"lang": "ru", "public": "x" * 100},
            )
        )
    )
    calls: list[tuple[str, dict[str, object]]] = []

    def connector(url: str, **kwargs: object) -> FakeConnection:
        calls.append((url, kwargs))
        return FakeConnection(websocket)

    result = await probe_max_public(public_settings(), connector=connector)

    assert result.endpoint_host == "api.oneme.ru"
    assert result.protocol_version == 10
    assert result.init_opcode == 6
    assert result.compressed_response is True
    assert result.public_config_key_count == 2
    assert len(websocket.sent) == 1
    request = codec.decode(websocket.sent[0])
    assert request.cmd == 0 and request.seq == 0 and request.opcode == 6
    assert isinstance(request.payload, dict)
    assert "token" not in request.payload
    assert calls[0][0] == "wss://api.oneme.ru/websocket"
    assert calls[0][1]["origin"] == "https://web.max.ru"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        "text frame",
        FrameCodec().encode(Frame(cmd=1, seq=9, opcode=6, payload={})),
        FrameCodec().encode(Frame(cmd=1, seq=0, opcode=6, payload=[])),
    ],
)
async def test_public_probe_rejects_unexpected_response(response: bytes | str) -> None:
    with pytest.raises(PublicProbeError):
        await probe_max_public(
            public_settings(),
            connector=lambda *_args, **_kwargs: FakeConnection(FakeWebSocket(response)),
        )
