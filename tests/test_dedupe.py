from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

import pytest

import max_to_telegram.dedupe as dedupe_module
from max_to_telegram.dedupe import DedupeError, DedupeStore
from max_to_telegram.parser import ParsedMessage


def parsed_message(**overrides: object) -> ParsedMessage:
    values: dict[str, object] = {
        "chat_id": 42,
        "message_id": "777",
        "sender_id": 123,
        "sender_name": "Тестовый отправитель",
        "chat_title": "Выбранный чат",
        "text": "сообщение",
        "timestamp": datetime(2026, 8, 22, tzinfo=timezone.utc),
        "timestamp_raw": 1_777_000_000_000,
        "update_time": None,
        "status": None,
        "message_type": "USER",
        "attachments": ("Фото",),
        "is_outgoing": False,
        "is_service": False,
    }
    values.update(overrides)
    return ParsedMessage(**values)  # type: ignore[arg-type]


def test_new_claim_is_deliverable_and_delivered_claim_is_not(tmp_path):
    path = tmp_path / "state.sqlite3"
    with DedupeStore(path) as store:
        first = store.claim("1:2:NEW:3")
        assert first.should_deliver is True
        assert first.previous_attempts == 0

        store.mark_delivered("1:2:NEW:3", (100, 101))
        repeated = store.claim("1:2:NEW:3")

    assert repeated.should_deliver is False
    assert repeated.previous_attempts == 1
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_pending_claim_is_retried_after_restart(tmp_path):
    path = tmp_path / "nested" / "state.sqlite3"
    with DedupeStore(path) as store:
        store.claim("message")
        store.mark_failed("message", "network_error")

    with DedupeStore(path) as reopened:
        retry = reopened.claim("message")
        reopened.mark_delivered("message", [77])

    assert retry.should_deliver is True
    assert retry.previous_attempts == 1


def test_stats_are_content_free_and_track_failures(tmp_path):
    secret_key = "super-secret-message-text"
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim(secret_key)
        store.mark_failed(secret_key, "network")
        store.claim("delivered")
        store.mark_delivered("delivered", [1])

        stats = store.stats()

    assert stats.pending == 1
    assert stats.pending_failed == 1
    assert stats.delivered == 1
    assert stats.total == 2
    assert secret_key not in repr(stats)


def test_empty_stats_are_zero(tmp_path):
    with DedupeStore(tmp_path / "state.db") as store:
        assert store.stats().total == 0
        assert store.health().integrity_ok is True
        assert store.health().recoverable_pending == 0
        assert store.health().unrecoverable_pending == 0


def test_health_distinguishes_recoverable_and_legacy_pending_rows(tmp_path):
    path = tmp_path / "state.db"
    with DedupeStore(path) as store:
        store.enqueue(parsed_message())
        store.claim("legacy-without-message")

        health = store.health()

    assert health.integrity_ok is True
    assert health.recoverable_pending == 1
    assert health.unrecoverable_pending == 1
    assert "legacy-without-message" not in repr(health)


def test_outbox_message_survives_restart(tmp_path):
    path = tmp_path / "outbox.sqlite3"
    original = parsed_message()
    with DedupeStore(path) as store:
        claim = store.enqueue(original)
        assert claim.should_deliver is True
        assert store.pending_messages() == (original,)

    with DedupeStore(path) as reopened:
        assert reopened.pending_messages() == (original,)
        reopened.mark_delivered(original.dedupe_key, [11])
        assert reopened.pending_messages() == ()
        assert reopened.enqueue(original).should_deliver is False

    with closing(sqlite3.connect(path)) as connection:
        stored = connection.execute(
            "SELECT state, message_json FROM deliveries WHERE dedupe_key = ?",
            (original.dedupe_key,),
        ).fetchone()
    assert stored == ("delivered", None)


def test_pending_outbox_edit_replaces_same_revision_payload(tmp_path):
    first = parsed_message(text="before")
    corrected = parsed_message(text="after")
    with DedupeStore(tmp_path / "state.db") as store:
        store.enqueue(first)
        claim = store.enqueue(corrected)

        assert claim.should_deliver is True
        assert claim.previous_attempts == 1
        assert store.pending_messages()[0].text == "after"


