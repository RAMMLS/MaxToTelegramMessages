from __future__ import annotations

import os

import pytest

from max_to_telegram.config import ConfigError, Settings

BOT_TOKEN = "12345:TEST_ONLY_NOT_A_REAL_BOT_TOKEN_12345"


def complete_env(**overrides: str) -> dict[str, str]:
    env = {
        "MAX_VIEWER_ID": "123",
        "MAX_AUTH_TOKEN": "max-secret",
        "MAX_CHAT_IDS": "10,-20",
        "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
        "TELEGRAM_CHAT_ID": "999",
    }
    env.update(overrides)
    return env


def test_loads_complete_environment() -> None:
    settings = Settings.from_env(complete_env())

    assert settings.max_viewer_id == 123
    assert settings.max_chat_ids == frozenset({10, -20})
    assert settings.queue_size == 100
    assert settings.discovery_mode is False


def test_empty_allowlist_fails_closed() -> None:
    with pytest.raises(ConfigError, match="MAX_CHAT_IDS"):
        Settings.from_env(complete_env(MAX_CHAT_IDS=""))


def test_discovery_mode_allows_empty_allowlist_and_no_telegram() -> None:
    settings = Settings.from_env(
        {
            "MAX_VIEWER_ID": "123",
            "MAX_AUTH_TOKEN": "max-secret",
            "MAX_DISCOVERY_MODE": "true",
        }
    )

    assert settings.discovery_mode is True
    assert settings.max_chat_ids == frozenset()
    assert settings.telegram_bot_token is None


def test_telegram_discovery_purpose_requires_only_bot_token() -> None:
    settings = Settings.from_env({"TELEGRAM_BOT_TOKEN": BOT_TOKEN}, purpose="telegram_discovery")

    assert settings.telegram_bot_token == BOT_TOKEN
    assert settings.telegram_chat_id is None
    assert settings.max_auth_token is None


def test_telegram_validation_purpose_requires_destination() -> None:
    with pytest.raises(ConfigError, match="TELEGRAM_CHAT_ID"):
        Settings.from_env({"TELEGRAM_BOT_TOKEN": BOT_TOKEN}, purpose="telegram")


@pytest.mark.parametrize("purpose", ["runtime", "telegram", "telegram_discovery"])
def test_rejects_malformed_telegram_token_for_active_purpose(purpose: str) -> None:
    env = complete_env(TELEGRAM_BOT_TOKEN="1234567890:bad/token")

    with pytest.raises(ConfigError, match="TELEGRAM_BOT_TOKEN is malformed"):
        Settings.from_env(env, purpose=purpose)  # type: ignore[arg-type]


def test_state_inspection_purpose_needs_no_credentials() -> None:
    settings = Settings.from_env({}, purpose="state")

    assert settings.max_auth_token is None
    assert settings.telegram_bot_token is None


def test_public_max_probe_purpose_needs_no_credentials() -> None:
    settings = Settings.from_env({}, purpose="max_public")

    assert settings.max_ws_url == "wss://api.oneme.ru/websocket"
    assert settings.max_auth_token is None
    assert settings.telegram_bot_token is None


@pytest.mark.parametrize("chat_ids", ["1,abc", "0", "1, 2, nope"])
def test_rejects_invalid_chat_ids(chat_ids: str) -> None:
    with pytest.raises(ConfigError, match="MAX_CHAT_IDS"):
        Settings.from_env(complete_env(MAX_CHAT_IDS=chat_ids))


def test_requires_complete_direct_auth_pair() -> None:
    with pytest.raises(ConfigError, match="MAX_VIEWER_ID and MAX_AUTH_TOKEN"):
        Settings.from_env(
            {
                "MAX_VIEWER_ID": "123",
                "MAX_CHAT_IDS": "10",
                "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
                "TELEGRAM_CHAT_ID": "999",
            }
        )


def test_session_file_can_replace_direct_credentials() -> None:
    settings = Settings.from_env(
        {
            "MAX_SESSION_FILE": ".max-session.json",
            "MAX_CHAT_IDS": "10",
            "TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "TELEGRAM_CHAT_ID": "999",
        }
    )

    assert settings.max_session_file is not None
    assert settings.max_viewer_id is None


