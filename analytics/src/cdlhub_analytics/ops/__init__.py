"""JSON views of the database and the pipeline's on-disk state.

Every command prints one JSON object to stdout; errors go to stderr with a
non-zero exit. Pipeline state is read from the JSON files the pipeline writes to
disk, so nothing here imports cdlhub_pipeline.

Everything reads except the identity decisions, whose one write target is
`aliases.json` — the file every load replays. Nothing here writes to the
database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import psycopg

REPO_ROOT = Path(__file__).resolve().parents[4]
SNAPSHOTS_DIR = REPO_ROOT / "pipeline" / "snapshots"
MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
ALIASES_PATH = REPO_ROOT / "pipeline" / "src" / "cdlhub_pipeline" / "aliases.json"

Conn = psycopg.Connection[tuple[object, ...]]


def rows_as_dicts(cur: psycopg.Cursor[tuple[object, ...]]) -> list[dict[str, Any]]:
    """Column-named rows, so a query's SELECT list is the JSON shape."""
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
