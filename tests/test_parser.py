from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from max_to_telegram.parser import (
    ChatPolicy,
    MessageParseError,
    MessageParser,
    PolicyDecision,
)
from max_to_telegram.protocol import Frame


def push(payload: object) -> Frame:
    return Frame(cmd=0, seq=1, opcode=128, payload=payload)


def text_payload(**message_overrides: object) -> dict[str, object]:
    message: dict[str, object] = {
        "id": 777,
        "sender": 456,
        "text": "  hello from MAX  ",
        "time": 1_700_000_000_000,
    }
    message.update(message_overrides)
    return {
        "chatId": -42,
        "chat": {
            "id": -42,
            "title": "Selected chat",
            "recipient": {"firstName": "Ada", "lastName": "Lovelace"},
        },
        "message": message,
    }


def test_parses_text_message_and_names() -> None:
    message = MessageParser(viewer_id=123).parse(push(text_payload()))

    assert message.chat_id == -42
    assert message.message_id == "777"
    assert message.sender_id == 456
    assert message.sender_name == "Ada Lovelace"
    assert message.chat_title == "Selected chat"
    assert message.text == "hello from MAX"
    assert message.timestamp == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)
    assert message.is_outgoing is False
    assert message.content == "hello from MAX"


def test_parses_attachment_only_message() -> None:
    payload = text_payload(
        text="",
        attaches=[{"_type": "PHOTO"}, {"_type": "FILE"}, {"_type": "FUTURE_KIND"}],
    )

    message = MessageParser(viewer_id=123).parse(push(payload))

    assert message.attachments == ("Фото", "Файл", "Вложение FUTURE_KIND")
    assert message.content == "[Фото]\n[Файл]\n[Вложение FUTURE_KIND]"


def test_control_attachment_marks_service_message() -> None:
    message = MessageParser(viewer_id=123).parse(
        push(text_payload(text="", attaches=[{"_type": "CONTROL", "event": "join"}]))
    )

    assert message.is_service is True
    assert ChatPolicy(frozenset({-42})).decide(message) is PolicyDecision.SERVICE


def test_policy_forwards_only_exact_selected_chat() -> None:
    message = MessageParser(viewer_id=123).parse(push(text_payload()))

    assert ChatPolicy(frozenset({-42})).decide(message) is PolicyDecision.FORWARD
    assert ChatPolicy(frozenset({42})).decide(message) is PolicyDecision.CHAT_NOT_ALLOWED
    assert ChatPolicy(frozenset()).decide(message) is PolicyDecision.CHAT_NOT_ALLOWED


def test_policy_rejects_outgoing_message_even_in_selected_chat() -> None:
    message = MessageParser(viewer_id=123).parse(push(text_payload(sender=123)))

    assert message.is_outgoing is True
    assert ChatPolicy(frozenset({-42})).decide(message) is PolicyDecision.OUTGOING


@pytest.mark.parametrize("status", ["REMOVED", "SPAM", "DELAYED_FIRE_ERROR"])
def test_policy_rejects_removed_states(status: str) -> None:
    message = MessageParser(viewer_id=123).parse(push(text_payload(status=status)))

    assert ChatPolicy(frozenset({-42})).decide(message) is PolicyDecision.REMOVED


def test_policy_allows_edited_message_as_a_revision() -> None:
    message = MessageParser(viewer_id=123).parse(
        push(text_payload(status="EDITED", updateTime=1_700_000_000_500))
    )

    assert ChatPolicy(frozenset({-42})).decide(message) is PolicyDecision.FORWARD
    original = MessageParser(viewer_id=123).parse(push(text_payload()))
    assert message.dedupe_key.startswith("v1:")
    assert message.dedupe_key != original.dedupe_key


def test_dedupe_key_is_opaque_and_separator_collision_resistant() -> None:
    original = MessageParser(viewer_id=123).parse(push(text_payload()))
    first = replace(original, message_id="a:b", status="c")
    second = replace(original, message_id="a", status="b:c")

    assert first.dedupe_key != second.dedupe_key
    assert len(first.dedupe_key) == 67
    assert str(first.chat_id) not in first.dedupe_key
    assert first.message_id not in first.dedupe_key


def test_empty_message_is_rejected_by_policy() -> None:
    message = MessageParser(viewer_id=123).parse(push(text_payload(text="", attaches=[])))

    assert ChatPolicy(frozenset({-42})).decide(message) is PolicyDecision.EMPTY


def test_falls_back_to_numeric_names() -> None:
    payload = text_payload()
    payload["chat"] = {}

    message = MessageParser(viewer_id=123).parse(push(payload))

    assert message.sender_name == "MAX user 456"
    assert message.chat_title == "MAX chat -42"


def test_supports_seconds_timestamp_and_string_ids() -> None:
    payload = text_payload(id="message-1", time=1_700_000_000)

    message = MessageParser(viewer_id=123).parse(push(payload))

    assert message.message_id == "message-1"
    assert message.timestamp == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "payload"),
        ({}, "message object"),
        ({"chatId": True, "message": {"id": 1, "time": 1}}, "chatId"),
        ({"chatId": 1, "message": {"id": True, "time": 1}}, "message.id"),
        ({"chatId": 1.5, "message": {"id": 1, "time": 1}}, "chatId"),
        ({"chatId": "1", "message": {"id": 1, "time": 1}}, "chatId"),
        ({"chatId": 1, "message": {"id": 1, "time": "never"}}, "message.time"),
        ({"chatId": 1, "message": {"id": 1, "time": 1.5}}, "message.time"),
        ({"chatId": 2**63, "message": {"id": 1, "time": 1}}, "signed int64"),
        ({"chatId": 1, "message": {"id": 1, "time": 2**63}}, "signed int64"),
        (
            {"chatId": 1, "message": {"id": 1, "time": 1, "sender": -(2**63) - 1}},
            "signed int64",
        ),
        ({"chatId": 1, "message": {"id": 1, "time": 1, "attaches": {}}}, "attaches"),
    ],
)
def test_rejects_malformed_pushes(payload: object, message: str) -> None:
    with pytest.raises(MessageParseError, match=message):
        MessageParser(viewer_id=123).parse(push(payload))


def test_rejects_non_message_opcode() -> None:
    with pytest.raises(MessageParseError, match="opcode 128"):
        MessageParser(viewer_id=123).parse(Frame(cmd=0, seq=1, opcode=129, payload={}))


def test_normalizes_whitespace_in_display_names() -> None:
    payload = text_payload(senderName="  Grace\n  Hopper ")
    payload["chat"] = {"displayName": "  Project   Room "}

    message = MessageParser(viewer_id=123).parse(push(payload))

    assert message.sender_name == "Grace Hopper"
    assert message.chat_title == "Project Room"


@pytest.mark.parametrize(
    ("attaches", "error"),
    [
        ([{"_type": "PHOTO"}] * 101, "too many"),
        ([{"_type": "X" * 65}], "type is too long"),
    ],
)
def test_bounds_attachment_metadata(attaches: list[dict[str, str]], error: str) -> None:
    with pytest.raises(MessageParseError, match=error):
        MessageParser(viewer_id=123).parse(push(text_payload(attaches=attaches)))
