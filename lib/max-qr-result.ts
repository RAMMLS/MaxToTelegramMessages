import type { StoredMaxSession } from '@/lib/session-store';

type JsonObject = Record<string, unknown>;

export function extractSession(payload: unknown, deviceId: string): StoredMaxSession {
  const root = asObject(payload);
  const tokenAttrs = asObject(root?.tokenAttrs);
  const login = asObject(tokenAttrs?.LOGIN);
  const profile = asObject(root?.profile);
  const contact = asObject(profile?.contact);
  const token = asString(login?.token);
  const viewerId = contact?.id;
  if (!token || (typeof viewerId !== 'number' && typeof viewerId !== 'bigint')) {
    throw new Error('MAX login response did not contain a session');
  }
  const viewerIdString = String(viewerId);
  if (!/^[1-9][0-9]{0,18}$/u.test(viewerIdString)) {
    throw new Error('MAX login returned an invalid viewer ID');
  }
  return { viewerId: viewerIdString, token, deviceId };
}

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as JsonObject
    : null;
}

function asString(value: unknown): string | null {
  return typeof value === 'string' ? value : null;
}
