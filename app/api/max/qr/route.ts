import { hasSameOrigin } from '@/lib/auth-utils';
import { cancelQrStream, openQrEventStream, submitQrPassword } from '@/lib/max-qr-stream';
import {
  MaxQrError,
  MaxQrStageError,
  safeErrorMessage,
  userFacingError,
} from '@/lib/max-qr-session';
import { hasPortalSession, portalOwnerKey } from '@/lib/portal-session';
import { claimQrAttempt, initializeSessionStore } from '@/lib/session-store';

export const dynamic = 'force-dynamic';

type Command =
  | { type: 'start' }
  | { type: 'password'; sessionId: string; password: string }
  | { type: 'cancel'; sessionId: string };

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
    if (command.type === 'start') {
      if (!(await claimQrAttempt(ownerId))) {
        return errorResponse('rate_limited', 'Слишком много попыток. Повторите немного позже.', 429);
      }
      return await openQrEventStream(ownerId);
    }
    if (command.type === 'password') {
      await submitQrPassword(ownerId, command.sessionId, command.password);
      return json({ ok: true });
    }
    await cancelQrStream(ownerId, command.sessionId);
    return json({ ok: true });
  } catch (error) {
    const code = error instanceof MaxQrError ? error.code : 'portal_failed';
    console.warn('MAX QR stream request failed', {
      code,
      errorType: error instanceof Error ? error.name : typeof error,
      errorMessage: safeErrorMessage(error),
      stage: error instanceof MaxQrStageError ? error.stage : 'request',
    });
    const status = code === 'track.not.found' ? 409 : code === 'password_invalid' ? 400 : 502;
    return errorResponse(code, userFacingError(code), status);
  }
}

async function parseCommand(request: Request): Promise<Command | null> {
  const raw = await request.text();
  if (raw.length > 1024) return null;
  try {
    const value = JSON.parse(raw) as Record<string, unknown>;
    if (value.type === 'start') return { type: 'start' };
    if (
      value.type === 'password'
      && typeof value.sessionId === 'string'
      && value.sessionId.length <= 128
      && typeof value.password === 'string'
    ) {
      return { type: 'password', sessionId: value.sessionId, password: value.password };
    }
    if (
      value.type === 'cancel'
      && typeof value.sessionId === 'string'
      && value.sessionId.length <= 128
    ) {
      return { type: 'cancel', sessionId: value.sessionId };
    }
  } catch {
    // Invalid JSON is handled as an invalid request.
  }
  return null;
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
