import { describe, expect, it } from 'vitest';
import { deriveOwnerKey, hasSameOrigin } from '@/lib/auth-utils';
import { createSignedPortalCookie, validatePortalCookie } from '@/lib/portal-cookie';

describe('portal access', () => {
  it('requires an exact same-origin request', () => {
    expect(hasSameOrigin(new Request('https://portal.example/api/portal/login', {
      headers: { Origin: 'https://portal.example' },
    }))).toBe(true);
    expect(hasSameOrigin(new Request('https://portal.example/api/portal/login', {
      headers: { Origin: 'https://attacker.example' },
    }))).toBe(false);
  });

  it('creates and validates an HttpOnly signed session cookie', async () => {
    const now = 1_787_800_000_000;
    const secret = 'p'.repeat(48);
    const setCookie = await createSignedPortalCookie(secret, now);
    expect(setCookie).toContain('HttpOnly');
    expect(setCookie).toContain('Secure');
    expect(setCookie).toContain('SameSite=Strict');
    const cookie = setCookie.split(';', 1)[0];
    await expect(validatePortalCookie(new Headers({ cookie }), secret, now + 1000))
      .resolves.toBe(true);
    await expect(validatePortalCookie(new Headers({ cookie }), 'x'.repeat(48), now + 1000))
      .resolves.toBe(false);
    await expect(validatePortalCookie(new Headers({ cookie }), secret, now + 13 * 60 * 60 * 1000))
      .resolves.toBe(false);
  });

  it('derives an opaque stable rate-limit key without storing client data', async () => {
    const key = await deriveOwnerKey(null, '198.51.100.7');
    expect(key).toHaveLength(64);
    expect(key).not.toContain('198.51.100.7');
    await expect(deriveOwnerKey(null, '198.51.100.7')).resolves.toBe(key);
  });
});
