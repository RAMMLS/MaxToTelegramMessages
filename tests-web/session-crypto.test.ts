import { describe, expect, it } from 'vitest';
import { decryptJson, encryptJson, toBase64Url } from '@/lib/session-crypto';

describe('session encryption', () => {
  it('round-trips a MAX session without plaintext output', async () => {
    const key = toBase64Url(crypto.getRandomValues(new Uint8Array(32)));
    const session = { viewerId: '123', token: 'secret-session-token', deviceId: crypto.randomUUID() };
    const encrypted = await encryptJson(session, key);
    expect(encrypted.ciphertext).not.toContain(session.token);
    await expect(decryptJson(encrypted, key)).resolves.toEqual(session);
  });

  it('rejects a key with the wrong length', async () => {
    const key = toBase64Url(new Uint8Array(16));
    await expect(encryptJson({ value: 1 }, key)).rejects.toThrow('exactly 32 bytes');
  });
});
