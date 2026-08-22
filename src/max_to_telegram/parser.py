"""Normalize MAX message pushes and enforce the selected-chat policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, cast

from max_to_telegram.max_client import OPCODE_NEW_MESSAGE
from max_to_telegram.protocol import Frame


class MessageParseError(ValueError):
    """Raised when a message push lacks fields required for safe forwarding."""


class PolicyDecision(str, Enum):
    FORWARD = "forward"
    CHAT_NOT_ALLOWED = "chat_not_allowed"
    OUTGOING = "outgoing"
    REMOVED = "removed"
    SERVICE = "service"
    EMPTY = "empty"


_ATTACHMENT_LABELS = {
    "PHOTO": "Фото",
    "VIDEO": "Видео",
    "AUDIO": "Аудио",
    "FILE": "Файл",
    "STICKER": "Стикер",
    "CONTACT": "Контакт",
    "LOCATION": "Геопозиция",
    "POLL": "Опрос",
    "CALL": "Звонок",
    "SHARE": "Пересланный материал",
    "WEB_APP": "Мини-приложение",
    "VIDEO_MESSAGE": "Видеосообщение",
}
_REMOVED_STATUSES = frozenset({"REMOVED", "SPAM", "DELAYED_FIRE_ERROR"})
_MAX_ATTACHMENTS = 100
_MAX_ATTACHMENT_TYPE_CHARS = 64


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    chat_id: int
    message_id: str
    sender_id: int | None
    sender_name: str
    chat_title: str
    text: str
    timestamp: datetime
    timestamp_raw: int
    update_time: int | None
    status: str | None
    message_type: str
    attachments: tuple[str, ...]
    is_outgoing: bool
    is_service: bool

    @property
    def content(self) -> str:
        parts = [self.text] if self.text else []
        parts.extend(f"[{attachment}]" for attachment in self.attachments)
        return "\n".join(parts)

    @property
    def dedupe_key(self) -> str:
        revision = self.update_time or self.timestamp_raw
        return f"{self.chat_id}:{self.message_id}:{self.status or 'NEW'}:{revision}"


@dataclass(frozen=True, slots=True)
class ChatPolicy:
    """Exact, fail-closed allowlist for MAX chats."""

    allowed_chat_ids: frozenset[int]

    def decide(self, message: ParsedMessage) -> PolicyDecision:
        if message.chat_id not in self.allowed_chat_ids:
            return PolicyDecision.CHAT_NOT_ALLOWED
        if message.is_outgoing:
            return PolicyDecision.OUTGOING
        if message.status in _REMOVED_STATUSES:
            return PolicyDecision.REMOVED
        if message.is_service:
            return PolicyDecision.SERVICE
        if not message.content:
            return PolicyDecision.EMPTY
        return PolicyDecision.FORWARD


class MessageParser:
    def __init__(self, *, viewer_id: int) -> None:
        self.viewer_id = viewer_id

    def parse(self, frame: Frame) -> ParsedMessage:
        if frame.opcode != OPCODE_NEW_MESSAGE:
            raise MessageParseError(f"expected opcode {OPCODE_NEW_MESSAGE}")
        if not isinstance(frame.payload, dict):
            raise MessageParseError("message push payload must be an object")

        payload = frame.payload
        message = payload.get("message")
        if not isinstance(message, dict):
            raise MessageParseError("message push has no message object")

        chat_id = _required_int(payload.get("chatId"), "chatId", allow_negative=True)
        message_id = _message_id(message.get("id"))
        sender_id = _optional_int(message.get("sender"), "message.sender")
        timestamp_raw = _required_int(
            message.get("time", message.get("updateTime")),
            "message.time",
            allow_negative=False,
        )
        update_time = _optional_int(message.get("updateTime"), "message.updateTime")
        status = _optional_text(message.get("status"), "message.status", maximum=64)
        message_type = _optional_text(message.get("type"), "message.type", maximum=64) or "USER"
        text = _optional_text(message.get("text"), "message.text", maximum=1_000_000) or ""
        attachments, is_service = _parse_attachments(message.get("attaches"))

        raw_chat = payload.get("chat")
        chat: dict[Any, Any] = raw_chat if isinstance(raw_chat, dict) else {}
        sender_name = _display_name(
            payload.get("sender"),
            message.get("senderName"),
            chat.get("recipient"),
            fallback=f"MAX user {sender_id}" if sender_id is not None else "MAX user",
        )
        chat_title = _display_name(
            chat.get("title"),
            chat.get("name"),
            chat.get("displayName"),
            chat.get("recipient"),
            fallback=f"MAX chat {chat_id}",
        )

        return ParsedMessage(
            chat_id=chat_id,
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            chat_title=chat_title,
            text=text.strip(),
            timestamp=_timestamp(timestamp_raw),
            timestamp_raw=timestamp_raw,
            update_time=update_time,
            status=status,
            message_type=message_type,
            attachments=attachments,
            is_outgoing=sender_id == self.viewer_id,
            is_service=is_service,
        )


def _required_int(value: Any, field: str, *, allow_negative: bool) -> int:
    parsed = _optional_int(value, field)
    if parsed is None:
        raise MessageParseError(f"{field} is required")
    if parsed == 0 or (parsed < 0 and not allow_negative):
        raise MessageParseError(f"{field} has an invalid value")
    return parsed


def _optional_int(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise MessageParseError(f"{field} must be an integer")
    return cast(int, value)


def _message_id(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        raise MessageParseError("message.id is required")
    if not isinstance(value, (int, str)):
        raise MessageParseError("message.id must be an integer or string")
    result = str(value).strip()
    if not result or len(result) > 256:
        raise MessageParseError("message.id has an invalid value")
    return result


def _optional_text(value: Any, field: str, *, maximum: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise MessageParseError(f"{field} must be text")
    if len(value) > maximum:
        raise MessageParseError(f"{field} is too long")
    return value


def _timestamp(value: int) -> datetime:
    seconds = value / 1000 if value >= 10_000_000_000 else value
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:
        raise MessageParseError("message.time is outside the supported range") from exc


def _parse_attachments(value: Any) -> tuple[tuple[str, ...], bool]:
    if value is None:
        return (), False
    if not isinstance(value, list):
        raise MessageParseError("message.attaches must be an array")
    if len(value) > _MAX_ATTACHMENTS:
        raise MessageParseError("message.attaches contains too many items")
    labels: list[str] = []
    is_service = False
    for attachment in value:
        if not isinstance(attachment, dict):
            labels.append("Вложение")
            continue
        raw_type = attachment.get("_type", attachment.get("type"))
        if not isinstance(raw_type, str) or not raw_type.strip():
            labels.append("Вложение")
            continue
        attachment_type = raw_type.strip().upper()
        if len(attachment_type) > _MAX_ATTACHMENT_TYPE_CHARS:
            raise MessageParseError("message attachment type is too long")
        if attachment_type == "CONTROL":
            is_service = True
            continue
        labels.append(_ATTACHMENT_LABELS.get(attachment_type, f"Вложение {attachment_type}"))
    return tuple(labels), is_service


def _display_name(*values: Any, fallback: str) -> str:
    for value in values:
        candidate = _name_candidate(value)
        if candidate:
            return candidate
    return fallback


def _name_candidate(value: Any) -> str:
    if isinstance(value, str):
        return _clean_display(value)
    if not isinstance(value, dict):
        return ""
    for key in ("displayName", "name", "title", "fullName"):
        if isinstance(value.get(key), str):
            candidate = _clean_display(value[key])
            if candidate:
                return candidate
    parts = [value.get("firstName"), value.get("lastName")]
    return _clean_display(" ".join(part for part in parts if isinstance(part, str)))


def _clean_display(value: str) -> str:
    cleaned = " ".join(value.split())
    return cleaned[:256]
