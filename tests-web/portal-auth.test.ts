import { describe, expect, it } from 'vitest';
import { hasSameOrigin, parseAllowedEmails } from '@/lib/auth-utils';

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
});
