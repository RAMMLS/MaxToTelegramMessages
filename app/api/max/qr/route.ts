import { hasSameOrigin } from '@/lib/auth-utils';
import {
  callQrRelay,
  pollQrRelayUntilTerminal,
  QrRelayError,
  type RelayEvent,
} from '@/lib/max-qr-relay';
import { hasPortalSession, portalOwnerKey } from '@/lib/portal-session';
import { claimQrAttempt, initializeSessionStore, saveMaxSession } from '@/lib/session-store';

export const dynamic = 'force-dynamic';

type Command =
  | { type: 'start' }
  | { type: 'poll' | 'cancel'; sessionId: string }
  | { type: 'password'; sessionId: string; password: string };

export async function POST(request: Request): Promise<Response> {
  if (!hasSameOrigin(request)) return Response.json({ error: 'forbidden' }, { status: 403 });
  if (!(await hasPortalSession(request.headers))) {
    return Response.json({ error: 'unauthorized' }, { status: 401 });
  }

  const command = await parseCommand(request);
  if (!command) return Response.json({ error: 'invalid_request' }, { status: 400 });

  const ownerId = portalOwnerKey();
  await initializeSessionStore();
  try {
    if (command.type === 'start' && !(await claimQrAttempt(ownerId))) {
      return errorResponse('rate_limited', 'Слишком много попыток. Повторите немного позже.', 429);
    }
    const event = command.type === 'poll'
      ? await pollQrRelayUntilTerminal({ type: 'poll', sessionId: command.sessionId })
      : await callQrRelay(command);
    return json(await portalEvent(ownerId, event));
  } catch (error) {
    const code = error instanceof QrRelayError ? error.code : 'relay_failed';
    const status = error instanceof QrRelayError ? error.status : 502;
    console.warn('MAX QR relay request failed', {
      code,
      errorType: error instanceof Error ? error.name : typeof error,
    });
    return errorResponse(code, userFacingError(code), status);
  }
}

async function portalEvent(ownerId: string, event: RelayEvent): Promise<unknown> {
  if (event.type !== 'completed') return event;
  const updatedAt = await saveMaxSession(ownerId, event.session);
  return { type: 'saved', updatedAt };
}

async function parseCommand(request: Request): Promise<Command | null> {
  const raw = await request.text();
  if (raw.length > 1024) return null;
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (value.type === 'start') return { type: 'start' };
    if (
      (value.type === 'poll' || value.type === 'cancel')
      && typeof value.sessionId === 'string'
      && value.sessionId.length <= 128
    ) return { type: value.type, sessionId: value.sessionId };
    if (
      value.type === 'password'
      && typeof value.sessionId === 'string'
      && value.sessionId.length <= 128
      && typeof value.password === 'string'
      && value.password.length <= 256
    ) return { type: 'password', sessionId: value.sessionId, password: value.password };
  } catch {
    // Invalid JSON is handled as an invalid request.
  }
  return null;
}

function userFacingError(code: string): string {
  if (code === 'track.not.found') return 'QR-сессия истекла. Создайте новый код.';
  if (code === 'password2fa.wrong') return 'Неверный пароль двухэтапной защиты.';
  if (code === 'password_invalid') return 'Проверьте пароль и повторите попытку.';
  if (code === 'relay_busy' || code === 'rate_limited') {
    return 'Слишком много активных попыток. Повторите немного позже.';
  }
  return 'Сервис входа временно недоступен. Повторите попытку.';
}

function json(payload: unknown): Response {
  return Response.json(payload, {
    headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' },
  });
}

function errorResponse(code: string, message: string, status: number): Response {
  return Response.json(
    { type: 'error', code, message },
    { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } },
  );
}
