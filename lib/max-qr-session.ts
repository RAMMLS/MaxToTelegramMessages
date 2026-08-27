import { env, waitUntil } from 'cloudflare:workers';
import { decodeMaxFrame, encodeMaxFrame, type MaxFrame } from '@/lib/max-protocol';
import { extractSession } from '@/lib/max-qr-result';
import { acceptBinarySocket } from '@/lib/max-socket';
import { saveMaxSession } from '@/lib/session-store';

type JsonObject = Record<string, unknown>;
type PendingRequest = {
  opcode: number;
  resolve: (frame: MaxFrame) => void;
  reject: (error: Error) => void;
  timer: ReturnType<typeof setTimeout>;
};

const MAX_SOCKET_URL = 'https://api.oneme.ru/websocket';
const MAX_WEB_ORIGIN = 'https://web.max.ru';
const REQUEST_TIMEOUT_MS = 20_000;

export type PortalSocketEvent =
  | { type: 'connecting' }
  | { type: 'qr'; qrLink: string; expiresAt: number; pollingInterval: number }
  | { type: 'waiting'; expiresAt: number }
  | { type: 'password_required'; hint: string | null }
  | { type: 'saving' }
  | { type: 'saved'; updatedAt: number }
  | { type: 'error'; code: string; message: string };

export class MaxProtocolClient {
  private sequence = 0;
  private readonly pending = new Map<number, PendingRequest>();
  private constructor(
    private readonly socket: WebSocket,
    readonly deviceId: string,
  ) {
    socket.addEventListener('message', (event) => this.handleMessage(event));
    socket.addEventListener('close', () => this.rejectPending(new Error('MAX socket closed')));
    socket.addEventListener('error', () => this.rejectPending(new Error('MAX socket failed')));
  }

  static async connect(deviceId = crypto.randomUUID()): Promise<MaxProtocolClient> {
    let response: Response;
    try {
      response = await fetch(MAX_SOCKET_URL, {
        headers: { Upgrade: 'websocket', Origin: MAX_WEB_ORIGIN },
      });
    } catch {
      throw new MaxQrStageError('upgrade_fetch');
    }
    if (response.status !== 101 || !response.webSocket) {
      throw new MaxQrStageError('upgrade_rejected');
    }
    const socket = response.webSocket;
    // Cloudflare Workers changed binary WebSocket messages to Blob by default
    // in 2026. MAX protocol frames are decoded synchronously, so explicitly
    // retain the legacy ArrayBuffer delivery mode before accepting the socket.
    try {
      acceptBinarySocket(socket);
    } catch {
      throw new MaxQrStageError('socket_accept');
    }
    const client = new MaxProtocolClient(socket, deviceId);
    try {
      await client.request(6, client.initPayload());
    } catch {
      client.close();
      throw new MaxQrStageError('init_command');
    }
    return client;
  }

