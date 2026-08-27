export const dynamic = 'force-dynamic';

export function GET(): Response {
  return new Response('WebSocket upgrade required', { status: 426 });
}
