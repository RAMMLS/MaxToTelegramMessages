import { extractSession } from '@/lib/max-qr-result';
import {
  MaxProtocolClient,
  MaxQrError,
  type PortalSocketEvent,
  userFacingError,
} from '@/lib/max-qr-session';
import {
  deleteQrAttempt,
  getQrAttempt,
  saveMaxSession,
  saveQrAttempt,
  type StoredQrAttempt,
} from '@/lib/session-store';

type JsonObject = Record<string, unknown>;
type StreamEvent = PortalSocketEvent | {
  type: 'qr';
  qrLink: string;
  expiresAt: number;
  pollingInterval: number;
  sessionId: string;
};

export async function openQrEventStream(ownerId: string): Promise<Response> {
  const client = await MaxProtocolClient.connect();
  let trackId: string;
  let qrLink: string;
  let expiresAt: number;
  let pollingInterval: number;
  const sessionId = crypto.randomUUID();
  try {
    const created = asObject((await client.request(288)).payload);
    trackId = asString(created?.trackId) ?? '';
    qrLink = asString(created?.qrLink) ?? '';
    if (!trackId || !qrLink.startsWith('https://')) {
      throw new Error('MAX returned an invalid QR session');
    }
    expiresAt = futureTimestamp(created?.expiresAt, Date.now() + 120_000);
    pollingInterval = clamp(asSafeInteger(created?.pollingInterval) ?? 5_000, 2_000, 10_000);
    await saveQrAttempt(ownerId, { sessionId, expiresAt, password: null });
  } catch (error) {
    client.close();
    throw error;
  }

  let phase: 'qr' | 'waiting' | 'password' | 'done' = 'qr';
  let closed = false;

  const cleanup = async (): Promise<void> => {
    if (closed) return;
    closed = true;
    phase = 'done';
    try { client.close(); } catch { /* The MAX socket may already be closed. */ }
    await deleteQrAttempt(ownerId, sessionId);
  };

  const stream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (phase === 'done') return;
      try {
        if (phase === 'qr') {
          phase = 'waiting';
          emit(controller, { type: 'qr', qrLink, expiresAt, pollingInterval, sessionId });
          return;
        }

        if (phase === 'waiting') {
          await delay(pollingInterval);
          await requireActiveAttempt(ownerId, sessionId);
          const result = asObject((await client.request(289, { trackId })).payload);
          const status = asObject(result?.status);
          expiresAt = futureTimestamp(status?.expiresAt, expiresAt);
          if (!status?.loginAvailable) {
            if (Date.now() >= expiresAt) throw new MaxQrError('track.not.found');
            emit(controller, { type: 'waiting', expiresAt });
            return;
          }

          const completed = await client.request(291, { trackId });
          const payload = asObject(completed.payload);
          if (payload?.passwordChallenge) {
            phase = 'password';
            const challenge = asObject(payload.passwordChallenge);
            emit(controller, { type: 'password_required', hint: asString(challenge?.hint) });
            return;
          }
          const updatedAt = await persistCompleted(ownerId, client.deviceId, completed.payload);
          emit(controller, { type: 'saved', updatedAt });
          await cleanup();
          controller.close();
          return;
        }

        const password = await waitForPassword(ownerId, sessionId, expiresAt);
        emit(controller, { type: 'saving' });
        const completed = await client.request(115, { trackId, password });
        const updatedAt = await persistCompleted(ownerId, client.deviceId, completed.payload);
        emit(controller, { type: 'saved', updatedAt });
        await cleanup();
        controller.close();
      } catch (error) {
        const code = error instanceof MaxQrError ? error.code : 'portal_failed';
        try { emit(controller, { type: 'error', code, message: userFacingError(code) }); } catch { /* Client disconnected. */ }
        await cleanup();
        try { controller.close(); } catch { /* Client disconnected. */ }
      }
    },
    async cancel() {
      await cleanup();
    },
  });

  return new Response(stream, {
    headers: {
      'Cache-Control': 'no-store, no-transform',
      'Content-Type': 'application/x-ndjson; charset=utf-8',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}

export async function submitQrPassword(
  ownerId: string,
  sessionId: string,
  password: string,
): Promise<void> {
  if (password.length < 1 || password.length > 256) throw new MaxQrError('password_invalid');
  const attempt = await requireActiveAttempt(ownerId, sessionId);
  await saveQrAttempt(ownerId, { ...attempt, password });
}

export async function cancelQrStream(ownerId: string, sessionId: string): Promise<void> {
  await deleteQrAttempt(ownerId, sessionId);
}

async function persistCompleted(ownerId: string, deviceId: string, payload: unknown): Promise<number> {
  return saveMaxSession(ownerId, extractSession(payload, deviceId));
}

async function requireActiveAttempt(ownerId: string, sessionId: string): Promise<StoredQrAttempt> {
  const attempt = await getQrAttempt(ownerId);
  if (!attempt || attempt.sessionId !== sessionId) throw new MaxQrError('track.not.found');
  return attempt;
}

async function waitForPassword(ownerId: string, sessionId: string, expiresAt: number): Promise<string> {
  while (Date.now() < expiresAt) {
    const attempt = await requireActiveAttempt(ownerId, sessionId);
    if (attempt.password) return attempt.password;
    await delay(1_000);
  }
  throw new MaxQrError('track.not.found');
}

function emit(controller: ReadableStreamDefaultController<Uint8Array>, event: StreamEvent): void {
  controller.enqueue(encoderLine(event));
}

function encoderLine(event: StreamEvent): Uint8Array {
  return new TextEncoder().encode(`${JSON.stringify(event)}\n`);
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
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

function futureTimestamp(value: unknown, fallback: number): number {
  const timestamp = asSafeInteger(value);
  return timestamp && timestamp > Date.now() ? timestamp : fallback;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.max(minimum, Math.min(maximum, value));
}
