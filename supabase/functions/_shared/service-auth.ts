export function constantTimeEqual(left: string, right: string): boolean {
  const encoder = new TextEncoder();
  const leftBytes = encoder.encode(left);
  const rightBytes = encoder.encode(right);
  const length = Math.max(leftBytes.length, rightBytes.length);
  let mismatch = leftBytes.length ^ rightBytes.length;
  for (let index = 0; index < length; index += 1) {
    mismatch |= (leftBytes[index] ?? 0) ^ (rightBytes[index] ?? 0);
  }
  return mismatch === 0;
}

export function suppliedServiceKey(req: Request): string | null {
  const apiKey = req.headers.get("apikey")?.trim();
  if (apiKey) return apiKey;
  const authorization = req.headers.get("authorization")?.trim() ?? "";
  if (authorization.toLowerCase().startsWith("bearer ")) {
    return authorization.slice(7).trim() || null;
  }
  return null;
}

export function hasServiceRole(req: Request, expectedKey: string): boolean {
  const supplied = suppliedServiceKey(req);
  return supplied !== null && constantTimeEqual(supplied, expectedKey);
}
