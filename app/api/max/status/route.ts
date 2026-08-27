import { hasPortalSession, portalOwnerKey } from '@/lib/portal-session';
import { getSessionStatus, initializeSessionStore } from '@/lib/session-store';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  if (!(await hasPortalSession(request.headers))) {
    return Response.json({ error: 'unauthorized' }, { status: 401 });
  }
  const ownerKey = portalOwnerKey();
  await initializeSessionStore();
  return Response.json(await getSessionStatus(ownerKey), {
    headers: { 'Cache-Control': 'no-store' },
  });
}
