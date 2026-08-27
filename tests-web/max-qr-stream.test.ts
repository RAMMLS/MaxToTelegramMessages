import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  connect: vi.fn(),
  deleteQrAttempt: vi.fn(),
  extractSession: vi.fn(),
  getQrAttempt: vi.fn(),
  saveMaxSession: vi.fn(),
  saveQrAttempt: vi.fn(),
}));

vi.mock('@/lib/max-qr-session', () => ({
  MaxProtocolClient: { connect: mocks.connect },
  MaxQrError: class MaxQrError extends Error {
    constructor(readonly code: string) { super(code); }
  },
  userFacingError: (code: string) => code,
}));
vi.mock('@/lib/max-qr-result', () => ({ extractSession: mocks.extractSession }));
vi.mock('@/lib/session-store', () => ({
  deleteQrAttempt: mocks.deleteQrAttempt,
  getQrAttempt: mocks.getQrAttempt,
  saveMaxSession: mocks.saveMaxSession,
  saveQrAttempt: mocks.saveQrAttempt,
}));

import { openQrEventStream, submitQrPassword } from '@/lib/max-qr-stream';

describe('streaming MAX QR flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('keeps one outbound MAX client alive while the response stream is open', async () => {
    vi.useFakeTimers();
    const client = {
      deviceId: 'device-1',
      request: vi.fn()
        .mockResolvedValueOnce({
          payload: {
            trackId: 'track-secret',
            qrLink: 'https://max.ru/qr/example',
            expiresAt: Date.now() + 120_000,
            pollingInterval: 2_000,
          },
        })
        .mockResolvedValueOnce({
          payload: { status: { loginAvailable: false, expiresAt: Date.now() + 110_000 } },
        }),
      close: vi.fn(),
    };
    mocks.connect.mockResolvedValue(client);
    mocks.getQrAttempt.mockImplementation(async () => {
      const saved = mocks.saveQrAttempt.mock.calls.at(-1)?.[1];
      return saved ?? null;
    });

    const response = await openQrEventStream('owner');
    const reader = response.body!.getReader();
    const first = JSON.parse(new TextDecoder().decode((await reader.read()).value));
    expect(first).toMatchObject({
      type: 'qr',
      qrLink: 'https://max.ru/qr/example',
      pollingInterval: 2_000,
    });
    expect(first).not.toHaveProperty('trackId');
    expect(client.close).not.toHaveBeenCalled();

    const nextRead = reader.read();
    await vi.advanceTimersByTimeAsync(2_000);
    const second = JSON.parse(new TextDecoder().decode((await nextRead).value));
    expect(second).toMatchObject({ type: 'waiting' });
    expect(mocks.connect).toHaveBeenCalledOnce();
    expect(client.request).toHaveBeenLastCalledWith(289, { trackId: 'track-secret' });

    await reader.cancel();
    expect(client.close).toHaveBeenCalledOnce();
  });

  it('stores a 2FA password only in the encrypted server-side command row', async () => {
    mocks.getQrAttempt.mockResolvedValue({
      sessionId: 'session-1',
      expiresAt: Date.now() + 60_000,
      password: null,
    });

    await submitQrPassword('owner', 'session-1', 'private-password');

    expect(mocks.saveQrAttempt).toHaveBeenCalledWith('owner', {
      sessionId: 'session-1',
      expiresAt: expect.any(Number),
      password: 'private-password',
    });
  });
});
