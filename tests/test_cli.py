from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from max_to_telegram.bridge import BridgeStats
from max_to_telegram.cli import (
    RuntimeBundle,
    build_runtime,
    inspect_state,
    main,
    run_runtime,
    validate_telegram,
)
from max_to_telegram.config import ConfigError, Settings
from max_to_telegram.dedupe import OutboxStats

BOT_TOKEN = "1234567890:AA_TEST_token_1234567890abcdefghijk"


class FakeBridge:
    def __init__(
        self,
        error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.error = error
        self.close_error = close_error
        self.ran = False
        self.stopped = False
        self.closed = False
        self.stats = BridgeStats()

    async def run(self) -> None:
        self.ran = True
        if self.error is not None:
            raise self.error

    def stop(self) -> None:
        self.stopped = True

    async def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


@dataclass
class FakeStore:
    pruned: bool = False
    closed: bool = False

    def prune_defaults(self) -> int:
        self.pruned = True
        return 0

    def close(self) -> None:
        self.closed = True

    def stats(self) -> OutboxStats:
        return OutboxStats(pending=0, pending_failed=0, delivered=0, total=0)


@pytest.mark.asyncio
async def test_runtime_closes_resources_after_clean_exit() -> None:
    bridge = FakeBridge()
    store = FakeStore()
    bundle = RuntimeBundle(bridge=bridge, store=store)  # type: ignore[arg-type]

    assert await run_runtime(bundle, install_signal_handlers=False) == 0
    assert bridge.ran and bridge.stopped and bridge.closed
    assert store.pruned and store.closed


@pytest.mark.asyncio
async def test_runtime_closes_resources_after_failure() -> None:
    bridge = FakeBridge(RuntimeError("boom"))
    store = FakeStore()
    bundle = RuntimeBundle(bridge=bridge, store=store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="boom"):
        await run_runtime(bundle, install_signal_handlers=False)

    assert bridge.stopped and bridge.closed
    assert store.pruned and store.closed


@pytest.mark.asyncio
async def test_store_closes_even_if_sender_close_fails() -> None:
    bridge = FakeBridge(close_error=RuntimeError("close failed"))
    store = FakeStore()
    bundle = RuntimeBundle(bridge=bridge, store=store)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="close failed"):
        await run_runtime(bundle, install_signal_handlers=False)

    assert store.pruned and store.closed


def test_builds_discovery_runtime_without_telegram(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "MAX_VIEWER_ID": "123",
            "MAX_AUTH_TOKEN": "m" * 32,
            "MAX_DISCOVERY_MODE": "true",
        }
    )

    bundle = build_runtime(settings)

    assert bundle.store is None
    assert bundle.bridge.discovery_mode is True


def test_build_runtime_rejects_unvalidated_delivery_settings() -> None:
    settings = Settings(
        max_viewer_id=123,
        max_auth_token="m" * 32,
        max_chat_ids=frozenset({42}),
    )

    with pytest.raises(ConfigError, match="Telegram credentials"):
        build_runtime(settings)


@pytest.mark.asyncio
async def test_validate_telegram_rejects_missing_credentials() -> None:
    settings = Settings(
        max_viewer_id=123,
        max_auth_token="m" * 32,
        max_chat_ids=frozenset({42}),
    )

    with pytest.raises(ConfigError, match="Telegram credentials"):
        await validate_telegram(settings)


def test_file_session_gets_stable_device_and_refresh_callback(tmp_path) -> None:
    session_file = tmp_path / ".max-session.json"
    session_file.write_text(
        json.dumps({"viewerId": 123, "token": "m" * 32}),
        encoding="utf-8",
    )
    session_file.chmod(0o600)
    settings = Settings.from_env(
        {
            "MAX_SESSION_FILE": str(session_file),
            "MAX_DISCOVERY_MODE": "true",
        }
    )

    bundle = build_runtime(settings)
    saved = json.loads(session_file.read_text(encoding="utf-8"))

    assert saved["viewerId"] == 123
    assert saved["token"] == "m" * 32
    assert saved["deviceId"] == bundle.bridge.source.device_id
    assert bundle.bridge.source.credentials_updated is not None


