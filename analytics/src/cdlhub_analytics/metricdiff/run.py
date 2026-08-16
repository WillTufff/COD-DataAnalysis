"""Take a snapshot, compare it to the one before it, store the report."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import psycopg

from .. import artifacts
from . import ATOL, MODEL, RTOL, VERSION, compare, evalpop, snapshot

Conn = psycopg.Connection[tuple[object, ...]]

# The two payloads this stage stores against its run.
REPORT_ARTIFACT = "metric_diff"
POPULATION_ARTIFACT = "evaluation_population"

# The snapshot's name while the transaction that produced it is still open, and
# the key the report carries it under until `publish` renames it.
PENDING_SUFFIX = artifacts.PARTIAL_SUFFIX
PENDING_KEY = "pending_snapshot"


def data_through(conn: Conn) -> date:
    """The newest data date any model has published, so the diff run sorts with
    the runs it describes."""
    row = conn.execute("SELECT max(data_through) FROM model_runs").fetchone()
    if row is None or row[0] is None:
        return date.today()
    return cast(date, row[0])


def execute(conn: Conn, rtol: float = RTOL, atol: float = ATOL) -> dict[str, Any]:
    """Snapshot, compare, store. Returns the report payload."""
    from ..writeback import open_run

    # Read before writing: `open_run` replaces a same-day rerun, and the
    # baseline a rerun needs is the snapshot that rerun is replacing.
    baseline = artifacts.latest_snapshot()

    # Written under a name `latest_snapshot` does not match, and renamed by
    # `publish` once the caller has committed. A rolled-back run must not leave
    # behind the baseline the next one would compare against.
    final_path = artifacts.new_snapshot_path()
    current_path = final_path.with_name(final_path.name + PENDING_SUFFIX)
    current_header = snapshot.write(current_path, conn)

    if baseline is None:
        report = compare.unavailable(
            "no earlier snapshot to compare against",
            current_header,
            artifacts.relative(final_path),
        )
    else:
        baseline_header = snapshot.header_of(baseline)
        merged = compare.merge(
            snapshot.read(baseline), snapshot.read(current_path), rtol=rtol, atol=atol
        )
        report = compare.payload(
            merged,
            baseline_header,
            current_header,
            artifacts.relative(baseline),
            artifacts.relative(final_path),
            rtol=rtol,
            atol=atol,
        )
    report["snapshot_path"] = artifacts.relative(final_path)

    population = evalpop.drift(conn)

    run_id = open_run(
        conn,
        MODEL,
        VERSION,
        {"rtol": rtol, "atol": atol, "snapshot_keep": artifacts.KEEP},
        data_through(conn),
    )
    for name, payload in ((REPORT_ARTIFACT, report), (POPULATION_ARTIFACT, population)):
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            # allow_nan=False: `jsonb` rejects Infinity and NaN, and it rejects
            # them at the end of a run that has already done all its work.
            (run_id, name, json.dumps(payload, allow_nan=False)),
        )

    # Reports older than the snapshots they describe are unreadable, so the run
    # rows are kept to the same depth as the files.
    conn.execute(
        "DELETE FROM model_runs WHERE model = %s AND id NOT IN "
        "(SELECT id FROM model_runs WHERE model = %s ORDER BY created_at DESC, id DESC LIMIT %s)",
        (MODEL, MODEL, artifacts.KEEP),
    )

    report["run_id"] = run_id
    report["population"] = population
    report[PENDING_KEY] = str(current_path)
    return report


def publish(report: dict[str, Any]) -> int:
    """Make this run's snapshot the baseline for the next one.

    Called after the caller commits: until then the snapshot sits under a name
    nothing looks for. Returns how many older snapshots were pruned.
    """
    pending = report.pop(PENDING_KEY, None)
    if pending is None:
        return 0
    path = Path(pending)
    path.replace(path.with_name(path.name.removesuffix(PENDING_SUFFIX)))
    pruned = artifacts.prune_snapshots()
    report["snapshots_pruned"] = pruned
    return pruned


def headline(report: dict[str, Any]) -> str:
    """One line for the run log."""
    current = report["current"]
    if not report["available"]:
        return (
            f"metric_diff run {report['run_id']}: baseline captured, "
            f"{current['n_entries']:,} published numbers ({report['reason']})"
        )
    totals = report["totals"]
    line = (
        f"metric_diff run {report['run_id']}: {totals['compared']:,} numbers compared, "
        f"{totals['moved']:,} moved, {totals['flipped']:,} flipped, "
        f"{totals['added']:,} new, {totals['removed']:,} gone"
    )
    reset = report.get("baseline_reset", {})
    if reset.get("reset"):
        gained = ", ".join(str(y) for y in reset["seasons_gained"]) or "none"
        lost = ", ".join(str(y) for y in reset["seasons_lost"]) or "none"
        line += (
            f"\n  baseline reset, not a regression: seasons gained {gained}, lost {lost}. "
            "Every number is standardized inside its own season's cohort, so every season "
            "that changed membership moved without any model changing."
        )
    return line


def population_line(population: dict[str, Any]) -> str:
    if not population.get("frozen"):
        return f"  evaluation population: {population.get('reason', 'not frozen')}"
    if not population.get("readable", False):
        return f"  evaluation population {population['cut']}: {population.get('reason')}"
    if population["matches"]:
        return (
            f"  evaluation population {population['cut']}: {population['n_maps']:,} maps, unchanged"
        )
    return (
        f"  evaluation population {population['cut']}: {population['n_maps']:,} maps frozen, "
        f"{population['eligible_now']:,} eligible now "
        f"(+{population['n_added']:,}/-{population['n_removed']:,}, not applied)"
    )
