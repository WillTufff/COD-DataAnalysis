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


@dataclass
class SeriesRow:
    id: int
    team1: int
    team2: int
    team1_won: bool
    played_at: datetime


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
) -> list[Prediction]:
    model = Elo(k=k)
    preds: list[Prediction] = []
    out: list[tuple[int, int, int, float, float, None]] = []
    for s in series:
        pre1, pre2 = model.rating(s.team1), model.rating(s.team2)
        p, post1, post2 = model.update(s.team1, s.team2, s.team1_won)
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
) -> list[Prediction]:
    model = Glicko2(tau=tau)
    preds: list[Prediction] = []
    out: list[tuple[int, int, int, float, float, float]] = []
    for s in series:
        a, b = model.state(s.team1), model.state(s.team2)
        pre1, pre2 = a.r, b.r
        p = model.update(s.team1, s.team2, s.team1_won)
        preds.append(Prediction(p=p, won=s.team1_won, when=s.played_at.date()))
        na, nb = model.state(s.team1), model.state(s.team2)
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
