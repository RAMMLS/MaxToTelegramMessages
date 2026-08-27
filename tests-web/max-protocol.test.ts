import { describe, expect, it } from 'vitest';
import { decodeMaxFrame, decompressLz4Block, encodeMaxFrame } from '@/lib/max-protocol';

describe('MAX protocol codec', () => {
  it('round-trips an uncompressed command frame', () => {
    const encoded = encodeMaxFrame({ cmd: 0, seq: 7, opcode: 289, payload: { trackId: 'safe' } });
    const decoded = decodeMaxFrame(encoded);
    expect(decoded).toMatchObject({ version: 10, cmd: 0, seq: 7, opcode: 289, compression: 0 });
    expect(decoded.payload).toEqual({ trackId: 'safe' });
  });

  it('decompresses overlapping LZ4 matches safely', () => {
    const compressed = new Uint8Array([0x66, 0xaf, 0x68, 0x65, 0x6c, 0x6c, 0x6f, 0x05, 0x00]);
    const unpacked = decompressLz4Block(compressed, 18);
    expect(Array.from(unpacked)).toEqual(Array.from(new Uint8Array([
      0xaf, 0x68, 0x65, 0x6c, 0x6c, 0x6f, 0x68, 0x65,
      0x6c, 0x6c, 0x6f, 0x68, 0x65, 0x6c, 0x6c, 0x6f,
    ])));
  });

  it('rejects invalid LZ4 offsets', () => {
    expect(() => decompressLz4Block(new Uint8Array([0x00, 0x01, 0x00]), 16))
      .toThrow('invalid LZ4 offset');
  });
});
