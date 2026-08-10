"""Weekly top-up: re-scope, snapshot what is new, reload both sources.

    uv run python -m cdlhub_pipeline.refresh [--dsn DSN] [--max-calls N]
                                             [--skip-lpdb] [--dry-run]

Every step is idempotent, so this is safe to run on a timer during a season and
safe to re-run after a failure. The order is fixed and the reason is the cito
wipe: reloading cito re-nulls the map winners that the LPDB series fixes filled,
so `lpdb load` always runs after `cito load`.

The check this exists for is the quarantine count. The cdl catalog attaches
another match's player-stats payload to some current-season fixtures; the
transform quarantines those automatically, which means a source that starts
doing it more often does not fail anything — it just quietly thins stats
coverage for the running season. So the counts are compared against the previous
run's report and a growth is reported as a warning with a non-zero exit, rather
than being left for someone to notice in a dashboard months later.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass

import psycopg

from . import events
from .cito import backfill as cito_backfill
from .cito import scope as cito_scope
from .cito.__main__ import REPORT_PATH as CITO_REPORT
from .cito.__main__ import run_load as cito_run_load
from .lpdb import pull as lpdb_pull
from .lpdb.__main__ import run_load as lpdb_run_load

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"


@dataclass
class Quarantine:
    """What the cito transform refused to load, by reason."""

    total: int
    by_reason: dict[str, int]

    @classmethod
    def read(cls) -> Quarantine:
        if not CITO_REPORT.exists():
            return cls(0, {})
        report = json.loads(CITO_REPORT.read_text())
        by_reason: dict[str, int] = {}
        for q in report.get("quarantined", []):
            # The reason carries the match id in some cases; the leading clause
            # is the class of problem, which is what a trend is about.
            reason = str(q.get("reason", "unknown")).split(":")[0].strip()
            by_reason[reason] = by_reason.get(reason, 0) + 1
        return cls(sum(by_reason.values()), by_reason)


def _series_by_season(dsn: str) -> dict[int, int]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT se.year, count(*) FROM series s "
            "JOIN events e ON e.id = s.event_id "
            "JOIN seasons se ON se.id = e.season_id "
            "GROUP BY se.year ORDER BY se.year"
        ).fetchall()
    return {int(y): int(n) for y, n in rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cdlhub_pipeline.refresh", description=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    ap.add_argument(
        "--max-calls",
        type=int,
        default=None,
        help="cap the cito player-stats calls this run (quota guard)",
    )
    ap.add_argument("--skip-lpdb", action="store_true", help="cito only; skips the LPDB refresh")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="re-scope and report what is missing locally, then stop before any load",
    )
    events.add_argument(ap)
    args = ap.parse_args(argv)
    progress = events.StageEvents(args.events == "ndjson")

    before_q = Quarantine.read()
    before_series = _series_by_season(args.dsn)

    progress.stage("cito scope")
    print("== cito scope ==")
    cito_scope.run()

    progress.stage("cito backfill")
    print("\n== cito backfill ==")
    cito_backfill.run(max_calls=args.max_calls)

    if args.dry_run:
        progress.done()
        print("\ndry run: snapshots are current, stopping before load")
        return 0

    progress.stage("cito load")
    print("\n== cito load ==")
    cito_run_load(args.dsn)

    if not args.skip_lpdb:
        progress.stage("lpdb pull")
        print("\n== lpdb pull ==")
        lpdb_pull.run(None)
        # Always after the cito load: the cito reload re-nulls the map winners
        # the series fixes filled, and this pass re-fills them.
        progress.stage("lpdb load")
        print("\n== lpdb load ==")
        lpdb_run_load(args.dsn)
    progress.done()

    after_q = Quarantine.read()
    after_series = _series_by_season(args.dsn)

    print("\n== refresh summary ==")
    added = {
        year: after_series[year] - before_series.get(year, 0)
        for year in sorted(after_series)
        if after_series[year] != before_series.get(year, 0)
    }
    print(f"series by season: {added or 'no change'}")

    grown: dict[str, tuple[int, int]] = {
        reason: (before_q.by_reason.get(reason, 0), n)
        for reason, n in after_q.by_reason.items()
        if n > before_q.by_reason.get(reason, 0)
    }
    print(f"quarantined: {before_q.total} -> {after_q.total}")
    if grown:
        for reason, (was, now) in sorted(grown.items()):
            print(f"  WARNING: '{reason}' grew {was} -> {now}")
        print(
            "\nQuarantined records carry no stat lines, so a rise here thins "
            "coverage for the running season without failing anything. Check "
            f"{CITO_REPORT} before trusting current-season stats."
        )
        return 1
    print("no new quarantine classes; stats coverage held")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
