from __future__ import annotations

import html
from collections import deque
from datetime import datetime, timezone
from typing import Any

import aiohttp
import pytest

from max_to_telegram.parser import ParsedMessage
from max_to_telegram.telegram import (
    TELEGRAM_TEXT_LIMIT,
    TelegramPermanentError,
    TelegramRetryExhausted,
    TelegramSender,
    format_message_chunks,
)


class FakeResponse:
    def __init__(self, status: int, document: Any) -> None:
        self.status = status
        self.document = document

    async def json(self, *, content_type: None = None) -> Any:
        if isinstance(self.document, Exception):
            raise self.document
        return self.document

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeSession:
    def __init__(self, *responses: FakeResponse | Exception) -> None:
        self.responses = deque(responses)
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.closed = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.requests.append((url, kwargs))
        response = self.responses.popleft()
        if isinstance(response, Exception):
            raise response
        return response

    async def close(self) -> None:
        self.closed = True


def parsed_message(**overrides: object) -> ParsedMessage:
    values: dict[str, object] = {
        "chat_id": 42,
        "message_id": "777",
        "sender_id": 456,
        "sender_name": "Ada <Admin>",
        "chat_title": "R&D & QA",
        "text": "Hello <world> & everyone",
        "timestamp": datetime(2026, 8, 22, 0, 0, tzinfo=timezone.utc),
        "timestamp_raw": 1_777_000_000_000,
        "update_time": None,
        "status": None,
        "message_type": "USER",
        "attachments": (),
        "is_outgoing": False,
        "is_service": False,
    }
    values.update(overrides)
    return ParsedMessage(**values)  # type: ignore[arg-type]


def test_formats_safe_html_and_moscow_time() -> None:
    chunks = format_message_chunks(parsed_message())

    assert len(chunks) == 1
    assert "Ada &lt;Admin&gt;" in chunks[0]
    assert "R&amp;D &amp; QA" in chunks[0]
    assert "Hello &lt;world&gt; &amp; everyone" in chunks[0]
    assert "22.08.2026 03:00:00 MSK" in chunks[0]


def test_marks_edited_message() -> None:
    chunk = format_message_chunks(parsed_message(status="EDITED"))[0]

    assert "изменено" in chunk


def test_splits_pathological_html_content_within_limit() -> None:
    content = "<&>" * 5000

    chunks = format_message_chunks(parsed_message(text=content))

    assert len(chunks) > 1
    assert all(len(chunk) <= TELEGRAM_TEXT_LIMIT for chunk in chunks)
    joined_visible = "".join(
        html.unescape(chunk.split("</i>\n", 1)[-1].rsplit("\n\n", 1)[-1]) for chunk in chunks
    )
    assert joined_visible == content


@pytest.mark.asyncio
async def test_sends_message_and_returns_telegram_id() -> None:
    session = FakeSession(FakeResponse(200, {"ok": True, "result": {"message_id": 99}}))
    sender = TelegramSender("token-1234567890123456", "42", session=session)

    result = await sender.send(parsed_message())

    assert result == (99,)
    url, kwargs = session.requests[0]
    assert url.endswith("/sendMessage")
    assert kwargs["json"]["chat_id"] == "42"
    assert kwargs["json"]["parse_mode"] == "HTML"


@pytest.mark.asyncio
async def test_rate_limit_uses_server_retry_after() -> None:
    session = FakeSession(
        FakeResponse(
            429,
            {
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 7},
            },
        ),
        FakeResponse(200, {"ok": True, "result": {"message_id": 100}}),
    )
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    sender = TelegramSender(
        "token-1234567890123456",
        "42",
        max_retries=2,
        session=session,
        sleep=fake_sleep,
    )

    assert await sender.send(parsed_message()) == (100,)
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_retries_server_error_with_exponential_delay() -> None:
    session = FakeSession(
        FakeResponse(502, {"ok": False, "description": "Bad Gateway"}),
        FakeResponse(200, {"ok": True, "result": {"message_id": 101}}),
    )
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    sender = TelegramSender(
        "token-1234567890123456", "42", max_retries=1, session=session, sleep=fake_sleep
    )

    assert await sender.send(parsed_message()) == (101,)
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_retries_network_error_without_leaking_url() -> None:
    session = FakeSession(aiohttp.ClientConnectionError("offline"))
    token = "token-super-secret-1234567890"
    sender = TelegramSender(token, "42", max_retries=0, session=session)

    with pytest.raises(TelegramRetryExhausted) as raised:
        await sender.send(parsed_message())

    assert token not in str(raised.value)


@pytest.mark.asyncio
async def test_does_not_retry_permanent_error_and_redacts_token() -> None:
    token = "token-super-secret-1234567890"
    session = FakeSession(
        FakeResponse(
            401,
            {"ok": False, "error_code": 401, "description": f"bad token {token}"},
        )
    )
    sender = TelegramSender(token, "42", max_retries=5, session=session)

    with pytest.raises(TelegramPermanentError) as raised:
        await sender.send(parsed_message())

    assert len(session.requests) == 1
    assert token not in str(raised.value)
    assert "<redacted>" in str(raised.value)


@pytest.mark.asyncio
async def test_sends_all_long_message_chunks() -> None:
    message = parsed_message(text="x" * 10_000)
    expected_chunks = format_message_chunks(message)
    responses = [
        FakeResponse(200, {"ok": True, "result": {"message_id": index}})
        for index in range(1, len(expected_chunks) + 1)
    ]
    session = FakeSession(*responses)
    sender = TelegramSender("token-1234567890123456", "42", session=session)

    result = await sender.send(message)

    assert result == tuple(range(1, len(expected_chunks) + 1))
    assert len(session.requests) == len(expected_chunks)


@pytest.mark.parametrize(
    ("token", "chat_id", "message"),
    [
        ("", "42", "token"),
        ("bad token", "42", "token"),
        ("token-1234567890123456", "", "chat ID"),
    ],
)
def test_rejects_invalid_sender_configuration(token: str, chat_id: str, message: str) -> None:
    with pytest.raises(TelegramPermanentError, match=message):
        TelegramSender(token, chat_id)
