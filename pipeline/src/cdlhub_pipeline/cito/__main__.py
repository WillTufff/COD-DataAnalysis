"""CLI: python -m cdlhub_pipeline.cito {scope,backfill,load} [options]"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from ..identity import Aliases
from . import backfill, scope
from .client import SNAPSHOT_ROOT
from .load import SOURCE, load
from .transform import transform

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"
REPORT_PATH = SNAPSHOT_ROOT / "load-report.json"


def run_load(dsn: str) -> None:
    aliases = Aliases.load()
    result = transform(aliases)
    with_stats = sum(1 for s in result.series if s.games)
    print(
        f"transform: {len(result.series)} series kept "
        f"({with_stats} with stats), {len(result.dropped)} deduped away"
    )
    if result.unmapped_slugs:
        print("UNMAPPED team slugs (add to aliases.json cito_teams):")
        for slug, n in sorted(result.unmapped_slugs.items()):
            print(f"  {slug}: {n} matches")
    for q in result.quarantined:
        print(f"  quarantined: {q['match_id']}: {q['reason']}")
    if result.inconsistent:
        print(
            f"  {len(result.inconsistent)} series with map results contradicting the "
            "series score (map winners nulled, stat lines kept)"
        )

    with psycopg.connect(dsn) as conn:
        counts = load(conn, result)
        conn.execute(
            "INSERT INTO ingest_runs (kind, params, status, rows_upserted) "
            "VALUES (%s, %s, 'success', %s)",
            (SOURCE, json.dumps({}), json.dumps(counts)),
        )
        conn.commit()
    print("upserted:", counts)

    for w in result.warnings:
        print(f"  warn: {w}")
    report = {
        "series_kept": len(result.series),
        "series_with_stats": with_stats,
        "deduped": result.dropped,
        "quarantined": result.quarantined,
        "inconsistent_map_results": result.inconsistent,
        "warnings": result.warnings,
        "unmapped_slugs": result.unmapped_slugs,
        "rows_upserted": counts,
    }
    REPORT_PATH.write_text(json.dumps(report, indent=1))
    print(f"report: {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="cdlhub_pipeline.cito")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scope", help="build classifier + enumerate CDL-pro matches")
    p_backfill = sub.add_parser("backfill", help="snapshot player-stats for scoped matches")
    p_backfill.add_argument("--max-calls", type=int, default=None)
    p_load = sub.add_parser("load", help="transform snapshots and load into Postgres")
    p_load.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    args = parser.parse_args()
    if args.command == "scope":
        scope.run()
    elif args.command == "backfill":
        backfill.run(max_calls=args.max_calls)
    else:
        run_load(args.dsn)


if __name__ == "__main__":
    main()
