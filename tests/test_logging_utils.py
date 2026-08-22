from __future__ import annotations

import io
import logging

from max_to_telegram.logging_utils import RedactingFormatter, configure_logging


def test_redacts_secrets_from_message_arguments_and_traceback() -> None:
    secret = "telegram-secret-token-123"
    formatter = RedactingFormatter("%(message)s\n%(exc_text)s", secrets=[secret])
    try:
        raise RuntimeError(f"request URL contained {secret}")
    except RuntimeError:
        record = logging.LogRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failed with %s",
            (secret,),
            __import__("sys").exc_info(),
        )

    rendered = formatter.format(record)

    assert secret not in rendered
    assert rendered.count("<redacted>") >= 2


def test_ignores_too_short_redaction_values() -> None:
    formatter = RedactingFormatter("%(message)s", secrets=["short", "long-secret"])
    record = logging.LogRecord("test", logging.INFO, __file__, 1, "short long-secret", (), None)

    assert formatter.format(record) == "short <redacted>"


def test_configure_logging_installs_safe_handler_and_quiets_transports() -> None:
    stream = io.StringIO()
    secret = "max-session-secret"
    configure_logging("DEBUG", secrets=[secret], stream=stream)

    logging.getLogger("max_to_telegram.test").debug("token=%s", secret)

    assert secret not in stream.getvalue()
    assert "<redacted>" in stream.getvalue()
    assert logging.getLogger("websockets").level == logging.WARNING
    assert logging.getLogger("aiohttp").level == logging.WARNING
