"""Telegram Bot API sender with bounded retries and safe HTML formatting."""

from __future__ import annotations

import asyncio
import html
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, cast
from zoneinfo import ZoneInfo

import aiohttp

from max_to_telegram.parser import ParsedMessage

TELEGRAM_TEXT_LIMIT = 4096
_MOSCOW = ZoneInfo("Europe/Moscow")


class TelegramError(RuntimeError):
    """Base class for sanitized Telegram delivery errors."""


class TelegramPermanentError(TelegramError):
    """A request that must not be retried without changing configuration."""


class TelegramRetryExhausted(TelegramError):
    """A transient request that failed after the configured retry budget."""


@dataclass(frozen=True, slots=True)
class TelegramValidation:
    """Non-secret result of Telegram getMe/getChat checks."""

    bot_username: str
    chat_type: str
    chat_title: str


@dataclass(frozen=True, slots=True)
class TelegramDiscoveredChat:
    """Content-free destination metadata found in recent bot updates."""

    chat_id: int
    chat_type: str
    chat_title: str


class ResponseLike(Protocol):
    status: int

    async def json(self, *, content_type: None = None) -> Any: ...

    async def __aenter__(self) -> ResponseLike: ...

    async def __aexit__(self, *args: object) -> None: ...


class SessionLike(Protocol):
    def post(self, url: str, **kwargs: Any) -> ResponseLike: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class TelegramSender:
    bot_token: str = field(repr=False)
    chat_id: str
    max_retries: int = 5
    session: SessionLike | aiohttp.ClientSession | None = field(default=None, repr=False)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    request_timeout: float = 20.0
    allow_missing_chat_id: bool = field(default=False, repr=False)
    _owns_session: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.bot_token or any(character.isspace() for character in self.bot_token):
            raise TelegramPermanentError("Telegram bot token is empty or malformed")
        if not self.chat_id and not self.allow_missing_chat_id:
            raise TelegramPermanentError("Telegram chat ID is empty")
        if self.max_retries < 0:
            raise TelegramPermanentError("Telegram retry count must not be negative")

    async def send(self, message: ParsedMessage) -> tuple[int, ...]:
        """Send one normalized MAX message, splitting it when necessary."""

        message_ids: list[int] = []
        for chunk in format_message_chunks(message):
            message_ids.append(await self._send_chunk(chunk))
        return tuple(message_ids)

    async def validate(self) -> TelegramValidation:
        """Validate the bot token and destination without sending a message."""

        bot_document = await self._call("getMe", {})
        chat_document = await self._call("getChat", {"chat_id": self.chat_id})
        bot = bot_document.get("result") if isinstance(bot_document, dict) else None
        chat = chat_document.get("result") if isinstance(chat_document, dict) else None
        if not isinstance(bot, dict) or not isinstance(bot.get("username"), str):
            raise TelegramPermanentError("Telegram getMe response has no bot username")
        if not isinstance(chat, dict) or not isinstance(chat.get("type"), str):
            raise TelegramPermanentError("Telegram getChat response has no chat metadata")
        title = _chat_title(chat)
        return TelegramValidation(
            bot_username=bot["username"],
            chat_type=chat["type"],
            chat_title=title,
        )

    async def discover_chats(self) -> tuple[TelegramDiscoveredChat, ...]:
        """Return unique chat metadata from recent updates without exposing content."""

        document = await self._call(
            "getUpdates",
            {
                "limit": 100,
                "timeout": 0,
            },
        )
        updates = document.get("result")
        if not isinstance(updates, list):
            raise TelegramPermanentError("Telegram getUpdates response has no update list")
        discovered: dict[int, TelegramDiscoveredChat] = {}
        for update in updates:
            chat = _update_chat(update)
            if chat is None:
                continue
            chat_id = chat.get("id")
            chat_type = chat.get("type")
            if isinstance(chat_id, bool) or not isinstance(chat_id, int):
                continue
            if not isinstance(chat_type, str) or not chat_type.strip():
                continue
            discovered[chat_id] = TelegramDiscoveredChat(
                chat_id=chat_id,
                chat_type=chat_type.strip()[:64],
                chat_title=_chat_title(chat),
            )
        return tuple(discovered.values())

    async def close(self) -> None:
        if self.session is not None and self._owns_session:
            await self.session.close()
            self.session = None
            self._owns_session = False

    async def _send_chunk(self, text: str) -> int:
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        }
        document = await self._call("sendMessage", payload)
        return _telegram_message_id(document)

    async def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"

        for attempt in range(self.max_retries + 1):
            try:
                response_status, document = await self._post_json(url, payload)
            except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError):
                if attempt >= self.max_retries:
                    raise TelegramRetryExhausted(
                        f"Telegram network request failed after {attempt + 1} attempts"
                    ) from None
                await self.sleep(_retry_delay(attempt))
                continue

            ok = isinstance(document, dict) and document.get("ok") is True
            if response_status == 200 and ok:
                return cast(dict[str, Any], document)

            error_code = (
                document.get("error_code") if isinstance(document, dict) else response_status
            )
            description = _safe_description(document, self.bot_token)
            retry_after = _retry_after(document)
            is_rate_limited = response_status == 429 or error_code == 429
            is_transient = response_status in {408, 425} or 500 <= response_status <= 599

            if is_rate_limited or is_transient:
                if attempt >= self.max_retries:
                    raise TelegramRetryExhausted(
                        f"Telegram request failed after {attempt + 1} attempts: {description}"
                    )
                await self.sleep(retry_after if is_rate_limited else _retry_delay(attempt))
                continue

            raise TelegramPermanentError(
                f"Telegram rejected {method} ({error_code}): {description}"
            )

        raise TelegramRetryExhausted("Telegram retry loop ended unexpectedly")

    async def _post_json(self, url: str, payload: dict[str, Any]) -> tuple[int, Any]:
        session = self._session()
        timeout = aiohttp.ClientTimeout(total=self.request_timeout)
        async with session.post(url, json=payload, timeout=timeout) as response:
            try:
                document = await response.json(content_type=None)
            except (aiohttp.ClientError, ValueError, TypeError):
                document = None
            return response.status, document

    def _session(self) -> SessionLike | aiohttp.ClientSession:
        session = self.session
        if session is None:
            session = aiohttp.ClientSession()
            self.session = session
            self._owns_session = True
        return session


