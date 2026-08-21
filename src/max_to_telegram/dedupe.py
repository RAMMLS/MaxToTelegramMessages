"""Durable delivery state used to suppress duplicate Telegram notifications."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


class DedupeError(RuntimeError):
    """Raised when the delivery state cannot be read or updated safely."""


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """Result of claiming a MAX message revision for delivery."""

    should_deliver: bool
    previous_attempts: int


class DedupeStore:
    """Small SQLite store with an atomic pending/delivered state machine.

    A pending record is deliberately retryable after a process crash. That can
    produce one duplicate if Telegram accepted a request immediately before the
    crash, but it avoids silently losing the notification.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None

    def open(self) -> DedupeStore:
        if self._connection is not None:
            return self
        if self.path.exists() and not self.path.is_file():
            raise DedupeError("state database path must be a regular file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=10000")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    dedupe_key TEXT PRIMARY KEY,
                    state TEXT NOT NULL CHECK (state IN ('pending', 'delivered')),
                    attempts INTEGER NOT NULL DEFAULT 1,
                    updated_at TEXT NOT NULL,
                    telegram_message_ids TEXT,
                    last_error_kind TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS deliveries_updated_at ON deliveries(updated_at)"
            )
        except sqlite3.Error as exc:
            raise DedupeError("could not initialize the delivery state database") from exc
        self._connection = connection
        with suppress(OSError):
            os.chmod(self.path, 0o600)
        return self

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> DedupeStore:
        return self.open()

    def __exit__(self, *_: object) -> None:
        self.close()

    def claim(self, dedupe_key: str, *, now: datetime | None = None) -> DeliveryClaim:
        """Atomically claim a message; delivered records are never claimed again."""

        key = _validate_key(dedupe_key)
        timestamp = _utc_iso(now)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, attempts FROM deliveries WHERE dedupe_key = ?", (key,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO deliveries (dedupe_key, state, attempts, updated_at)
                    VALUES (?, 'pending', 1, ?)
                    """,
                    (key, timestamp),
                )
                connection.execute("COMMIT")
                return DeliveryClaim(should_deliver=True, previous_attempts=0)

            state, attempts = str(row[0]), int(row[1])
            if state == "delivered":
                connection.execute("COMMIT")
                return DeliveryClaim(should_deliver=False, previous_attempts=attempts)

            connection.execute(
                """
                UPDATE deliveries
                SET attempts = attempts + 1, updated_at = ?, last_error_kind = NULL
                WHERE dedupe_key = ?
                """,
                (timestamp, key),
            )
            connection.execute("COMMIT")
            return DeliveryClaim(should_deliver=True, previous_attempts=attempts)
        except sqlite3.Error as exc:
            _rollback(connection)
            raise DedupeError("could not claim a delivery record") from exc

    def mark_delivered(
        self,
        dedupe_key: str,
        telegram_message_ids: tuple[int, ...] | list[int],
        *,
        now: datetime | None = None,
    ) -> None:
        key = _validate_key(dedupe_key)
        if not telegram_message_ids or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in telegram_message_ids
        ):
            raise DedupeError("telegram message IDs must be positive integers")
        connection = self._require_connection()
        try:
            cursor = connection.execute(
                """
                UPDATE deliveries
                SET state = 'delivered', updated_at = ?, telegram_message_ids = ?,
                    last_error_kind = NULL
                WHERE dedupe_key = ? AND state = 'pending'
                """,
                (_utc_iso(now), json.dumps(list(telegram_message_ids)), key),
            )
        except sqlite3.Error as exc:
            raise DedupeError("could not mark a delivery record as delivered") from exc
        if cursor.rowcount != 1:
            raise DedupeError("delivery record was not pending")

    def mark_failed(
        self,
        dedupe_key: str,
        error_kind: str,
        *,
        now: datetime | None = None,
    ) -> None:
        key = _validate_key(dedupe_key)
        safe_kind = error_kind.strip().lower()
        if not safe_kind or len(safe_kind) > 64 or not safe_kind.replace("_", "").isalnum():
            raise DedupeError("error kind must be a short identifier")
        connection = self._require_connection()
        try:
            cursor = connection.execute(
                """
                UPDATE deliveries
                SET updated_at = ?, last_error_kind = ?
                WHERE dedupe_key = ? AND state = 'pending'
                """,
                (_utc_iso(now), safe_kind, key),
            )
        except sqlite3.Error as exc:
            raise DedupeError("could not update a failed delivery record") from exc
        if cursor.rowcount != 1:
            raise DedupeError("delivery record was not pending")

    def prune(self, *, delivered_before: datetime, keep_at_most: int = 100_000) -> int:
        """Delete old delivered rows and cap retained delivered history."""

        if keep_at_most < 0:
            raise DedupeError("keep_at_most must not be negative")
        cutoff = _utc_iso(delivered_before)
        connection = self._require_connection()
        try:
            old = connection.execute(
                "DELETE FROM deliveries WHERE state = 'delivered' AND updated_at < ?", (cutoff,)
            ).rowcount
            excess = connection.execute(
                """
                DELETE FROM deliveries
                WHERE dedupe_key IN (
                    SELECT dedupe_key FROM deliveries
                    WHERE state = 'delivered'
                    ORDER BY updated_at DESC
                    LIMIT -1 OFFSET ?
                )
                """,
                (keep_at_most,),
            ).rowcount
        except sqlite3.Error as exc:
            raise DedupeError("could not prune delivery records") from exc
        return old + excess

    def prune_defaults(self, *, now: datetime | None = None) -> int:
        current = _as_utc(now or datetime.now(UTC))
        return self.prune(delivered_before=current - timedelta(days=30))

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DedupeError("delivery state database is not open")
        return self._connection


def _validate_key(value: str) -> str:
    if not isinstance(value, str):
        raise DedupeError("dedupe key must be text")
    key = value.strip()
    if not key or len(key) > 1024 or "\x00" in key:
        raise DedupeError("dedupe key has an invalid value")
    return key


def _utc_iso(value: datetime | None) -> str:
    current = value or datetime.now(UTC)
    return _as_utc(current).isoformat(timespec="microseconds")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DedupeError("timestamps must include a timezone")
    return value.astimezone(UTC)


def _rollback(connection: sqlite3.Connection) -> None:
    with suppress(sqlite3.Error):
        connection.execute("ROLLBACK")
