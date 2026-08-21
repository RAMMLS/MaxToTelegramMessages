"""Binary codec used by the current MAX web client."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any

import lz4.block
import msgpack

PROTOCOL_VERSION = 10
HEADER_SIZE = 10
COMPRESSION_THRESHOLD = 32
MAX_FRAME_BYTES = 16 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024

_FIXED_HEADER = struct.Struct(">BBhhB")


class ProtocolError(ValueError):
    """Raised for malformed, oversized, or unsupported MAX frames."""


@dataclass(frozen=True, slots=True)
class Frame:
    """Decoded MAX protocol frame."""

    cmd: int
    seq: int
    opcode: int
    payload: Any = None
    version: int = PROTOCOL_VERSION
    compression: int = 0


class FrameCodec:
    """Encode and decode MAX protocol v10 frames."""

    def __init__(self, *, expected_version: int = PROTOCOL_VERSION) -> None:
        self.expected_version = expected_version

    def encode(self, frame: Frame) -> bytes:
        self._validate_header_fields(frame)
        try:
            payload = (
                b""
                if frame.payload is None
                else msgpack.packb(frame.payload, use_bin_type=True, strict_types=False)
            )
        except (OverflowError, TypeError, ValueError) as exc:
            raise ProtocolError("cannot encode MessagePack payload") from exc

        compression = 0
        if len(payload) > COMPRESSION_THRESHOLD:
            try:
                compressed = lz4.block.compress(payload, store_size=False)
            except lz4.block.LZ4BlockError as exc:
                raise ProtocolError("cannot compress payload") from exc
            compression = min(math.ceil(len(payload) / len(compressed)), 255)
            if compression > 0:
                payload = compressed

        if len(payload) > MAX_FRAME_BYTES - HEADER_SIZE:
            raise ProtocolError("encoded frame is too large")
        payload_length = len(payload)
        if payload_length > 0xFFFFFF:
            raise ProtocolError("payload does not fit the 24-bit length field")

        fixed = _FIXED_HEADER.pack(
            frame.version,
            frame.cmd,
            frame.seq,
            frame.opcode,
            compression,
        )
        return fixed + payload_length.to_bytes(3, "big") + payload

    def decode(self, raw: bytes | bytearray | memoryview) -> Frame:
        data = bytes(raw)
        if len(data) < HEADER_SIZE:
            raise ProtocolError("frame is shorter than the 10-byte header")
        if len(data) > MAX_FRAME_BYTES:
            raise ProtocolError("frame is too large")

        version, cmd, seq, opcode, compression = _FIXED_HEADER.unpack_from(data)
        if version != self.expected_version:
            raise ProtocolError(f"unsupported protocol version: {version}")
        payload_length = int.from_bytes(data[7:10], "big")
        if len(data) != HEADER_SIZE + payload_length:
            raise ProtocolError("frame payload length does not match the header")

        packed = data[HEADER_SIZE:]
        if compression:
            capacity = payload_length * compression
            if capacity > MAX_DECOMPRESSED_BYTES:
                raise ProtocolError("declared decompressed payload is too large")
            try:
                packed = lz4.block.decompress(packed, uncompressed_size=capacity)
            except (lz4.block.LZ4BlockError, ValueError) as exc:
                raise ProtocolError("cannot decompress LZ4 payload") from exc

        if not packed:
            payload: Any = None
        else:
            try:
                payload = msgpack.unpackb(
                    packed,
                    raw=False,
                    strict_map_key=False,
                    ext_hook=_decode_extension,
                )
            except (msgpack.UnpackException, ValueError) as exc:
                raise ProtocolError("cannot decode MessagePack payload") from exc

        return Frame(
            version=version,
            cmd=cmd,
            seq=seq,
            opcode=opcode,
            compression=compression,
            payload=payload,
        )

    @staticmethod
    def _validate_header_fields(frame: Frame) -> None:
        if not 0 <= frame.version <= 255:
            raise ProtocolError("version must fit uint8")
        if not 0 <= frame.cmd <= 255:
            raise ProtocolError("cmd must fit uint8")
        if not -32768 <= frame.seq <= 32767:
            raise ProtocolError("seq must fit int16")
        if not -32768 <= frame.opcode <= 32767:
            raise ProtocolError("opcode must fit int16")


def _decode_extension(code: int, data: bytes) -> Any:
    if code != 1:
        return msgpack.ExtType(code, data)
    try:
        value = msgpack.unpackb(data, raw=False, strict_map_key=False)
    except (msgpack.UnpackException, ValueError) as exc:
        raise ProtocolError("invalid int64 MessagePack extension") from exc
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("int64 MessagePack extension did not contain an integer")
    return value
