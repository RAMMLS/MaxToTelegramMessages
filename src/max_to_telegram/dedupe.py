"""Durable delivery state used to suppress duplicate Telegram notifications."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from max_to_telegram.parser import ParsedMessage

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]


class DedupeError(RuntimeError):
    """Raised when the delivery state cannot be read or updated safely."""


@dataclass(frozen=True, slots=True)
class DeliveryClaim:
    """Result of claiming a MAX message revision for delivery."""

    should_deliver: bool
    previous_attempts: int


@dataclass(frozen=True, slots=True)
class OutboxStats:
    pending: int
    pending_failed: int
    delivered: int
    total: int


class DedupeStore:
    """Small SQLite store with an atomic pending/delivered state machine.

    A pending record is deliberately retryable after a process crash. That can
    produce one duplicate if Telegram accepted a request immediately before the
    crash, but it avoids silently losing the notification.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock_descriptor: int | None = None

    def open(self) -> DedupeStore:
        if self._connection is not None:
            return self
        if self.path.exists() and not self.path.is_file():
            raise DedupeError("state database path must be a regular file")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._acquire_process_lock()
        connection: sqlite3.Connection | None = None
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
                    last_error_kind TEXT,
                    message_json TEXT
                )
                """
            )
            columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(deliveries)")}
            if "message_json" not in columns:
                connection.execute("ALTER TABLE deliveries ADD COLUMN message_json TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS deliveries_updated_at ON deliveries(updated_at)"
            )
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            self._release_process_lock()
            raise DedupeError("could not initialize the delivery state database") from exc
        self._connection = connection
        with suppress(OSError):
            os.chmod(self.path, 0o600)
        return self

    def close(self) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
        finally:
            self._release_process_lock()

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

    def enqueue(
        self,
        message: ParsedMessage,
        *,
        now: datetime | None = None,
    ) -> DeliveryClaim:
        """Persist a normalized message before it enters the in-memory queue."""

        if not isinstance(message, ParsedMessage):
            raise DedupeError("outbox accepts only parsed MAX messages")
        key = _validate_key(message.dedupe_key)
        timestamp = _utc_iso(now)
        serialized = _serialize_message(message)
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT state, attempts FROM deliveries WHERE dedupe_key = ?", (key,)
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO deliveries (
                        dedupe_key, state, attempts, updated_at, message_json
                    ) VALUES (?, 'pending', 1, ?, ?)
                    """,
                    (key, timestamp, serialized),
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
                SET message_json = ?, updated_at = ?
                WHERE dedupe_key = ?
                """,
                (serialized, timestamp, key),
            )
            connection.execute("COMMIT")
            return DeliveryClaim(should_deliver=True, previous_attempts=attempts)
        except sqlite3.Error as exc:
            _rollback(connection)
            raise DedupeError("could not enqueue a delivery record") from exc

    def pending_messages(self, *, limit: int = 10_000) -> tuple[ParsedMessage, ...]:
        """Return recoverable pending messages in durable insertion order."""

        if limit < 1 or limit > 100_000:
            raise DedupeError("pending message limit must be between 1 and 100000")
        connection = self._require_connection()
        try:
            rows = connection.execute(
                """
                SELECT dedupe_key, message_json FROM deliveries
                WHERE state = 'pending' AND message_json IS NOT NULL
                ORDER BY updated_at ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        except sqlite3.Error as exc:
            raise DedupeError("could not load pending delivery records") from exc
        messages: list[ParsedMessage] = []
        for key, payload in rows:
            try:
                message = _deserialize_message(str(payload))
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise DedupeError(f"pending delivery record is corrupt: {key!r}") from exc
            if message.dedupe_key != key:
                raise DedupeError(f"pending delivery key does not match its payload: {key!r}")
            messages.append(message)
        return tuple(messages)

    def stats(self) -> OutboxStats:
        """Return content-free operational counters."""

        connection = self._require_connection()
        try:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN state = 'pending' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN state = 'pending' AND last_error_kind IS NOT NULL
                        THEN 1 ELSE 0 END),
                    SUM(CASE WHEN state = 'delivered' THEN 1 ELSE 0 END),
                    COUNT(*)
                FROM deliveries
                """
            ).fetchone()
        except sqlite3.Error as exc:
            raise DedupeError("could not inspect delivery records") from exc
        if row is None:
            raise DedupeError("delivery counters query returned no result")
        return OutboxStats(*(int(value or 0) for value in row))

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
                    last_error_kind = NULL, message_json = NULL
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

    def discard_pending(self, dedupe_key: str) -> bool:
        """Remove an undelivered item when the current chat policy rejects it."""

        key = _validate_key(dedupe_key)
        connection = self._require_connection()
        try:
            cursor = connection.execute(
                "DELETE FROM deliveries WHERE dedupe_key = ? AND state = 'pending'", (key,)
            )
        except sqlite3.Error as exc:
            raise DedupeError("could not discard a pending delivery record") from exc
        return cursor.rowcount == 1

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
        current = _as_utc(now or datetime.now(timezone.utc))
        return self.prune(delivered_before=current - timedelta(days=30))

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise DedupeError("delivery state database is not open")
        return self._connection

    def _acquire_process_lock(self) -> None:
        if fcntl is None or self._lock_descriptor is not None:
            return
        lock_path = Path(f"{self.path}.lock")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(lock_path, flags, 0o600)
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise DedupeError("state lock path must be a regular file")
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
            raise DedupeError("state database is already used by another bridge process") from exc
        except (OSError, DedupeError) as exc:
            if descriptor >= 0:
                with suppress(OSError):
                    os.close(descriptor)
            if isinstance(exc, DedupeError):
                raise
            raise DedupeError("could not acquire the state database process lock") from exc
        self._lock_descriptor = descriptor

    def _release_process_lock(self) -> None:
        if self._lock_descriptor is None:
            return
        descriptor = self._lock_descriptor
        self._lock_descriptor = None
        if fcntl is not None:
            with suppress(OSError):
                fcntl.flock(descriptor, fcntl.LOCK_UN)
        with suppress(OSError):
            os.close(descriptor)


def _validate_key(value: str) -> str:
    if not isinstance(value, str):
        raise DedupeError("dedupe key must be text")
    key = value.strip()
    if not key or len(key) > 1024 or "\x00" in key:
        raise DedupeError("dedupe key has an invalid value")
    return key


def _utc_iso(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    return _as_utc(current).isoformat(timespec="microseconds")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise DedupeError("timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def _rollback(connection: sqlite3.Connection) -> None:
    with suppress(sqlite3.Error):
        connection.execute("ROLLBACK")


def _serialize_message(message: ParsedMessage) -> str:
    return json.dumps(
        {
            "chat_id": message.chat_id,
            "message_id": message.message_id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "chat_title": message.chat_title,
            "text": message.text,
            "timestamp": message.timestamp.isoformat(),
            "timestamp_raw": message.timestamp_raw,
            "update_time": message.update_time,
            "status": message.status,
            "message_type": message.message_type,
            "attachments": list(message.attachments),
            "is_outgoing": message.is_outgoing,
            "is_service": message.is_service,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _deserialize_message(payload: str) -> ParsedMessage:
    document = json.loads(payload)
    if not isinstance(document, dict):
        raise TypeError("message payload must be an object")
    timestamp = datetime.fromisoformat(document["timestamp"])
    if timestamp.tzinfo is None:
        raise ValueError("message timestamp has no timezone")
    attachments = document["attachments"]
    if not isinstance(attachments, list) or not all(isinstance(item, str) for item in attachments):
        raise TypeError("message attachments must be text")
    message = ParsedMessage(
        chat_id=_stored_int(document, "chat_id"),
        message_id=_stored_text(document, "message_id"),
        sender_id=_stored_optional_int(document, "sender_id"),
        sender_name=_stored_text(document, "sender_name"),
        chat_title=_stored_text(document, "chat_title"),
        text=_stored_text(document, "text"),
        timestamp=timestamp.astimezone(timezone.utc),
        timestamp_raw=_stored_int(document, "timestamp_raw"),
        update_time=_stored_optional_int(document, "update_time"),
        status=_stored_optional_text(document, "status"),
        message_type=_stored_text(document, "message_type"),
        attachments=tuple(attachments),
        is_outgoing=_stored_bool(document, "is_outgoing"),
        is_service=_stored_bool(document, "is_service"),
    )
    if len(message.text) > 1_000_000:
        raise ValueError("stored message is too long")
    return message


def _stored_int(document: dict[str, object], key: str) -> int:
    value = document[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _stored_optional_int(document: dict[str, object], key: str) -> int | None:
    value = document[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{key} must be an integer")
    return value


def _stored_text(document: dict[str, object], key: str) -> str:
    value = document[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be text")
    return value


def _stored_optional_text(document: dict[str, object], key: str) -> str | None:
    value = document[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{key} must be text")
    return value


def _stored_bool(document: dict[str, object], key: str) -> bool:
    value = document[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value
