"""Command-line entry point for the MAX-to-Telegram bridge."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError, version

from max_to_telegram.auth import (
    AuthError,
    LocalMaxSession,
    load_local_session,
    save_session_file,
)
from max_to_telegram.bridge import Bridge
from max_to_telegram.config import ConfigError, Settings
from max_to_telegram.dedupe import DedupeError, DedupeStore
from max_to_telegram.logging_utils import configure_logging
from max_to_telegram.max_client import MaxAuthenticationError, MaxClient, MaxClientError
from max_to_telegram.parser import ChatPolicy, MessageParser
from max_to_telegram.protocol import ProtocolError
from max_to_telegram.telegram import TelegramError, TelegramSender

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeBundle:
    bridge: Bridge
    store: DedupeStore | None


def build_runtime(
    settings: Settings,
    local_session: LocalMaxSession | None = None,
) -> RuntimeBundle:
    loaded = local_session or load_local_session(settings)
    credentials = loaded.credentials
    source = MaxClient(settings, credentials, device_id=loaded.device_id)
    if settings.max_session_file is not None:
        save_session_file(
            settings.max_session_file,
            LocalMaxSession(credentials, source.device_id),
        )
        source.credentials_updated = lambda updated: save_session_file(
            settings.max_session_file, updated
        )
    parser = MessageParser(viewer_id=credentials.viewer_id)
    policy = ChatPolicy(settings.max_chat_ids)

    if settings.discovery_mode:
        return RuntimeBundle(
            bridge=Bridge(
                source=source,
                parser=parser,
                policy=policy,
                discovery_mode=True,
                queue_size=settings.queue_size,
            ),
            store=None,
        )

    assert settings.telegram_bot_token is not None
    assert settings.telegram_chat_id is not None
    store = DedupeStore(settings.state_db).open()
    try:
        sender = TelegramSender(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            max_retries=settings.telegram_max_retries,
        )
        bridge = Bridge(
            source=source,
            parser=parser,
            policy=policy,
            discovery_mode=False,
            queue_size=settings.queue_size,
            store=store,
            sender=sender,
        )
        source.set_before_message_ack(bridge.persist_before_ack)
    except BaseException:
        store.close()
        raise
    return RuntimeBundle(bridge=bridge, store=store)


async def run_runtime(bundle: RuntimeBundle, *, install_signal_handlers: bool = True) -> int:
    shutdown_requested = False
    runtime_task = asyncio.create_task(bundle.bridge.run(), name="max-to-telegram")
    loop = asyncio.get_running_loop()
    installed_signals: list[signal.Signals] = []

    def request_shutdown() -> None:
        nonlocal shutdown_requested
        if shutdown_requested:
            return
        shutdown_requested = True
        logger.info("Shutdown requested")
        bundle.bridge.stop()
        runtime_task.cancel()

    if install_signal_handlers:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, request_shutdown)
            except (NotImplementedError, RuntimeError):
                break
            installed_signals.append(signum)

    try:
        await runtime_task
        return 0
    except asyncio.CancelledError:
        if shutdown_requested:
            return 0
        raise
    finally:
        for signum in installed_signals:
            loop.remove_signal_handler(signum)
        bundle.bridge.stop()
        try:
            await bundle.bridge.close()
        finally:
            logger.info("Bridge counters: %s", asdict(bundle.bridge.stats))
            if bundle.store is not None:
                try:
                    logger.info("Outbox counters: %s", asdict(bundle.store.stats()))
                    bundle.store.prune_defaults()
                finally:
                    bundle.store.close()


async def validate_telegram(settings: Settings) -> dict[str, str]:
    assert settings.telegram_bot_token is not None
    assert settings.telegram_chat_id is not None
    sender = TelegramSender(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        max_retries=settings.telegram_max_retries,
    )
    try:
        return asdict(await sender.validate())
    finally:
        await sender.close()


def inspect_state(settings: Settings) -> dict[str, int]:
    store = DedupeStore(settings.state_db).open()
    try:
        return asdict(store.stats())
    finally:
        store.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="max-to-telegram",
        description="Forward messages from selected MAX chats to Telegram",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate local configuration without opening network connections",
    )
    parser.add_argument(
        "--check-telegram",
        action="store_true",
        help="call getMe/getChat without sending a message",
    )
    parser.add_argument(
        "--check-state",
        action="store_true",
        help="print content-free durable outbox counters",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    return parser


def _version() -> str:
    try:
        return version("max-to-telegram")
    except PackageNotFoundError:
        return "0.1.0"


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.version:
        print(_version())
        return 0

    try:
        settings = Settings.from_env()
        local_session = load_local_session(settings)
        credentials = local_session.credentials
        configure_logging(
            settings.log_level,
            secrets=(credentials.token, settings.telegram_bot_token or ""),
        )
        if args.check_config:
            summary = settings.safe_summary()
            summary["max_viewer_id"] = credentials.viewer_id
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0
        if args.check_telegram:
            result = asyncio.run(validate_telegram(settings))
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.check_state:
            print(json.dumps(inspect_state(settings), sort_keys=True))
            return 0

        logger.info("Starting MAX-to-Telegram bridge: %s", settings.safe_summary())
        bundle = build_runtime(settings, local_session)
        return asyncio.run(run_runtime(bundle))
    except KeyboardInterrupt:
        return 130
    except (
        AuthError,
        ConfigError,
        DedupeError,
        MaxAuthenticationError,
        MaxClientError,
        ProtocolError,
        TelegramError,
    ) as exc:
        print(f"max-to-telegram: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
