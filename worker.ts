import vinextApp from 'vinext/server/app-router-entry';

export default {
  async fetch(request: Request, environment: Cloudflare.Env, context: ExecutionContext) {
    return vinextApp.fetch(request, environment, context);
  },
};
