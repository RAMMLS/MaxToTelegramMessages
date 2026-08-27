export function constantTimeEqual(expected: string, supplied: string): boolean {
  const left = new TextEncoder().encode(expected);
  const right = new TextEncoder().encode(supplied);
  let different = left.byteLength ^ right.byteLength;
  const length = Math.max(left.byteLength, right.byteLength);
  for (let index = 0; index < length; index += 1) {
    different |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return different === 0;
}
