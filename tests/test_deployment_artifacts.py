import re
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


def test_alwaysdata_bootstrap_keeps_runtime_state_private() -> None:
    bootstrap = (PROJECT_ROOT / "deploy/alwaysdata/bootstrap.sh").read_text(encoding="utf-8")

    assert "set -eu" in bootstrap
    assert "umask 077" in bootstrap
    assert "PIP_NO_CACHE_DIR=1" in bootstrap
    assert "mkdir -p data" in bootstrap
    assert "chmod 700 data" in bootstrap
    assert "chmod 600 .env" in bootstrap
    assert "chmod 600 data/max-session.json" in bootstrap
    assert ".venv/bin/max-to-telegram --version" in bootstrap
    assert "cat .env" not in bootstrap
    assert "set -x" not in bootstrap


def test_alwaysdata_template_is_fail_closed_and_contains_no_credentials() -> None:
    template = (PROJECT_ROOT / "deploy/alwaysdata/bridge.env.example").read_text(encoding="utf-8")

    assert "MAX_SESSION_FILE=data/max-session.json" in template
    assert "BRIDGE_STATE_DB=data/state.sqlite3" in template
    assert "MAX_CHAT_IDS=\n" in template
    assert "MAX_DISCOVERY_MODE=false" in template
    assert "TELEGRAM_BOT_TOKEN=\n" in template
    assert not re.search(r"[0-9]{8,12}:[A-Za-z0-9_-]{30,}", template)


def test_alwaysdata_runbook_uses_foreground_service_without_inline_secrets() -> None:
    runbook = (PROJECT_ROOT / "deploy/alwaysdata/README.md").read_text(encoding="utf-8")

    assert "command: `.venv/bin/max-to-telegram`" in runbook
    assert "working directory: `max-to-telegram`" in runbook
    assert "environment: empty" in runbook
    assert "/home/ACCOUNT/home/ACCOUNT/max-to-telegram" in runbook
    assert "monitoring command: empty" in runbook
    assert "No incoming port" in runbook
    assert "Do not paste tokens into the service command" in runbook
    assert "MAX_SESSION_FILE=data/max-session.json" in runbook
    assert "BRIDGE_STATE_DB=data/state.sqlite3" in runbook
