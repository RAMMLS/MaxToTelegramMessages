from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from max_to_telegram.dedupe import DedupeStore
from max_to_telegram.reporting import DailyReporter, format_daily_report
from max_to_telegram.telegram import TelegramRetryExhausted


class FakeReportSender:
    def __init__(self, on_send=None) -> None:
        self.messages: list[str] = []
        self.on_send = on_send

    async def send_text(self, text: str) -> int:
        self.messages.append(text)
        if self.on_send is not None:
            self.on_send()
        return 700 + len(self.messages)


@pytest.mark.asyncio
async def test_first_report_is_immediate_and_persists_success(tmp_path) -> None:
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    with DedupeStore(tmp_path / "state.db") as store:
        store.record_report_metric("received_messages", amount=4)
        store.record_report_metric("selected_messages", amount=2)
        store.record_report_metric("delivered_messages", amount=2)
        sender = FakeReportSender()
        reporter = DailyReporter(
            store=store,
            sender=sender,
            max_connected=lambda: True,
            process_started_at=now - timedelta(hours=3),
            now=lambda: now,
        )

        assert await reporter.send_if_due() is True
        state = store.daily_report_snapshot(now=now)

    assert len(sender.messages) == 1
    assert "мост работает" in sender.messages[0]
    assert "MAX:</b> подключён" in sender.messages[0]
    assert "Получено сообщений MAX: <b>4</b>" in sender.messages[0]
    assert "Принято из выбранных чатов: <b>2</b>" in sender.messages[0]
    assert state.last_report_at == now
    assert state.received_messages == 0


@pytest.mark.asyncio
async def test_report_is_not_resent_before_twenty_four_hours(tmp_path) -> None:
    first = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    current = first + timedelta(hours=23, minutes=59)
    with DedupeStore(tmp_path / "state.db") as store:
        initial = store.daily_report_snapshot(now=first)
        store.mark_daily_report_sent(initial, sent_at=first)
        sender = FakeReportSender()
        reporter = DailyReporter(
            store=store,
            sender=sender,
            max_connected=lambda: True,
            now=lambda: current,
        )

        assert await reporter.send_if_due() is False
        assert reporter.seconds_until_due(store.daily_report_snapshot(now=current)) == 60.0

    assert sender.messages == []


@pytest.mark.asyncio
async def test_events_during_send_remain_for_next_report(tmp_path) -> None:
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    with DedupeStore(tmp_path / "state.db") as store:
        store.record_report_metric("received_messages", amount=2)
        sender = FakeReportSender(on_send=lambda: store.record_report_metric("received_messages"))
        reporter = DailyReporter(
            store=store,
            sender=sender,
            max_connected=lambda: False,
            now=lambda: now,
        )

        assert await reporter.send_if_due() is True
        remaining = store.daily_report_snapshot(now=now)

    assert remaining.received_messages == 1
    assert "MAX:</b> переподключается" in sender.messages[0]


def test_report_format_contains_only_content_free_counters(tmp_path) -> None:
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    with DedupeStore(tmp_path / "state.db") as store:
        store.record_report_metric("rejected_messages", amount=5)
        report = format_daily_report(
            store.daily_report_snapshot(now=now),
            store.stats(),
            max_connected=True,
            process_started_at=now,
        )

    assert "Отфильтровано: <b>5</b>" in report
    assert "chat_id" not in report
    assert "message_id" not in report


@pytest.mark.asyncio
async def test_report_worker_retries_without_stopping_bridge_state(tmp_path) -> None:
    now = datetime(2026, 8, 22, 9, 0, tzinfo=timezone.utc)
    attempts = 0
    sleeps: list[float] = []

    class FlakySender:
        async def send_text(self, _text: str) -> int:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise TelegramRetryExhausted("offline")
            return 99

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    with DedupeStore(tmp_path / "state.db") as store:
        reporter = DailyReporter(
            store=store,
            sender=FlakySender(),
            max_connected=lambda: True,
            now=lambda: now,
            sleep=fake_sleep,
        )

        with pytest.raises(asyncio.CancelledError):
            await reporter.run()

        assert store.daily_report_snapshot(now=now).last_report_at == now

    assert attempts == 2
    assert sleeps == [10.0, 900.0, 86_400.0]
