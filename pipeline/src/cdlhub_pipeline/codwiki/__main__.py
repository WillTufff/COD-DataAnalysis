"""CLI: python -m cdlhub_pipeline.codwiki {fields,pull,load,reconcile} [options]"""

from __future__ import annotations

import argparse
import json
import os

import psycopg

from ..identity import Aliases
from . import identity, pull, reconcile, results, transform
from . import load as load_module
from .client import SNAPSHOT_ROOT, CodWikiClient

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"
REPORT_PATH = SNAPSHOT_ROOT / "load-report.json"
RECON_PATH = SNAPSHOT_ROOT / "overlap-reconciliation.json"
RESULTS_PATH = SNAPSHOT_ROOT / "results-report.json"


def run_results(dsn: str, dry_run: bool) -> None:
    aliases = Aliases.load()
    with psycopg.connect(dsn) as conn:
        report = results.load(conn, aliases)
        if dry_run:
            conn.rollback()
            print("dry run: rolled back")
        else:
            conn.execute(
                "INSERT INTO ingest_runs (kind, params, status, rows_upserted) "
                "VALUES (%s, %s, 'success', %s)",
                (f"{transform.SOURCE}_results", json.dumps({}), json.dumps(report["counts"])),
            )
            conn.commit()
    RESULTS_PATH.write_text(json.dumps(report, indent=1))
    print("scope:", report["scope"])
    print("counts:", report["counts"])
    print("skipped:", report["skipped"])
    print("collisions:", len(report["collisions"]))
    print("unresolved players:", len(report["unresolved_players"]))
    print(f"report: {RESULTS_PATH}")


def run_reconcile(dsn: str, store: bool) -> None:
    with psycopg.connect(dsn) as conn:
        result = reconcile.payload(conn)
        if store:
            run_id = reconcile.store(conn, result)
            conn.commit()
            print(f"stored as model_runs #{run_id} ({reconcile.MODEL})")
        else:
            conn.rollback()
    RECON_PATH.write_text(json.dumps(result, indent=1, default=str))
    print("maps:", result["maps"])
    print("player-maps:", result["player_maps"])
    print("series winners:", {k: v for k, v in result["series_winners"].items() if k != "rule"})
    print("placements:", {k: v for k, v in result["placements"].items() if k != "mismatches"})
    print(f"report: {RECON_PATH}")


def run_load(dsn: str, dry_run: bool) -> None:
    # Identity resolves over rows a load could use. A page seen only on rows
    # with no kill count would otherwise earn a player row carrying no stats.
    rows = [
        row
        for row in json.loads(transform.rows_path().read_text())
        if (row.get("PlayerName") or "").strip() and (row.get("Kills") or "") != ""
    ]
    aliases = Aliases.load()
    overrides = json.loads(
        (SNAPSHOT_ROOT.parents[1] / "src" / "cdlhub_pipeline" / "aliases.json").read_text()
    ).get("codwiki_players", {})
    with psycopg.connect(dsn) as conn:
        player_ids, id_report = identity.resolve(conn, rows, overrides)
        result = transform.transform(player_ids)
        report = load_module.load(conn, result, aliases)
        if dry_run:
            conn.rollback()
            print("dry run: rolled back")
        else:
            conn.execute(
                "INSERT INTO ingest_runs (kind, params, status, rows_upserted) "
                "VALUES (%s, %s, 'success', %s)",
                (transform.SOURCE, json.dumps({}), json.dumps(report["counts"])),
            )
            conn.commit()
    report["identity"] = id_report
    report["quarantine"] = result.quarantine
    REPORT_PATH.write_text(json.dumps(report, indent=1))
    print("identity:", {k: v if not isinstance(v, list) else len(v) for k, v in id_report.items()})
    print("counts:", report["counts"])
    print("dropped rows:", report["dropped"])
    print("collisions:", len(report["collisions"]))
    print(f"report: {REPORT_PATH}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="cdlhub_pipeline.codwiki")
    sub = parser.add_subparsers(dest="command", required=True)
    p_fields = sub.add_parser("fields", help="read a Cargo table's declared fields")
    p_fields.add_argument("table")
    p_pull = sub.add_parser("pull", help="pull Cargo tables to snapshots")
    p_pull.add_argument("target", choices=["reference", *pull.WINDOWS])
    p_pull.add_argument(
        "tables",
        nargs="*",
        metavar="table",
        help=f"reference only; default: all of {list(pull.REFERENCE_TABLES)}",
    )
    p_load = sub.add_parser("load", help="load the 2013-2016 window into Postgres")
    p_load.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    p_load.add_argument("--dry-run", action="store_true", help="transform and roll back")
    p_results = sub.add_parser("results", help="load 2013-2016 placements, rosters and awards")
    p_results.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    p_results.add_argument("--dry-run", action="store_true", help="load and roll back")
    p_recon = sub.add_parser("reconcile", help="check the 2017-2026 overlap against what we hold")
    p_recon.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    p_recon.add_argument("--store", action="store_true", help="write the model_artifacts row")
    args = parser.parse_args()
    if args.command == "fields":
        print(json.dumps(CodWikiClient().table_fields(args.table), indent=1))
    elif args.command == "pull":
        pull.run(args.target, args.tables or None)
    elif args.command == "reconcile":
        run_reconcile(args.dsn, args.store)
    elif args.command == "results":
        run_results(args.dsn, args.dry_run)
    else:
        run_load(args.dsn, args.dry_run)


if __name__ == "__main__":
    main()