def test_different_edit_revision_is_a_second_outbox_item(tmp_path):
    original = parsed_message()
    edited = parsed_message(status="EDITED", update_time=1_777_000_000_100, text="edited")
    with DedupeStore(tmp_path / "state.db") as store:
        store.enqueue(original)
        store.enqueue(edited)

        assert store.pending_messages() == (original, edited)


@pytest.mark.parametrize("limit", [0, -1, 100_001])
def test_invalid_pending_limit_is_rejected(tmp_path, limit):
    with (
        DedupeStore(tmp_path / "state.db") as store,
        pytest.raises(DedupeError, match="pending message limit"),
    ):
        store.pending_messages(limit=limit)


@pytest.mark.parametrize("fetch_size", [0, -1, 1_001])
def test_invalid_pending_fetch_size_is_rejected(tmp_path, fetch_size):
    with (
        DedupeStore(tmp_path / "state.db") as store,
        pytest.raises(DedupeError, match="fetch size"),
    ):
        tuple(store.iter_pending_messages(fetch_size=fetch_size))


def test_outbox_rejects_non_message(tmp_path):
    with (
        DedupeStore(tmp_path / "state.db") as store,
        pytest.raises(DedupeError, match="only parsed"),
    ):
        store.enqueue("message")  # type: ignore[arg-type]


def test_corrupt_outbox_payload_fails_closed(tmp_path):
    path = tmp_path / "state.db"
    message = parsed_message()
    with DedupeStore(path) as store:
        store.enqueue(message)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE deliveries SET message_json = ? WHERE dedupe_key = ?",
            ('{"chat_id":"wrong"}', message.dedupe_key),
        )
        connection.commit()

    with DedupeStore(path) as reopened, pytest.raises(DedupeError, match="corrupt") as raised:
        reopened.pending_messages()

    assert message.dedupe_key not in str(raised.value)


def test_mismatched_outbox_key_fails_closed(tmp_path):
    path = tmp_path / "state.db"
    message = parsed_message()
    with DedupeStore(path) as store:
        store.enqueue(message)

    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "UPDATE deliveries SET dedupe_key = 'different' WHERE dedupe_key = ?",
            (message.dedupe_key,),
        )
        connection.commit()

    with (
        DedupeStore(path) as reopened,
        pytest.raises(DedupeError, match="does not match") as raised,
    ):
        reopened.pending_messages()

    assert "different" not in str(raised.value)


