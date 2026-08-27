import { describe, expect, it } from 'vitest';
import { getChatGPTUserFromHeaders } from '@/app/chatgpt-auth';
import { deriveOwnerKey, hasSameOrigin, parseAllowedEmails } from '@/lib/auth-utils';

describe('portal authorization helpers', () => {
  it('normalizes the explicit email allowlist', () => {
    expect([...parseAllowedEmails(' OWNER@example.com,scanner@example.com ')])
      .toEqual(['owner@example.com', 'scanner@example.com']);
  });

  it('requires an exact same-origin WebSocket handshake', () => {
    expect(hasSameOrigin(new Request('https://portal.example/api/max/qr', {
      headers: { Origin: 'https://portal.example' },
    }))).toBe(true);
    expect(hasSameOrigin(new Request('https://portal.example/api/max/qr', {
      headers: { Origin: 'https://attacker.example' },
    }))).toBe(false);
  });

  it('accepts a Sites-authenticated email when the optional user ID is absent', () => {
    expect(getChatGPTUserFromHeaders(new Headers({
      'oai-authenticated-user-email': 'owner@example.com',
    }))).toMatchObject({ userId: null, email: 'owner@example.com' });
  });

  it('derives an opaque stable database key without storing the email', async () => {
    const key = await deriveOwnerKey(null, 'Owner@example.com');
    expect(key).toHaveLength(64);
    expect(key).not.toContain('owner@example.com');
    await expect(deriveOwnerKey(null, 'owner@example.com')).resolves.toBe(key);
  });
});
