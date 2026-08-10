"""Report cards, calibration bins and per-model metric history."""

from __future__ import annotations

from typing import Any, cast

from . import Conn, rows_as_dicts

_CARD_SQL = """
SELECT run_id, brier, log_loss, accuracy, n_predictions
FROM backtests WHERE run_id = ANY(%s)
"""

_DETAIL_SQL = """
SELECT brier, log_loss, accuracy, n_predictions, window_from, window_to, calibration
FROM backtests WHERE run_id = %s
"""

_HISTORY_SQL = """
SELECT r.id AS run_id, r.version, r.code_ref, r.created_at, r.data_through,
       b.brier, b.log_loss, b.accuracy, b.n_predictions
FROM model_runs r
LEFT JOIN backtests b ON b.run_id = r.id
WHERE r.model = %s
ORDER BY r.created_at, r.id
"""


def cards(conn: Conn, run_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not run_ids:
        return {}
    out: dict[int, dict[str, Any]] = {}
    for row in rows_as_dicts(conn.execute(_CARD_SQL, (run_ids,))):
        out[cast(int, row.pop("run_id"))] = row
    return out


def detail(conn: Conn, run_id: int) -> dict[str, Any] | None:
    """The report card with its window and raw calibration bins."""
    rows = rows_as_dicts(conn.execute(_DETAIL_SQL, (run_id,)))
    return rows[0] if rows else None


def history(conn: Conn, model: str) -> dict[str, Any]:
    return {"model": model, "points": rows_as_dicts(conn.execute(_HISTORY_SQL, (model,)))}
