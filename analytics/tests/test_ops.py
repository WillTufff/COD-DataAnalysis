"""Contract tests for the ops command surface.

Each command must emit one parseable JSON object with the documented top-level
keys; values are the database's business. The DB-backed cases skip when no
database is reachable, like the other DB-backed tests here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from cdlhub_analytics.ops import REPO_ROOT
from cdlhub_analytics.ops import backups as ops_backups
from cdlhub_analytics.ops import identity as ops_identity
from cdlhub_analytics.ops import lineage as ops_lineage
from cdlhub_analytics.ops import models as ops_models
from cdlhub_analytics.ops import pacing as ops_pacing
from cdlhub_analytics.ops import quality as ops_quality
from cdlhub_analytics.ops import runs as ops_runs
from cdlhub_analytics.ops import services as ops_services
from cdlhub_analytics.ops import sources as ops_sources
from cdlhub_analytics.ops.__main__ import JOBS, main

UNREACHABLE_DSN = "postgres://cdlhub:cdlhub@127.0.0.1:1/cdlhub"


def _dsn() -> str:
    return os.environ.get("DATABASE_URL", "postgres://cdlhub:cdlhub@localhost:54329/cdlhub")


@pytest.fixture
def db() -> Iterator[str]:
    psycopg = pytest.importorskip("psycopg")
    dsn = _dsn()
    try:
        psycopg.connect(dsn, connect_timeout=2).close()
    except Exception:  # noqa: BLE001 - any connection failure means no DB here
        pytest.skip("no database reachable")
    yield dsn


def call(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    code = main(list(argv))
    out = capsys.readouterr().out
    assert code == 0, out
    payload = json.loads(out)
    assert isinstance(payload, dict)
    return payload


def test_jobs_needs_no_database(capsys: pytest.CaptureFixture[str]) -> None:
    payload = call(capsys, "jobs", "--dsn", UNREACHABLE_DSN)
    assert [job["id"] for job in payload["jobs"]] == [job["id"] for job in JOBS]
    for job in payload["jobs"]:
        assert {"id", "label", "cwd", "argv", "stages", "destructive", "est_seconds"} <= set(job)
        assert isinstance(job["argv"], list) and job["argv"]
        assert isinstance(job["stages"], list)


def test_every_destructive_job_is_marked() -> None:
    destructive = {job["id"] for job in JOBS if job["destructive"]}
    assert destructive == {
        job["id"] for job in JOBS if any("--reset" in arg for arg in job["argv"])
    }


def test_every_events_job_emits_the_stages_the_catalog_declares() -> None:
    for job in JOBS:
        if not job.get("events"):
            continue
        module = job["argv"][job["argv"].index("-m") + 1]
        source = REPO_ROOT / job["cwd"] / "src" / Path(*module.split(".")).with_suffix(".py")
        emitted = re.findall(r'progress\.stage\("([^"]+)"\)', source.read_text(encoding="utf-8"))
        assert emitted == job["stages"], job["id"]


def test_script_jobs_declare_the_stages_the_script_lists() -> None:
    """A job that is a script and declares stages is checked against it.

    The script names its own stages under --list-stages, so a check renamed
    there without the catalog following fails here rather than showing a
    checklist that never ticks.
    """
    for job in JOBS:
        if job.get("events") or not job["stages"]:
            continue
        script = REPO_ROOT / job["cwd"] / job["argv"][0]
        assert script.is_file(), job["id"]
        listed = subprocess.run(  # noqa: S603
            [str(script), "--list-stages"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        assert listed == job["stages"], job["id"]


def test_summary_reports_an_unreachable_database_instead_of_failing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = call(capsys, "summary", "--dsn", UNREACHABLE_DSN)
    assert payload["db"]["reachable"] is False
    assert payload["db"]["dsn_host"] == "127.0.0.1:1"
    assert payload["db"]["error"]
    assert payload["migrations"]["on_disk"] == payload["migrations"]["applied"] + len(
        payload["migrations"]["pending"]
    )
    assert set(payload["data"]) == {
        "series_decided",
        "player_map_rows",
        "events",
        "data_through",
    }


def test_summary(capsys: pytest.CaptureFixture[str], db: str) -> None:
    payload = call(capsys, "summary", "--dsn", db)
    assert set(payload) == {"generated_at", "db", "migrations", "data", "runs", "quarantine"}
    assert payload["db"]["reachable"] is True
    assert payload["db"]["size_bytes"] > 0
    assert set(payload["migrations"]) == {"applied", "on_disk", "pending"}
    assert set(payload["data"]) == {
        "series_decided",
        "player_map_rows",
        "events",
        "data_through",
    }
    assert set(payload["runs"]) == {"total", "models", "latest_created_at"}
    assert set(payload["quarantine"]) == {"total", "by_reason"}


def test_runs(capsys: pytest.CaptureFixture[str], db: str) -> None:
    payload = call(capsys, "runs", "--dsn", db, "--limit", "5")
    listing = payload["runs"]
    assert len(listing) <= 5
    for run in listing:
        assert set(run) == {
            "id",
            "model",
            "version",
            "code_ref",
            "data_through",
            "created_at",
            "superseded",
            "outputs",
            "backtest",
            "artifacts",
        }
        assert isinstance(run["superseded"], bool)
        assert set(run["outputs"]) <= set(ops_runs.OUTPUT_TABLES)
        if run["backtest"] is not None:
            assert set(run["backtest"]) == {"brier", "log_loss", "accuracy", "n_predictions"}


def test_runs_model_filter(capsys: pytest.CaptureFixture[str], db: str) -> None:
    payload = call(capsys, "runs", "--dsn", db, "--model", "elo")
    assert {run["model"] for run in payload["runs"]} <= {"elo"}


def test_run_detail(capsys: pytest.CaptureFixture[str], db: str) -> None:
    listing = call(capsys, "runs", "--dsn", db, "--limit", "1")["runs"]
    if not listing:
        pytest.skip("no model runs in this database")
    payload = call(capsys, "run", "--dsn", db, str(listing[0]["id"]))
    assert set(payload) == {
        "id",
        "model",
        "version",
        "code_ref",
        "params",
        "data_through",
        "created_at",
        "outputs",
        "backtest",
        "artifacts",
    }
    for artifact in payload["artifacts"]:
        assert set(artifact) == {"name", "bytes"}
    if payload["backtest"] is not None:
        assert set(payload["backtest"]) == {
            "brier",
            "log_loss",
            "accuracy",
            "n_predictions",
            "window_from",
            "window_to",
            "calibration",
        }
        assert isinstance(payload["backtest"]["calibration"], list)


def test_run_detail_of_a_missing_run_fails(capsys: pytest.CaptureFixture[str], db: str) -> None:
    assert main(["run", "--dsn", db, "-1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "-1" in captured.err


def test_history(capsys: pytest.CaptureFixture[str], db: str) -> None:
    payload = call(capsys, "history", "--dsn", db, "elo")
    assert payload["model"] == "elo"
    for point in payload["points"]:
        assert set(point) == {
            "run_id",
            "version",
            "code_ref",
            "created_at",
            "data_through",
            "brier",
            "log_loss",
            "accuracy",
            "n_predictions",
        }
    stamps = [point["created_at"] for point in payload["points"]]
    assert stamps == sorted(stamps)


def test_history_of_an_unknown_model_is_empty(capsys: pytest.CaptureFixture[str], db: str) -> None:
    payload = call(capsys, "history", "--dsn", db, "not_a_model")
    assert payload == {"model": "not_a_model", "points": []}


def test_sources(capsys: pytest.CaptureFixture[str], db: str) -> None:
    payload = call(capsys, "sources", "--dsn", db)
    assert set(payload) == {"by_source", "by_season", "snapshots", "load_reports"}
    for row in payload["by_source"]:
        assert set(row) == {
            "data_source",
            "series",
            "games",
            "player_map_rows",
            "first_date",
            "last_date",
        }
    for row in payload["by_season"]:
        assert set(row) == {
            "season",
            "title",
            "series",
            "player_map_rows",
            "sources",
            "completeness",
        }
        assert isinstance(row["sources"], list)
    for snapshot in payload["snapshots"]:
        assert set(snapshot) == {"name", "files", "bytes", "newest_mtime"}
    for source in ("cito", "lpdb"):
        assert set(payload["load_reports"][source]) == {
            "loaded",
            "skipped",
            "quarantined",
            "by_reason",
        }


def test_artifact(capsys: pytest.CaptureFixture[str], db: str) -> None:
    named = [run for run in call(capsys, "runs", "--dsn", db)["runs"] if run["artifacts"]]
    if not named:
        pytest.skip("no run has artifacts in this database")
    run = named[0]
    payload = call(capsys, "artifact", "--dsn", db, str(run["id"]), run["artifacts"][0])
    assert payload["run_id"] == run["id"]
    assert payload["name"] == run["artifacts"][0]
    assert payload["payload"] is not None


def test_artifact_of_a_missing_name_fails(capsys: pytest.CaptureFixture[str], db: str) -> None:
    assert main(["artifact", "--dsn", db, "1", "no_such_artifact"]) == 1
    assert capsys.readouterr().out == ""


def test_snapshot_scan_of_a_missing_directory_is_empty(tmp_path: Path) -> None:
    assert ops_sources.snapshot_dirs(tmp_path / "absent") == []


def test_snapshot_scan_reports_each_directory_holding_files(tmp_path: Path) -> None:
    (tmp_path / "cito" / "matches").mkdir(parents=True)
    (tmp_path / "cito" / "load-report.json").write_text("{}")
    (tmp_path / "cito" / "matches" / "a.json").write_text('{"a": 1}')
    (tmp_path / "cito" / "matches" / "b.json").write_text('{"b": 2}')
    (tmp_path / "empty").mkdir()

    stats = ops_sources.snapshot_dirs(tmp_path)

    assert [entry["name"] for entry in stats] == ["cito", "cito/matches"]
    assert stats[1]["files"] == 2
    assert stats[1]["bytes"] == 16
    assert stats[1]["newest_mtime"].endswith("Z")


def test_quarantine_of_a_missing_report_is_zero(tmp_path: Path) -> None:
    assert ops_sources.quarantine(tmp_path) == {"total": 0, "by_reason": {}}


def test_quarantine_groups_by_the_leading_clause_of_the_reason(tmp_path: Path) -> None:
    (tmp_path / "cito").mkdir()
    (tmp_path / "cito" / "load-report.json").write_text(
        json.dumps(
            {
                "quarantined": [
                    {"reason": "stats belong to another match: bp-match-1"},
                    {"reason": "stats belong to another match: bp-match-2"},
                    {"reason": "same team slug on both sides"},
                ]
            }
        )
    )

    assert ops_sources.quarantine(tmp_path) == {
        "total": 3,
        "by_reason": {
            "stats belong to another match": 2,
            "same team slug on both sides": 1,
        },
    }


# MARK: pacing


def write_requests(snapshots: Path, source: str, lines: list[str]) -> None:
    directory = snapshots / source
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "requests.ndjson").write_text("\n".join(lines) + "\n", encoding="utf-8")


def request_line(at: datetime, *, status: int = 200, min_interval_s: float = 5.0) -> str:
    return json.dumps(
        {
            "at": at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "endpoint": "/match",
            "status": status,
            "waited_ms": None,
            "min_interval_s": min_interval_s,
        }
    )


def source_named(payload: dict[str, Any], name: str) -> dict[str, Any]:
    found = next(source for source in payload["sources"] if source["source"] == name)
    assert isinstance(found, dict)
    return found


def test_pacing_without_any_request_log(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    payload = call(capsys, "pacing", "--snapshots", str(tmp_path))
    assert [source["source"] for source in payload["sources"]] == list(ops_pacing.SOURCES)
    for source in payload["sources"]:
        assert source["calls_total"] == 0
        assert source["calls_today"] == 0
        assert source["active"] is False
        assert source["by_hour"] == []


def test_pacing_measures_the_gaps_between_calls(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    stamps = [now - timedelta(seconds=offset) for offset in (30, 20, 10, 9)]
    write_requests(tmp_path, "lpdb", [request_line(at) for at in stamps])

    payload = call(capsys, "pacing", "--snapshots", str(tmp_path))
    lpdb = source_named(payload, "lpdb")

    assert lpdb["min_interval_s"] == 5.0
    assert lpdb["calls_total"] == 4
    assert lpdb["calls_today"] == sum(1 for at in stamps if at.date() == now.date())
    assert lpdb["errors_today"] == 0
    assert lpdb["active"] is True
    assert lpdb["since_last_s"] < ops_pacing.ACTIVE_WITHIN_S
    assert lpdb["recent"] == {
        "calls": 4,
        "gaps": 3,
        "span_s": 21.0,
        "mean_gap_s": 7.0,
        "min_gap_s": 1.0,
        "per_minute": round(60 * 3 / 21, 2),
        # The 1s gap is the only one under the 5s the log itself reports.
        "under_min_interval": 1,
    }
    assert len(lpdb["by_hour"]) == ops_pacing.HOURS
    assert sum(hour["calls"] for hour in lpdb["by_hour"]) == 4
    assert source_named(payload, "cito")["calls_total"] == 0


def test_pacing_counts_failed_calls_and_ignores_unreadable_lines(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    write_requests(
        tmp_path,
        "cito",
        [
            request_line(now - timedelta(seconds=20), status=500),
            "not json",
            json.dumps({"endpoint": "/match", "status": 200}),
            json.dumps({"at": "the other day", "status": 200}),
            request_line(now - timedelta(seconds=10)),
        ],
    )

    cito = source_named(call(capsys, "pacing", "--snapshots", str(tmp_path)), "cito")

    assert cito["calls_total"] == 2
    assert cito["errors_today"] == sum(
        1 for at in (now - timedelta(seconds=20),) if at.date() == now.date()
    )


def test_pacing_reports_a_source_last_called_days_ago_as_idle(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    write_requests(tmp_path, "lpdb", [request_line(now - timedelta(days=3))])

    lpdb = source_named(call(capsys, "pacing", "--snapshots", str(tmp_path)), "lpdb")

    assert lpdb["calls_total"] == 1
    assert lpdb["calls_today"] == 0
    assert lpdb["active"] is False
    assert lpdb["recent"] == {"calls": 1, "gaps": 0}
    assert sum(hour["calls"] for hour in lpdb["by_hour"]) == 0


# MARK: quality


def write_quality(snapshots: Path, report: dict[str, Any], history: list[str]) -> None:
    directory = snapshots / ops_quality.DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ops_quality.REPORT_FILENAME).write_text(json.dumps(report), encoding="utf-8")
    if history:
        (directory / ops_quality.HISTORY_FILENAME).write_text(
            "\n".join(history) + "\n", encoding="utf-8"
        )


def quality_report(at: datetime, *, failing: list[str] | None = None) -> dict[str, Any]:
    failing = failing or []
    return {
        "generated_at": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "passed": not failing,
        "failures": len(failing),
        "duration_s": 1.2,
        "hard_checks": [
            {"name": "negative_stats", "passed": "negative_stats" not in failing, "rows": 0},
            {"name": "orphan_events", "passed": "orphan_events" not in failing, "rows": 4},
        ],
        "soft_checks": [{"name": "undecided_series", "rows": 9}],
        "coverage": [{"year": 2026}],
        "reconciliation": {"overall": {"rate": "0.9952"}, "by_title": []},
    }


def test_quality_before_the_gate_has_ever_run(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    payload = call(capsys, "quality", "--snapshots", str(tmp_path))
    assert payload["has_run"] is False
    assert payload["passed"] is None
    assert payload["stale"] is True
    assert payload["failing"] == []
    assert payload["history"] == []


def test_quality_reports_the_last_verdict(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    write_quality(tmp_path, quality_report(datetime.now(UTC)), [])

    payload = call(capsys, "quality", "--snapshots", str(tmp_path))

    assert payload["has_run"] is True
    assert payload["passed"] is True
    assert payload["stale"] is False
    assert payload["age_hours"] < 1
    assert payload["failing"] == []
    assert payload["warning"] == ["undecided_series"]
    assert payload["coverage"] == [{"year": 2026}]


def test_quality_names_the_checks_that_failed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    write_quality(tmp_path, quality_report(datetime.now(UTC), failing=["orphan_events"]), [])

    payload = call(capsys, "quality", "--snapshots", str(tmp_path))

    assert payload["passed"] is False
    assert payload["failing"] == ["orphan_events"]


def test_quality_marks_an_old_verdict_stale(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    old = datetime.now(UTC) - timedelta(hours=ops_quality.STALE_AFTER_HOURS + 1)
    write_quality(tmp_path, quality_report(old), [])

    payload = call(capsys, "quality", "--snapshots", str(tmp_path))

    assert payload["stale"] is True
    assert payload["age_hours"] > ops_quality.STALE_AFTER_HOURS


def test_quality_history_is_trimmed_and_skips_unreadable_lines(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    now = datetime.now(UTC)
    written = ops_quality.HISTORY_LIMIT + 5
    # The gate appends, so the file runs oldest first; `run` counts up with it.
    lines = [
        json.dumps(
            {
                "at": (now - timedelta(hours=written - run)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "passed": True,
                "hard": {"orphan_events": 0},
                "soft": {"undecided_series": run},
            }
        )
        for run in range(written)
    ]
    write_quality(tmp_path, quality_report(now), [*lines, "not json", ""])

    payload = call(capsys, "quality", "--snapshots", str(tmp_path))

    assert len(payload["history"]) == ops_quality.HISTORY_LIMIT
    assert payload["history"][-1]["soft"]["undecided_series"] == written - 1
    assert payload["history"][0]["soft"]["undecided_series"] == written - ops_quality.HISTORY_LIMIT


# MARK: backups


COMPOSE_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"
REMOTE_DSN = "postgres://user:secret@db.example.com:5432/cdlhub"


def no_local_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _: None)


def with_docker(monkeypatch: pytest.MonkeyPatch, path: str = "/usr/bin/docker") -> None:
    no_local_tools(monkeypatch)
    monkeypatch.setattr(ops_backups, "_docker", lambda: path)


def dump(directory: Path, name: str, *, size: int = 32, age_hours: float = 0.0) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"x" * size)
    when = (datetime.now(UTC) - timedelta(hours=age_hours)).timestamp()
    os.utime(path, (when, when))
    return path


def test_a_local_pg_dump_is_preferred_over_the_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", lambda tool: f"/usr/bin/{tool}")

    how = ops_backups.method(REMOTE_DSN)

    assert how == {
        "method": "local",
        "available": True,
        "binary": "/usr/bin/pg_dump",
        "reason": None,
    }
    assert ops_backups._argv(how, REMOTE_DSN, "pg_dump", ["--format=custom"]) == [
        "pg_dump",
        "--format=custom",
        REMOTE_DSN,
    ]


def test_the_container_is_used_when_nothing_is_installed_locally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_docker(monkeypatch)

    how = ops_backups.method(COMPOSE_DSN)

    assert how["method"] == "docker"
    assert how["available"] is True
    assert ops_backups._argv(how, COMPOSE_DSN, "pg_restore", ["--no-owner"]) == [
        "/usr/bin/docker",
        "compose",
        "-f",
        str(ops_backups.COMPOSE_FILE),
        "exec",
        "-T",
        ops_backups.COMPOSE_SERVICE,
        "pg_restore",
        "-U",
        "cdlhub",
        "-d",
        "cdlhub",
        "--no-owner",
    ]


def test_the_containers_tools_cannot_reach_a_database_it_does_not_serve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with_docker(monkeypatch)

    how = ops_backups.method(REMOTE_DSN)

    assert how["available"] is False
    assert "compose" in str(how["reason"])


def test_no_tools_at_all_is_reported_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    no_local_tools(monkeypatch)
    monkeypatch.setattr(ops_backups, "_docker", lambda: None)

    report = ops_backups.report(COMPOSE_DSN, tmp_path)

    assert report["available"] is False
    assert report["backups"] == []
    with pytest.raises(ops_backups.BackupError):
        ops_backups.create(COMPOSE_DSN, tmp_path)


def test_backups_are_listed_newest_first_with_their_age(tmp_path: Path) -> None:
    dump(tmp_path, "cdlhub-20260808T100000Z.dump", size=10, age_hours=25)
    dump(tmp_path, "cdlhub-20260809T100000Z.dump", size=20, age_hours=1)
    (tmp_path / "notes.txt").write_text("ignored", encoding="utf-8")

    report = ops_backups.report(COMPOSE_DSN, tmp_path)

    assert [entry["name"] for entry in report["backups"]] == [
        "cdlhub-20260809T100000Z.dump",
        "cdlhub-20260808T100000Z.dump",
    ]
    assert report["total_bytes"] == 30
    assert report["backups"][0]["age_hours"] == pytest.approx(1, abs=0.1)


def test_creating_a_backup_names_it_labels_it_and_prunes_the_oldest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_docker(monkeypatch)
    for hour in range(ops_backups.KEEP):
        dump(tmp_path, f"cdlhub-old-{hour:02d}.dump", age_hours=hour + 1)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert argv[0] == "/usr/bin/docker"
        kwargs["stdout"].write(b"PGDMP fake")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ops_backups.create(COMPOSE_DSN, tmp_path, label="Pre Restore!")

    assert result["created"].startswith("cdlhub-")
    assert result["created"].endswith("-pre-restore.dump")
    assert len(result["backups"]) == ops_backups.KEEP
    assert result["pruned"] == ["cdlhub-old-09.dump"]
    assert not list(tmp_path.glob("*.partial"))


def test_a_failed_dump_leaves_no_file_behind(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_docker(monkeypatch)

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", b"could not connect")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ops_backups.BackupError, match="could not connect"):
        ops_backups.create(COMPOSE_DSN, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_restoring_something_that_is_not_there_touches_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_docker(monkeypatch)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("nothing should be run"))

    with pytest.raises(ops_backups.BackupError, match="no backup named"):
        ops_backups.restore(COMPOSE_DSN, "missing.dump", tmp_path)
    with pytest.raises(ops_backups.BackupError, match="no backup named"):
        ops_backups.restore(COMPOSE_DSN, "../escape.dump", tmp_path)


def test_a_restore_empties_the_schema_before_loading_the_dump(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_docker(monkeypatch)
    dump(tmp_path, "cdlhub-20260809T100000Z.dump")
    calls: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append(argv)
        if "pg_dump" in argv:
            kwargs["stdout"].write(b"PGDMP fake")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    result = ops_backups.restore(COMPOSE_DSN, "cdlhub-20260809T100000Z.dump", tmp_path)

    tools = [
        next(part for part in argv if part.startswith("pg") or part == "psql") for argv in calls
    ]
    assert tools == ["pg_dump", "psql", "pg_restore"]
    assert "DROP SCHEMA public CASCADE" in " ".join(calls[1])
    assert "--exit-on-error" in calls[2]
    assert result["restored"] == "cdlhub-20260809T100000Z.dump"
    assert result["safety_backup"].endswith("-pre-restore.dump")


def test_a_failed_restore_says_where_the_replaced_state_went(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with_docker(monkeypatch)
    dump(tmp_path, "cdlhub-20260809T100000Z.dump")

    def fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        if "pg_dump" in argv:
            kwargs["stdout"].write(b"PGDMP fake")
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if "pg_restore" in argv:
            return subprocess.CompletedProcess(argv, 1, b"", b"unsupported version")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(ops_backups.BackupError) as raised:
        ops_backups.restore(COMPOSE_DSN, "cdlhub-20260809T100000Z.dump", tmp_path)

    assert "unsupported version" in str(raised.value)
    assert "-pre-restore.dump" in str(raised.value)


# MARK: identity


@pytest.fixture
def aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "aliases.json"
    path.write_text(json.dumps({"players": {}, "teams": {}}), encoding="utf-8")
    monkeypatch.setattr(ops_identity, "ALIASES_PATH", path)
    return path


def test_normalize_collapses_case_accents_and_punctuation() -> None:
    assert ops_identity.normalize("aBeZy") == "abezy"
    assert ops_identity.normalize("Sim-P") == "simp"
    assert ops_identity.normalize("Óscar") == "oscar"
    assert ops_identity.normalize("[???]") == ""


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [
        ("scump", "scumps", True),
        ("scumps", "scump", True),
        ("scump", "stump", True),
        ("scump", "scump", True),
        ("scump", "scumpy2", False),
        ("scump", "stumpy", False),
    ],
)
def test_within_one_edit(left: str, right: str, expected: bool) -> None:
    assert ops_identity._within_one_edit(left, right) is expected


def evidence(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "one_edit",
        "games_together": 0,
        "shared_teams": [],
        "stint_conflicts": [],
    }
    return base | overrides


def test_two_handles_that_played_a_map_together_are_two_people() -> None:
    assert ops_identity._suggestion(evidence(games_together=3)) == "keep_separate"


def test_two_handles_on_different_teams_at_once_are_two_people() -> None:
    conflict = {"left": {"team": "OpTic"}, "right": {"team": "FaZe"}}
    assert ops_identity._suggestion(evidence(stint_conflicts=[conflict])) == "keep_separate"


def test_a_shared_team_never_played_together_reads_as_one_person() -> None:
    assert ops_identity._suggestion(evidence(shared_teams=["OpTic"])) == "merge"


def test_a_spelling_pair_is_a_merge_and_anything_else_is_a_review() -> None:
    assert ops_identity._suggestion(evidence(kind="spelling")) == "merge"
    assert ops_identity._suggestion(evidence()) == "review"


def test_gap_and_overlap_days() -> None:
    early = {"first_date": date(2021, 1, 1), "last_date": date(2021, 6, 1)}
    late = {"first_date": date(2021, 9, 1), "last_date": date(2021, 12, 1)}
    overlapping = {"first_date": date(2021, 5, 1), "last_date": date(2021, 7, 1)}
    undated = {"first_date": None, "last_date": None}

    assert ops_identity._gap_days(early, late) == 92
    assert ops_identity._gap_days(early, overlapping) == 0
    assert ops_identity._overlap_days(early, overlapping) == 32
    assert ops_identity._overlap_days(early, late) == -91
    assert ops_identity._gap_days(early, undated) is None
    assert ops_identity._overlap_days(early, undated) is None


def test_merge_appends_one_mapping_to_aliases(aliases: Path) -> None:
    applied = ops_identity.merge("Scumps", "Scump")

    assert applied == {"merged": {"from": "Scumps", "to": "Scump"}, "players": 1}
    written = json.loads(aliases.read_text(encoding="utf-8"))
    assert written["players"] == {"Scumps": "Scump"}
    assert written["teams"] == {}


def test_merging_the_same_pair_twice_is_accepted_once(aliases: Path) -> None:
    ops_identity.merge("Scumps", "Scump")
    assert ops_identity.merge("Scumps", "Scump")["players"] == 1
    assert json.loads(aliases.read_text(encoding="utf-8"))["players"] == {"Scumps": "Scump"}


def test_a_merge_that_would_contradict_the_file_is_refused(aliases: Path) -> None:
    ops_identity.merge("Scumps", "Scump")

    with pytest.raises(ops_identity.DecisionError):
        ops_identity.merge("Scumps", "Formal")
    with pytest.raises(ops_identity.DecisionError):
        ops_identity.merge("Formal", "Scumps")
    with pytest.raises(ops_identity.DecisionError):
        ops_identity.merge("Scump", "Scump")

    assert json.loads(aliases.read_text(encoding="utf-8"))["players"] == {"Scumps": "Scump"}


def test_keep_separate_records_the_pair_in_a_stable_order(aliases: Path) -> None:
    first = ops_identity.keep_separate("Zed", "Abe")
    second = ops_identity.keep_separate("Abe", "Zed")

    assert first == {"kept_separate": ["Abe", "Zed"], "decisions": 1}
    assert second == first
    written = json.loads(aliases.read_text(encoding="utf-8"))
    assert written["identity_kept_separate"] == [["Abe", "Zed"]]
    assert written["_comment_identity"] == ops_identity.KEPT_SEPARATE_COMMENT

    with pytest.raises(ops_identity.DecisionError):
        ops_identity.keep_separate("Abe", "Abe")


def test_a_refused_decision_exits_without_printing_a_payload(
    capsys: pytest.CaptureFixture[str], aliases: Path
) -> None:
    assert main(["identity", "--dsn", UNREACHABLE_DSN, "--merge", "Scump", "Scump"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "itself" in captured.err
    assert json.loads(aliases.read_text(encoding="utf-8"))["players"] == {}


def test_a_resolved_handle_is_no_longer_offered_as_a_candidate(aliases: Path) -> None:
    players = [
        {"player_id": 1, "handle": "Scump"},
        {"player_id": 2, "handle": "Scumps"},
        {"player_id": 3, "handle": "Formal"},
        {"player_id": 4, "handle": "FormaL"},
    ]

    pairs = ops_identity._pair_up(players, resolved=set(), separated=set())
    assert {(left, right, kind) for left, right, kind in pairs} == {
        (4, 3, "spelling"),
        (1, 2, "one_edit"),
    }

    assert ops_identity._pair_up(players, {"Scumps"}, {("FormaL", "Formal")}) == []


def test_identity_report_lists_the_queue_and_what_is_already_resolved(
    capsys: pytest.CaptureFixture[str], db: str, aliases: Path
) -> None:
    ops_identity.merge("Scumps", "Scump")
    payload = call(capsys, "identity", "--dsn", db)

    assert set(payload) == {"aliases_path", "applied", "resolved", "reload_job", "candidates"}
    assert payload["aliases_path"] == str(aliases)
    assert payload["applied"] is None
    assert payload["resolved"] == {"players": 1, "kept_separate": 0}
    assert payload["reload_job"] in {job["id"] for job in JOBS}
    for candidate in payload["candidates"]:
        assert set(candidate) == {"id", "left", "right", "evidence", "suggestion"}
        assert candidate["suggestion"] in {"merge", "keep_separate", "review"}
        assert candidate["id"] == f"{candidate['left']['handle']}|{candidate['right']['handle']}"
        assert set(candidate["evidence"]) == {
            "kind",
            "normalized",
            "games_together",
            "games_opposed",
            "shared_teams",
            "overlap_days",
            "gap_days",
            "stint_conflicts",
        }
        for side in (candidate["left"], candidate["right"]):
            assert set(side) == {
                "player_id",
                "handle",
                "maps",
                "series",
                "first_date",
                "last_date",
                "sources",
                "teams",
                "stints",
            }
            assert len(side["teams"]) <= ops_identity.TOP_TEAMS


def test_a_decision_is_reported_alongside_the_queue_it_leaves(
    capsys: pytest.CaptureFixture[str], db: str, aliases: Path
) -> None:
    payload = call(capsys, "identity", "--dsn", db, "--keep-separate", "Abe", "Zed")

    assert payload["applied"] == {"kept_separate": ["Abe", "Zed"], "decisions": 1}
    assert payload["resolved"]["kept_separate"] == 1
    assert all(candidate["id"] != "Abe|Zed" for candidate in payload["candidates"])


# MARK: services


def test_a_port_nothing_listens_on_reports_down() -> None:
    # 1 is reserved and unbindable, so nothing can be serving it.
    status = ops_services.web(port=1)

    assert status["state"] == "down"
    assert status["processes"] == []
    assert status["probe"] is None
    assert status["argv"] == ["npm", "run", "dev"]
    assert status["url"].endswith(":1")


def test_services_reports_an_unreachable_database_without_failing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = call(capsys, "services", "--dsn", UNREACHABLE_DSN, "--web-port", "1")
    web, database = payload["services"]

    assert [service["name"] for service in payload["services"]] == ["web", "database"]
    assert web["state"] == "down"
    assert database["state"] == "down"
    assert database["error"]
    assert database["port"] == 1
    assert database["dsn_host"] == "127.0.0.1:1"
    # Every service names the command that starts it and where to run it.
    for service in payload["services"]:
        assert service["argv"] and service["directory"]


def test_a_reachable_database_reports_its_size(capsys: pytest.CaptureFixture[str], db: str) -> None:
    database = call(capsys, "services", "--dsn", db, "--web-port", "1")["services"][1]

    assert database["state"] == "up"
    assert database["size_bytes"] > 0
    assert database["version"].startswith("PostgreSQL")
    assert database["connections"] >= 1
    assert "error" not in database


# MARK: models


def test_models_collapses_runs_to_one_card_each(
    capsys: pytest.CaptureFixture[str], db: str
) -> None:
    payload = call(capsys, "models", "--dsn", db)
    listing = call(capsys, "runs", "--dsn", db)["runs"]

    assert {card["model"] for card in payload["models"]} == {run["model"] for run in listing}
    assert [card["model"] for card in payload["models"]] == sorted(
        card["model"] for card in payload["models"]
    )
    for card in payload["models"]:
        assert set(card) == {
            "model",
            "runs",
            "versions",
            "first_run_at",
            "current",
            "previous",
            "delta",
        }
        assert card["runs"] >= 1
        current = card["current"]
        assert set(current) == {
            "id",
            "version",
            "code_ref",
            "params",
            "data_through",
            "created_at",
            "age_days",
            "outputs",
            "artifacts",
            "backtest",
        }
        # The card's run is the newest run of that model in the listing.
        newest = max(
            (run for run in listing if run["model"] == card["model"]),
            key=lambda run: (run["created_at"], run["id"]),
        )
        assert current["id"] == newest["id"]
        assert current["age_days"] >= 0
        if card["previous"] is None:
            assert card["delta"] == {}


def test_a_metric_that_fell_counts_as_better_only_where_lower_is_better() -> None:
    current = {"brier": 0.20, "log_loss": 0.60, "accuracy": 0.66, "n_predictions": 3000}
    previous = {"brier": 0.22, "log_loss": 0.63, "accuracy": 0.64, "n_predictions": 2900}

    delta = ops_models._delta(current, previous)

    assert delta["brier"]["improved"] is True
    assert delta["brier"]["change"] == pytest.approx(-0.02)
    assert delta["log_loss"]["improved"] is True
    assert delta["accuracy"]["improved"] is True
    assert delta["accuracy"]["change"] == pytest.approx(0.02)
    assert ops_models._delta(previous, current)["brier"]["improved"] is False


def test_a_delta_needs_both_report_cards() -> None:
    card = {"brier": 0.2, "log_loss": 0.6, "accuracy": 0.6, "n_predictions": 10}

    assert ops_models._delta(card, None) == {}
    assert ops_models._delta(None, card) == {}
    assert ops_models._delta(card, {"accuracy": None}) == {}


# MARK: lineage


def test_lineage_is_a_graph_whose_every_edge_lands_on_a_node(
    capsys: pytest.CaptureFixture[str], db: str
) -> None:
    payload = call(capsys, "lineage", "--dsn", db)
    ids = {node["id"] for node in payload["nodes"]}

    assert set(payload) == {"nodes", "edges"}
    assert len(ids) == len(payload["nodes"])
    for edge in payload["edges"]:
        assert edge["from"] in ids, edge
        assert edge["to"] in ids, edge
        assert edge["from"] != edge["to"]
    kinds = {node["kind"] for node in payload["nodes"]}
    assert kinds <= {"source", "job", "table", "model", "output", "page"}
    # Every source is loaded by a job, and every model is fit by one.
    for node in payload["nodes"]:
        if node["kind"] in {"source", "model"}:
            assert any(
                edge["from"] == node["id"] or edge["to"] == node["id"]
                for edge in payload["edges"]
                if edge["from"].startswith("job:") or edge["to"].startswith("job:")
            ), node["id"]


def test_lineage_counts_a_source_against_its_own_rows(
    capsys: pytest.CaptureFixture[str], db: str
) -> None:
    lineage = call(capsys, "lineage", "--dsn", db)
    sources = call(capsys, "sources", "--dsn", db)["by_source"]
    by_source = {row["data_source"]: row for row in sources}

    for node in lineage["nodes"]:
        if node["kind"] != "source":
            continue
        row = by_source[node["source"]]
        assert node["series"] == row["series"]
        assert node["rows"] == row["player_map_rows"]
        assert node["first_date"] == row["first_date"]


def test_a_table_name_becomes_the_identifier_the_web_source_uses() -> None:
    assert ops_lineage._camel("game_player_stats") == "gamePlayerStats"
    assert ops_lineage._camel("players") == "players"
    assert ops_lineage._camel("player_rapm") == "playerRapm"


def test_a_page_reads_what_the_functions_it_imports_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "analytics.ts").write_text(
        "export async function getFeed() { return db.select().from(insights); }\n"
        "export async function getRatings() { return db.select().from(teamRatings); }\n",
        encoding="utf-8",
    )
    (tmp_path / "app" / "teams").mkdir(parents=True)
    (tmp_path / "app" / "page.tsx").write_text(
        'import { getFeed } from "@/lib/analytics";\n', encoding="utf-8"
    )
    (tmp_path / "app" / "teams" / "page.tsx").write_text(
        'import { getRatings } from "@/lib/analytics";\nconst rows = players;\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(ops_lineage, "WEB_DIR", tmp_path)

    pages = ops_lineage._pages(("insights", "team_ratings", "players"))

    assert pages == [
        {"route": "/", "tables": ["insights"]},
        {"route": "/teams", "tables": ["players", "team_ratings"]},
    ]
