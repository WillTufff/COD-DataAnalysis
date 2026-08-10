"""The files the quality gate leaves behind for anything reading it later."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cdlhub_pipeline import quality


def result(passed: bool, at: str = "2026-08-09T20:58:38Z") -> dict[str, Any]:
    return {
        "generated_at": at,
        "passed": passed,
        "failures": 0 if passed else 1,
        "duration_s": 0.6,
        "hard_checks": [{"name": "orphan_events", "passed": passed, "rows": 0 if passed else 4}],
        "soft_checks": [{"name": "undecided_series", "rows": 9}],
        "coverage": [],
        "reconciliation": {},
    }


def history_lines(reports: Path) -> list[dict[str, Any]]:
    text = (reports / quality.HISTORY_FILENAME).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_report_is_written_with_the_run_intact(tmp_path: Path) -> None:
    quality.write_report(tmp_path, result(passed=True))

    saved = json.loads((tmp_path / quality.REPORT_FILENAME).read_text(encoding="utf-8"))
    assert saved["passed"] is True
    assert saved["hard_checks"][0]["name"] == "orphan_events"


def test_each_run_appends_one_history_line(tmp_path: Path) -> None:
    quality.write_report(tmp_path, result(passed=True))
    quality.write_report(tmp_path, result(passed=False, at="2026-08-09T21:10:00Z"))

    entries = history_lines(tmp_path)
    assert [entry["passed"] for entry in entries] == [True, False]
    assert entries[-1]["hard"]["orphan_events"] == 4
    assert entries[-1]["soft"]["undecided_series"] == 9


def test_history_keeps_the_most_recent_runs(tmp_path: Path) -> None:
    start = datetime(2026, 8, 9, tzinfo=UTC)
    stamps = [
        (start + timedelta(minutes=run)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for run in range(quality.HISTORY_LIMIT + 3)
    ]
    for stamp in stamps:
        quality.write_report(tmp_path, result(passed=True, at=stamp))

    entries = history_lines(tmp_path)
    assert len(entries) == quality.HISTORY_LIMIT
    assert [entry["at"] for entry in entries] == stamps[-quality.HISTORY_LIMIT :]


def test_an_unwritable_reports_directory_is_not_fatal(tmp_path: Path) -> None:
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")

    quality.write_report(blocked / "quality", result(passed=True))
