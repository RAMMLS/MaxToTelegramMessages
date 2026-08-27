import { index, integer, sqliteTable, text } from 'drizzle-orm/sqlite-core';

export const maxSessions = sqliteTable('max_sessions', {
  ownerId: text('owner_id').primaryKey(),
  encryptedPayload: text('encrypted_payload').notNull(),
  iv: text('iv').notNull(),
  version: integer('version').notNull().default(1),
  updatedAt: integer('updated_at', { mode: 'timestamp_ms' }).notNull(),
});

export const portalEvents = sqliteTable(
  'portal_events',
  {
    id: text('id').primaryKey(),
    ownerId: text('owner_id').notNull(),
    kind: text('kind').notNull(),
    createdAt: integer('created_at', { mode: 'timestamp_ms' }).notNull(),
  },
  (table) => [index('idx_portal_events_owner_created').on(table.ownerId, table.createdAt)],
);
