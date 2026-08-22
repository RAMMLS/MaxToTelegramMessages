from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "tests/ci_junit_summary.py"


def _run_script(*args: Path) -> subprocess.CompletedProcess[str]:
    # Interpreter and script path are repository-controlled constants.
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *(str(value) for value in args)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_emits_bounded_redacted_failure_annotation(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    token_shaped_value = f"{1234567890}:{'A' * 35}"
    report.write_text(
        f"""<?xml version="1.0"?>
<testsuites><testsuite><testcase classname="tests.test_lock" name="test_second">
<failure message="token {token_shaped_value}&#10;denied%now" />
</testcase></testsuite></testsuites>
""",
        encoding="utf-8",
    )

    completed = _run_script(report)

    assert completed.returncode == 0
    assert completed.stdout.splitlines() == [
        "::error title=Pytest: tests.test_lock.test_second::"
        "tests.test_lock.test_second: token <redacted> denied%25now"
    ]


def test_reports_invalid_xml_without_raising(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    report.write_text("not XML", encoding="utf-8")

    completed = _run_script(report)

    assert completed.returncode == 0
    assert completed.stdout.startswith("::error title=Pytest report unavailable::")


def test_limits_failure_annotations_to_ten(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    cases = "".join(
        f'<testcase classname="suite" name="case_{index}"><failure message="boom" /></testcase>'
        for index in range(12)
    )
    report.write_text(f"<testsuite>{cases}</testsuite>", encoding="utf-8")

    completed = _run_script(report)
    lines = completed.stdout.splitlines()

    assert completed.returncode == 0
    assert len(lines) == 10
    assert "suite.case_9" in lines[-1]
    assert all("suite.case_10" not in line for line in lines)


def test_reports_collection_error_with_case_name(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuite><testcase classname="tests.test_import" name="collection">'
        '<error message="collection failure" /></testcase></testsuite>',
        encoding="utf-8",
    )

    completed = _run_script(report)

    assert completed.returncode == 0
    assert "tests.test_import.collection: collection failure" in completed.stdout


def test_reports_empty_failed_suite(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    report.write_text('<testsuite><testcase name="passed" /></testsuite>', encoding="utf-8")

    completed = _run_script(report)

    assert completed.returncode == 0
    assert "No failed testcase was present" in completed.stdout


def test_main_rejects_missing_report_argument() -> None:
    completed = _run_script()

    assert completed.returncode == 2
    assert "usage:" in completed.stderr
