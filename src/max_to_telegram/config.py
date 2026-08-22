"""Environment-backed configuration with fail-closed chat filtering."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from types import MappingProxyType
from typing import Literal
from urllib.parse import urlparse

from dotenv import load_dotenv

from max_to_telegram.safe_files import PrivateFileError, read_private_text


class ConfigError(ValueError):
    """Raised when bridge configuration is unsafe or incomplete."""


_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})
_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})
_DEFAULT_MAX_WS_URL = "wss://api.oneme.ru/websocket"
_DEFAULT_MAX_APP_VERSION = "26.8.8"
_DEFAULT_MAX_LOCALE = "ru"
_MAX_DOTENV_BYTES = 64 * 1024
_TELEGRAM_BOT_TOKEN_PATTERN = re.compile(r"[1-9][0-9]{4,19}:[A-Za-z0-9_-]{20,128}\Z")
_TELEGRAM_CHAT_ID_PATTERN = re.compile(r"-?[1-9][0-9]{0,19}\Z")
ValidationPurpose = Literal["runtime", "max_public", "telegram", "telegram_discovery", "state"]


def is_valid_telegram_bot_token(value: str | None) -> bool:
    return value is not None and _TELEGRAM_BOT_TOKEN_PATTERN.fullmatch(value) is not None


def is_valid_telegram_chat_id(value: str | None) -> bool:
    if value is None or _TELEGRAM_CHAT_ID_PATTERN.fullmatch(value) is None:
        return False
    numeric = int(value)
    return -(2**63) <= numeric <= 2**63 - 1


def _parse_bool(name: str, raw: str | None, *, default: bool = False) -> bool:
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be one of: true, false, 1, 0, yes, no, on, off")


def _parse_int(
    name: str,
    raw: str | None,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int | None:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be at most {maximum}")
    return value


def _parse_chat_ids(raw: str | None) -> frozenset[int]:
    if raw is None or not raw.strip():
        return frozenset()
    result: set[int] = set()
    for item in raw.split(","):
        value = item.strip()
        if not value:
            continue
        try:
            chat_id = int(value)
        except ValueError as exc:
            raise ConfigError("MAX_CHAT_IDS contains a non-numeric value") from exc
        if chat_id == 0:
            raise ConfigError("MAX_CHAT_IDS must not contain 0")
        result.add(chat_id)
    return frozenset(result)


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime configuration.

    Secret fields are excluded from ``repr``. The chat allowlist intentionally
    defaults to an empty set, which means "forward nothing".
    """

    max_ws_url: str = _DEFAULT_MAX_WS_URL
    max_app_version: str = _DEFAULT_MAX_APP_VERSION
    max_locale: str = _DEFAULT_MAX_LOCALE
    max_viewer_id: int | None = None
    max_auth_token: str | None = field(default=None, repr=False)
    max_device_id: str | None = None
    max_session_file: Path | None = None
    max_chat_ids: frozenset[int] = frozenset()
    discovery_mode: bool = False
    telegram_bot_token: str | None = field(default=None, repr=False)
    telegram_chat_id: str | None = None
    queue_size: int = 100
    state_db: Path = Path(".max-to-telegram.sqlite3")
    reconnect_max_seconds: int = 10
    telegram_max_retries: int = 5
    log_level: str = "INFO"

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        load_dotenv_file: bool = True,
        purpose: ValidationPurpose = "runtime",
    ) -> Settings:
        """Build settings from a mapping or the process environment."""

        if env is None:
            if load_dotenv_file:
                _load_protected_dotenv()
            source: Mapping[str, str] = os.environ
        else:
            source = MappingProxyType(dict(env))

        session_raw = source.get("MAX_SESSION_FILE", "").strip()
        viewer_id = _parse_int("MAX_VIEWER_ID", source.get("MAX_VIEWER_ID"))
        queue_size = _parse_int(
            "BRIDGE_QUEUE_SIZE",
            source.get("BRIDGE_QUEUE_SIZE"),
            default=100,
            minimum=1,
            maximum=10_000,
        )
        reconnect_max = _parse_int(
            "MAX_RECONNECT_MAX_SECONDS",
            source.get("MAX_RECONNECT_MAX_SECONDS"),
            default=10,
            minimum=1,
            maximum=3_600,
        )
        telegram_retries = _parse_int(
            "TELEGRAM_MAX_RETRIES",
            source.get("TELEGRAM_MAX_RETRIES"),
            default=5,
            minimum=0,
            maximum=20,
        )

        settings = cls(
            max_ws_url=source.get("MAX_WS_URL", _DEFAULT_MAX_WS_URL).strip(),
            max_app_version=source.get("MAX_APP_VERSION", _DEFAULT_MAX_APP_VERSION).strip(),
            max_locale=source.get("MAX_LOCALE", _DEFAULT_MAX_LOCALE).strip(),
            max_viewer_id=viewer_id,
            max_auth_token=source.get("MAX_AUTH_TOKEN", "").strip() or None,
            max_device_id=source.get("MAX_DEVICE_ID", "").strip() or None,
            max_session_file=Path(session_raw).expanduser() if session_raw else None,
            max_chat_ids=_parse_chat_ids(source.get("MAX_CHAT_IDS")),
            discovery_mode=_parse_bool(
                "MAX_DISCOVERY_MODE", source.get("MAX_DISCOVERY_MODE"), default=False
            ),
            telegram_bot_token=source.get("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_chat_id=source.get("TELEGRAM_CHAT_ID", "").strip() or None,
            queue_size=queue_size if queue_size is not None else 100,
            state_db=Path(
                source.get("BRIDGE_STATE_DB", ".max-to-telegram.sqlite3").strip()
            ).expanduser(),
            reconnect_max_seconds=reconnect_max if reconnect_max is not None else 10,
            telegram_max_retries=telegram_retries if telegram_retries is not None else 5,
            log_level=source.get("LOG_LEVEL", "INFO").strip().upper(),
        )
        settings.validate(purpose=purpose)
        return settings

    def validate(self, *, purpose: ValidationPurpose = "runtime") -> None:
        """Reject incomplete settings before any network connection is opened."""

        errors: list[str] = []
        if purpose in {"runtime", "max_public"}:
            parsed_ws_url = urlparse(self.max_ws_url)
            if parsed_ws_url.scheme != "wss" or not parsed_ws_url.netloc:
                errors.append("MAX_WS_URL must be an absolute wss:// URL")
            if not self.max_app_version:
                errors.append("MAX_APP_VERSION must not be empty")
            if not self.max_locale:
                errors.append("MAX_LOCALE must not be empty")

        if purpose == "runtime":
            direct_auth_parts = (self.max_viewer_id is not None, self.max_auth_token is not None)
            if any(direct_auth_parts) and not all(direct_auth_parts):
                errors.append("MAX_VIEWER_ID and MAX_AUTH_TOKEN must be set together")
            if all(direct_auth_parts) and self.max_session_file is not None:
                errors.append(
                    "choose either MAX_VIEWER_ID with MAX_AUTH_TOKEN or MAX_SESSION_FILE, not both"
                )
            if not all(direct_auth_parts) and self.max_session_file is None:
                errors.append("set MAX_VIEWER_ID with MAX_AUTH_TOKEN, or provide MAX_SESSION_FILE")
            if self.max_device_id:
                try:
                    uuid.UUID(self.max_device_id)
                except ValueError:
                    errors.append("MAX_DEVICE_ID must be a UUID")

            if not self.discovery_mode:
                if not self.max_chat_ids:
                    errors.append(
                        "MAX_CHAT_IDS must contain at least one selected chat; "
                        "use MAX_DISCOVERY_MODE=true to discover IDs"
                    )
                if not self.telegram_bot_token:
                    errors.append("TELEGRAM_BOT_TOKEN is required outside discovery mode")
                elif not is_valid_telegram_bot_token(self.telegram_bot_token):
                    errors.append("TELEGRAM_BOT_TOKEN is malformed")
                if not self.telegram_chat_id:
                    errors.append("TELEGRAM_CHAT_ID is required outside discovery mode")
                elif not is_valid_telegram_chat_id(self.telegram_chat_id):
                    errors.append("TELEGRAM_CHAT_ID must be a non-zero numeric int64 chat ID")
        elif purpose == "max_public":
            pass
        elif purpose == "telegram":
            if not self.telegram_bot_token:
                errors.append("TELEGRAM_BOT_TOKEN is required")
            elif not is_valid_telegram_bot_token(self.telegram_bot_token):
                errors.append("TELEGRAM_BOT_TOKEN is malformed")
            if not self.telegram_chat_id:
                errors.append("TELEGRAM_CHAT_ID is required")
            elif not is_valid_telegram_chat_id(self.telegram_chat_id):
                errors.append("TELEGRAM_CHAT_ID must be a non-zero numeric int64 chat ID")
        elif purpose == "telegram_discovery":
            if not self.telegram_bot_token:
                errors.append("TELEGRAM_BOT_TOKEN is required")
            elif not is_valid_telegram_bot_token(self.telegram_bot_token):
                errors.append("TELEGRAM_BOT_TOKEN is malformed")
        elif purpose != "state":
            errors.append("unknown configuration validation purpose")

        if self.log_level not in _LOG_LEVELS:
            errors.append(f"LOG_LEVEL must be one of: {', '.join(sorted(_LOG_LEVELS))}")

        if errors:
            raise ConfigError("invalid configuration:\n- " + "\n- ".join(errors))

    def safe_summary(self) -> dict[str, object]:
        """Return diagnostics that never include credentials or destination IDs."""

        return {
            "max_ws_host": urlparse(self.max_ws_url).hostname,
            "max_app_version": self.max_app_version,
            "max_locale": self.max_locale,
            "auth_source": "file" if self.max_session_file else "environment",
            "device_id_configured": bool(self.max_device_id),
            "selected_chat_count": len(self.max_chat_ids),
            "discovery_mode": self.discovery_mode,
            "telegram_configured": bool(self.telegram_bot_token and self.telegram_chat_id),
            "queue_size": self.queue_size,
            "reconnect_max_seconds": self.reconnect_max_seconds,
            "telegram_max_retries": self.telegram_max_retries,
            "log_level": self.log_level,
        }


def _load_protected_dotenv() -> None:
    path = Path.cwd() / ".env"
    try:
        path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ConfigError("cannot inspect .env file in the current directory") from exc
    try:
        raw = read_private_text(path, maximum_bytes=_MAX_DOTENV_BYTES, label=".env file")
    except PrivateFileError as exc:
        raise ConfigError(str(exc)) from exc
    load_dotenv(stream=StringIO(raw), override=False)
