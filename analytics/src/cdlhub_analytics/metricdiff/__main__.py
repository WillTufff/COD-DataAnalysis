"""The metric-diff harness, standalone.

    uv run python -m cdlhub_analytics.metricdiff [--dsn DSN]
    uv run python -m cdlhub_analytics.metricdiff --freeze CUT
    uv run python -m cdlhub_analytics.metricdiff --population

Bare, it snapshots the published surface, compares it to the previous snapshot
and stores the report — the same thing `run_all`'s last stage does, for use
between runs. `--freeze` cuts a new evaluation population under the given label
and makes it the frozen one. `--population` reports how far the eligible set has
drifted from the frozen one without changing anything.
"""

from __future__ import annotations

import argparse
import sys

from ..db import connect
from ..events import StageEvents
from ..events import add_argument as add_events_argument
from . import ATOL, RTOL, evalpop, run


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cdlhub_analytics.metricdiff", description=__doc__)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--rtol", type=float, default=RTOL)
    ap.add_argument("--atol", type=float, default=ATOL)
    ap.add_argument("--freeze", metavar="CUT", help="cut a new evaluation population")
    ap.add_argument(
        "--population", action="store_true", help="report evaluation-population drift only"
    )
    add_events_argument(ap)
    args = ap.parse_args(argv)
    progress = StageEvents(args.events == "ndjson")

    with connect(args.dsn) as conn:
        if args.freeze:
            pointer = evalpop.freeze(conn, args.freeze)
            print(
                f"evaluation population '{pointer['cut']}' frozen: "
                f"{pointer['n_maps']:,} maps, sha256 {pointer['sha256'][:16]}"
            )
            for season, count in pointer["by_season"].items():
                print(f"  {season}: {count:,}")
            return 0

        if args.population:
            print(run.population_line(evalpop.drift(conn)).strip())
            return 0

        progress.stage("metric_diff")
        report = run.execute(conn, rtol=args.rtol, atol=args.atol)
        progress.done()
        conn.commit()
        run.publish(report)

    print(run.headline(report))
    print(run.population_line(report["population"]))
    for family in report["families"]:
        if family["moved"] or family["flipped"] or family["added"] or family["removed"]:
            print(
                f"  {family['family']:16s} {family['moved']:>8,} moved  "
                f"{family['flipped']:>6,} flipped  "
                f"{family['added']:>6,} new  {family['removed']:>6,} gone  "
                f"max |delta| {family['max_abs_delta']:g}"
            )
    for mover in report["movers"][:20]:
        print(f"  {mover['key']}: {mover['old']:g} -> {mover['new']:g} ({mover['delta']:+g})")
    if report["movers_omitted"]:
        print(f"  ... and {report['movers_omitted']:,} more moves not named here")
    return 0


if __name__ == "__main__":
    sys.exit(main())
