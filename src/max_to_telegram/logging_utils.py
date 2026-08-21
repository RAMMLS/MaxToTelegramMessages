"""Logging configuration that redacts configured credentials after formatting."""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from typing import TextIO


class RedactingFormatter(logging.Formatter):
    """Redact secrets from messages, arguments, and formatted tracebacks."""

    def __init__(self, *args: object, secrets: Iterable[str] = (), **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._secrets = tuple(
            sorted(
                {secret for secret in secrets if isinstance(secret, str) and len(secret) >= 8},
                key=len,
                reverse=True,
            )
        )

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self._secrets:
            rendered = rendered.replace(secret, "<redacted>")
        return rendered


def configure_logging(
    level: str,
    *,
    secrets: Iterable[str] = (),
    stream: TextIO | None = None,
) -> None:
    """Install one predictable root handler and quiet verbose transports."""

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(
        RedactingFormatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
            secrets=secrets,
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # These libraries can log raw protocol data at DEBUG. The bridge's own
    # diagnostics remain available without exposing session frames.
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
