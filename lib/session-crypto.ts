export type EncryptedValue = {
  ciphertext: string;
  iv: string;
};

const KEY_BYTES = 32;
const IV_BYTES = 12;

export async function encryptJson(
  value: unknown,
  encodedKey: string,
): Promise<EncryptedValue> {
  const key = await importKey(encodedKey);
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const plaintext = new TextEncoder().encode(JSON.stringify(value));
  const ciphertext = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, plaintext);
  return { ciphertext: toBase64Url(new Uint8Array(ciphertext)), iv: toBase64Url(iv) };
}

export async function decryptJson<T>(
  value: EncryptedValue,
  encodedKey: string,
): Promise<T> {
  const key = await importKey(encodedKey);
  const plaintext = await crypto.subtle.decrypt(
    { name: 'AES-GCM', iv: fromBase64Url(value.iv) },
    key,
    fromBase64Url(value.ciphertext),
  );
  return JSON.parse(new TextDecoder().decode(plaintext)) as T;
}

async function importKey(encodedKey: string): Promise<CryptoKey> {
  const raw = fromBase64Url(encodedKey);
  if (raw.byteLength !== KEY_BYTES) {
    throw new Error('MAX_SESSION_ENCRYPTION_KEY must contain exactly 32 bytes');
  }
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

export function toBase64Url(value: Uint8Array): string {
  let binary = '';
  for (const byte of value) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll('+', '-').replaceAll('/', '_').replace(/=+$/u, '');
}

export function fromBase64Url(value: string): Uint8Array<ArrayBuffer> {
  if (!/^[A-Za-z0-9_-]+$/u.test(value)) throw new Error('invalid base64url value');
  const padded = value.replaceAll('-', '+').replaceAll('_', '/').padEnd(
    Math.ceil(value.length / 4) * 4,
    '=',
  );
  const binary = atob(padded);
  const result = new Uint8Array(new ArrayBuffer(binary.length));
  for (let index = 0; index < binary.length; index += 1) result[index] = binary.charCodeAt(index);
  return result;
}
