"""CLI: python -m cdlhub_pipeline.lpdb {probe,pull,load} [options]"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from . import pull
from .client import SNAPSHOT_ROOT, LpdbClient
from .load import SOURCE, load

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"
REPORT_PATH = SNAPSHOT_ROOT / "load-report.json"


def run_load(dsn: str) -> None:
    with psycopg.connect(dsn) as conn:
        counts, report = load(conn)
        conn.execute(
            "INSERT INTO ingest_runs (kind, params, status, rows_upserted) "
            "VALUES (%s, %s, 'success', %s)",
            (SOURCE, json.dumps({}), json.dumps(counts)),
        )
        conn.commit()
    print("upserted:", counts)
    print(
        f"events matched: {len(set(report['matched_events'].values()))} "
        f"({len(report['matched_events'])} LPDB pages); "
        f"skipped: {len(report['skipped_tournaments'])}; "
        f"unmatched tournaments: {len(report['unmatched_tournaments'])}; "
        f"teams created: {report['teams_created']}; "
        f"teams updated: {len(report['teams_updated'])} "
        f"(+{len(report['teams_region_from_lineage'])} region via lineage)"
    )
    print(
        f"events enriched: {len(report['events_enriched'])} "
        f"(tournament pages unmatched: {len(report['tournaments_unmatched'])}); "
        f"stints: {report['stints_loaded']} "
        f"(+{len(report['stint_players_created'])} players created, "
        f"{len(report['stints_skipped'])} skipped); "
        f"transfers: {report['transfers_loaded']} "
        f"({report['transfers_unresolved']} unresolved, "
        f"{report['transfers_noncompetitive']} non-competitive dropped); "
        f"orphan players removed: {len(report['orphan_players_removed'])}; "
        f"bios: {len(report['bios_updated'])} "
        f"({len(report['players_without_bio'])} players without bio)"
    )
    fix = report.get("series_fix")
    if fix:
        print(
            f"series fixes: backfilled {len(fix['backfilled'])}; "
            f"nulled maps filled {len(fix['nulled_filled'])}; "
            f"score-only games added {len(fix['scoreonly_games_added'])}; "
            f"score agree/disagree {fix['score_agreements']}"
            f"/{len(fix['score_disagreements'])}; "
            f"unmatched {len(fix['unmatched'])}; "
            f"lpdb-map-inconsistent {len(fix['inconsistent_lpdb_maps'])}"
        )
    REPORT_PATH.write_text(json.dumps(report, indent=1))
    print(f"report: {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="cdlhub_pipeline.lpdb")
    sub = parser.add_subparsers(dest="command", required=True)
    p_probe = sub.add_parser("probe", help="discover a table's columns with limit=1")
    p_probe.add_argument("table")
    p_pull = sub.add_parser("pull", help="pull LPDB tables to snapshots")
    p_pull.add_argument(
        "tables", nargs="*", metavar="table", help=f"default: all of {list(pull.PULLS)}"
    )
    p_load = sub.add_parser("load", help="load pulled snapshots into Postgres")
    p_load.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    args = parser.parse_args()
    if args.command == "probe":
        print(json.dumps(LpdbClient().probe(args.table), indent=1))
    elif args.command == "pull":
        pull.run(args.tables or None)
    else:
        run_load(args.dsn)


if __name__ == "__main__":
    main()
