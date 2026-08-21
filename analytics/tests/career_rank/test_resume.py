"""The RESUME component: the curve, the weight, and the per-year division.

Every number here was declared before a result was read, so these tests are
written against the declaration and not against what the current archive
happens to produce.
"""

from __future__ import annotations

import math

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