@pytest.mark.asyncio
async def test_builds_delivery_runtime_with_exact_allowlist(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "MAX_VIEWER_ID": "123",
            "MAX_AUTH_TOKEN": "m" * 32,
            "MAX_CHAT_IDS": "42,-77",
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "TELEGRAM_CHAT_ID": "99",
            "BRIDGE_STATE_DB": str(tmp_path / "state.db"),
        }
    )

    bundle = build_runtime(settings)
    try:
        assert bundle.bridge.policy.allowed_chat_ids == frozenset({42, -77})
        assert bundle.store is not None
        assert bundle.bridge.source.before_message_ack == bundle.bridge.persist_before_ack
    finally:
        await bundle.bridge.close()
        assert bundle.store is not None
        bundle.store.close()


def test_check_config_prints_only_safe_summary(monkeypatch, capsys) -> None:
    max_token = "m" * 32
    telegram_token = BOT_TOKEN
    env = {
        "MAX_VIEWER_ID": "123",
        "MAX_AUTH_TOKEN": max_token,
        "MAX_CHAT_IDS": "42",
        "TELEGRAM_BOT_TOKEN": telegram_token,
        "TELEGRAM_CHAT_ID": "99",
    }
    for key in list(env):
        monkeypatch.setenv(key, env[key])
    monkeypatch.chdir("/")

    assert main(["--check-config"]) == 0
    captured = capsys.readouterr()
    assert '"selected_chat_count": 1' in captured.out
    assert '"max_viewer_id": 123' in captured.out
    assert max_token not in captured.out + captured.err
    assert telegram_token not in captured.out + captured.err


def test_invalid_config_returns_two_without_traceback(monkeypatch, capsys) -> None:
    for key in (
        "MAX_VIEWER_ID",
        "MAX_AUTH_TOKEN",
        "MAX_SESSION_FILE",
        "MAX_CHAT_IDS",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir("/")

    assert main(["--check-config"]) == 2
    captured = capsys.readouterr()
    assert "MAX credentials" in captured.err or "MAX_VIEWER_ID" in captured.err
    assert "Traceback" not in captured.err


def test_version_does_not_require_configuration(capsys) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "0.1.0"


def test_check_telegram_prints_safe_metadata(monkeypatch, capsys) -> None:
    telegram_token = BOT_TOKEN
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", telegram_token)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "99")
    monkeypatch.chdir("/")

    async def fake_validate(_settings):
        return {
            "bot_username": "bridge_bot",
            "chat_type": "private",
            "chat_title": "Selected",
        }

    monkeypatch.setattr("max_to_telegram.cli.validate_telegram", fake_validate)

    assert main(["--check-telegram"]) == 0
    captured = capsys.readouterr()
    assert '"bot_username": "bridge_bot"' in captured.out
    assert telegram_token not in captured.out + captured.err


def test_discovers_telegram_chats_without_max_configuration(monkeypatch, capsys) -> None:
    telegram_token = BOT_TOKEN
    for key in ("MAX_VIEWER_ID", "MAX_AUTH_TOKEN", "MAX_SESSION_FILE", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", telegram_token)
    monkeypatch.chdir("/")

    async def fake_discovery(_settings):
        return (
            {"chat_id": 42, "chat_type": "private", "chat_title": "Selected"},
            {"chat_id": -100, "chat_type": "group", "chat_title": "Team"},
        )

    monkeypatch.setattr("max_to_telegram.cli.discover_telegram_chats", fake_discovery)

    assert main(["--discover-telegram-chats"]) == 0
    captured = capsys.readouterr()
    assert '"chat_id": 42' in captured.out
    assert '"chat_id": -100' in captured.out
    assert telegram_token not in captured.out + captured.err


def test_inspect_state_returns_only_counters(tmp_path) -> None:
    settings = Settings.from_env(
        {
            "MAX_VIEWER_ID": "123",
            "MAX_AUTH_TOKEN": "m" * 32,
            "MAX_DISCOVERY_MODE": "true",
            "BRIDGE_STATE_DB": str(tmp_path / "state.db"),
        }
    )

    assert inspect_state(settings) == {
        "pending": 0,
        "pending_failed": 0,
        "delivered": 0,
        "total": 0,
    }
