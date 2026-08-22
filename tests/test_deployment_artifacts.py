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
    assert "ProtectKernelLogs=true" in unit
    assert "ProtectProc=invisible" in unit
    assert "ProcSubset=pid" in unit
    assert "MemoryDenyWriteExecute=true" in unit
    assert "PrivateMounts=true" in unit
    assert "RestrictRealtime=true" in unit
    assert "RestartPreventExitStatus=2" in unit
    assert "CapabilityBoundingSet=" in unit
    assert "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6" in unit
    assert "ReadWritePaths=/opt/max-to-telegram" not in unit


def test_example_environment_contains_no_credentials() -> None:
    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")

    assert "MAX_AUTH_TOKEN=\n" in example
    assert "TELEGRAM_BOT_TOKEN=\n" in example
    assert "\nMAX_CHAT_IDS=\n" in example


def test_gitignore_covers_documented_state_file_variants() -> None:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")

    for pattern in (
        "*.sqlite3",
        "*.sqlite3-shm",
        "*.sqlite3-wal",
        "*.sqlite3.lock",
        "*.db",
        "*.db-shm",
        "*.db-wal",
        "*.db.lock",
    ):
        assert pattern in gitignore


def test_ci_has_timeout_and_non_echoing_secret_guard() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "timeout-minutes: 15" in workflow
    assert "git grep -q -E" in workflow
    assert "git ls-files --error-unmatch .env" in workflow
    assert "git grep -n" not in workflow
