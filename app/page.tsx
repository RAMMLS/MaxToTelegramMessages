import { requireChatGPTUser } from '@/app/chatgpt-auth';
import { LoginPortal } from '@/app/login-portal';

export const dynamic = 'force-dynamic';

export default async function Home() {
  const user = await requireChatGPTUser('/');
  return <LoginPortal displayName={user.displayName} />;
}
