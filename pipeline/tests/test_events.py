import argparse
import json
from typing import Any

import pytest

from cdlhub_pipeline import events


def emitted(captured: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in captured.splitlines() if line.startswith("{")]


def test_the_flag_is_off_unless_asked_for() -> None:
    ap = argparse.ArgumentParser()
    events.add_argument(ap)

    assert ap.parse_args([]).events is None
    assert ap.parse_args(["--events", "ndjson"]).events == "ndjson"


def test_a_disabled_emitter_prints_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    progress = events.StageEvents(False)
    progress.stage("cito load")
    progress.done()

    assert capsys.readouterr().out == ""


def test_stages_open_and_close_in_order(capsys: pytest.CaptureFixture[str]) -> None:
    progress = events.StageEvents(True)
    progress.stage("cito scope")
    print("== cito scope ==")
    progress.stage("cito load")
    progress.done()
    progress.done()

    out = capsys.readouterr().out
    assert [(e["stage"], e["status"]) for e in emitted(out)] == [
        ("cito scope", "start"),
        ("cito scope", "done"),
        ("cito load", "start"),
        ("cito load", "done"),
    ]
    assert "== cito scope ==" in out
    assert all("stage_ms" in e for e in emitted(out) if e["status"] == "done")
