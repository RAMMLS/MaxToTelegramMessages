from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from max_to_telegram.bridge import Bridge, DiscoveredChat
from max_to_telegram.dedupe import DedupeStore
from max_to_telegram.max_client import OPCODE_NEW_MESSAGE
from max_to_telegram.parser import ChatPolicy, MessageParser, ParsedMessage
from max_to_telegram.protocol import Frame
from max_to_telegram.telegram import TelegramRetryExhausted


class FakeSource:
    def __init__(self, frames: list[Frame]) -> None:
        self.frames = frames
        self.stopped = False

    async def events(self) -> AsyncIterator[Frame]:
        for frame in self.frames:
            yield frame

    def stop(self) -> None:
        self.stopped = True


class FakeSender:
    def __init__(self, error: Exception | None = None) -> None:
        self.messages: list[ParsedMessage] = []
        self.error = error
        self.closed = False

    async def send(self, message: ParsedMessage) -> tuple[int, ...]:
        self.messages.append(message)
        if self.error is not None:
            raise self.error
        return (1000 + len(self.messages),)

    async def close(self) -> None:
        self.closed = True


def frame(
    *,
    chat_id: int = 42,
    message_id: int = 1,
    sender_id: int = 456,
    text: str = "hello",
) -> Frame:
    return Frame(
        cmd=0,
        seq=message_id,
        opcode=OPCODE_NEW_MESSAGE,
        payload={
            "chatId": chat_id,
            "chat": {"title": f"Chat {chat_id}"},
            "sender": {"displayName": f"User {sender_id}"},
            "message": {
                "id": message_id,
                "sender": sender_id,
                "text": text,
                "time": 1_777_000_000_000 + message_id,
            },
        },
    )


def parsed_message(*, chat_id: int = 42, message_id: str = "1") -> ParsedMessage:
    return ParsedMessage(
        chat_id=chat_id,
        message_id=message_id,
        sender_id=456,
        sender_name="User",
        chat_title="Chat",
        text="pending",
        timestamp=datetime(2026, 8, 22, tzinfo=UTC),
        timestamp_raw=1_777_000_000_000,
        update_time=None,
        status=None,
        message_type="USER",
        attachments=(),
        is_outgoing=False,
        is_service=False,
    )


def bridge(
    tmp_path,
    frames: list[Frame],
    *,
    allowed: frozenset[int] = frozenset({42}),
    sender: FakeSender | None = None,
    store: DedupeStore | None = None,
) -> tuple[Bridge, FakeSender, DedupeStore]:
    actual_sender = sender or FakeSender()
    actual_store = store or DedupeStore(tmp_path / "state.db").open()
    return (
        Bridge(
            source=FakeSource(frames),
            parser=MessageParser(viewer_id=123),
            policy=ChatPolicy(allowed),
            discovery_mode=False,
            queue_size=2,
            store=actual_store,
            sender=actual_sender,
        ),
        actual_sender,
        actual_store,
    )


@pytest.mark.asyncio
async def test_forwards_only_selected_incoming_chat(tmp_path) -> None:
    runtime, sender, store = bridge(
        tmp_path,
        [
            frame(chat_id=42, message_id=1),
            frame(chat_id=99, message_id=2),
            frame(chat_id=42, message_id=3, sender_id=123),
        ],
    )
    try:
        await runtime.run()
    finally:
        store.close()

    assert [message.message_id for message in sender.messages] == ["1"]
    assert runtime.stats.delivered_messages == 1
    assert runtime.stats.rejected_messages == 2


@pytest.mark.asyncio
async def test_duplicate_push_is_sent_once(tmp_path) -> None:
    same = frame(message_id=1)
    runtime, sender, store = bridge(tmp_path, [same, same])
    try:
        await runtime.run()
    finally:
        store.close()

    assert len(sender.messages) == 1
    assert runtime.stats.duplicate_messages == 1


