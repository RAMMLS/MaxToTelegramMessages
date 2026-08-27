import { authorizePortalRequest } from '@/lib/portal-auth';
import { deriveOwnerKey } from '@/lib/auth-utils';
import { getSessionStatus, initializeSessionStore } from '@/lib/session-store';

export const dynamic = 'force-dynamic';

export async function GET(request: Request): Promise<Response> {
  const authorization = authorizePortalRequest(request.headers);
  if (!authorization.ok) return Response.json({ error: 'unauthorized' }, { status: authorization.status });
  const ownerKey = await deriveOwnerKey(
    authorization.user.userId,
    authorization.user.email,
  );
  await initializeSessionStore();
  return Response.json(await getSessionStatus(ownerKey), {
    headers: { 'Cache-Control': 'no-store' },
  });
}
