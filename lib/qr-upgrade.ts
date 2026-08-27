import { waitUntil } from 'cloudflare:workers';
import { attachQrSession } from '@/lib/max-qr-session';
import { deriveOwnerKey, hasSameOrigin } from '@/lib/auth-utils';
import { authorizePortalRequest } from '@/lib/portal-auth';
import { claimQrAttempt, initializeSessionStore } from '@/lib/session-store';

type WebSocketResponseInit = ResponseInit & { webSocket: WebSocket };

export async function handleQrUpgrade(request: Request): Promise<Response> {
  if (!hasSameOrigin(request)) return new Response('Forbidden', { status: 403 });

  const authorization = authorizePortalRequest(request.headers);
  if (!authorization.ok) return new Response('Unauthorized', { status: authorization.status });
  const ownerKey = await deriveOwnerKey(
    authorization.user.userId,
    authorization.user.email,
  );

  await initializeSessionStore();
  if (!(await claimQrAttempt(ownerKey))) {
    return new Response('Too many QR attempts', { status: 429 });
  }

  const pair = new WebSocketPair();
  const [client, server] = Object.values(pair);
  server.accept();
  waitUntil(Promise.resolve().then(() => attachQrSession(server, ownerKey)));
  return new Response(null, { status: 101, webSocket: client } as WebSocketResponseInit);
}