  request(opcode: number, payload?: unknown): Promise<MaxFrame> {
    const seq = this.takeSequence();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(seq);
        reject(new Error('MAX request timed out'));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(seq, { opcode, resolve, reject, timer });
      try {
        this.socket.send(encodeMaxFrame({ cmd: 0, seq, opcode, payload }));
      } catch (error) {
        clearTimeout(timer);
        this.pending.delete(seq);
        reject(error instanceof Error ? error : new Error('MAX send failed'));
      }
    });
  }

  close(): void {
    this.socket.close(1000, 'portal session complete');
    this.rejectPending(new Error('MAX socket closed'));
  }

  private handleMessage(event: MessageEvent): void {
    try {
      if (!(event.data instanceof ArrayBuffer)) throw new Error('MAX sent a non-binary frame');
      const frame = decodeMaxFrame(event.data);
      if (frame.cmd === 0 && frame.opcode === 1) {
        this.socket.send(encodeMaxFrame({ cmd: 1, seq: frame.seq, opcode: 1 }));
        return;
      }
      const pending = this.pending.get(frame.seq);
      if (!pending || pending.opcode !== frame.opcode) return;
      clearTimeout(pending.timer);
      this.pending.delete(frame.seq);
      if (frame.cmd === 3) {
        const payload = asObject(frame.payload);
        pending.reject(new MaxQrError(asString(payload?.error) ?? 'max_command_failed'));
      } else if (frame.cmd === 1) {
        pending.resolve(frame);
      }
    } catch (error) {
      this.rejectPending(error instanceof Error ? error : new Error('MAX frame failed'));
    }
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(error);
    }
    this.pending.clear();
  }

  private takeSequence(): number {
    if (this.sequence > 32767) this.sequence = 0;
    return this.sequence++;
  }

  private initPayload(): JsonObject {
    return {
      userAgent: {
        deviceType: 'WEB',
        pushDeviceType: 'WEBPUSH',
        locale: 'ru',
        deviceLocale: 'ru',
        osVersion: 'Cloudflare Workers',
        deviceName: 'MAX Telegram bridge portal',
        headerUserAgent: 'Mozilla/5.0 MAX-to-Telegram-Portal/0.1',
        isPwa: false,
        appVersion: env.MAX_WEB_APP_VERSION?.trim() || '26.8.8',
        screen: '0x0 1.0x',
        timezone: 'Europe/Moscow',
      },
      deviceId: this.deviceId,
    };
  }
}

export function attachQrSession(browserSocket: WebSocket, ownerId: string): void {
  let maxClient: MaxProtocolClient | null = null;
  let trackId: string | null = null;
  let phase: 'starting' | 'waiting' | 'password' | 'saving' | 'done' = 'starting';
  let inFlight = false;

  const emit = (event: PortalSocketEvent): void => {
    if (browserSocket.readyState !== WebSocket.OPEN) return;
    try {
      browserSocket.send(JSON.stringify(event));
    } catch {
      // The browser can disconnect between the readyState check and send().
    }
  };
  const fail = (error: unknown): void => {
    const code = error instanceof MaxQrError ? error.code : 'portal_failed';
    console.warn('MAX QR login failed', {
      code,
      errorType: error instanceof Error ? error.name : typeof error,
      errorMessage: safeErrorMessage(error),
      stage: error instanceof MaxQrStageError ? error.stage : 'session',
    });
    emit({ type: 'error', code, message: userFacingError(code) });
    closeMaxClient();
    if (browserSocket.readyState === WebSocket.OPEN) {
      try {
        browserSocket.close(1011, 'QR login failed');
      } catch {
        // The peer may have closed while the failure was being handled.
      }
    }
  };

  browserSocket.addEventListener('message', (event) => {
    waitUntil(handleBrowserMessage(event).catch(fail));
  });
  browserSocket.addEventListener('close', closeMaxClient);
  browserSocket.addEventListener('error', closeMaxClient);

  waitUntil(start().catch(fail));

  async function start(): Promise<void> {
    emit({ type: 'connecting' });
    maxClient = await MaxProtocolClient.connect();
    const created = asObject((await maxClient.request(288)).payload);
    trackId = asString(created?.trackId);
    const qrLink = asString(created?.qrLink);
    if (!trackId || !qrLink || !qrLink.startsWith('https://')) {
      throw new Error('MAX returned an invalid QR session');
    }
    phase = 'waiting';
    emit({
      type: 'qr',
      qrLink,
      expiresAt: asSafeInteger(created?.expiresAt) ?? Date.now() + 120_000,
      pollingInterval: clamp(asSafeInteger(created?.pollingInterval) ?? 5_000, 2_000, 10_000),
    });
  }

  async function handleBrowserMessage(event: MessageEvent): Promise<void> {
    if (typeof event.data !== 'string') return;
    const input = parseBrowserCommand(event.data);
    if (!input || inFlight) return;
    if (input.type === 'cancel') {
      phase = 'done';
      maxClient?.close();
      browserSocket.close(1000, 'cancelled');
      return;
    }
    if (input.type === 'poll' && phase === 'waiting') {
      inFlight = true;
      try { await poll(); } finally { inFlight = false; }
      return;
    }
    if (input.type === 'password' && phase === 'password') {
      if (input.password.length < 1 || input.password.length > 256) {
        throw new MaxQrError('password_invalid');
      }
      inFlight = true;
      try {
        const completed = await maxClientOrThrow().request(115, {
          trackId: trackIdOrThrow(),
          password: input.password,
        });
        await persistCompleted(completed.payload);
      } finally {
        inFlight = false;
      }
    }
  }

  async function poll(): Promise<void> {
    const result = asObject((await maxClientOrThrow().request(289, {
      trackId: trackIdOrThrow(),
    })).payload);
    const status = asObject(result?.status);
    const expiresAt = asSafeInteger(status?.expiresAt) ?? Date.now();
    if (!status?.loginAvailable) {
      emit({ type: 'waiting', expiresAt });
      return;
    }

    const completed = await maxClientOrThrow().request(291, { trackId: trackIdOrThrow() });
    const payload = asObject(completed.payload);
    if (payload?.passwordChallenge) {
      const challenge = asObject(payload.passwordChallenge);
      phase = 'password';
      emit({ type: 'password_required', hint: asString(challenge?.hint) });
      return;
    }
    await persistCompleted(completed.payload);
  }

  async function persistCompleted(payload: unknown): Promise<void> {
    phase = 'saving';
    emit({ type: 'saving' });
    const session = extractSession(payload, maxClientOrThrow().deviceId);
    const updatedAt = await saveMaxSession(ownerId, session);
    phase = 'done';
    emit({ type: 'saved', updatedAt });
    maxClient?.close();
    browserSocket.close(1000, 'session saved');
  }

  function maxClientOrThrow(): MaxProtocolClient {
    if (!maxClient) throw new Error('MAX client is not ready');
    return maxClient;
  }

  function trackIdOrThrow(): string {
    if (!trackId) throw new Error('MAX QR session is not ready');
    return trackId;
  }

  function closeMaxClient(): void {
    try {
      maxClient?.close();
    } catch {
      // Closing an already-failed WebSocket is harmless.
    }
  }
}

