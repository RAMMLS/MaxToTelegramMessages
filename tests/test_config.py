from __future__ import annotations

import pytest

from max_to_telegram.config import ConfigError, Settings


def complete_env(**overrides: str) -> dict[str, str]:
    env = {
        "MAX_VIEWER_ID": "123",
        "MAX_AUTH_TOKEN": "max-secret",
        "MAX_CHAT_IDS": "10,-20",
        "TELEGRAM_BOT_TOKEN": "telegram-secret",
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
                "TELEGRAM_BOT_TOKEN": "telegram-secret",
                "TELEGRAM_CHAT_ID": "999",
            }
        )


def test_session_file_can_replace_direct_credentials() -> None:
    settings = Settings.from_env(
        {
            "MAX_SESSION_FILE": ".max-session.json",
            "MAX_CHAT_IDS": "10",
            "TELEGRAM_BOT_TOKEN": "telegram-secret",
            "TELEGRAM_CHAT_ID": "999",
        }
    )

    assert settings.max_session_file is not None
    assert settings.max_viewer_id is None


def test_repr_and_safe_summary_do_not_leak_secrets() -> None:
    settings = Settings.from_env(complete_env())

    rendered = repr(settings)
    summary = repr(settings.safe_summary())

    assert "max-secret" not in rendered
    assert "telegram-secret" not in rendered
    assert "max-secret" not in summary
    assert "telegram-secret" not in summary
    assert "999" not in summary


@pytest.mark.parametrize("value", ["sometimes", "2", "enabled"])
def test_rejects_ambiguous_boolean(value: str) -> None:
    with pytest.raises(ConfigError, match="MAX_DISCOVERY_MODE"):
        Settings.from_env(complete_env(MAX_DISCOVERY_MODE=value))


def test_rejects_non_tls_websocket_url() -> None:
    with pytest.raises(ConfigError, match="wss://"):
        Settings.from_env(complete_env(MAX_WS_URL="ws://api.oneme.ru/websocket"))
