import { describe, expect, it } from 'vitest';
import { extractSession } from '@/lib/max-qr-result';
import { acceptBinarySocket } from '@/lib/max-socket';

describe('MAX QR transport', () => {
  it('opts out of Blob delivery before accepting the outbound socket', () => {
    const calls: string[] = [];
    const socketState = {
      binaryType: 'blob',
    };
    const socket = {
      get binaryType() { return socketState.binaryType; },
      set binaryType(value: string) { socketState.binaryType = value; },
      accept() { calls.push(socketState.binaryType); },
    } as WebSocket;

    acceptBinarySocket(socket);

    expect(socket.binaryType).toBe('arraybuffer');
    expect(calls).toEqual(['arraybuffer']);
  });
});

describe('MAX QR result extraction', () => {
  it('extracts only the login token, viewer ID and server-side device ID', () => {
    expect(extractSession({
      tokenAttrs: { LOGIN: { token: 'session-token' } },
      profile: { contact: { id: 211430102 } },
      unrelated: { secret: 'ignored' },
    }, '123e4567-e89b-12d3-a456-426614174000')).toEqual({
      viewerId: '211430102',
      token: 'session-token',
      deviceId: '123e4567-e89b-12d3-a456-426614174000',
    });
  });

  it('rejects incomplete or unsafe login results', () => {
    expect(() => extractSession({ profile: { contact: { id: 1 } } }, 'device'))
      .toThrow('did not contain a session');
    expect(() => extractSession({
      tokenAttrs: { LOGIN: { token: 'token' } },
      profile: { contact: { id: -1 } },
    }, 'device')).toThrow('invalid viewer ID');
  });
});