def format_message_chunks(message: ParsedMessage) -> tuple[str, ...]:
    """Render conservative Telegram HTML chunks, each at most 4096 bytes/chars."""

    chat = html.escape(message.chat_title, quote=False)
    sender = html.escape(message.sender_name, quote=False)
    status = " · ✏️ изменено" if message.status == "EDITED" else ""
    timestamp = _format_timestamp(message.timestamp)
    header = f"<b>MAX · {chat}</b>\n<b>{sender}</b>{status}\n<code>{timestamp}</code>\n\n"

    content = message.content or "(без текста)"
    part_reserve = len("<i>Часть 9999/9999</i>\n")
    budget = TELEGRAM_TEXT_LIMIT - len(header) - part_reserve
    if budget < 256:
        raise TelegramPermanentError("MAX sender/chat names leave no room for Telegram content")
    escaped_parts = _split_escaped(content, budget)
    total = len(escaped_parts)

    result: list[str] = []
    for index, part in enumerate(escaped_parts, start=1):
        part_label = f"<i>Часть {index}/{total}</i>\n" if total > 1 else ""
        rendered = f"{header}{part_label}{part}"
        if len(rendered) > TELEGRAM_TEXT_LIMIT:
            raise TelegramPermanentError("formatted Telegram message exceeds 4096 characters")
        result.append(rendered)
    return tuple(result)


def _split_escaped(text: str, maximum_encoded_length: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    for character in text:
        escaped = html.escape(character, quote=False)
        if current and current_length + len(escaped) > maximum_encoded_length:
            chunks.append("".join(current))
            current = []
            current_length = 0
        current.append(escaped)
        current_length += len(escaped)
    if current:
        chunks.append("".join(current))
    return chunks or [html.escape("(без текста)", quote=False)]


def _format_timestamp(timestamp: datetime) -> str:
    return timestamp.astimezone(_MOSCOW).strftime("%d.%m.%Y %H:%M:%S MSK")


def _telegram_message_id(document: dict[str, Any]) -> int:
    result = document.get("result")
    message_id = result.get("message_id") if isinstance(result, dict) else None
    if isinstance(message_id, bool) or not isinstance(message_id, int):
        raise TelegramPermanentError("Telegram success response has no message_id")
    return message_id


def _retry_after(document: Any) -> float:
    if isinstance(document, dict):
        parameters = document.get("parameters")
        if isinstance(parameters, dict):
            raw = parameters.get("retry_after")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and raw >= 0:
                return min(float(raw), 300.0)
    return 1.0


def _retry_delay(attempt: int) -> float:
    return float(min(0.5 * (2**attempt), 10.0))


def _safe_description(document: Any, token: str) -> str:
    if not isinstance(document, dict):
        return "unknown Telegram error"
    description = document.get("description")
    if not isinstance(description, str):
        return "unknown Telegram error"
    return description.replace(token, "<redacted>")[:500]


def _chat_title(chat: dict[str, Any]) -> str:
    for key in ("title", "username"):
        value = chat.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:256]
    names = [chat.get("first_name"), chat.get("last_name")]
    rendered = " ".join(
        value.strip() for value in names if isinstance(value, str) and value.strip()
    )
    return rendered[:256] or "(без названия)"


def _update_chat(update: Any) -> dict[str, Any] | None:
    if not isinstance(update, dict):
        return None
    for key in (
        "message",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "my_chat_member",
    ):
        event = update.get(key)
        if isinstance(event, dict) and isinstance(event.get("chat"), dict):
            return cast(dict[str, Any], event["chat"])
    return None
