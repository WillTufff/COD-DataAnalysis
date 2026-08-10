"""Row counts by source and season, snapshot directory state, load reports."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import Conn, rows_as_dicts

_BY_SOURCE_SQL = """
SELECT s.data_source,
       count(DISTINCT s.id)   AS series,
       count(DISTINCT g.id)   AS games,
       count(gps.player_id)   AS player_map_rows,
       min(s.played_at)::date AS first_date,
       max(s.played_at)::date AS last_date
FROM series s
LEFT JOIN games g ON g.series_id = s.id
LEFT JOIN game_player_stats gps ON gps.game_id = g.id
GROUP BY s.data_source
ORDER BY s.data_source
"""

# completeness is the share of a season's maps carrying at least one stat line,
# the same measure the pipeline's coverage report publishes.
_BY_SEASON_SQL = """
SELECT se.year::text          AS season,
       t.short_name           AS title,
       count(DISTINCT s.id)   AS series,
       count(gps.player_id)   AS player_map_rows,
       array_agg(DISTINCT s.data_source) AS sources,
       round(count(DISTINCT g.id) FILTER (WHERE gps.player_id IS NOT NULL)::numeric
             / NULLIF(count(DISTINCT g.id), 0), 3) AS completeness
FROM seasons se
JOIN titles t  ON t.id = se.title_id
JOIN events e  ON e.season_id = se.id
JOIN series s  ON s.event_id = e.id
LEFT JOIN games g ON g.series_id = s.id
LEFT JOIN game_player_stats gps ON gps.game_id = g.id
GROUP BY se.year, t.short_name
ORDER BY se.year
"""


def by_source(conn: Conn) -> list[dict[str, Any]]:
    return rows_as_dicts(conn.execute(_BY_SOURCE_SQL))


def by_season(conn: Conn) -> list[dict[str, Any]]:
    return rows_as_dicts(conn.execute(_BY_SEASON_SQL))


def snapshot_dirs(root: Path) -> list[dict[str, Any]]:
    """File count, total size and newest mtime for every directory holding files."""
    if not root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    _scan(root, root, entries)
    return sorted(entries, key=lambda e: str(e["name"]))


def _scan(path: Path, root: Path, out: list[dict[str, Any]]) -> None:
    files = 0
    total = 0
    newest = 0.0
    subdirs: list[Path] = []
    with os.scandir(path) as it:
        for entry in it:
            if entry.is_dir(follow_symlinks=False):
                subdirs.append(Path(entry.path))
                continue
            stat = entry.stat(follow_symlinks=False)
            files += 1
            total += stat.st_size
            newest = max(newest, stat.st_mtime)
    if files:
        out.append(
            {
                "name": path.relative_to(root).as_posix() or ".",
                "files": files,
                "bytes": total,
                "newest_mtime": datetime.fromtimestamp(newest, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    for sub in subdirs:
        _scan(sub, root, out)


def _read_report(root: Path, source: str) -> dict[str, Any]:
    path = root / source / "load-report.json"
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _count(value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, list | dict):
        return len(value)
    return 0


def quarantine(root: Path) -> dict[str, Any]:
    """Cito's refused records, grouped by the leading clause of the reason."""
    report = _read_report(root, "cito")
    by_reason: dict[str, int] = {}
    records = report.get("quarantined", [])
    if isinstance(records, list):
        for record in records:
            reason = "unknown"
            if isinstance(record, dict):
                reason = str(record.get("reason", "unknown")).split(":")[0].strip()
            by_reason[reason] = by_reason.get(reason, 0) + 1
    return {"total": sum(by_reason.values()), "by_reason": by_reason}


def load_reports(root: Path) -> dict[str, dict[str, Any]]:
    cito = _read_report(root, "cito")
    lpdb = _read_report(root, "lpdb")
    cito_q = quarantine(root)
    return {
        "cito": {
            "loaded": _count(cito.get("series_kept")),
            "skipped": _count(cito.get("deduped")),
            "quarantined": cito_q["total"],
            "by_reason": cito_q["by_reason"],
        },
        "lpdb": {
            "loaded": _count(lpdb.get("stints_loaded")) + _count(lpdb.get("transfers_loaded")),
            "skipped": _count(lpdb.get("skipped_tournaments")),
            "quarantined": _count(lpdb.get("unparseable_placements")),
            "by_reason": {},
        },
    }


def report(conn: Conn, snapshots: Path) -> dict[str, Any]:
    return {
        "by_source": by_source(conn),
        "by_season": by_season(conn),
        "snapshots": snapshot_dirs(snapshots),
        "load_reports": load_reports(snapshots),
    }
