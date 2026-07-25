"""Org lineage: a rebrand keeps one rating curve.

`fit_elo` / `fit_glicko2` key their rating state on the lineage but write rows
under the team that actually played, so these tests assert both halves: the
second brand inherits the first brand's rating, and the stored rows still name
the brand of the day. A DB-free stub connection stands in for Postgres — the
fit functions only ever read via `execute` and write via `cursor().executemany`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from cdlhub_analytics.ratings import fit
from cdlhub_analytics.ratings.elo import INITIAL

# Teams 1 and 2 are one org (1 founded it); team 3 is unaffiliated.
LINEAGE = {1: 1, 2: 1, 3: 3}


class _Cursor:
    def __init__(self, sink: list[tuple[Any, ...]]) -> None:
        self.sink = sink

    def executemany(self, _sql: str, rows: list[tuple[Any, ...]]) -> None:
        self.sink.extend(rows)


class _Conn:
    """Captures written rating rows; `execute` answers only LINEAGE_SQL."""

    def __init__(self, lineage_rows: list[tuple[int, int]] | None = None) -> None:
        self.written: list[tuple[Any, ...]] = []
        self._lineage = lineage_rows or []

    def execute(self, _sql: str, _params: tuple[Any, ...] = ()) -> Any:
        rows = self._lineage

        class R:
            def fetchall(self) -> list[tuple[int, int]]:
                return rows

        return R()

    def cursor(self) -> _Cursor:
        return _Cursor(self.written)


def series(*specs: tuple[int, int, int, bool]) -> list[fit.SeriesRow]:
    """(id, team1, team2, team1_won) with a fixed increasing date."""
    return [
        fit.SeriesRow(
            id=sid,
            team1=t1,
            team2=t2,
            team1_won=won,
            played_at=datetime(2018, 1, 1 + i),
        )
        for i, (sid, t1, t2, won) in enumerate(specs)
    ]


def ratings_by_team(written: list[tuple[Any, ...]]) -> dict[int, list[tuple[float, float]]]:
    """team_id -> [(rating_pre, rating_post)] in write order."""
    out: dict[int, list[tuple[float, float]]] = {}
    for row in written:
        _run, team_id, _series_id, pre, post = row[0], row[1], row[2], row[3], row[4]
        out.setdefault(team_id, []).append((pre, post))
    return out


def test_lineage_carries_rating_through_a_rebrand() -> None:
    # Team 1 wins twice as the old brand, then team 2 (same org) plays team 3.
    # Team 2's pre-rating must be team 1's post-rating, not the 1500 default.
    conn = _Conn()
    rows = series((10, 1, 3, True), (11, 1, 3, True), (12, 2, 3, True))
    fit.fit_elo(conn, run_id=1, series=rows, k=32.0, lineage=LINEAGE)  # type: ignore[arg-type]

    by_team = ratings_by_team(conn.written)
    old_brand_final = by_team[1][-1][1]
    new_brand_first_pre = by_team[2][0][0]
    assert new_brand_first_pre == old_brand_final
    assert new_brand_first_pre > INITIAL  # it actually inherited a won-two record


def test_rows_are_written_under_the_team_that_played() -> None:
    """The curve merges; the rows do not. The site still shows the brand of the day."""
    conn = _Conn()
    rows = series((10, 1, 3, True), (12, 2, 3, True))
    fit.fit_elo(conn, run_id=1, series=rows, k=32.0, lineage=LINEAGE)  # type: ignore[arg-type]

    by_team = ratings_by_team(conn.written)
    assert set(by_team) == {1, 2, 3}
    assert len(by_team[1]) == 1 and len(by_team[2]) == 1


def test_no_lineage_is_identical_to_before() -> None:
    """An empty lineage must reproduce plain per-team rating exactly."""
    rows = series((10, 1, 3, True), (11, 2, 3, True), (12, 1, 2, False))
    a, b = _Conn(), _Conn()
    fit.fit_elo(a, run_id=1, series=rows, k=32.0, lineage=None)  # type: ignore[arg-type]
    fit.fit_elo(b, run_id=1, series=rows, k=32.0, lineage={1: 1, 2: 2, 3: 3})  # type: ignore[arg-type]
    assert a.written == b.written


def test_glicko2_lineage_carries_rating_and_rd() -> None:
    conn = _Conn()
    rows = series((10, 1, 3, True), (11, 1, 3, True), (12, 2, 3, True))
    fit.fit_glicko2(conn, run_id=1, series=rows, tau=0.5, lineage=LINEAGE)  # type: ignore[arg-type]

    by_team = ratings_by_team(conn.written)
    assert by_team[2][0][0] == by_team[1][-1][1]
    # RD is written too, and a lineage with three rated series is more certain
    # than an unrated default.
    rd_new_brand = conn.written[-2][5] if conn.written[-2][1] == 2 else conn.written[-1][5]
    assert 0 < rd_new_brand < 350.0


def test_load_lineage_maps_orgless_teams_to_themselves() -> None:
    conn = _Conn(lineage_rows=[(1, 1), (2, 1), (3, 3)])
    assert fit.load_lineage(conn) == LINEAGE  # type: ignore[arg-type]
