import { env } from 'cloudflare:workers';
import type { ChatGPTUser } from '@/app/chatgpt-auth';
import { getChatGPTUserFromHeaders } from '@/app/chatgpt-auth';
import { parseAllowedEmails } from '@/lib/auth-utils';

export type PortalAuthorization =
  | { ok: true; user: ChatGPTUser }
  | { ok: false; status: 401 | 403 };

export function authorizePortalRequest(headers: Headers): PortalAuthorization {
  const user = getChatGPTUserFromHeaders(headers);
  if (!user) return { ok: false, status: 401 };

  const allowlist = parseAllowedEmails(env.MAX_PORTAL_ALLOWED_EMAILS);
  if (allowlist.size > 0 && !allowlist.has(user.email.toLowerCase())) {
    return { ok: false, status: 403 };
  }
  return { ok: true, user };
}
