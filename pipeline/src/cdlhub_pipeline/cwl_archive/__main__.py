"""CLI: import the CWL 2017-2019 archive into Postgres.

    uv run python -m cdlhub_pipeline.cwl_archive [--reset] [--dir PATH] [--dsn DSN]

--reset truncates all competition data first (dev convenience; the default is
an idempotent natural-key upsert on top of whatever is present).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import psycopg

from .load import SOURCE, Loader, derive_roster_stints
from .parse import Aliases, canonical_handles, parse_archive

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"
_RESET_TABLES = (
    "game_player_stats, event_placements, games, series, stages, events, seasons, "
    "roster_stints, player_aliases, players, teams, orgs, maps, game_modes, titles"
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    ap.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "snapshots" / "cwl-archive",
    )
    ap.add_argument("--reset", action="store_true", help="truncate competition data first")
    args = ap.parse_args(argv)

    aliases = Aliases.load()
    events = parse_archive(args.dir, aliases)
    n_lines = sum(len(pe.lines) for pe in events)
    print(f"parsed {len(events)} events, {n_lines} stat lines")

    # Canonical casing across the whole archive, alias-map overrides included.
    canon = canonical_handles(events)
    spellings: dict[str, set[str]] = {}
    for pe in events:
        for ln in pe.lines:
            spellings.setdefault(ln.player.lower(), set()).add(ln.player)

    with psycopg.connect(args.dsn) as conn:
        if args.reset:
            conn.execute(
                f"TRUNCATE {_RESET_TABLES} RESTART IDENTITY CASCADE"  # noqa: S608
            )
            print("reset: competition tables truncated")
        loader = Loader(conn)
        player_ids = {low: loader.player_id(canon[low], spellings[low]) for low in sorted(canon)}
        for pe in events:
            loader.load_event(pe, player_ids)
            print(f"  {pe.event.slug}: loaded")
        derive_roster_stints(events, player_ids, loader)

        # Every archive game must carry its structured-feed id (source_uid), or
        # the events importer has nothing to join kill feeds onto. The loader is
        # idempotent, so a plain re-run backfills existing rows; assert the
        # result rather than trusting it. Uniqueness is held by the column
        # constraint — a duplicate would have already failed the INSERT.
        missing = conn.execute("SELECT count(*) FROM games WHERE source_uid IS NULL").fetchone()
        assert missing is not None
        if missing[0]:
            raise SystemExit(f"source_uid backfill incomplete: {missing[0]} games missing a uid")

        counts = dict(sorted(loader.counts.items()))
        conn.execute(
            "INSERT INTO ingest_runs (kind, params, status, rows_upserted) "
            "VALUES (%s, %s, 'success', %s)",
            (SOURCE, json.dumps({"reset": args.reset}), json.dumps(counts)),
        )
        conn.commit()
    print("upserted:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
