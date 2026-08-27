CREATE TABLE `max_qr_attempts` (
	`owner_id` text PRIMARY KEY NOT NULL,
	`encrypted_payload` text NOT NULL,
	`iv` text NOT NULL,
	`expires_at` integer NOT NULL,
	`updated_at` integer NOT NULL
);
