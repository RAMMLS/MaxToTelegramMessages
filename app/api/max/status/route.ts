import { authorizePortalRequest } from '@/lib/portal-auth';
import { getSessionStatus, initializeSessionStore } from '@/lib/session-store';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  const authorization = authorizePortalRequest(request.headers);
  if (!authorization.ok) return Response.json({ error: 'unauthorized' }, { status: authorization.status });
  await initializeSessionStore();
  return Response.json(await getSessionStatus(authorization.user.userId), {
    headers: { 'Cache-Control': 'no-store' },
  });
}
