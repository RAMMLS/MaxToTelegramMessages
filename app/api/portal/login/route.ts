import { deriveOwnerKey, hasSameOrigin } from '@/lib/auth-utils';
import { createPortalSessionCookie, verifyPortalAccessKey } from '@/lib/portal-session';
import { claimLoginAttempt, initializeSessionStore } from '@/lib/session-store';

export const dynamic = 'force-dynamic';

export async function POST(request: Request): Promise<Response> {
  if (!hasSameOrigin(request)) return Response.json({ error: 'forbidden' }, { status: 403 });

  const raw = await request.text();
  if (raw.length > 1024) return Response.json({ error: 'invalid_request' }, { status: 400 });
  let accessKey: unknown;
  try {
    accessKey = (JSON.parse(raw) as { accessKey?: unknown }).accessKey;
  } catch {
    return Response.json({ error: 'invalid_request' }, { status: 400 });
  }
  if (typeof accessKey !== 'string' || accessKey.length < 1 || accessKey.length > 256) {
    return Response.json({ error: 'invalid_request' }, { status: 400 });
  }

  await initializeSessionStore();
  const clientKey = await deriveOwnerKey(
    null,
    request.headers.get('cf-connecting-ip') || 'unknown-client',
  );
  if (!(await claimLoginAttempt(clientKey))) {
    return Response.json({ error: 'rate_limited' }, { status: 429 });
  }
  if (!(await verifyPortalAccessKey(accessKey))) {
    return Response.json({ error: 'invalid_access_key' }, { status: 401 });
  }

  return Response.json(
    { ok: true },
    {
      headers: {
        'Cache-Control': 'no-store',
        'Set-Cookie': await createPortalSessionCookie(),
      },
    },
  );
}
