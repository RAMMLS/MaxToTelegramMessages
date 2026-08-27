CREATE TABLE `max_sessions` (
	`owner_id` text PRIMARY KEY NOT NULL,
	`encrypted_payload` text NOT NULL,
	`iv` text NOT NULL,
	`version` integer DEFAULT 1 NOT NULL,
	`updated_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `portal_events` (
	`id` text PRIMARY KEY NOT NULL,
	`owner_id` text NOT NULL,
	`kind` text NOT NULL,
	`created_at` integer NOT NULL
);
