"""The career-rank evaluation population, standalone.

    uv run python -m cdlhub_analytics.career_rank --freeze CUT
    uv run python -m cdlhub_analytics.career_rank --population

`--freeze` cuts a new population under the given label and makes it the frozen
one; the label it replaces stays on record in `supersedes` and `history`.
`--population` reports how far the eligible set has drifted from the frozen one
without changing anything. The engine itself runs inside `run_all`.
"""

from __future__ import annotations

import argparse
import sys

from ..db import connect
from . import evalpop


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="cdlhub_analytics.career_rank", description=__doc__)
    ap.add_argument("--dsn", default=None)
    ap.add_argument("--freeze", metavar="CUT", help="cut a new evaluation population")
    ap.add_argument(
        "--population", action="store_true", help="report evaluation-population drift only"
    )
    args = ap.parse_args(argv)
    if not args.freeze and not args.population:
        ap.error("one of --freeze or --population is required")

    with connect(args.dsn) as conn:
        if args.freeze:
            pointer = evalpop.freeze(conn, args.freeze)
            print(
                f"career population '{pointer['cut']}' frozen: "
                f"{pointer['n_players']:,} players, sha256 {pointer['sha256'][:16]}"
            )
            if pointer["supersedes"]:
                print(f"  supersedes {pointer['supersedes']}")
            for seasons, count in pointer["season_count_histogram"].items():
                print(f"  {seasons} seasons: {count:,}")
            return 0

        print(population_line(evalpop.drift(conn)))
    return 0


def population_line(drift: dict[str, object]) -> str:
    """One line: which cut, and whether the eligible set still matches it."""
    if not drift.get("frozen"):
        return f"career population: {drift.get('reason')}"
    if not drift.get("readable", True):
        return f"career population '{drift.get('cut')}': {drift.get('reason')}"
    head = f"career population '{drift['cut']}': {drift['n_players']:,} players"
    if drift.get("matches"):
        return f"{head}, matches"
    return (
        f"{head}, eligible now {drift['eligible_now']:,} "
        f"(+{drift['n_added']:,} / -{drift['n_removed']:,}, not applied)"
    )


if __name__ == "__main__":
    sys.exit(main())
