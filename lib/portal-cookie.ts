import { constantTimeEqual } from '@/lib/secure-compare';
import { toBase64Url } from '@/lib/session-crypto';

const COOKIE_NAME = 'max_portal_session';
const SESSION_SECONDS = 12 * 60 * 60;
const COOKIE_VERSION = 'v1';

export async function createSignedPortalCookie(secret: string, now = Date.now()): Promise<string> {
  validateSecret(secret);
  const expiresAt = now + SESSION_SECONDS * 1000;
  const value = `${COOKIE_VERSION}.${expiresAt}`;
  const signature = await sign(secret, value);
  return `${COOKIE_NAME}=${value}.${signature}; Path=/; Max-Age=${SESSION_SECONDS}; HttpOnly; Secure; SameSite=Strict`;
}

export async function validatePortalCookie(
  headers: Headers,
  secret: string,
  now = Date.now(),
): Promise<boolean> {
  try {
    validateSecret(secret);
    const raw = readCookie(headers.get('cookie'), COOKIE_NAME);
    if (!raw) return false;
    const parts = raw.split('.');
    if (parts.length !== 3 || parts[0] !== COOKIE_VERSION || !/^\d{13}$/u.test(parts[1])) {
      return false;
    }
    const expiresAt = Number(parts[1]);
    if (expiresAt <= now || expiresAt > now + (SESSION_SECONDS + 60) * 1000) return false;
    return constantTimeEqual(await sign(secret, `${parts[0]}.${parts[1]}`), parts[2]);
  } catch {
    return false;
  }
}

async function sign(secret: string, value: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign'],
  );
  const signature = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(value));
  return toBase64Url(new Uint8Array(signature));
}

function validateSecret(secret: string): void {
  if (secret.length < 32 || secret.length > 256) {
    throw new Error('Portal access key must be 32..256 characters');
  }
}

function readCookie(header: string | null, name: string): string | null {
  if (!header || header.length > 8192) return null;
  for (const part of header.split(';')) {
    const separator = part.indexOf('=');
    if (separator < 0 || part.slice(0, separator).trim() !== name) continue;
    return part.slice(separator + 1).trim() || null;
  }
  return null;
}
