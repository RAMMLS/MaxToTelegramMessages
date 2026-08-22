"""Emit bounded GitHub annotations for failures from a pytest JUnit report."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from xml.etree import ElementTree

_TOKEN_PATTERN = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")
_MAX_ANNOTATIONS = 10
_MAX_MESSAGE_CHARS = 500


def render_annotations(report: Path) -> list[str]:
    """Return privacy-bounded workflow commands for failed/error test cases."""

    try:
        # The report is generated locally by pytest in the same CI step.
        root = ElementTree.parse(report).getroot()  # noqa: S314
    except (OSError, ElementTree.ParseError) as exc:
        detail = _escape(str(exc))
        return [f"::error title=Pytest report unavailable::{detail[:_MAX_MESSAGE_CHARS]}"]

    rendered: list[str] = []
    for case in root.iter("testcase"):
        outcome = case.find("failure")
        if outcome is None:
            outcome = case.find("error")
        if outcome is None:
            continue
        suite = case.get("classname", "test")
        name = case.get("name", "unknown")
        title = _escape(f"Pytest: {suite}.{name}")[:200]
        raw_message = outcome.get("message") or outcome.text or "test failed"
        message = _TOKEN_PATTERN.sub("<redacted>", " ".join(raw_message.split()))
        detail = _escape(f"{suite}.{name}: {message}")[:_MAX_MESSAGE_CHARS]
        rendered.append(f"::error title={title}::{detail}")
        if len(rendered) >= _MAX_ANNOTATIONS:
            break
    if not rendered:
        rendered.append("::error title=Pytest failed::No failed testcase was present in JUnit XML")
    return rendered


def _escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: ci_junit_summary.py REPORT.xml", file=sys.stderr)
        return 2
    for annotation in render_annotations(Path(args[0])):
        print(annotation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
