from __future__ import annotations

from pathlib import Path

from max_to_telegram.ci_junit_summary import main, render_annotations


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

    result = render_annotations(report)

    assert result == [
        "::error title=Pytest: tests.test_lock.test_second::token <redacted> denied%25now"
    ]


def test_reports_invalid_xml_without_raising(tmp_path: Path) -> None:
    report = tmp_path / "results.xml"
    report.write_text("not XML", encoding="utf-8")

    assert render_annotations(report)[0].startswith("::error title=Pytest report unavailable::")


def test_main_rejects_missing_report_argument(capsys) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().err
