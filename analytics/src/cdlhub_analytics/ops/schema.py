"""Database reachability, size, migration state and headline row counts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import psycopg
from psycopg.conninfo import conninfo_to_dict

from . import Conn


def dsn_host(dsn: str) -> str:
    """host:port from a DSN in either URL or key=value form."""
    try:
        info = conninfo_to_dict(dsn)
    except psycopg.Error:
        return "unknown"
    host = str(info.get("host") or "localhost")
    port = str(info.get("port") or 5432)
    return f"{host}:{port}"


def database(conn: Conn, dsn: str) -> dict[str, Any]:
    row = conn.execute("SELECT pg_database_size(current_database())").fetchone()
    size = cast(int, row[0]) if row is not None else None
    return {"reachable": True, "dsn_host": dsn_host(dsn), "size_bytes": size}


def unreachable(dsn: str, error: str) -> dict[str, Any]:
    return {"reachable": False, "dsn_host": dsn_host(dsn), "size_bytes": None, "error": error}


def migrations(conn: Conn | None, migrations_dir: Path) -> dict[str, Any]:
    """Files in db/migrations against the rows migrate.sh recorded."""
    on_disk: list[str] = []
    if migrations_dir.is_dir():
        on_disk = sorted(p.name for p in migrations_dir.glob("*.sql"))
    if conn is None:
        return {"applied": 0, "on_disk": len(on_disk), "pending": on_disk}
    applied = {
        cast(str, r[0]) for r in conn.execute("SELECT filename FROM schema_migrations").fetchall()
    }
    return {
        "applied": len(applied),
        "on_disk": len(on_disk),
        "pending": [name for name in on_disk if name not in applied],
    }


_DATA_SQL = """
SELECT (SELECT count(*) FROM series
          WHERE team1_score IS NOT NULL AND team2_score IS NOT NULL) AS series_decided,
       (SELECT count(*) FROM game_player_stats)                      AS player_map_rows,
       (SELECT count(*) FROM events)                                 AS events,
       (SELECT max(played_at)::date FROM series)                     AS data_through
"""


def data_counts(conn: Conn) -> dict[str, Any]:
    row = conn.execute(_DATA_SQL).fetchone()
    if row is None:
        return {"series_decided": 0, "player_map_rows": 0, "events": 0, "data_through": None}
    series, player_maps, events, through = row
    return {
        "series_decided": cast(int, series),
        "player_map_rows": cast(int, player_maps),
        "events": cast(int, events),
        "data_through": through,
    }


def empty_data_counts() -> dict[str, Any]:
    return {
        "series_decided": None,
        "player_map_rows": None,
        "events": None,
        "data_through": None,
    }
