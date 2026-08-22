from __future__ import annotations

import random
from datetime import datetime, timezone

from max_to_telegram.max_client import OPCODE_NEW_MESSAGE
from max_to_telegram.parser import MessageParseError, MessageParser, ParsedMessage
from max_to_telegram.protocol import Frame, FrameCodec, ProtocolError
from max_to_telegram.telegram import TELEGRAM_TEXT_LIMIT, format_message_chunks


def test_random_binary_frames_fail_safely() -> None:
    randomizer = random.Random(0x4D4158)
    codec = FrameCodec()
    for _ in range(2_000):
        raw = randomizer.randbytes(randomizer.randrange(0, 513))
        try:
            decoded = codec.decode(raw)
        except ProtocolError:
            continue
        assert decoded.version == 10
        assert -32768 <= decoded.seq <= 32767
        assert -32768 <= decoded.opcode <= 32767


def test_random_messagepack_payloads_round_trip() -> None:
    randomizer = random.Random(0x544F4B454E)
    codec = FrameCodec()
    for sequence in range(500):
        payload = _random_value(randomizer, depth=3)
        frame = Frame(cmd=sequence % 4, seq=sequence, opcode=128, payload=payload)

        decoded = codec.decode(codec.encode(frame))

        assert decoded.payload == payload
        assert decoded.cmd == frame.cmd
        assert decoded.seq == frame.seq
        assert decoded.opcode == frame.opcode


def test_random_message_push_shapes_never_raise_internal_exceptions() -> None:
    randomizer = random.Random(0x43484154)
    parser = MessageParser(viewer_id=123)
    for sequence in range(2_000):
        frame = Frame(
            cmd=0,
            seq=sequence,
            opcode=OPCODE_NEW_MESSAGE,
            payload=_random_value(randomizer, depth=4),
        )
        try:
            message = parser.parse(frame)
        except MessageParseError:
            continue
        assert message.chat_id != 0
        assert message.message_id
        assert message.timestamp.tzinfo is not None
        assert len(message.text) <= 1_000_000


def test_random_unicode_telegram_content_respects_chunk_limit() -> None:
    randomizer = random.Random(0x48544D4C)
    alphabet = "abc АБВ <>&\n\t🙂𐍈"
    for index in range(250):
        content = "".join(
            randomizer.choice(alphabet) for _ in range(randomizer.randrange(0, 10_000))
        )
        message = ParsedMessage(
            chat_id=42,
            message_id=str(index),
            sender_id=7,
            sender_name="Sender <&>",
            chat_title="Chat <&>",
            text=content,
            timestamp=datetime(2026, 8, 22, tzinfo=timezone.utc),
            timestamp_raw=1_777_000_000_000 + index,
            update_time=None,
            status=None,
            message_type="USER",
            attachments=(),
            is_outgoing=False,
            is_service=False,
        )

        chunks = format_message_chunks(message)

        assert chunks
        assert all(len(chunk) <= TELEGRAM_TEXT_LIMIT for chunk in chunks)
        assert all("Sender &lt;&amp;&gt;" in chunk for chunk in chunks)
        assert all("Chat &lt;&amp;&gt;" in chunk for chunk in chunks)


def _random_value(randomizer: random.Random, *, depth: int):
    primitive = randomizer.randrange(7)
    if depth <= 0 or primitive < 5:
        values = (
            None,
            bool(randomizer.getrandbits(1)),
            randomizer.randrange(-(2**53), 2**53),
            "".join(randomizer.choice("ab Я🙂<>&") for _ in range(randomizer.randrange(20))),
            randomizer.randbytes(randomizer.randrange(20)),
        )
        return values[primitive % len(values)]
    if primitive == 5:
        return [_random_value(randomizer, depth=depth - 1) for _ in range(randomizer.randrange(5))]
    return {
        f"key-{index}": _random_value(randomizer, depth=depth - 1)
        for index in range(randomizer.randrange(5))
    }
