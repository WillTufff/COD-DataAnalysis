"""Versioned model-output writeback.

Every model writes through a model_runs row. Runs are immutable in place: a
rerun with the same (model, version, data_through) *replaces* the whole run
(cascade-deletes its outputs, reuses the id); anything else creates a new run.

A version bump therefore leaves the old run behind, which is right for the
rating versions that are kept as backtest baselines and wrong for everything
else — the site only ever reads the newest run of a model, so superseded
insights and metric-layer runs are dead rows that grow without bound.
`prune_superseded` closes that off: whatever a run produced for a model is what
survives for that model. Models the run never touched are left alone, so the
pipeline's own runs (kill-feed reconciliation) are not collateral.
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


def prune_superseded(
    conn: psycopg.Connection[tuple[object, ...]],
    produced: dict[str, list[int]],
) -> dict[str, int]:
    """Drop runs of each produced model that this run did not produce.

    `produced` maps a model name to every run id the current run wrote for it —
    several for player_rating, whose feature-set versions are all published and
    compared. Returns the delete count per model, for logging. Outputs go with
    the run via ON DELETE CASCADE.
    """
    removed: dict[str, int] = {}
    for model, keep in produced.items():
        if not keep:  # never prune a model down to nothing
            continue
        cur = conn.execute(
            "DELETE FROM model_runs WHERE model = %s AND NOT (id = ANY(%s))",
            (model, list(keep)),
        )
        if cur.rowcount:
            removed[model] = cur.rowcount
    return removed


def latest_run_id(conn: psycopg.Connection[tuple[object, ...]], model: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM model_runs WHERE model = %s ORDER BY created_at DESC, id DESC LIMIT 1",
        (model,),
    ).fetchone()
    return None if row is None else cast(int, row[0])
