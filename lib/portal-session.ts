import { env } from 'cloudflare:workers';
import { createSignedPortalCookie, validatePortalCookie } from '@/lib/portal-cookie';
import { constantTimeEqual } from '@/lib/secure-compare';

const OWNER_KEY = 'single-owner-v1';

export function verifyPortalAccessKey(supplied: string): boolean {
  const expected = env.MAX_PORTAL_ACCESS_KEY;
  return Boolean(expected && expected.length >= 32 && constantTimeEqual(expected, supplied));
}

export async function createPortalSessionCookie(now = Date.now()): Promise<string> {
  return createSignedPortalCookie(requireAccessKey(), now);
}

export async function hasPortalSession(headers: Headers, now = Date.now()): Promise<boolean> {
  const accessKey = env.MAX_PORTAL_ACCESS_KEY;
  return accessKey ? validatePortalCookie(headers, accessKey, now) : false;
}

export function portalOwnerKey(): string {
  return OWNER_KEY;
}

function requireAccessKey(): string {
  const secret = env.MAX_PORTAL_ACCESS_KEY;
  if (!secret || secret.length < 32) throw new Error('Portal access key is not configured');
  return secret;
}
