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
SELECT s.id, s.team1_id, s.team2_id, s.team1_score, s.team2_score, s.played_at,
       s.event_id
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
    event_id: int


# How a rating period is cut. Whole-roster advancement applies at every length —
# these differ only in how finely time is sliced, so "series" is a period per
# series and *not* the old participants-only behaviour, which was a bug rather
# than a setting. Shorter periods inflate an idle team's deviation more often,
# which is why this is swept rather than assumed.
PERIODS = ("series", "event", "week", "month")
DEFAULT_PERIOD = "event"


def period_key(s: SeriesRow, period: str) -> tuple[object, ...]:
    """The period a series falls in. Keys sort in chronological order."""
    match period:
        case "series":
            return (s.played_at, s.id)
        case "event":
            # An event is the natural period here: a CWL event is a few days of
            # dense play, then weeks of nothing. Keyed with its start so two
            # concurrently-running events still order deterministically.
            return (s.event_id,)
        case "week":
            iso = s.played_at.isocalendar()
            return (iso[0], iso[1])
        case "month":
            return (s.played_at.year, s.played_at.month)
    raise ValueError(f"unknown rating period: {period!r}")


def group_periods(series: list[SeriesRow], period: str) -> list[list[SeriesRow]]:
    """Series in play order, cut into consecutive rating periods.

    Grouping is over the already-chronological list rather than by sorting the
    keys, so a period is always a contiguous run of play. An event whose series
    are interleaved with another event's stays one period either way.
    """
    out: list[list[SeriesRow]] = []
    current: tuple[object, ...] | None = None
    for s in series:
        key = period_key(s, period)
        if key != current:
            out.append([])
            current = key
        out[-1].append(s)
    return out


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
            event_id=cast(int, r[6]),
        )
        for r in rows
    ]


def elo_walk_forward(
    series: list[SeriesRow], k: float, lineage: dict[int, int] | None = None
) -> tuple[list[Prediction], list[tuple[int, int, float, float, None]]]:
    """Rate every series; return predictions and rating rows, touching no DB.

    Split out from `fit_elo` so the hyperparameter sweep can score a K without
    writing a run for it.
    """
    lin = lineage or {}
    model = Elo(k=k)
    preds: list[Prediction] = []
    rows: list[tuple[int, int, float, float, None]] = []
    for s in series:
        l1, l2 = lin.get(s.team1, s.team1), lin.get(s.team2, s.team2)
        pre1, pre2 = model.rating(l1), model.rating(l2)
        p, post1, post2 = model.update(l1, l2, s.team1_won)
        preds.append(Prediction(p=p, won=s.team1_won, when=s.played_at.date(), series_id=s.id))
        rows.append((s.team1, s.id, pre1, post1, None))
        rows.append((s.team2, s.id, pre2, post2, None))
    return preds, rows


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
    preds, rows = elo_walk_forward(series, k, lineage)
    conn.cursor().executemany(
        "INSERT INTO team_ratings (run_id, team_id, series_id, rating_pre, rating_post,"
        " rating_sd) VALUES (%s, %s, %s, %s, %s, %s)",
        [(run_id, *r) for r in rows],
    )
    return preds


def glicko2_walk_forward(
    series: list[SeriesRow],
    tau: float,
    lineage: dict[int, int] | None = None,
    period: str = DEFAULT_PERIOD,
) -> tuple[list[Prediction], list[tuple[int, int, float, float, float]]]:
    """Rate every series in rating periods; return predictions and rating rows.

    Every series in a period is predicted from the ratings as they stood when
    the period opened, and the ratings move once at the close. That is stricter
    walk-forward than the old per-series update, not looser: a model cannot use
    the first day of an event to sharpen its guess about the third.

    The roster advanced each period is every lineage rated so far. Teams that
    have not yet debuted are left alone — they sit at the initial 1500 ± 350,
    and inflating an unplayed team would only push its deviation past the value
    that already means "nothing is known".
    """
    lin = lineage or {}
    model = Glicko2(tau=tau)
    preds: list[Prediction] = []
    rows: list[tuple[int, int, float, float, float]] = []

    for block in group_periods(series, period):
        results: dict[int, list[tuple[int, float]]] = {}
        pre: dict[int, float] = {}
        pending: list[tuple[int, int, float]] = []  # (team_id, series_id, pre-rating)
        for s in block:
            l1, l2 = lin.get(s.team1, s.team1), lin.get(s.team2, s.team2)
            preds.append(
                Prediction(
                    p=model.predict(l1, l2),
                    won=s.team1_won,
                    when=s.played_at.date(),
                    series_id=s.id,
                )
            )
            score = 1.0 if s.team1_won else 0.0
            results.setdefault(l1, []).append((l2, score))
            results.setdefault(l2, []).append((l1, 1.0 - score))
            for team_id, lineage_id in ((s.team1, l1), (s.team2, l2)):
                pre.setdefault(lineage_id, model.state(lineage_id).r)
                pending.append((team_id, s.id, pre[lineage_id]))

        # Idle lineages inflate too, which is the whole point of a period.
        model.advance(results, list(model.teams))
        for team_id, series_id, pre_r in pending:
            post = model.state(lin.get(team_id, team_id))
            rows.append((team_id, series_id, pre_r, post.r, post.rd))
    return preds, rows


def fit_glicko2(
    conn: psycopg.Connection[tuple[object, ...]],
    run_id: int,
    series: list[SeriesRow],
    tau: float,
    lineage: dict[int, int] | None = None,
    period: str = DEFAULT_PERIOD,
) -> list[Prediction]:
    """As `fit_elo`: lineage-keyed state, team-keyed rows."""
    preds, rows = glicko2_walk_forward(series, tau, lineage, period)
    conn.cursor().executemany(
        "INSERT INTO team_ratings (run_id, team_id, series_id, rating_pre, rating_post,"
        " rating_sd) VALUES (%s, %s, %s, %s, %s, %s)",
        [(run_id, *r) for r in rows],
    )
    return preds


def data_through(series: list[SeriesRow]) -> date:
    return max(s.played_at for s in series).date()
