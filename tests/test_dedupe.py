from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest

from max_to_telegram.dedupe import DedupeError, DedupeStore


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


@pytest.mark.parametrize("kind", ["", " ", "not safe!", "x" * 65])
def test_invalid_failure_kind_is_rejected(tmp_path, kind):
    with DedupeStore(tmp_path / "state.db") as store:
        store.claim("key")
        with pytest.raises(DedupeError, match="error kind"):
            store.mark_failed("key", kind)


def test_prune_removes_only_old_delivered_rows(tmp_path):
    now = datetime(2026, 8, 22, tzinfo=UTC)
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
    now = datetime(2026, 8, 22, tzinfo=UTC)
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
            store.prune(delivered_before=datetime.now(UTC), keep_at_most=-1)
