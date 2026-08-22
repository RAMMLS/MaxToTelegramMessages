from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_systemd_unit_uses_unprivileged_private_writable_state() -> None:
    unit = (PROJECT_ROOT / "deploy/max-to-telegram.service").read_text(encoding="utf-8")

    assert "User=maxbridge" in unit
    assert "Group=maxbridge" in unit
    assert "UMask=0077" in unit
    assert "StateDirectory=max-to-telegram" in unit
    assert "StateDirectoryMode=0700" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "CapabilityBoundingSet=" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit
    assert "ReadWritePaths=/opt/max-to-telegram" not in unit


def test_example_environment_contains_no_credentials() -> None:
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "MAX_AUTH_TOKEN=\n" in example
    assert "TELEGRAM_BOT_TOKEN=\n" in example
    assert "\nMAX_CHAT_IDS=\n" in example
