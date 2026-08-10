import json
from datetime import UTC, datetime
from pathlib import Path

from cdlhub_pipeline import reqlog


def read(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_a_call_is_one_line_carrying_the_configured_interval(tmp_path: Path) -> None:
    reqlog.append(tmp_path / "lpdb", "/match", 200, 4998, 5.0)

    records = read(reqlog.log_path(tmp_path / "lpdb"))
    assert len(records) == 1
    record = records[0]
    assert record["endpoint"] == "/match"
    assert record["status"] == 200
    assert record["waited_ms"] == 4998
    assert record["min_interval_s"] == 5.0
    stamp = str(record["at"])
    assert stamp.endswith("Z")
    assert (
        abs((datetime.fromisoformat(stamp[:-1] + "+00:00") - datetime.now(UTC)).total_seconds())
        < 60
    )


def test_calls_append_under_a_directory_that_does_not_exist_yet(tmp_path: Path) -> None:
    root = tmp_path / "cito" / "nested"
    reqlog.append(root, "/match", 200, None, 1.0)
    reqlog.append(root, "/tournament", 500, 1002, 1.0)

    records = read(reqlog.log_path(root))
    assert [r["endpoint"] for r in records] == ["/match", "/tournament"]
    assert records[0]["waited_ms"] is None
    assert records[1]["status"] == 500


def test_a_log_that_cannot_be_written_does_not_interrupt_the_pull(tmp_path: Path) -> None:
    blocked = tmp_path / "lpdb"
    blocked.write_text("not a directory", encoding="utf-8")

    reqlog.append(blocked, "/match", 200, None, 5.0)

    assert blocked.read_text(encoding="utf-8") == "not a directory"
