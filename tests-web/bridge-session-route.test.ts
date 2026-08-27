import { describe, expect, it } from 'vitest';
import { constantTimeEqual } from '@/lib/secure-compare';

describe('bridge session bearer token', () => {
  it('accepts only an exact match', () => {
    expect(constantTimeEqual('expected-token', 'expected-token')).toBe(true);
    expect(constantTimeEqual('expected-token', 'expected-toke')).toBe(false);
    expect(constantTimeEqual('expected-token', 'different-token')).toBe(false);
  });
});