def test_rejects_ambiguous_direct_and_file_auth_sources() -> None:
    with pytest.raises(ConfigError, match="not both"):
        Settings.from_env(complete_env(MAX_SESSION_FILE=".max-session.json"))


def test_repr_and_safe_summary_do_not_leak_secrets() -> None:
    settings = Settings.from_env(complete_env())

    rendered = repr(settings)
    summary = repr(settings.safe_summary())

    assert "max-secret" not in rendered
    assert BOT_TOKEN not in rendered
    assert "max-secret" not in summary
    assert BOT_TOKEN not in summary
    assert "999" not in summary


@pytest.mark.parametrize("value", ["sometimes", "2", "enabled"])
def test_rejects_ambiguous_boolean(value: str) -> None:
    with pytest.raises(ConfigError, match="MAX_DISCOVERY_MODE"):
        Settings.from_env(complete_env(MAX_DISCOVERY_MODE=value))


def test_rejects_non_tls_websocket_url() -> None:
    with pytest.raises(ConfigError, match="wss://"):
        Settings.from_env(complete_env(MAX_WS_URL="ws://api.oneme.ru/websocket"))


def test_accepts_valid_device_id() -> None:
    settings = Settings.from_env(complete_env(MAX_DEVICE_ID="123e4567-e89b-12d3-a456-426614174000"))

    assert settings.max_device_id == "123e4567-e89b-12d3-a456-426614174000"


def test_rejects_invalid_device_id() -> None:
    with pytest.raises(ConfigError, match="MAX_DEVICE_ID"):
        Settings.from_env(complete_env(MAX_DEVICE_ID="not-a-uuid"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits are required")
def test_rejects_world_readable_dotenv(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("MAX_DISCOVERY_MODE=true\n", encoding="utf-8")
    dotenv.chmod(0o644)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError, match="chmod 600"):
        Settings.from_env()


def test_loads_private_dotenv(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "MAX_VIEWER_ID=123\nMAX_AUTH_TOKEN=" + "z" * 32 + "\nMAX_DISCOVERY_MODE=true\n",
        encoding="utf-8",
    )
    dotenv.chmod(0o600)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MAX_VIEWER_ID", raising=False)
    monkeypatch.delenv("MAX_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MAX_DISCOVERY_MODE", raising=False)

    settings = Settings.from_env()

    assert settings.max_viewer_id == 123
    assert settings.max_auth_token == "z" * 32


def test_rejects_symlinked_dotenv(tmp_path, monkeypatch) -> None:
    target = tmp_path / "secrets"
    target.write_text("MAX_DISCOVERY_MODE=true\n", encoding="utf-8")
    target.chmod(0o600)
    (tmp_path / ".env").symlink_to(target)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError, match="symbolic link"):
        Settings.from_env()


def test_rejects_oversized_dotenv(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text("#" * (65 * 1024), encoding="utf-8")
    dotenv.chmod(0o600)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError, match="unexpectedly large"):
        Settings.from_env()


def test_rejects_non_utf8_dotenv(tmp_path, monkeypatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_bytes(b"\xff\xfe")
    dotenv.chmod(0o600)
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ConfigError, match="UTF-8"):
        Settings.from_env()


@pytest.mark.parametrize(
    ("name", "value", "maximum"),
    [
        ("BRIDGE_QUEUE_SIZE", "10001", "10000"),
        ("MAX_RECONNECT_MAX_SECONDS", "3601", "3600"),
        ("TELEGRAM_MAX_RETRIES", "21", "20"),
    ],
)
def test_rejects_resource_settings_above_safe_bounds(name: str, value: str, maximum: str) -> None:
    with pytest.raises(ConfigError, match=rf"{name} must be at most {maximum}"):
        Settings.from_env(complete_env(**{name: value}))


@pytest.mark.parametrize("chat_id", ["0", "@channel", "+123", "01", str(2**63)])
def test_rejects_non_numeric_or_out_of_range_telegram_destination(chat_id: str) -> None:
    with pytest.raises(ConfigError, match="numeric int64"):
        Settings.from_env(complete_env(TELEGRAM_CHAT_ID=chat_id))
