import { headers } from 'next/headers';
import { LoginPortal } from '@/app/login-portal';
import { PortalGate } from '@/app/portal-gate';
import { hasPortalSession } from '@/lib/portal-session';

export const dynamic = 'force-dynamic';

export default async function Home() {
  return await hasPortalSession(await headers()) ? <LoginPortal /> : <PortalGate />;
}
