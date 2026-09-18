"""The RESUME component: the curve, the weight, and the per-year division.

Every number here was declared before a result was read, so these tests are
written against the declaration and not against what the current archive
happens to produce.
"""

from __future__ import annotations

import math
import os
from collections.abc import Iterator
from typing import Any

import pytest

from cdlhub_analytics.career_rank import resume

from .conftest import FakeConn, as_conn


def test_curve_matches_the_declared_table() -> None:
    published = {
        1: 1.0,
        2: 0.2471,
        3: 0.1076,
        4: 0.0588,
        5: 0.0362,
        8: 0.0118,
        12: 0.0031,
    }
    for placement, value in published.items():
        assert resume.placement_curve(placement) == pytest.approx(value, abs=5e-5)


def test_curve_is_zero_at_the_floor_and_below_it() -> None:
    assert resume.placement_curve(resume.CURVE_FLOOR) == 0.0
    assert resume.placement_curve(resume.CURVE_FLOOR + 1) == 0.0
    assert resume.placement_curve(64) == 0.0


def test_a_win_is_four_times_a_second_place() -> None:
    ratio = resume.placement_curve(1) / resume.placement_curve(2)
    assert ratio == pytest.approx(4.05, abs=0.01)


def test_placement_must_be_a_real_finish() -> None:
    with pytest.raises(ValueError):
        resume.placement_curve(0)


def test_pooled_finish_is_the_mean_of_its_range() -> None:
    # The 2020 Launch Weekend's `1-4`: worth more than a clean second place
    # and nothing like a win, which is what the chip rule already says.
    pooled = resume.pooled_placement(1, 4)
    assert pooled == pytest.approx(0.3534, abs=5e-5)
    assert resume.placement_curve(2) < pooled < resume.placement_curve(1)


def test_pooled_range_below_the_floor_pays_nothing() -> None:
    assert resume.pooled_placement(17, 24) == 0.0


def test_pooled_range_straddling_the_floor_divides_by_the_whole_range() -> None:
    # 13th through 20th: four scoring places, eight teams sharing the finish.
    expected = sum(resume.placement_curve(p) for p in range(13, 17)) / 8
    assert resume.pooled_placement(13, 20) == pytest.approx(expected)


def test_a_clean_finish_is_its_own_curve_value() -> None:
    assert resume.pooled_placement(3, 3) == resume.placement_curve(3)


def test_weight_is_the_root_of_the_pool() -> None:
    assert resume.event_weight(2_000_000.0, None) == pytest.approx(math.sqrt(2_000_000.0))


def test_root_compresses_the_gap_a_raw_pool_would_open() -> None:
    # 2020: a $4.6m championship beside a $100k event. Raw pool makes one
    # event 46 times the other and lets it own the season.
    raw = 4_600_000.0 / 100_000.0
    rooted = resume.event_weight(4_600_000.0, None) / resume.event_weight(100_000.0, None)
    assert raw == pytest.approx(46.0)
    assert rooted == pytest.approx(6.78, abs=0.01)


def test_unknown_pool_takes_the_years_smallest_known_one() -> None:
    events = [(1, 10, 2021, 2_500_000.0), (2, 10, 2021, 500_000.0), (3, 10, 2021, None)]
    fallbacks = resume.year_fallbacks(events)
    assert fallbacks[2021] == 500_000.0
    assert resume.event_weight(None, fallbacks[2021]) == resume.event_weight(500_000.0, None)


def test_a_year_with_no_known_pool_weights_every_event_one() -> None:
    # 2013-2016: `prize_pool` is null for all 57 title events. Only ratios
    # inside a year are used, so equal weight is internally consistent.
    events = [(1, 4, 2015, None), (2, 4, 2015, None)]
    fallbacks = resume.year_fallbacks(events)
    assert fallbacks[2015] is None
    assert resume.event_weight(None, fallbacks[2015]) == 1.0


def test_season_resume_is_the_share_of_the_years_winnable_credit() -> None:
    events = [(1, 4, 2015, None), (2, 4, 2015, None), (3, 4, 2015, None)]
    finishes = [(7, 1, 4, 1, 1), (7, 2, 4, 2, 2)]
    rows = resume.score(events, finishes)
    assert len(rows) == 1
    row = rows[0]
    assert row.year_credit == 3.0
    assert row.credit == pytest.approx(1.0 + resume.placement_curve(2))
    assert row.resume == pytest.approx(row.credit / 3.0)
    assert row.events == 2


def test_winning_every_title_event_scores_exactly_one() -> None:
    events = [(1, 4, 2015, None), (2, 4, 2015, 900_000.0), (3, 4, 2015, 100_000.0)]
    finishes = [(7, 1, 4, 1, 1), (7, 2, 4, 1, 1), (7, 3, 4, 1, 1)]
    assert resume.score(events, finishes)[0].resume == pytest.approx(1.0)


def test_normalisation_makes_a_thin_year_and_a_crowded_one_comparable() -> None:
    # Same finish — winning half the year's weighted credit — in a year with
    # thirteen titles and a year with five.
    crowded = [(i, 1, 2020, 1_000_000.0) for i in range(1, 14)]
    thin = [(i, 2, 2024, 1_000_000.0) for i in range(100, 105)]
    crowded_rows = resume.score(crowded, [(7, i, 1, 1, 1) for i in range(1, 8)])
    thin_rows = resume.score(thin, [(7, i, 2, 1, 1) for i in range(100, 103)])
    assert crowded_rows[0].resume == pytest.approx(7 / 13)
    assert thin_rows[0].resume == pytest.approx(3 / 5)


