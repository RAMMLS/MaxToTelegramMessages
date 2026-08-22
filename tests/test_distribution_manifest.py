from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_source_manifest_includes_audit_and_operations_materials() -> None:
    manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    for required in (
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "DEVELOPMENT.md",
        "include .env.example",
        "MANUAL_TEST.md",
        "RESEARCH.md",
        "SECURITY.md",
        "recursive-include deploy *.example *.md *.service *.sh",
        "recursive-include tests *.py",
    ):
        assert required in manifest


def test_source_manifest_excludes_local_secret_and_state_patterns() -> None:
    manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "global-exclude .env" in manifest
    assert "*.sqlite3" in manifest
    assert "*.db" in manifest


def test_package_declares_pep561_type_information() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert 'max_to_telegram = ["py.typed"]' in pyproject
    assert (PROJECT_ROOT / "src/max_to_telegram/py.typed").is_file()


def test_package_installs_timezone_database_on_windows() -> None:
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "tzdata>=2024.1,<2027; platform_system == 'Windows'" in pyproject


def test_ci_failure_formatter_is_not_a_runtime_package_module() -> None:
    assert (PROJECT_ROOT / "tests/ci_junit_summary.py").is_file()
    assert not (PROJECT_ROOT / "src/max_to_telegram/ci_junit_summary.py").exists()
