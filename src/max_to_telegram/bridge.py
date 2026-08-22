"""Bridge orchestration: parse, filter, persist, and deliver MAX events."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from max_to_telegram.dedupe import DedupeError, DedupeStore
from max_to_telegram.max_client import OPCODE_NEW_MESSAGE
from max_to_telegram.parser import (
    ChatPolicy,
    MessageParseError,
    MessageParser,
    ParsedMessage,
    PolicyDecision,
)
from max_to_telegram.protocol import Frame
from max_to_telegram.telegram import TelegramError, TelegramSender

logger = logging.getLogger(__name__)


class MaxEventSource(Protocol):
    def events(self) -> AsyncIterator[Frame]: ...

    def stop(self) -> None: ...


class MessageSender(Protocol):
    async def send(self, message: ParsedMessage) -> tuple[int, ...]: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DiscoveredChat:
    chat_id: int
    title: str


@dataclass(slots=True)
class BridgeStats:
    frames_seen: int = 0
    parsed_messages: int = 0
    enqueued_messages: int = 0
    delivered_messages: int = 0
    duplicate_messages: int = 0
    rejected_messages: int = 0
    parse_errors: int = 0


_STOP = object()


class Bridge:
    """Coordinate the MAX listener and Telegram sender with a durable outbox."""

    def __init__(
        self,
        *,
        source: MaxEventSource,
        parser: MessageParser,
        policy: ChatPolicy,
        discovery_mode: bool,
        queue_size: int,
        store: DedupeStore | None = None,
        sender: MessageSender | TelegramSender | None = None,
        discovery_sink: Callable[[DiscoveredChat], None] | None = None,
        delivery_preflight: Callable[[], Awaitable[object]] | None = None,
    ) -> None:
        if queue_size < 1:
            raise ValueError("queue size must be positive")
        if not discovery_mode and (store is None or sender is None):
            raise ValueError("delivery mode requires an outbox and Telegram sender")
        self.source = source
        self.parser = parser
        self.policy = policy
        self.discovery_mode = discovery_mode
        self.store = store
        self.sender = sender
        self.discovery_sink = discovery_sink
        self.delivery_preflight = delivery_preflight
        self.stats = BridgeStats()
        self._queue: asyncio.Queue[ParsedMessage | object] = asyncio.Queue(maxsize=queue_size)
        self._queued_keys: set[str] = set()
        self._discovered_chat_ids: set[int] = set()

    async def run(self) -> None:
        """Run until the MAX source stops or a fatal delivery error occurs."""

        if self.discovery_mode:
            await self._produce()
            return

        if self.delivery_preflight is not None:
            await self.delivery_preflight()
            logger.info("Telegram bot and destination preflight passed")

        producer = asyncio.create_task(self._produce(), name="max-event-producer")
        worker = asyncio.create_task(self._deliver(), name="telegram-delivery-worker")
        tasks = (producer, worker)
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def stop(self) -> None:
        self.source.stop()

    async def close(self) -> None:
        if self.sender is not None:
            await self.sender.close()

    async def persist_before_ack(self, frame: Frame) -> None:
        """Persist selected messages before MAX receives its protocol ACK.

        Parsing problems are left for the normal consumer to report and skip.
        Storage failures deliberately propagate so the client does not ACK a
        selected message that was not made durable.
        """

        if self.discovery_mode or frame.opcode != OPCODE_NEW_MESSAGE:
            return
        store = self._delivery_store()
        try:
            message = self.parser.parse(frame)
        except MessageParseError as exc:
            if self._frame_targets_allowed_chat(frame):
                raise DedupeError(
                    "selected MAX message could not be normalized before protocol ACK"
                ) from exc
            return
        if self.policy.decide(message) is PolicyDecision.FORWARD:
            store.enqueue(message)

    def _frame_targets_allowed_chat(self, frame: Frame) -> bool:
        if not isinstance(frame.payload, dict):
            return False
        raw_chat_id = frame.payload.get("chatId")
        if raw_chat_id is None or isinstance(raw_chat_id, bool):
            return False
        try:
            chat_id = int(raw_chat_id)
        except (TypeError, ValueError, OverflowError):
            return False
        return chat_id in self.policy.allowed_chat_ids

    async def _produce(self) -> None:
        try:
            if not self.discovery_mode:
                await self._recover_pending()
            async for frame in self.source.events():
                self.stats.frames_seen += 1
                if frame.opcode != OPCODE_NEW_MESSAGE:
                    continue
                try:
                    message = self.parser.parse(frame)
                except MessageParseError as exc:
                    self.stats.parse_errors += 1
                    logger.warning("Ignoring malformed MAX message push: %s", exc)
                    continue
                self.stats.parsed_messages += 1
                if self.discovery_mode:
                    self._record_discovery(message)
                    continue
                await self._apply_policy_and_enqueue(message)
        finally:
            if not self.discovery_mode:
                await self._queue.put(_STOP)

    async def _recover_pending(self) -> None:
        store = self._delivery_store()
        health = store.health()
        if not health.integrity_ok:
            raise DedupeError("delivery state database failed its integrity check")
        if health.unrecoverable_pending:
            raise DedupeError(
                "pending outbox contains records without recoverable message data; "
                "inspect the state database before restarting"
            )
        pending_count = store.stats().pending
        if pending_count > 100_000:
            raise DedupeError("pending outbox exceeds the 100000-message recovery limit")
        if pending_count:
            logger.info("Recovering %d pending Telegram deliveries", pending_count)
        fetch_size = min(self._queue.maxsize, 100)
        for message in store.iter_pending_messages(limit=100_000, fetch_size=fetch_size):
            decision = self.policy.decide(message)
            if decision is not PolicyDecision.FORWARD:
                store.discard_pending(message.dedupe_key)
                self.stats.rejected_messages += 1
                logger.info(
                    "Discarded pending message because current policy rejects it: reason=%s",
                    decision.value,
                )
                continue
            await self._queue_message(message)

    async def _apply_policy_and_enqueue(self, message: ParsedMessage) -> None:
        store = self._delivery_store()
        decision = self.policy.decide(message)
        if decision is not PolicyDecision.FORWARD:
            self.stats.rejected_messages += 1
            logger.debug(
                "MAX message rejected by policy: reason=%s",
                decision.value,
            )
            return

        claim = store.enqueue(message)
        if not claim.should_deliver or message.dedupe_key in self._queued_keys:
            self.stats.duplicate_messages += 1
            return
        await self._queue_message(message)

    async def _queue_message(self, message: ParsedMessage) -> None:
        self._queued_keys.add(message.dedupe_key)
        try:
            await self._queue.put(message)
        except BaseException:
            self._queued_keys.discard(message.dedupe_key)
            raise
        self.stats.enqueued_messages += 1

    async def _deliver(self) -> None:
        store = self._delivery_store()
        sender = self._delivery_sender()
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                if not isinstance(item, ParsedMessage):
                    raise RuntimeError("bridge delivery queue contains an invalid item")
                try:
                    telegram_ids = await sender.send(item)
                    store.mark_delivered(item.dedupe_key, telegram_ids)
                except TelegramError as exc:
                    store.mark_failed(item.dedupe_key, type(exc).__name__)
                    raise
                self.stats.delivered_messages += 1
                logger.info(
                    "Forwarded one selected MAX message: chunks=%s",
                    len(telegram_ids),
                )
            finally:
                if isinstance(item, ParsedMessage):
                    self._queued_keys.discard(item.dedupe_key)
                self._queue.task_done()

    def _delivery_store(self) -> DedupeStore:
        if self.store is None:
            raise RuntimeError("bridge delivery outbox is not configured")
        return self.store

    def _delivery_sender(self) -> MessageSender | TelegramSender:
        if self.sender is None:
            raise RuntimeError("bridge delivery sender is not configured")
        return self.sender

    def _record_discovery(self, message: ParsedMessage) -> None:
        if message.chat_id in self._discovered_chat_ids:
            return
        self._discovered_chat_ids.add(message.chat_id)
        discovered = DiscoveredChat(
            chat_id=message.chat_id,
            title=message.chat_title,
        )
        logger.info(
            "Discovered MAX chat: chat_id=%s title=%r",
            discovered.chat_id,
            discovered.title,
        )
        if self.discovery_sink is not None:
            self.discovery_sink(discovered)
