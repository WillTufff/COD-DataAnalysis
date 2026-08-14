"""JSON command surface over the database and the pipeline's on-disk state.

    uv run python -m cdlhub_analytics.ops <command> [--dsn DSN] [--snapshots DIR]

Commands: summary, runs, run <id>, history <model>, models, metric-diff,
sources, lineage, pacing, quality, services, identity, jobs,
artifact <run_id> <name>, backups, backup, restore <name>. One JSON object per
command on stdout, errors on stderr with a non-zero exit.

`identity` writes to aliases.json only. `backup` writes a dump outside the
repository, and `restore` is the one command that writes to the database.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg

from ..db import DEFAULT_DSN, connect
from . import (
    MIGRATIONS_DIR,
    SNAPSHOTS_DIR,
    Conn,
    backups,
    identity,
    lineage,
    metricdiff,
    models,
    pacing,
    quality,
    runs,
    schema,
    services,
    sources,
)
from .backtests import history

# The catalog of commands the app is allowed to launch. cwd is relative to the
# repository root; argv[0] is resolved by the caller. "events": true marks a job
# that accepts `--events ndjson`, which the caller appends to get one JSON line
# per stage transition instead of matching the printed lines.
JOBS: list[dict[str, Any]] = [
    {
        "id": "run_all",
        "label": "Fit all models",
        "cwd": "analytics",
        "argv": ["uv", "run", "python", "-m", "cdlhub_analytics.run_all"],
        "stages": [
            "era_adjust",
            "metric_layer",
            "round_wp",
            "segment_wp",
            "elo",
            "glicko2",
            "player_rating",
            "rapm",
            "winprob",
            "map_elo",
            "preflight",
            "season_rapm",
            "opponent_adjust",
            "match_context",
            "openskill",
            "skill_prior",
            "evaluate",
            "series_dynamics",
            "career",
            "aging",
            "player_style",
            "role",
            "validate",
            "insights",
            "error_control",
            "metric_diff",
        ],
        "destructive": False,
        "events": True,
        # Measured, not guessed. player_rating fits every feature-set version and
        # there are four; its stage alone runs about two minutes. `preflight`
        # adds 20 seconds, measured: one second for the identification
        # statistics — one pass over the admitted maps — and the rest for the
        # simulated leagues. `season_rapm` adds 12 seconds, measured: the
        # penalty search is a few dozen Cholesky factorizations of a
        # thousand-column matrix, and the split-half reliability refits every
        # cell twice.
        #
        # Re-measured end to end on 2026-08-11 against a full run: 494 seconds,
        # of which `opponent_adjust` is 237 — the largest single stage. Its
        # ladder is ten seconds over 106 cohort-features and the rest is the two
        # controls, a placebo refitting eight shuffled schedules per rung and a
        # series bootstrap of two hundred draws over the headline columns. The
        # rest of the pipeline came in at 257 against the 515 declared here
        # before, so the figure is taken from the measurement rather than by
        # adding the new stage to a stale total.
        #
        # Re-measured again on 2026-08-11 with `openskill` and `evaluate` added:
        # 959 seconds end to end, so the two of them are 465 by difference — the
        # largest addition the pipeline has taken. `evaluate` carries most of it:
        # the primary test bootstraps 2,000 draws twice, clustered and not, and
        # the placebo suite refits the plus-minus eight times on shuffled sides
        # and once more on a planted duplicate column.
        #
        # Re-measured on 2026-08-13 with `skill_prior` added: **963 seconds** end
        # to end, against 959 without it. The stage costs a handful of seconds
        # rather than the 90 budgeted for it, because the two non-linear arms
        # lost their comparison and were dropped: what remains is one ridge over
        # six walk-forward folds at two hundred drawn targets, on 431 rows and
        # sixteen columns, which is a few dozen Cholesky factorizations.
        #
        # An earlier reading of 523 seconds was taken off a run whose output went
        # through a pipe, and the file timestamps it was read from were not the
        # process's. Timed directly, the two measurements agree to four seconds.
        #
        # `career` adds five seconds: it opens no design matrix and fits nothing,
        # so its cost is four queries and an aggregation over a few thousand
        # player-seasons. `aging` adds twenty: three fits over two populations,
        # the largest of which is 532 paired seasons.
        "est_seconds": 990,
    },
    {
        "id": "metric_diff",
        "label": "Metric diff (snapshot and compare)",
        "cwd": "analytics",
        "argv": ["uv", "run", "python", "-m", "cdlhub_analytics.metricdiff"],
        "stages": ["metric_diff"],
        "destructive": False,
        "events": True,
        "est_seconds": 30,
    },
    {
        "id": "refresh",
        "label": "Refresh sources (top-up)",
        "cwd": "pipeline",
        "argv": ["uv", "run", "python", "-m", "cdlhub_pipeline.refresh"],
        "stages": ["cito scope", "cito backfill", "cito load", "lpdb pull", "lpdb load"],
        "destructive": False,
        "events": True,
        "est_seconds": 900,
        "flags": [
            {"name": "--dry-run", "label": "Dry run (fetch, don't load)"},
            {"name": "--skip-lpdb", "label": "Skip LPDB"},
        ],
    },
    {
        "id": "reload",
        "label": "Reload sources from snapshots (no network)",
        "cwd": "pipeline",
        "argv": ["uv", "run", "python", "-m", "cdlhub_pipeline.reload"],
        "stages": ["cito load", "lpdb load"],
        "destructive": False,
        "events": True,
        "est_seconds": 180,
        "flags": [{"name": "--skip-lpdb", "label": "Skip LPDB"}],
    },
    {
        "id": "quality",
        "label": "Quality gate",
        "cwd": "pipeline",
        "argv": ["uv", "run", "python", "-m", "cdlhub_pipeline.quality"],
        "stages": [],
        "destructive": False,
        "est_seconds": 30,
    },
    {
        "id": "checks",
        "label": "Code checks (lint, types, tests)",
        "cwd": ".",
        "argv": ["./scripts/checks.sh"],
        "stages": [
            "analytics lint",
            "analytics format",
            "analytics types",
            "analytics tests",
            "analytics gates",
            "pipeline lint",
            "pipeline format",
            "pipeline types",
            "pipeline tests",
            "web lint",
            "web types",
            "web tests",
            "web e2e",
        ],
        "destructive": False,
        "est_seconds": 210,
        "flags": [
            {"name": "--skip-web", "label": "Python only"},
            {"name": "--skip-python", "label": "Site only"},
        ],
    },
    {
        "id": "migrate",
        "label": "Apply migrations",
        "cwd": ".",
        "argv": ["./db/migrate.sh"],
        "stages": [],
        "destructive": False,
        "est_seconds": 10,
    },
    {
        "id": "cwl_reset",
        "label": "Reimport CWL archive (RESETS DB)",
        "cwd": "pipeline",
        "argv": ["uv", "run", "python", "-m", "cdlhub_pipeline.cwl_archive", "--reset"],
        "stages": [],
        "destructive": True,
        "est_seconds": 240,
    },
]


def _encode(value: object) -> object:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"cannot serialise {type(value).__name__}")


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def summary(dsn: str, snapshots: Path) -> dict[str, Any]:
    """The launch/refresh call: db state, migrations, row counts, quarantine.

    An unreachable database degrades to nulls rather than failing, so the app
    can render the reason instead of an empty window.
    """
    payload: dict[str, Any] = {"generated_at": _now()}
    try:
        with connect(dsn) as conn:
            payload["db"] = schema.database(conn, dsn)
            payload["migrations"] = schema.migrations(conn, MIGRATIONS_DIR)
            payload["data"] = schema.data_counts(conn)
            payload["runs"] = runs.totals(conn)
    except psycopg.Error as exc:
        payload["db"] = schema.unreachable(dsn, str(exc).strip())
        payload["migrations"] = schema.migrations(None, MIGRATIONS_DIR)
        payload["data"] = schema.empty_data_counts()
        payload["runs"] = runs.empty_totals()
    payload["quarantine"] = sources.quarantine(snapshots)
    return payload


def _with_conn(dsn: str, fn: Any) -> dict[str, Any]:
    conn: Conn
    with connect(dsn) as conn:
        result = fn(conn)
    return dict(result)


def _parser() -> argparse.ArgumentParser:
    # The global options are repeated on every subcommand so they are accepted
    # on either side of it; SUPPRESS keeps the unused copy from clobbering the
    # value given on the other side.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dsn", default=argparse.SUPPRESS)
    common.add_argument("--snapshots", type=Path, default=argparse.SUPPRESS)

    ap = argparse.ArgumentParser(prog="cdlhub_analytics.ops", description=__doc__, parents=[common])
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", parents=[common])

    runs_cmd = sub.add_parser("runs", parents=[common])
    runs_cmd.add_argument("--model")
    runs_cmd.add_argument("--limit", type=int, default=runs.DEFAULT_LIMIT)

    run_cmd = sub.add_parser("run", parents=[common])
    run_cmd.add_argument("id", type=int)

    history_cmd = sub.add_parser("history", parents=[common])
    history_cmd.add_argument("model")

    sub.add_parser("sources", parents=[common])
    sub.add_parser("pacing", parents=[common])
    sub.add_parser("quality", parents=[common])

    for name in ("backups", "backup", "restore"):
        command = sub.add_parser(name, parents=[common])
        command.add_argument("--backups-dir", type=Path, default=backups.DEFAULT_DIRECTORY)
        command.add_argument("--keep", type=int, default=backups.KEEP)
        if name == "backup":
            command.add_argument("--label")
        if name == "restore":
            command.add_argument("name")
    sub.add_parser("jobs", parents=[common])
    sub.add_parser("models", parents=[common])
    sub.add_parser("metric-diff", parents=[common])
    sub.add_parser("lineage", parents=[common])

    services_cmd = sub.add_parser("services", parents=[common])
    services_cmd.add_argument("--web-port", type=int, default=services.DEFAULT_WEB_PORT)

    # Bare, this lists the queue; with a decision it applies exactly one and
    # then lists what is left, so the caller needs no second call.
    identity_cmd = sub.add_parser("identity", parents=[common])
    decision = identity_cmd.add_mutually_exclusive_group()
    decision.add_argument("--merge", nargs=2, metavar=("SOURCE", "CANONICAL"))
    decision.add_argument("--keep-separate", nargs=2, metavar=("LEFT", "RIGHT"))

    artifact_cmd = sub.add_parser("artifact", parents=[common])
    artifact_cmd.add_argument("run_id", type=int)
    artifact_cmd.add_argument("name")
    return ap


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    dsn: str = getattr(args, "dsn", None) or os.environ.get("DATABASE_URL", DEFAULT_DSN)
    snapshots: Path = getattr(args, "snapshots", None) or SNAPSHOTS_DIR
    if args.command == "summary":
        return summary(dsn, snapshots)
    if args.command == "jobs":
        return {"jobs": JOBS}
    if args.command == "pacing":
        return pacing.report(snapshots)
    if args.command == "quality":
        return quality.report(snapshots)
    if args.command == "backups":
        return backups.report(dsn, args.backups_dir, args.keep)
    if args.command == "backup":
        return backups.create(dsn, args.backups_dir, args.label, args.keep)
    if args.command == "restore":
        return backups.restore(dsn, args.name, args.backups_dir, args.keep)
    if args.command == "services":
        return services.report(dsn, args.web_port)
    if args.command == "models":
        return _with_conn(dsn, models.report)
    if args.command == "metric-diff":
        return _with_conn(dsn, metricdiff.report)
    if args.command == "lineage":
        return _with_conn(dsn, lambda c: lineage.report(c, snapshots))
    if args.command == "runs":
        return _with_conn(dsn, lambda c: runs.listing(c, args.model, args.limit))
    if args.command == "run":
        return _with_conn(dsn, lambda c: runs.one(c, args.id))
    if args.command == "history":
        return _with_conn(dsn, lambda c: history(c, args.model))
    if args.command == "sources":
        return _with_conn(dsn, lambda c: sources.report(c, snapshots))
    if args.command == "identity":
        applied: dict[str, Any] | None = None
        if args.merge:
            applied = identity.merge(*args.merge)
        elif args.keep_separate:
            applied = identity.keep_separate(*args.keep_separate)
        return _with_conn(dsn, lambda c: identity.report(c, applied))
    if args.command == "artifact":
        return _with_conn(dsn, lambda c: runs.artifact(c, args.run_id, args.name))
    raise ValueError(f"unknown command {args.command!r}")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload = dispatch(args)
        json.dump(payload, sys.stdout, default=_encode)
        sys.stdout.write("\n")
    except BrokenPipeError:
        # The reader went away mid-write; nothing left to report to.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    except (psycopg.Error, LookupError, OSError, backups.BackupError) as exc:
        print(str(exc).strip() or type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
