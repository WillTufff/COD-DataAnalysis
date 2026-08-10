"""The NDJSON progress lines a job emits under `--events ndjson`.

The reader is the ops app's stage tracker: one object per transition, on stdout,
interleaved with the job's ordinary output.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from cdlhub_analytics import events


def emitted(captured: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


def test_the_flag_is_off_unless_asked_for() -> None:
    ap = argparse.ArgumentParser()
    events.add_argument(ap)

    assert ap.parse_args([]).events is None
    assert ap.parse_args(["--events", "ndjson"]).events == "ndjson"
    with pytest.raises(SystemExit):
        ap.parse_args(["--events", "yaml"])


def test_a_disabled_emitter_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    progress = events.StageEvents(False)
    progress.stage("elo")
    progress.stage("glicko2")
    progress.done()

    assert capsys.readouterr().out == ""


def test_opening_a_stage_closes_the_one_in_flight(capsys: pytest.CaptureFixture[str]) -> None:
    progress = events.StageEvents(True)
    progress.stage("elo")
    print("elo run 12: 3027 series")
    progress.stage("glicko2")
    progress.done()

    out = capsys.readouterr().out
    assert [(e["stage"], e["status"]) for e in emitted(out)] == [
        ("elo", "start"),
        ("elo", "done"),
        ("glicko2", "start"),
        ("glicko2", "done"),
    ]
    assert "elo run 12: 3027 series" in out


def test_done_carries_the_stage_duration_and_start_does_not(
    capsys: pytest.CaptureFixture[str],
) -> None:
    progress = events.StageEvents(True)
    progress.stage("elo")
    progress.done()

    start, done = emitted(capsys.readouterr().out)
    assert set(start) == {"stage", "status", "elapsed_ms"}
    assert set(done) == {"stage", "status", "elapsed_ms", "stage_ms"}
    assert done["elapsed_ms"] >= start["elapsed_ms"] >= 0
    assert done["stage_ms"] <= done["elapsed_ms"]


def test_closing_when_no_stage_is_open_emits_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    progress = events.StageEvents(True)
    progress.done()
    progress.done()

    assert capsys.readouterr().out == ""
