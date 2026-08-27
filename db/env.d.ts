declare namespace Cloudflare {
  interface Env {
    ASSETS?: Fetcher;
    DB: D1Database;
    BRIDGE_SESSION_SYNC_TOKEN?: string;
    MAX_PORTAL_ACCESS_KEY?: string;
    MAX_SESSION_ENCRYPTION_KEY?: string;
    MAX_WEB_APP_VERSION?: string;
  }
}