@pytest.mark.asyncio
async def test_pre_ack_hook_persists_only_selected_incoming_messages(tmp_path) -> None:
    runtime, _, store = bridge(tmp_path, [])
    try:
        await runtime.persist_before_ack(frame(chat_id=42, message_id=1))
        await runtime.persist_before_ack(frame(chat_id=99, message_id=2))
        await runtime.persist_before_ack(frame(chat_id=42, message_id=3, sender_id=123))

        assert [message.message_id for message in store.pending_messages()] == ["1"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_pre_ack_hook_ignores_malformed_or_irrelevant_frame(tmp_path) -> None:
    runtime, _, store = bridge(tmp_path, [])
    try:
        await runtime.persist_before_ack(
            Frame(cmd=0, seq=1, opcode=OPCODE_NEW_MESSAGE, payload={"chatId": 42})
        )
        await runtime.persist_before_ack(Frame(cmd=0, seq=2, opcode=777))

        assert store.pending_messages() == ()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_recovers_pending_outbox_before_live_events(tmp_path) -> None:
    store = DedupeStore(tmp_path / "state.db").open()
    pending = parsed_message()
    store.enqueue(pending)
    runtime, sender, _ = bridge(tmp_path, [], store=store)
    try:
        await runtime.run()
    finally:
        store.close()

    assert sender.messages == [pending]
    with DedupeStore(tmp_path / "state.db") as reopened:
        assert reopened.pending_messages() == ()


@pytest.mark.asyncio
async def test_removed_allowlist_chat_discards_pending_item(tmp_path) -> None:
    store = DedupeStore(tmp_path / "state.db").open()
    pending = parsed_message(chat_id=77)
    store.enqueue(pending)
    runtime, sender, _ = bridge(tmp_path, [], store=store, allowed=frozenset({42}))
    try:
        await runtime.run()
        assert sender.messages == []
        assert store.pending_messages() == ()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_telegram_failure_stays_pending_and_propagates(tmp_path) -> None:
    sender = FakeSender(TelegramRetryExhausted("offline"))
    runtime, _, store = bridge(tmp_path, [frame()], sender=sender)
    try:
        with pytest.raises(TelegramRetryExhausted, match="offline"):
            await runtime.run()
        assert len(store.pending_messages()) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_malformed_message_is_ignored(tmp_path) -> None:
    bad = Frame(cmd=0, seq=1, opcode=OPCODE_NEW_MESSAGE, payload={"chatId": 42})
    runtime, sender, store = bridge(tmp_path, [bad])
    try:
        await runtime.run()
    finally:
        store.close()

    assert sender.messages == []
    assert runtime.stats.parse_errors == 1


@pytest.mark.asyncio
async def test_ignores_non_message_frame(tmp_path) -> None:
    runtime, sender, store = bridge(tmp_path, [Frame(cmd=0, seq=1, opcode=777)])
    try:
        await runtime.run()
    finally:
        store.close()

    assert sender.messages == []
    assert runtime.stats.frames_seen == 1


@pytest.mark.asyncio
async def test_discovery_reports_each_chat_once_without_sender_or_store() -> None:
    discovered: list[DiscoveredChat] = []
    runtime = Bridge(
        source=FakeSource([frame(chat_id=42), frame(chat_id=42, message_id=2), frame(chat_id=99)]),
        parser=MessageParser(viewer_id=123),
        policy=ChatPolicy(frozenset()),
        discovery_mode=True,
        queue_size=1,
        discovery_sink=discovered.append,
    )

    await runtime.run()

    assert [item.chat_id for item in discovered] == [42, 99]
    assert runtime.stats.parsed_messages == 3


@pytest.mark.asyncio
async def test_close_and_stop_are_forwarded(tmp_path) -> None:
    source = FakeSource([])
    sender = FakeSender()
    store = DedupeStore(tmp_path / "state.db").open()
    runtime = Bridge(
        source=source,
        parser=MessageParser(viewer_id=123),
        policy=ChatPolicy(frozenset({42})),
        discovery_mode=False,
        queue_size=1,
        store=store,
        sender=sender,
    )

    runtime.stop()
    await runtime.close()
    store.close()

    assert source.stopped is True
    assert sender.closed is True


def test_rejects_invalid_runtime_configuration(tmp_path) -> None:
    source = FakeSource([])
    parser = MessageParser(viewer_id=1)
    policy = ChatPolicy(frozenset())
    with pytest.raises(ValueError, match="queue"):
        Bridge(
            source=source,
            parser=parser,
            policy=policy,
            discovery_mode=True,
            queue_size=0,
        )
    with pytest.raises(ValueError, match="requires"):
        Bridge(
            source=source,
            parser=parser,
            policy=policy,
            discovery_mode=False,
            queue_size=1,
        )
