import vinextApp from 'vinext/server/app-router-entry';
import { handleQrUpgrade } from '@/lib/qr-upgrade';

export default {
  async fetch(request: Request, environment: Cloudflare.Env, context: ExecutionContext) {
    const url = new URL(request.url);
    if (url.pathname === '/api/max/qr' && request.headers.get('upgrade')?.toLowerCase() === 'websocket') {
      return handleQrUpgrade(request);
    }
    return vinextApp.fetch(request, environment, context);
  },
};
