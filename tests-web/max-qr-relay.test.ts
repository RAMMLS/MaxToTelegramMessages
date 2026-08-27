import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('cloudflare:workers', () => ({
  env: {
    MAX_QR_RELAY_URL: 'https://relay.example',
    MAX_QR_RELAY_TOKEN: 'r'.repeat(48),
  },
}));

import { callQrRelay } from '@/lib/max-qr-relay';

describe('MAX QR relay client', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('uses the server-only bearer token and validates a QR response', async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json({
      type: 'qr',
      sessionId: 'session-1',
      qrLink: 'https://max.ru/qr/example',
      expiresAt: 1_900_000_000_000,
      pollingInterval: 5_000,
    }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(callQrRelay({ type: 'start' })).resolves.toMatchObject({
      type: 'qr',
      sessionId: 'session-1',
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as [URL, RequestInit];
    expect(url.toString()).toBe('https://relay.example/v1/qr/start');
    expect(new Headers(init.headers).get('authorization')).toBe(`Bearer ${'r'.repeat(48)}`);
  });

  it('accepts only a complete relay session payload', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
      type: 'completed',
      session: { viewerId: '42', token: 'session-token', deviceId: 'device-1' },
    })));

    await expect(callQrRelay({ type: 'poll', sessionId: 'session-1' })).resolves.toEqual({
      type: 'completed',
      session: { viewerId: '42', token: 'session-token', deviceId: 'device-1' },
    });
  });

  it('fails closed on a malformed credential response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
      type: 'completed',
      session: { viewerId: '42', deviceId: 'device-1' },
    })));

    await expect(callQrRelay({ type: 'poll', sessionId: 'session-1' }))
      .rejects.toMatchObject({ code: 'relay_invalid_response' });
  });
});
