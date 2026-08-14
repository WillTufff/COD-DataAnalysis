"""model_runs listing, detail and artifact payloads."""

from __future__ import annotations

from typing import Any, cast

from . import Conn, rows_as_dicts
from .backtests import cards, detail

# Every table keyed to model_runs whose rows are a run's output. backtests is
# excluded: it is the run's report card, reported under its own key.
OUTPUT_TABLES: tuple[str, ...] = (
    "career_curves",
    "insights",
    "model_artifacts",
    "player_career",
    "player_metric_season",
    "player_rapm",
    "player_season_adjusted",
    "player_skill",
    "player_style_season",
    "team_metric_season",
    "team_ratings",
    "team_season_effect",
)

DEFAULT_LIMIT = 200

_LIST_SQL = """
SELECT r.id, r.model, r.version, r.code_ref, r.data_through, r.created_at,
       EXISTS (SELECT 1 FROM model_runs o
                WHERE o.model = r.model AND (o.created_at, o.id) > (r.created_at, r.id))
         AS superseded
FROM model_runs r
WHERE %(model)s::text IS NULL OR r.model = %(model)s
ORDER BY r.created_at DESC, r.id DESC
LIMIT %(limit)s
"""


def outputs(conn: Conn, run_ids: list[int]) -> dict[int, dict[str, int]]:
    """Row counts per output table, one query per table over every run listed."""
    counts: dict[int, dict[str, int]] = {run_id: {} for run_id in run_ids}
    if not run_ids:
        return counts
    for table in OUTPUT_TABLES:
        sql = f"SELECT run_id, count(*) FROM {table} WHERE run_id = ANY(%s) GROUP BY run_id"  # noqa: S608
        for row in conn.execute(sql, (run_ids,)).fetchall():
            counts[cast(int, row[0])][table] = cast(int, row[1])
    return counts


def artifact_names(conn: Conn, run_ids: list[int]) -> dict[int, list[str]]:
    names: dict[int, list[str]] = {run_id: [] for run_id in run_ids}
    if not run_ids:
        return names
    rows = conn.execute(
        "SELECT run_id, name FROM model_artifacts WHERE run_id = ANY(%s) ORDER BY run_id, name",
        (run_ids,),
    ).fetchall()
    for row in rows:
        names[cast(int, row[0])].append(cast(str, row[1]))
    return names


def listing(conn: Conn, model: str | None = None, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    runs = rows_as_dicts(conn.execute(_LIST_SQL, {"model": model, "limit": limit}))
    ids = [cast(int, r["id"]) for r in runs]
    counts = outputs(conn, ids)
    names = artifact_names(conn, ids)
    backtests = cards(conn, ids)
    for run in runs:
        run_id = cast(int, run["id"])
        run["outputs"] = counts[run_id]
        run["backtest"] = backtests.get(run_id)
        run["artifacts"] = names[run_id]
    return {"runs": runs}


def totals(conn: Conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT count(*), count(DISTINCT model), max(created_at) FROM model_runs"
    ).fetchone()
    if row is None:
        return {"total": 0, "models": 0, "latest_created_at": None}
    return {
        "total": cast(int, row[0]),
        "models": cast(int, row[1]),
        "latest_created_at": row[2],
    }


def empty_totals() -> dict[str, Any]:
    return {"total": None, "models": None, "latest_created_at": None}


def one(conn: Conn, run_id: int) -> dict[str, Any]:
    rows = rows_as_dicts(
        conn.execute(
            "SELECT id, model, version, code_ref, params, data_through, created_at "
            "FROM model_runs WHERE id = %s",
            (run_id,),
        )
    )
    if not rows:
        raise LookupError(f"no model_runs row with id {run_id}")
    run = rows[0]
    run["outputs"] = outputs(conn, [run_id])[run_id]
    run["backtest"] = detail(conn, run_id)
    run["artifacts"] = rows_as_dicts(
        conn.execute(
            "SELECT name, octet_length(payload::text) AS bytes "
            "FROM model_artifacts WHERE run_id = %s ORDER BY name",
            (run_id,),
        )
    )
    return run


def artifact(conn: Conn, run_id: int, name: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = %s",
        (run_id, name),
    ).fetchone()
    if row is None:
        raise LookupError(f"run {run_id} has no artifact named {name!r}")
    return {"run_id": run_id, "name": name, "payload": row[0]}
