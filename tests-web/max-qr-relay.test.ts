import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('cloudflare:workers', () => ({
  env: {
    MAX_QR_RELAY_URL: 'https://relay.example',
    MAX_QR_RELAY_TOKEN: 'r'.repeat(48),
  },
}));

import { callQrRelay, pollQrRelayUntilTerminal } from '@/lib/max-qr-relay';

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
    expect(init.redirect).toBe('manual');
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

  it('keeps polling on the server until MAX completes the QR login', async () => {
    const call = vi.fn()
      .mockResolvedValueOnce({ type: 'waiting', expiresAt: 1_900_000_010_000 })
      .mockResolvedValueOnce({ type: 'waiting', expiresAt: 1_900_000_020_000 })
      .mockResolvedValueOnce({
        type: 'completed',
        session: { viewerId: '42', token: 'session-token', deviceId: 'device-1' },
      });
    const sleep = vi.fn().mockResolvedValue(undefined);

    await expect(pollQrRelayUntilTerminal(
      { type: 'poll', sessionId: 'session-1' },
      { call, sleep, now: () => 1_900_000_000_000 },
    )).resolves.toMatchObject({ type: 'completed' });

    expect(call).toHaveBeenCalledTimes(3);
    expect(sleep).toHaveBeenNthCalledWith(1, 5_000);
    expect(sleep).toHaveBeenNthCalledWith(2, 5_000);
  });

  it('fails closed when the QR expires during server-side polling', async () => {
    const call = vi.fn().mockResolvedValue({
      type: 'waiting',
      expiresAt: 1_900_000_001_000,
    });
    let current = 1_900_000_000_000;

    await expect(pollQrRelayUntilTerminal(
      { type: 'poll', sessionId: 'session-1' },
      {
        call,
        now: () => current,
        sleep: async (milliseconds) => { current += milliseconds; },
      },
    )).rejects.toMatchObject({ code: 'track.not.found', status: 409 });

    expect(call).toHaveBeenCalledOnce();
  });

  it('fails closed on a malformed credential response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
      type: 'completed',
      session: { viewerId: '42', deviceId: 'device-1' },
    })));

    await expect(callQrRelay({ type: 'poll', sessionId: 'session-1' }))
      .rejects.toMatchObject({ code: 'relay_invalid_response' });
  });

  it('rejects relay redirects without following them', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, {
      status: 302,
      headers: { Location: 'https://attacker.example/collect' },
    })));

    await expect(callQrRelay({ type: 'start' }))
      .rejects.toMatchObject({ code: 'relay_redirect_rejected', status: 502 });
  });

  it('logs bounded transport metadata without changing the public error', async () => {
    const warning = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('network unavailable')));

    await expect(callQrRelay({ type: 'start' }))
      .rejects.toMatchObject({ code: 'relay_unavailable', status: 502 });
    expect(warning).toHaveBeenCalledWith('MAX QR relay transport failed', {
      errorType: 'TypeError',
      errorMessage: 'network unavailable',
    });
  });
});
