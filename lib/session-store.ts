import { env } from 'cloudflare:workers';
import { decryptJson, encryptJson } from '@/lib/session-crypto';

export type StoredMaxSession = {
  viewerId: string;
  token: string;
  deviceId: string;
};

type SessionRow = {
  encrypted_payload: string;
  iv: string;
  updated_at: number;
};

const RATE_WINDOW_MS = 10 * 60 * 1000;
const RATE_LIMIT = 6;
const LOGIN_RATE_LIMIT = 10;

export async function initializeSessionStore(): Promise<void> {
  const db = requireDatabase();
  await db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS max_sessions (
      owner_id TEXT PRIMARY KEY,
      encrypted_payload TEXT NOT NULL,
      iv TEXT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1,
      updated_at INTEGER NOT NULL
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS portal_events (
      id TEXT PRIMARY KEY,
      owner_id TEXT NOT NULL,
      kind TEXT NOT NULL,
      created_at INTEGER NOT NULL
    )`),
    db.prepare(`CREATE INDEX IF NOT EXISTS idx_portal_events_owner_created
      ON portal_events(owner_id, created_at)`),
  ]);
}

export async function claimQrAttempt(ownerId: string, now = Date.now()): Promise<boolean> {
  return claimPortalAttempt(ownerId, 'qr_started', RATE_LIMIT, now);
}

export async function claimLoginAttempt(clientId: string, now = Date.now()): Promise<boolean> {
  return claimPortalAttempt(clientId, 'login_attempt', LOGIN_RATE_LIMIT, now);
}

async function claimPortalAttempt(
  ownerId: string,
  kind: string,
  limit: number,
  now: number,
): Promise<boolean> {
  const db = requireDatabase();
  const cutoff = now - RATE_WINDOW_MS;
  const result = await db
    .prepare(`SELECT COUNT(*) AS count FROM portal_events
      WHERE owner_id = ? AND kind = ? AND created_at >= ?`)
    .bind(ownerId, kind, cutoff)
    .first<{ count: number }>();
  if ((result?.count ?? 0) >= limit) return false;

  await db.batch([
    db.prepare('INSERT INTO portal_events (id, owner_id, kind, created_at) VALUES (?, ?, ?, ?)')
      .bind(crypto.randomUUID(), ownerId, kind, now),
    db.prepare('DELETE FROM portal_events WHERE created_at < ?').bind(now - 24 * 60 * 60 * 1000),
  ]);
  return true;
}

export async function saveMaxSession(ownerId: string, session: StoredMaxSession): Promise<number> {
  const encryptionKey = requireEncryptionKey();
  const encrypted = await encryptJson(session, encryptionKey);
  const updatedAt = Date.now();
  await requireDatabase()
    .prepare(`INSERT INTO max_sessions
      (owner_id, encrypted_payload, iv, version, updated_at)
      VALUES (?, ?, ?, 1, ?)
      ON CONFLICT(owner_id) DO UPDATE SET
        encrypted_payload = excluded.encrypted_payload,
        iv = excluded.iv,
        version = max_sessions.version + 1,
        updated_at = excluded.updated_at`)
    .bind(ownerId, encrypted.ciphertext, encrypted.iv, updatedAt)
    .run();
  return updatedAt;
}

export async function getLatestMaxSession(): Promise<{
  session: StoredMaxSession;
  updatedAt: number;
} | null> {
  const row = await requireDatabase()
    .prepare(`SELECT encrypted_payload, iv, updated_at
      FROM max_sessions ORDER BY updated_at DESC LIMIT 1`)
    .first<SessionRow>();
  if (!row) return null;
  return {
    session: await decryptJson<StoredMaxSession>(
      { ciphertext: row.encrypted_payload, iv: row.iv },
      requireEncryptionKey(),
    ),
    updatedAt: row.updated_at,
  };
}

export async function getSessionStatus(ownerId: string): Promise<{
  connected: boolean;
  updatedAt: number | null;
}> {
  const row = await requireDatabase()
    .prepare('SELECT updated_at FROM max_sessions WHERE owner_id = ?')
    .bind(ownerId)
    .first<{ updated_at: number }>();
  return { connected: Boolean(row), updatedAt: row?.updated_at ?? null };
}

function requireDatabase(): D1Database {
  if (!env.DB) throw new Error('D1 database is unavailable');
  return env.DB;
}

function requireEncryptionKey(): string {
  if (!env.MAX_SESSION_ENCRYPTION_KEY) {
    throw new Error('MAX session encryption is not configured');
  }
  return env.MAX_SESSION_ENCRYPTION_KEY;
}