export class MaxQrError extends Error {
  constructor(readonly code: string) { super(code); }
}

export class MaxQrStageError extends Error {
  constructor(readonly stage: string) { super('MAX QR stage failed'); }
}

function parseBrowserCommand(raw: string):
  | { type: 'poll' | 'cancel' }
  | { type: 'password'; password: string }
  | null {
  if (raw.length > 1024) return null;
  try {
    const value = JSON.parse(raw) as unknown;
    const object = asObject(value);
    if (object?.type === 'poll' || object?.type === 'cancel') return { type: object.type };
    if (object?.type === 'password' && typeof object.password === 'string') {
      return { type: 'password', password: object.password };
    }
  } catch { /* Ignore malformed browser frames. */ }
  return null;
}

export function userFacingError(code: string): string {
  if (code === 'track.not.found') return 'QR-сессия истекла. Создайте новый код.';
  if (code === 'password2fa.wrong') return 'Неверный пароль двухэтапной защиты.';
  if (code === 'password_invalid') return 'Проверьте пароль и повторите попытку.';
  return 'Не удалось завершить вход в MAX. Создайте новый QR и попробуйте ещё раз.';
}

export function safeErrorMessage(error: unknown): string {
  if (!(error instanceof Error)) return 'non-error failure';
  const allowed = new Set([
    'MAX WebSocket upgrade failed',
    'MAX socket closed',
    'MAX socket failed',
    'MAX request timed out',
    'MAX sent a non-binary frame',
    'MAX frame failed',
    'MAX send failed',
    'MAX returned an invalid QR session',
    'MAX client is not ready',
    'MAX QR session is not ready',
  ]);
  return allowed.has(error.message) ? error.message : 'redacted failure';
}

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function asSafeInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}
