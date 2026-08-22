"""Persistent daily Telegram heartbeat and content-free bridge counters."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from max_to_telegram.dedupe import DailyReportSnapshot, DedupeError, DedupeStore, OutboxStats
from max_to_telegram.telegram import TELEGRAM_TEXT_LIMIT, TelegramError

logger = logging.getLogger(__name__)

_REPORT_INTERVAL = timedelta(days=1)
_RETRY_DELAY_SECONDS = 15 * 60.0
_INITIAL_DELAY_SECONDS = 10.0
_MOSCOW = ZoneInfo("Europe/Moscow")


class ReportSender(Protocol):
    async def send_text(self, text: str) -> int: ...


@dataclass(slots=True)
class DailyReporter:
    """Send one report immediately when first enabled, then every 24 hours."""

    store: DedupeStore
    sender: ReportSender
    max_connected: Callable[[], bool]
    process_started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    now: Callable[[], datetime] = field(
        default=lambda: datetime.now(timezone.utc),
        repr=False,
    )
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    interval: timedelta = field(default=_REPORT_INTERVAL, repr=False)
    retry_delay_seconds: float = field(default=_RETRY_DELAY_SECONDS, repr=False)
    initial_delay_seconds: float = field(default=_INITIAL_DELAY_SECONDS, repr=False)

    def __post_init__(self) -> None:
        self.process_started_at = _as_utc(self.process_started_at)
        if self.interval.total_seconds() <= 0:
            raise ValueError("daily report interval must be positive")
        if self.retry_delay_seconds <= 0:
            raise ValueError("daily report retry delay must be positive")
        if self.initial_delay_seconds < 0:
            raise ValueError("daily report initial delay must not be negative")

    async def run(self) -> None:
        """Run until cancelled, keeping report failures isolated from forwarding."""

        initial_delay_done = False
        while True:
            try:
                snapshot = self.store.daily_report_snapshot(now=self.now())
                if snapshot.last_report_at is None and not initial_delay_done:
                    initial_delay_done = True
                    if self.initial_delay_seconds:
                        await self.sleep(self.initial_delay_seconds)
                    snapshot = self.store.daily_report_snapshot(now=self.now())
                delay = self.seconds_until_due(snapshot)
                if delay > 0:
                    await self.sleep(delay)
                await self.send_if_due()
            except asyncio.CancelledError:
                raise
            except (DedupeError, TelegramError) as exc:
                logger.warning(
                    "Daily health report failed; retrying in %.0fs (%s)",
                    self.retry_delay_seconds,
                    type(exc).__name__,
                )
                await self.sleep(self.retry_delay_seconds)

    def seconds_until_due(self, snapshot: DailyReportSnapshot) -> float:
        if snapshot.last_report_at is None:
            return 0.0
        due_at = snapshot.last_report_at + self.interval
        return max(0.0, (due_at - _as_utc(snapshot.generated_at)).total_seconds())

    async def send_if_due(self) -> bool:
        """Send and acknowledge one due report; return whether Telegram was called."""

        snapshot = self.store.daily_report_snapshot(now=self.now())
        if self.seconds_until_due(snapshot) > 0:
            return False
        outbox = self.store.stats()
        text = format_daily_report(
            snapshot,
            outbox,
            max_connected=self.max_connected(),
            process_started_at=self.process_started_at,
        )
        await self.sender.send_text(text)
        self.store.mark_daily_report_sent(snapshot, sent_at=self.now())
        logger.info("Daily health report delivered")
        return True


def format_daily_report(
    snapshot: DailyReportSnapshot,
    outbox: OutboxStats,
    *,
    max_connected: bool,
    process_started_at: datetime,
) -> str:
    """Render a bounded HTML report without message content or private IDs."""

    generated_at = _as_utc(snapshot.generated_at)
    started_at = _as_utc(snapshot.period_started_at)
    process_started = _as_utc(process_started_at)
    connection = "подключён" if max_connected else "переподключается"
    status_icon = "🟢" if max_connected and outbox.pending_failed == 0 else "🟡"
    report = (
        f"{status_icon} <b>MAX → Telegram: мост работает</b>\n\n"
        f"<b>MAX:</b> {connection}\n"
        f"<b>Период:</b> {_format_time(started_at)} — {_format_time(generated_at)}\n"
        f"<b>Текущий процесс:</b> {_format_duration(generated_at - process_started)}\n\n"
        f"Получено сообщений MAX: <b>{snapshot.received_messages}</b>\n"
        f"Принято из выбранных чатов: <b>{snapshot.selected_messages}</b>\n"
        f"Переслано в Telegram: <b>{snapshot.delivered_messages}</b>\n"
        f"Отфильтровано: <b>{snapshot.rejected_messages}</b>\n"
        f"Дубликаты: <b>{snapshot.duplicate_messages}</b>\n"
        f"Ошибки разбора/доставки: <b>{snapshot.parse_errors} / "
        f"{snapshot.delivery_errors}</b>\n\n"
        f"Очередь: ожидают <b>{outbox.pending}</b>, ошибочных <b>{outbox.pending_failed}</b>"
    )
    if len(report) > TELEGRAM_TEXT_LIMIT:
        raise DedupeError("formatted daily report exceeds Telegram text limit")
    return report


def _format_time(value: datetime) -> str:
    return value.astimezone(_MOSCOW).strftime("%d.%m.%Y %H:%M MSK")


def _format_duration(value: timedelta) -> str:
    total_seconds = max(0, int(value.total_seconds()))
    days, remainder = divmod(total_seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes = remainder // 60
    if days:
        return f"{days} д {hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("report timestamps must include a timezone")
    return value.astimezone(timezone.utc)
