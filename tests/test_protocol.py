from __future__ import annotations

import struct

import lz4.block
import msgpack
import pytest

from max_to_telegram.protocol import (
    HEADER_SIZE,
    MAX_DECOMPRESSED_BYTES,
    Frame,
    FrameCodec,
    ProtocolError,
)


@pytest.fixture
def codec() -> FrameCodec:
    return FrameCodec()


def test_encodes_empty_init_frame(codec: FrameCodec) -> None:
    encoded = codec.encode(Frame(cmd=0, seq=0, opcode=6))

    assert encoded.hex() == "0a000000000600000000"


def test_round_trips_small_payload_without_compression(codec: FrameCodec) -> None:
    frame = Frame(cmd=0, seq=7, opcode=19, payload={"token": "short"})

    decoded = codec.decode(codec.encode(frame))

    assert decoded.cmd == frame.cmd
    assert decoded.seq == frame.seq
    assert decoded.opcode == frame.opcode
    assert decoded.payload == frame.payload
    assert decoded.compression == 0


def test_round_trips_compressed_payload(codec: FrameCodec) -> None:
    frame = Frame(cmd=1, seq=11, opcode=6, payload={"config": "x" * 1000})

    encoded = codec.encode(frame)
    decoded = codec.decode(encoded)

    assert encoded[6] > 0
    assert decoded.payload == frame.payload
    assert decoded.compression == encoded[6]


def test_decodes_client_int64_extension(codec: FrameCodec) -> None:
    extension_payload = msgpack.packb(2**60)
    packed = msgpack.packb({"id": msgpack.ExtType(1, extension_payload)})
    raw = struct.pack(">BBhhB", 10, 0, 1, 128, 0) + len(packed).to_bytes(3, "big") + packed

    decoded = codec.decode(raw)

    assert decoded.payload == {"id": 2**60}


def test_preserves_unknown_messagepack_extension(codec: FrameCodec) -> None:
    packed = msgpack.packb(msgpack.ExtType(9, b"opaque"))
    raw = struct.pack(">BBhhB", 10, 0, 1, 128, 0) + len(packed).to_bytes(3, "big") + packed

    decoded = codec.decode(raw)

    assert decoded.payload == msgpack.ExtType(9, b"opaque")


@pytest.mark.parametrize("raw", [b"", b"\x0a", b"\x0a" * (HEADER_SIZE - 1)])
def test_rejects_truncated_header(codec: FrameCodec, raw: bytes) -> None:
    with pytest.raises(ProtocolError, match="shorter"):
        codec.decode(raw)


def test_rejects_unknown_protocol_version(codec: FrameCodec) -> None:
    raw = bytes.fromhex("0b000000000600000000")

    with pytest.raises(ProtocolError, match="version"):
        codec.decode(raw)


def test_rejects_incorrect_payload_length(codec: FrameCodec) -> None:
    raw = bytes.fromhex("0a000000000600000001")

    with pytest.raises(ProtocolError, match="length"):
        codec.decode(raw)


def test_rejects_invalid_lz4_payload(codec: FrameCodec) -> None:
    packed = b"\x10"
    raw = struct.pack(">BBhhB", 10, 0, 1, 6, 2) + len(packed).to_bytes(3, "big") + packed

    with pytest.raises(ProtocolError, match="decompress"):
        codec.decode(raw)


def test_rejects_declared_decompression_bomb(codec: FrameCodec) -> None:
    payload_size = MAX_DECOMPRESSED_BYTES // 255 + 1
    packed = b"x" * payload_size
    raw = struct.pack(">BBhhB", 10, 0, 1, 6, 255) + len(packed).to_bytes(3, "big") + packed

    with pytest.raises(ProtocolError, match="decompressed payload"):
        codec.decode(raw)


@pytest.mark.parametrize(
    "frame",
    [
        Frame(version=256, cmd=0, seq=0, opcode=0),
        Frame(cmd=-1, seq=0, opcode=0),
        Frame(cmd=0, seq=32768, opcode=0),
        Frame(cmd=0, seq=0, opcode=-32769),
    ],
)
def test_rejects_header_values_out_of_range(codec: FrameCodec, frame: Frame) -> None:
    with pytest.raises(ProtocolError):
        codec.encode(frame)


def test_rejects_payload_that_messagepack_cannot_encode(codec: FrameCodec) -> None:
    with pytest.raises(ProtocolError, match="MessagePack"):
        codec.encode(Frame(cmd=0, seq=0, opcode=6, payload=object()))


def test_decode_does_not_include_payload_bytes_in_error(codec: FrameCodec) -> None:
    secret = b"super-secret-session-token"
    raw = struct.pack(">BBhhB", 10, 0, 1, 6, 0) + len(secret).to_bytes(3, "big") + secret

    with pytest.raises(ProtocolError) as raised:
        codec.decode(raw)

    assert secret.decode() not in str(raised.value)


def test_accepts_exact_lz4_capacity(codec: FrameCodec) -> None:
    payload = msgpack.packb({"value": "z" * 200})
    compressed = lz4.block.compress(payload, store_size=False)
    ratio = (len(payload) + len(compressed) - 1) // len(compressed)
    raw = (
        struct.pack(">BBhhB", 10, 1, 1, 6, ratio) + len(compressed).to_bytes(3, "big") + compressed
    )

    assert codec.decode(raw).payload == {"value": "z" * 200}
