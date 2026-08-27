import { decode, encode, ExtensionCodec } from '@msgpack/msgpack';

export type MaxFrame = {
  version: number;
  cmd: number;
  seq: number;
  opcode: number;
  compression: number;
  payload: unknown;
};

const VERSION = 10;
const HEADER_BYTES = 10;
const MAX_FRAME_BYTES = 16 * 1024 * 1024;
const MAX_DECOMPRESSED_BYTES = 64 * 1024 * 1024;

const extensionCodec = new ExtensionCodec();
extensionCodec.register({
  type: 1,
  encode() {
    return null;
  },
  decode(data) {
    const value = decode(data, { useBigInt64: true });
    if (typeof value !== 'number' && typeof value !== 'bigint') {
      throw new Error('MAX int64 extension is invalid');
    }
    return value;
  },
});

export function encodeMaxFrame(input: {
  cmd: number;
  seq: number;
  opcode: number;
  payload?: unknown;
}): Uint8Array<ArrayBuffer> {
  validateInt(input.cmd, 0, 255, 'cmd');
  validateInt(input.seq, -32768, 32767, 'seq');
  validateInt(input.opcode, -32768, 32767, 'opcode');
  const payload = input.payload === undefined
    ? new Uint8Array()
    : encode(input.payload, { extensionCodec, ignoreUndefined: true });
  if (payload.byteLength > 0xffffff || payload.byteLength + HEADER_BYTES > MAX_FRAME_BYTES) {
    throw new Error('MAX frame is too large');
  }

  const frame = new Uint8Array(new ArrayBuffer(HEADER_BYTES + payload.byteLength));
  const view = new DataView(frame.buffer);
  view.setUint8(0, VERSION);
  view.setUint8(1, input.cmd);
  view.setInt16(2, input.seq);
  view.setInt16(4, input.opcode);
  view.setUint8(6, 0);
  frame[7] = payload.byteLength >>> 16;
  frame[8] = payload.byteLength >>> 8;
  frame[9] = payload.byteLength;
  frame.set(payload, HEADER_BYTES);
  return frame;
}

export function decodeMaxFrame(raw: ArrayBuffer | ArrayBufferView): MaxFrame {
  const data = raw instanceof ArrayBuffer
    ? new Uint8Array(raw)
    : new Uint8Array(raw.buffer, raw.byteOffset, raw.byteLength);
  if (data.byteLength < HEADER_BYTES || data.byteLength > MAX_FRAME_BYTES) {
    throw new Error('MAX frame has an invalid size');
  }
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const version = view.getUint8(0);
  if (version !== VERSION) throw new Error('MAX protocol version is unsupported');
  const payloadLength = (data[7] << 16) | (data[8] << 8) | data[9];
  if (payloadLength + HEADER_BYTES !== data.byteLength) {
    throw new Error('MAX frame length does not match its header');
  }

  const compression = view.getUint8(6);
  let packed = data.subarray(HEADER_BYTES);
  if (compression > 0) {
    const capacity = payloadLength * compression;
    if (capacity <= 0 || capacity > MAX_DECOMPRESSED_BYTES) {
      throw new Error('MAX decompressed payload is too large');
    }
    packed = decompressLz4Block(packed, capacity);
  }

  return {
    version,
    cmd: view.getUint8(1),
    seq: view.getInt16(2),
    opcode: view.getInt16(4),
    compression,
    payload: packed.byteLength === 0
      ? null
      : decode(packed, { extensionCodec, useBigInt64: true }),
  };
}

export function decompressLz4Block(
  source: Uint8Array,
  capacity: number,
): Uint8Array<ArrayBuffer> {
  const output = new Uint8Array(new ArrayBuffer(capacity));
  let sourceIndex = 0;
  let outputIndex = 0;

  while (sourceIndex < source.byteLength) {
    const token = source[sourceIndex++];
    let literalLength = token >>> 4;
    if (literalLength === 15) {
      let value = 255;
      while (value === 255) {
        if (sourceIndex >= source.byteLength) throw new Error('truncated LZ4 literal length');
        value = source[sourceIndex++];
        literalLength += value;
      }
    }
    if (sourceIndex + literalLength > source.byteLength || outputIndex + literalLength > capacity) {
      throw new Error('invalid LZ4 literal range');
    }
    output.set(source.subarray(sourceIndex, sourceIndex + literalLength), outputIndex);
    sourceIndex += literalLength;
    outputIndex += literalLength;
    if (sourceIndex === source.byteLength) break;

    if (sourceIndex + 2 > source.byteLength) throw new Error('truncated LZ4 offset');
    const offset = source[sourceIndex] | (source[sourceIndex + 1] << 8);
    sourceIndex += 2;
    if (offset === 0 || offset > outputIndex) throw new Error('invalid LZ4 offset');

    let matchLength = token & 0x0f;
    if (matchLength === 15) {
      let value = 255;
      while (value === 255) {
        if (sourceIndex >= source.byteLength) throw new Error('truncated LZ4 match length');
        value = source[sourceIndex++];
        matchLength += value;
      }
    }
    matchLength += 4;
    if (outputIndex + matchLength > capacity) throw new Error('invalid LZ4 match range');
    for (let index = 0; index < matchLength; index += 1) {
      output[outputIndex] = output[outputIndex - offset];
      outputIndex += 1;
    }
  }
  return output.slice(0, outputIndex);
}

function validateInt(value: number, minimum: number, maximum: number, label: string): void {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new Error(`${label} is outside its protocol range`);
  }
}
