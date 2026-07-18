"""Versioned model-output writeback.

Every model writes through a model_runs row. Runs are immutable in place: a
rerun with the same (model, version, data_through) *replaces* the whole run
(cascade-deletes its outputs, reuses the id); anything else creates a new run.
"""

from __future__ import annotations

import json
import subprocess
from datetime import date
from typing import Any, cast

import psycopg


def git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def open_run(
    conn: psycopg.Connection[tuple[object, ...]],
    model: str,
    version: str,
    params: dict[str, Any],
    data_through: date,
) -> int:
    row = conn.execute(
        "SELECT id FROM model_runs WHERE model = %s AND version = %s AND data_through = %s",
        (model, version, data_through),
    ).fetchone()
    if row is not None:
        run_id = cast(int, row[0])
        # Replace the run wholesale: cascade clears child tables.
        conn.execute("DELETE FROM model_runs WHERE id = %s", (run_id,))
    new = conn.execute(
        "INSERT INTO model_runs (model, version, code_ref, params, data_through) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (model, version, git_sha(), json.dumps(params), data_through),
    ).fetchone()
    assert new is not None
    return cast(int, new[0])


def latest_run_id(conn: psycopg.Connection[tuple[object, ...]], model: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM model_runs WHERE model = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (model,),
    ).fetchone()
    return None if row is None else cast(int, row[0])
