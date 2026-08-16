"""Award weight: tier caps, the ROTY once-per-career rule, unresolved rows."""

from __future__ import annotations

import pytest

from cdlhub_analytics.career_rank import awards as awards

from .conftest import FakeConn, as_conn


def test_apply_adds_the_credits_points() -> None:
    credit = awards.AwardCredit(1, 19, 8.0, ("first_team",))
    assert awards.apply(50.0, credit) == pytest.approx(58.0)


def test_apply_is_a_no_op_with_no_credit() -> None:
    assert awards.apply(50.0, None) == pytest.approx(50.0)


def test_apply_caps_at_100() -> None:
    credit = awards.AwardCredit(1, 19, 8.0, ("first_team",))
    assert awards.apply(97.0, credit) == pytest.approx(100.0)


def test_multiple_second_tier_awards_do_not_stack() -> None:
    """Five event MVPs in a season cannot out-credit a genuine first-team season."""
    conn = FakeConn([(1, 19, "event_mvp"), (1, 19, "mode_best")])
    credits = {(c.player_id, c.season_id): c for c in awards.load_award_credits(as_conn(conn))}
    assert credits[(1, 19)].points == pytest.approx(awards.SECOND_TIER_POINTS)


def test_top_and_second_tier_stack_within_one_season() -> None:
    conn = FakeConn([(1, 19, "first_team"), (1, 19, "event_mvp")])
    credits = {(c.player_id, c.season_id): c for c in awards.load_award_credits(as_conn(conn))}
    assert credits[(1, 19)].points == pytest.approx(
        awards.TOP_TIER_POINTS + awards.SECOND_TIER_POINTS
    )


def test_roty_only_fires_once_on_the_earliest_qualifying_season() -> None:
    conn = FakeConn([(1, 19, "roty"), (1, 20, "roty")])
    credits = {(c.player_id, c.season_id): c for c in awards.load_award_credits(as_conn(conn))}
    assert credits.get((1, 19)) is not None
    assert credits.get((1, 20)) is None


def test_an_unmapped_award_credits_nothing() -> None:
    conn = FakeConn([(1, 19, "unmapped")])
    credits = awards.load_award_credits(as_conn(conn))
    assert credits == []