def test_legacy_database_gets_outbox_column(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            CREATE TABLE deliveries (
                dedupe_key TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                attempts INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                telegram_message_ids TEXT,
                last_error_kind TEXT
            )
            """
        )
        connection.commit()
    path.chmod(0o600)

    with DedupeStore(path) as store:
        store.enqueue(parsed_message())
        assert store.pending_messages() == (parsed_message(),)


def test_edited_revision_uses_a_distinct_key(tmp_path):
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim("chat:id:NEW:100")
        store.mark_delivered("chat:id:NEW:100", [1])
        edited = store.claim("chat:id:EDITED:101")

    assert edited.should_deliver is True


def test_sql_metacharacters_are_data_not_sql(tmp_path):
    key = "x'); DROP TABLE deliveries; --"
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim(key)
        store.mark_delivered(key, [1])
        assert store.claim(key).should_deliver is False
        assert store.claim("another").should_deliver is True


@pytest.mark.parametrize("key", ["", "   ", "a\x00b", "x" * 1025])
def test_invalid_keys_are_rejected(tmp_path, key):
    with (
        DedupeStore(tmp_path / "state.db") as store,
        pytest.raises(DedupeError, match="dedupe key"),
    ):
        store.claim(key)


def test_store_must_be_open(tmp_path):
    store = DedupeStore(tmp_path / "state.db")
    with pytest.raises(DedupeError, match="not open"):
        store.claim("key")


def test_open_is_idempotent_and_directory_path_is_rejected(tmp_path):
    store = DedupeStore(tmp_path / "state.db")
    assert store.open() is store
    assert store.open() is store
    store.close()
    store.close()

    with pytest.raises(DedupeError, match="regular file"):
        DedupeStore(tmp_path).open()


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions are required")
def test_state_database_rejects_symlink_and_broad_permissions(tmp_path):
    target = tmp_path / "target.db"
    target.touch(mode=0o600)
    linked = tmp_path / "linked.db"
    linked.symlink_to(target)

    with pytest.raises(DedupeError, match="symlink"):
        DedupeStore(linked).open()

    broad = tmp_path / "broad.db"
    broad.touch(mode=0o600)
    broad.chmod(0o644)
    with pytest.raises(DedupeError, match="chmod 600"):
        DedupeStore(broad).open()


def test_state_database_parent_creation_error_is_sanitized(tmp_path):
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")

    with pytest.raises(DedupeError, match="prepare"):
        DedupeStore(parent_file / "state.db").open()


@pytest.mark.skipif(dedupe_module.fcntl is None, reason="requires POSIX flock")
def test_second_process_store_is_rejected_until_first_closes(tmp_path):
    path = tmp_path / "state.sqlite3"
    first = DedupeStore(path).open()
    second = DedupeStore(path)
    try:
        with pytest.raises(DedupeError, match="another bridge process"):
            second.open()
    finally:
        first.close()

    try:
        assert second.open() is second
        lock_path = tmp_path / "state.sqlite3.lock"
        assert lock_path.is_file()
        if os.name == "posix":
            assert lock_path.stat().st_mode & 0o777 == 0o600
    finally:
        second.close()


@pytest.mark.parametrize("ids", [[], [0], [-1], [True], ["1"]])
def test_invalid_telegram_ids_are_rejected(tmp_path, ids):
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim("key")
        with pytest.raises(DedupeError, match="positive integers"):
            store.mark_delivered("key", ids)


def test_missing_or_already_delivered_state_cannot_be_marked(tmp_path):
    with DedupeStore(tmp_path / "state.db") as store:
        with pytest.raises(DedupeError, match="not pending"):
            store.mark_delivered("missing", [1])
        store.claim("key")
        store.mark_delivered("key", [1])
        with pytest.raises(DedupeError, match="not pending"):
            store.mark_delivered("key", [2])
        with pytest.raises(DedupeError, match="not pending"):
            store.mark_failed("key", "network")


def test_discard_removes_only_pending_records(tmp_path):
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim("pending")
        store.claim("delivered")
        store.mark_delivered("delivered", [1])

        assert store.discard_pending("pending") is True
        assert store.discard_pending("pending") is False
        assert store.discard_pending("delivered") is False
        assert store.claim("delivered").should_deliver is False


@pytest.mark.parametrize("kind", ["", " ", "not safe!", "x" * 65])
def test_invalid_failure_kind_is_rejected(tmp_path, kind):
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim("key")
        with pytest.raises(DedupeError, match="error kind"):
            store.mark_failed("key", kind)


def test_prune_removes_only_old_delivered_rows(tmp_path):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim("old", now=now - timedelta(days=40))
        store.mark_delivered("old", [1], now=now - timedelta(days=40))
        store.claim("recent", now=now)
        store.mark_delivered("recent", [2], now=now)
        store.claim("pending", now=now - timedelta(days=40))

        assert store.prune_defaults(now=now) == 1
        assert store.claim("old", now=now).should_deliver is True
        assert store.claim("recent", now=now).should_deliver is False
        assert store.claim("pending", now=now).should_deliver is True


def test_prune_caps_delivered_history(tmp_path):
    now = datetime(2026, 8, 22, tzinfo=timezone.utc)
    with DedupeStore(tmp_path / "state.db") as store:
        for index in range(4):
            key = f"key-{index}"
            instant = now + timedelta(seconds=index)
            store.claim(key, now=instant)
            store.mark_delivered(key, [index + 1], now=instant)

        assert store.prune(delivered_before=now - timedelta(days=1), keep_at_most=2) == 2
        assert store.claim("key-0").should_deliver is True
        assert store.claim("key-1").should_deliver is True
        assert store.claim("key-2").should_deliver is False
        assert store.claim("key-3").should_deliver is False


def test_naive_timestamps_and_negative_cap_are_rejected(tmp_path):
    with DedupeStore(tmp_path / "state.db") as store:
        with pytest.raises(DedupeError, match="timezone"):
            store.claim("key", now=datetime(2026, 1, 1))
        with pytest.raises(DedupeError, match="must not be negative"):
            store.prune(delivered_before=datetime.now(timezone.utc), keep_at_most=-1)
