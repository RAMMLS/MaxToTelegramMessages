import { env } from 'cloudflare:workers';
import { constantTimeEqual } from '@/lib/secure-compare';
import { getLatestMaxSession, initializeSessionStore } from '@/lib/session-store';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  const expected = env.BRIDGE_SESSION_SYNC_TOKEN;
  const supplied = request.headers.get('authorization')?.replace(/^Bearer\s+/iu, '') ?? '';
  if (!expected || !constantTimeEqual(expected, supplied)) {
    return Response.json({ error: 'unauthorized' }, { status: 401 });
  }
  await initializeSessionStore();
  const stored = await getLatestMaxSession();
  if (!stored) return Response.json({ error: 'session_unavailable' }, { status: 404 });
  return Response.json(
    { ...stored.session, updatedAt: stored.updatedAt },
    { headers: { 'Cache-Control': 'no-store, private', 'X-Content-Type-Options': 'nosniff' } },
  );
}
