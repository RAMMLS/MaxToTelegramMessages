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
from max_to_telegram.config import ConfigError, Settings, ValidationPurpose
from max_to_telegram.dedupe import DedupeError, DedupeStore
from max_to_telegram.logging_utils import configure_logging
from max_to_telegram.max_client import MaxAuthenticationError, MaxClient, MaxClientError
from max_to_telegram.parser import ChatPolicy, MessageParser
from max_to_telegram.protocol import ProtocolError
from max_to_telegram.telegram import TelegramError, TelegramRetryExhausted, TelegramSender

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
    session_path = settings.max_session_file
    if session_path is not None:
        save_session_file(
            session_path,
            LocalMaxSession(credentials, source.device_id),
        )
        source.credentials_updated = lambda updated: save_session_file(session_path, updated)
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

    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        raise ConfigError("Telegram credentials are required in delivery mode")
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
    if settings.telegram_bot_token is None or settings.telegram_chat_id is None:
        raise ConfigError("Telegram credentials are required for validation")
    sender = TelegramSender(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        max_retries=settings.telegram_max_retries,
    )
    try:
        return asdict(await sender.validate())
    finally:
        await sender.close()


async def discover_telegram_chats(settings: Settings) -> tuple[dict[str, object], ...]:
    if settings.telegram_bot_token is None:
        raise ConfigError("Telegram bot token is required for chat discovery")
    sender = TelegramSender(
        settings.telegram_bot_token,
        "",
        max_retries=settings.telegram_max_retries,
        allow_missing_chat_id=True,
    )
    try:
        return tuple(asdict(chat) for chat in await sender.discover_chats())
    finally:
        await sender.close()


def inspect_state(settings: Settings) -> dict[str, int | bool]:
    store = DedupeStore(settings.state_db).open()
    try:
        return {**asdict(store.stats()), **asdict(store.health())}
    finally:
        store.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="max-to-telegram",
        description="Forward messages from selected MAX chats to Telegram",
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--check-config",
        action="store_true",
        help="validate local configuration without opening network connections",
    )
    modes.add_argument(
        "--check-telegram",
        action="store_true",
        help="call getMe/getChat without sending a message",
    )
    modes.add_argument(
        "--discover-telegram-chats",
        action="store_true",
        help="list content-free chat IDs from recent Telegram bot updates",
    )
    modes.add_argument(
        "--check-state",
        action="store_true",
        help="print content-free durable outbox counters",
    )
    modes.add_argument("--version", action="store_true", help="print version and exit")
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
        purpose: ValidationPurpose = "runtime"
        if args.check_telegram:
            purpose = "telegram"
        elif args.discover_telegram_chats:
            purpose = "telegram_discovery"
        elif args.check_state:
            purpose = "state"
        settings = Settings.from_env(purpose=purpose)
        local_session: LocalMaxSession | None = None
        redacted_secrets = [settings.max_auth_token or "", settings.telegram_bot_token or ""]
        if purpose == "runtime":
            local_session = load_local_session(settings)
            redacted_secrets.append(local_session.credentials.token)
        configure_logging(
            settings.log_level,
            secrets=redacted_secrets,
        )
        if args.check_config:
            if local_session is None:
                raise ConfigError("MAX session is required for configuration validation")
            summary = settings.safe_summary()
            summary["max_viewer_id"] = local_session.credentials.viewer_id
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0
        if args.check_telegram:
            validation_result = asyncio.run(validate_telegram(settings))
            print(json.dumps(validation_result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.discover_telegram_chats:
            discovered_result = asyncio.run(discover_telegram_chats(settings))
            print(json.dumps(discovered_result, ensure_ascii=False, sort_keys=True))
            return 0
        if args.check_state:
            print(json.dumps(inspect_state(settings), sort_keys=True))
            return 0

        logger.info("Starting MAX-to-Telegram bridge: %s", settings.safe_summary())
        if local_session is None:
            raise ConfigError("MAX session is required to start the bridge")
        bundle = build_runtime(settings, local_session)
        return asyncio.run(run_runtime(bundle))
    except KeyboardInterrupt:
        return 130
    except TelegramRetryExhausted as exc:
        print(f"max-to-telegram: {exc}", file=sys.stderr)
        return 75
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
