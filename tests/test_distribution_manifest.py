from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_source_manifest_includes_audit_and_operations_materials() -> None:
    manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    for required in (
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "DEVELOPMENT.md",
        "RESEARCH.md",
        "SECURITY.md",
        "recursive-include deploy *.service",
        "recursive-include tests *.py",
    ):
        assert required in manifest


def test_source_manifest_excludes_local_secret_and_state_patterns() -> None:
    manifest = (PROJECT_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "global-exclude .env .env.*" in manifest
    assert "*.sqlite3" in manifest
    assert "*.db" in manifest