def test_a_finish_below_the_floor_earns_nothing() -> None:
    events = [(1, 4, 2015, None)]
    rows = resume.score(events, [(7, 1, 4, 20, 20)])
    assert rows[0].credit == 0.0
    assert rows[0].resume == 0.0


def test_coverage_from_walks_back_while_every_win_reaches_a_roster() -> None:
    conn = as_conn(FakeConn([(2013, 14, 14), (2014, 15, 15), (2015, 14, 14)]))
    assert resume.coverage_from(conn) == 2013


def test_coverage_from_stops_at_the_first_short_year() -> None:
    conn = as_conn(FakeConn([(2013, 14, 12), (2014, 15, 15), (2015, 14, 14)]))
    assert resume.coverage_from(conn) == 2014


def test_coverage_from_stops_at_a_gap_in_the_years() -> None:
    conn = as_conn(FakeConn([(2013, 14, 14), (2015, 14, 14), (2016, 14, 14)]))
    assert resume.coverage_from(conn) == 2015


def test_coverage_from_is_none_when_the_latest_year_is_short() -> None:
    conn = as_conn(FakeConn([(2025, 7, 7), (2026, 8, 6)]))
    assert resume.coverage_from(conn) is None


# MARK: earned placements, against the real schema


@pytest.fixture
def rollback_conn() -> Iterator[Any]:
    """A live connection whose transaction is always rolled back.

    `_EARNED_SQL` joins three tables through a role column; a fixture of
    canned rows cannot tell whether that SQL text is right. This runs
    against the real schema and nothing is kept.
    """
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("DATABASE_URL", "postgres://cdlhub:cdlhub@localhost:54329/cdlhub")
    try:
        conn = psycopg.connect(dsn, connect_timeout=2)
    except Exception:  # noqa: BLE001 - any connection failure means no DB here
        pytest.skip("no database reachable")
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _make_title_event(conn: Any, name: str) -> tuple[int, int]:
    """A season and an event that satisfy `TITLE_EVENT`, for a synthetic finish."""
    title_id = conn.execute("SELECT id FROM titles LIMIT 1").fetchone()
    if title_id is None:
        pytest.skip("no titles loaded")
    season = conn.execute(
        "INSERT INTO seasons (year, title_id, league) VALUES (2013, %s, 'ZzTest') RETURNING id",
        (title_id[0],),
    ).fetchone()
    event = conn.execute(
        "INSERT INTO events (season_id, name, tier, tier_type, prize_pool)"
        " VALUES (%s, %s, '1', 'Premier', 100000) RETURNING id",
        (season[0], name),
    ).fetchone()
    return int(event[0]), int(season[0])


def _make_player(conn: Any, handle: str) -> int:
    row = conn.execute(
        "INSERT INTO players (handle) VALUES (%s) RETURNING id", (handle,)
    ).fetchone()
    return int(row[0])


def _make_team(conn: Any, name: str) -> int:
    row = conn.execute("INSERT INTO teams (name) VALUES (%s) RETURNING id", (name,)).fetchone()
    return int(row[0])


def _place(conn: Any, event_id: int, team_id: int, pmin: int, pmax: int) -> None:
    conn.execute(
        "INSERT INTO event_placements (event_id, team_id, placement_min, placement_max,"
        " data_source) VALUES (%s, %s, %s, %s, 'lpdb')",
        (event_id, team_id, pmin, pmax),
    )


def _roster(conn: Any, event_id: int, team_id: int, player_id: int, role: str | None) -> None:
    conn.execute(
        "INSERT INTO event_rosters (event_id, team_id, player_id, role, data_source)"
        " VALUES (%s, %s, %s, %s, 'lpdb')",
        (event_id, team_id, player_id, role),
    )


def test_a_coach_row_earns_no_placement_credit(rollback_conn: Any) -> None:
    conn = rollback_conn
    event_id, season_id = _make_title_event(conn, "ZzTest Coach Only")
    team_id = _make_team(conn, "ZzTest Team A")
    coach_id = _make_player(conn, "ZzTestCoach")
    _place(conn, event_id, team_id, 1, 1)
    _roster(conn, event_id, team_id, coach_id, "Coach")

    finishes = resume.load_finishes(conn)

    assert not [f for f in finishes if f[0] == coach_id]


def test_a_player_row_still_earns_credit(rollback_conn: Any) -> None:
    conn = rollback_conn
    event_id, season_id = _make_title_event(conn, "ZzTest Player Only")
    team_id = _make_team(conn, "ZzTest Team B")
    player_id = _make_player(conn, "ZzTestPlayer")
    _place(conn, event_id, team_id, 1, 1)
    _roster(conn, event_id, team_id, player_id, None)

    finishes = resume.load_finishes(conn)

    matches = [f for f in finishes if f[0] == player_id]
    assert matches == [(player_id, event_id, season_id, 1, 1)]


def test_a_player_who_also_coaches_a_second_team_is_credited_once(rollback_conn: Any) -> None:
    """`event_rosters` keys on (event, team, player), not (event, player): a
    person can hold a player row on one team and a coach row on another at
    the same event. Only the player row should reach a finish."""
    conn = rollback_conn
    event_id, season_id = _make_title_event(conn, "ZzTest Dual Role")
    player_team = _make_team(conn, "ZzTest Team C")
    coach_team = _make_team(conn, "ZzTest Team D")
    person_id = _make_player(conn, "ZzTestDual")
    _place(conn, event_id, player_team, 1, 1)
    _place(conn, event_id, coach_team, 3, 3)
    _roster(conn, event_id, player_team, person_id, None)
    _roster(conn, event_id, coach_team, person_id, "Coach")

    finishes = resume.load_finishes(conn)

    matches = [f for f in finishes if f[0] == person_id]
    assert matches == [(person_id, event_id, season_id, 1, 1)]
