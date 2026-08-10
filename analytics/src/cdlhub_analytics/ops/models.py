"""One card per model: what the current run is and how it moved.

`runs` is the ledger of every fit; this is the same rows collapsed to the state
each model is in now — its current run, what that run wrote, and the direction
its report card moved against the run it replaced. A model here is any name
that has ever appeared in `model_runs`, so a stage that stops writing runs
keeps its card and shows its age rather than disappearing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from . import Conn, rows_as_dicts
from .runs import OUTPUT_TABLES

# Lower is better for these; the sign of a delta alone does not say which way
# a metric moved in quality terms.
LOWER_IS_BETTER = ("brier", "log_loss")
METRICS = ("brier", "log_loss", "accuracy", "n_predictions")

_MODELS_SQL = """
SELECT model,
       count(*)                AS runs,
       min(created_at)         AS first_run_at,
       count(DISTINCT version) AS versions
FROM model_runs
GROUP BY model
ORDER BY model
"""

# The two most recent runs of every model in one pass: the current card and the
# one it is compared against.
_RECENT_SQL = """
SELECT id, model, version, code_ref, params, data_through, created_at, rank
FROM (
  SELECT r.*, row_number() OVER (PARTITION BY model ORDER BY created_at DESC, id DESC) AS rank
  FROM model_runs r
) ranked
WHERE rank <= 2
"""

_BACKTEST_SQL = """
SELECT run_id, brier, log_loss, accuracy, n_predictions
FROM backtests WHERE run_id = ANY(%s)
"""

_ARTIFACT_SQL = """
SELECT run_id, name, octet_length(payload::text) AS bytes
FROM model_artifacts WHERE run_id = ANY(%s) ORDER BY run_id, name
"""


def _outputs(conn: Conn, run_ids: list[int]) -> dict[int, dict[str, int]]:
    counts: dict[int, dict[str, int]] = {run_id: {} for run_id in run_ids}
    if not run_ids:
        return counts
    for table in OUTPUT_TABLES:
        sql = f"SELECT run_id, count(*) FROM {table} WHERE run_id = ANY(%s) GROUP BY run_id"  # noqa: S608
        for row in conn.execute(sql, (run_ids,)).fetchall():
            counts[cast(int, row[0])][table] = cast(int, row[1])
    return counts


def _delta(current: dict[str, Any] | None, previous: dict[str, Any] | None) -> dict[str, Any]:
    """Metric by metric change, with the direction that counts as better."""
    if current is None or previous is None:
        return {}
    moved: dict[str, Any] = {}
    for metric in METRICS:
        now, before = current.get(metric), previous.get(metric)
        if not isinstance(now, int | float) or not isinstance(before, int | float):
            continue
        change = float(now) - float(before)
        moved[metric] = {
            "change": change,
            "improved": change < 0 if metric in LOWER_IS_BETTER else change > 0,
        }
    return moved


def _age_days(created_at: Any, now: datetime) -> int | None:
    if not isinstance(created_at, datetime):
        return None
    stamped = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return (now - stamped).days


def report(conn: Conn) -> dict[str, Any]:
    now = datetime.now(UTC)
    totals = rows_as_dicts(conn.execute(_MODELS_SQL))
    recent = rows_as_dicts(conn.execute(_RECENT_SQL))
    run_ids = [cast(int, row["id"]) for row in recent]

    outputs = _outputs(conn, run_ids)
    cards: dict[int, dict[str, Any]] = {}
    for row in rows_as_dicts(conn.execute(_BACKTEST_SQL, (run_ids,))):
        cards[cast(int, row.pop("run_id"))] = row
    artifacts: dict[int, list[dict[str, Any]]] = {run_id: [] for run_id in run_ids}
    for row in rows_as_dicts(conn.execute(_ARTIFACT_SQL, (run_ids,))):
        artifacts[cast(int, row.pop("run_id"))].append(row)

    by_model: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(recent, key=lambda r: cast(int, r["rank"])):
        by_model.setdefault(cast(str, row["model"]), []).append(row)

    models: list[dict[str, Any]] = []
    for total in totals:
        name = cast(str, total["model"])
        ranked = by_model.get(name, [])
        current = ranked[0] if ranked else None
        previous = ranked[1] if len(ranked) > 1 else None
        current_id = cast(int, current["id"]) if current else None
        models.append(
            {
                "model": name,
                "runs": total["runs"],
                "versions": total["versions"],
                "first_run_at": total["first_run_at"],
                "current": None
                if current is None
                else {
                    "id": current_id,
                    "version": current["version"],
                    "code_ref": current["code_ref"],
                    "params": current["params"],
                    "data_through": current["data_through"],
                    "created_at": current["created_at"],
                    "age_days": _age_days(current["created_at"], now),
                    "outputs": outputs.get(cast(int, current_id), {}),
                    "artifacts": artifacts.get(cast(int, current_id), []),
                    "backtest": cards.get(cast(int, current_id)),
                },
                "previous": None
                if previous is None
                else {
                    "id": previous["id"],
                    "version": previous["version"],
                    "created_at": previous["created_at"],
                    "backtest": cards.get(cast(int, previous["id"])),
                },
                "delta": _delta(
                    cards.get(current_id) if current_id is not None else None,
                    cards.get(cast(int, previous["id"])) if previous else None,
                ),
            }
        )
    return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "models": models}
