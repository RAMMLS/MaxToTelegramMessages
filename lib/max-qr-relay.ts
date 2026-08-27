import { env } from 'cloudflare:workers';
import type { StoredMaxSession } from '@/lib/session-store';

type RelayCommand =
  | { type: 'start' }
  | { type: 'poll' | 'cancel'; sessionId: string }
  | { type: 'password'; sessionId: string; password: string };

export type RelayEvent =
  | { type: 'qr'; sessionId: string; qrLink: string; expiresAt: number; pollingInterval: number }
  | { type: 'waiting'; expiresAt: number }
  | { type: 'password_required'; hint: string | null }
  | { type: 'completed'; session: StoredMaxSession }
  | { type: 'cancelled' };

export class QrRelayError extends Error {
  constructor(readonly code: string, readonly status = 502) {
    super('QR relay request failed');
  }
}

export async function callQrRelay(command: RelayCommand): Promise<RelayEvent> {
  const baseUrl = relayBaseUrl();
  const endpoint = command.type === 'start' ? 'start' : command.type;
  let response: Response;
  try {
    response = await fetch(new URL(`/v1/qr/${endpoint}`, baseUrl), {
      method: 'POST',
      redirect: 'error',
      headers: {
        Authorization: `Bearer ${relayToken()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(command.type === 'start' ? {} : command),
      signal: AbortSignal.timeout(30_000),
    });
  } catch (error) {
    console.warn('MAX QR relay transport failed', relayTransportMetadata(error));
    throw new QrRelayError('relay_unavailable', 502);
  }
  const declaredLength = Number(response.headers.get('content-length') ?? 0);
  if (declaredLength > 65_536) throw new QrRelayError('relay_invalid_response');
  const raw = await response.text();
  if (raw.length > 65_536) throw new QrRelayError('relay_invalid_response');
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    throw new QrRelayError('relay_invalid_response');
  }
  const object = asObject(value);
  if (!response.ok) {
    throw new QrRelayError(asString(object?.code) ?? 'relay_failed', response.status);
  }
  return validateEvent(object);
}

function validateEvent(object: Record<string, unknown> | null): RelayEvent {
  const type = asString(object?.type);
  if (type === 'qr') {
    const sessionId = asString(object?.sessionId);
    const qrLink = asString(object?.qrLink);
    const expiresAt = asSafeInteger(object?.expiresAt);
    const pollingInterval = asSafeInteger(object?.pollingInterval);
    if (
      !sessionId || sessionId.length > 128
      || !qrLink?.startsWith('https://')
      || !expiresAt
      || !pollingInterval
    ) throw new QrRelayError('relay_invalid_response');
    return { type, sessionId, qrLink, expiresAt, pollingInterval };
  }
  if (type === 'waiting') {
    const expiresAt = asSafeInteger(object?.expiresAt);
    if (!expiresAt) throw new QrRelayError('relay_invalid_response');
    return { type, expiresAt };
  }
  if (type === 'password_required') {
    const hint = object?.hint;
    if (hint !== null && typeof hint !== 'string') {
      throw new QrRelayError('relay_invalid_response');
    }
    return { type, hint };
  }
  if (type === 'completed') {
    return { type, session: validateSession(object?.session) };
  }
  if (object?.ok === true) return { type: 'cancelled' };
  throw new QrRelayError('relay_invalid_response');
}

function validateSession(value: unknown): StoredMaxSession {
  const object = asObject(value);
  const viewerId = asString(object?.viewerId);
  const token = asString(object?.token);
  const deviceId = asString(object?.deviceId);
  if (
    !viewerId || !/^\d+$/u.test(viewerId) || viewerId === '0'
    || !token || token.length > 16_384
    || !deviceId || deviceId.length > 128
  ) throw new QrRelayError('relay_invalid_response');
  return { viewerId, token, deviceId };
}

function relayBaseUrl(): URL {
  const configured = env.MAX_QR_RELAY_URL?.trim();
  if (!configured) throw new QrRelayError('relay_not_configured', 503);
  let url: URL;
  try { url = new URL(configured); } catch { throw new QrRelayError('relay_not_configured', 503); }
  if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
    throw new QrRelayError('relay_not_configured', 503);
  }
  return url;
}

function relayToken(): string {
  const token = env.MAX_QR_RELAY_TOKEN;
  if (!token || token.length < 32) throw new QrRelayError('relay_not_configured', 503);
  return token;
}

function relayTransportMetadata(error: unknown): Record<string, string | number> {
  if (!(error instanceof Error)) return { errorType: typeof error };
  const metadata: Record<string, string | number> = {
    errorType: error.name,
    errorMessage: error.message.slice(0, 256),
  };
  const cause = error.cause;
  if (cause && typeof cause === 'object') {
    const code = Reflect.get(cause, 'code');
    if (typeof code === 'string' || typeof code === 'number') metadata.causeCode = code;
  }
  return metadata;
}

function asObject(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}

function asSafeInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
}
