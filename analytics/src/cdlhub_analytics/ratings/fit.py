"""Fit Elo and Glicko-2 over all decided series in chronological order,
writing team_ratings time series and walk-forward predictions for backtests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

import psycopg

from ..backtest import Prediction
from .elo import Elo
from .glicko2 import Glicko2

SERIES_SQL = """
SELECT s.id, s.team1_id, s.team2_id, s.team1_score, s.team2_score, s.played_at
FROM series s
WHERE s.team1_id IS NOT NULL AND s.team2_id IS NOT NULL
  AND s.team1_score IS NOT NULL AND s.team2_score IS NOT NULL
  AND s.team1_score <> s.team2_score           -- undecided series are never rated
ORDER BY s.played_at, s.id
"""

# A lineage is every team sharing an org, and it is rated under its earliest
# team id — the founding brand. Teams with no org are their own lineage and map
# to themselves, so a database with no orgs declared rates exactly as it did
# before lineage existed.
LINEAGE_SQL = """
SELECT t.id, COALESCE(o.founder, t.id) AS lineage_id
FROM teams t
LEFT JOIN (
  SELECT org_id, min(id) AS founder FROM teams WHERE org_id IS NOT NULL GROUP BY org_id
) o ON o.org_id = t.org_id
"""


@dataclass
class SeriesRow:
    id: int
    team1: int
    team2: int
    team1_won: bool
    played_at: datetime


def load_lineage(conn: psycopg.Connection[tuple[object, ...]]) -> dict[int, int]:
    """team_id -> the team id its rating curve is kept under."""
    rows = conn.execute(LINEAGE_SQL).fetchall()
    return {cast(int, r[0]): cast(int, r[1]) for r in rows}


def load_series(conn: psycopg.Connection[tuple[object, ...]]) -> list[SeriesRow]:
    rows = conn.execute(SERIES_SQL).fetchall()
    return [
        SeriesRow(
            id=cast(int, r[0]),
            team1=cast(int, r[1]),
            team2=cast(int, r[2]),
            team1_won=cast(int, r[3]) > cast(int, r[4]),
            played_at=cast(datetime, r[5]),
        )
        for r in rows
    ]


def fit_elo(
    conn: psycopg.Connection[tuple[object, ...]],
    run_id: int,
    series: list[SeriesRow],
    k: float,
    lineage: dict[int, int] | None = None,
) -> list[Prediction]:
    """Rate every series. Rating state is keyed on the lineage, so a rebrand
    continues one curve; the written rows keep the team that actually played,
    so the site still shows the brand of the day."""
    lin = lineage or {}
    model = Elo(k=k)
    preds: list[Prediction] = []
    out: list[tuple[int, int, int, float, float, None]] = []
    for s in series:
        l1, l2 = lin.get(s.team1, s.team1), lin.get(s.team2, s.team2)
        pre1, pre2 = model.rating(l1), model.rating(l2)
        p, post1, post2 = model.update(l1, l2, s.team1_won)
        preds.append(Prediction(p=p, won=s.team1_won, when=s.played_at.date()))
        out.append((run_id, s.team1, s.id, pre1, post1, None))
        out.append((run_id, s.team2, s.id, pre2, post2, None))
    conn.cursor().executemany(
        "INSERT INTO team_ratings (run_id, team_id, series_id, rating_pre, rating_post,"
        " rating_sd) VALUES (%s, %s, %s, %s, %s, %s)",
        out,
    )
    return preds


def fit_glicko2(
    conn: psycopg.Connection[tuple[object, ...]],
    run_id: int,
    series: list[SeriesRow],
    tau: float,
    lineage: dict[int, int] | None = None,
) -> list[Prediction]:
    """As `fit_elo`: lineage-keyed state, team-keyed rows."""
    lin = lineage or {}
    model = Glicko2(tau=tau)
    preds: list[Prediction] = []
    out: list[tuple[int, int, int, float, float, float]] = []
    for s in series:
        l1, l2 = lin.get(s.team1, s.team1), lin.get(s.team2, s.team2)
        a, b = model.state(l1), model.state(l2)
        pre1, pre2 = a.r, b.r
        p = model.update(l1, l2, s.team1_won)
        preds.append(Prediction(p=p, won=s.team1_won, when=s.played_at.date()))
        na, nb = model.state(l1), model.state(l2)
        out.append((run_id, s.team1, s.id, pre1, na.r, na.rd))
        out.append((run_id, s.team2, s.id, pre2, nb.r, nb.rd))
    conn.cursor().executemany(
        "INSERT INTO team_ratings (run_id, team_id, series_id, rating_pre, rating_post,"
        " rating_sd) VALUES (%s, %s, %s, %s, %s, %s)",
        out,
    )
    return preds


def data_through(series: list[SeriesRow]) -> date:
    return max(s.played_at for s in series).date()
