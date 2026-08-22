"""ACCOLADE: tier caps, the ROTY once-per-career rule, per-year normalisation,
the thin-year floor and unresolved rows."""

from __future__ import annotations

import pytest

from cdlhub_analytics.career_rank import awards as awards

from .conftest import FakeConn, as_conn


def _credits(*rows: awards.AwardRow) -> dict[tuple[int, int], awards.AwardCredit]:
    return {(c.player_id, c.season_id): c for c in awards.credits(rows)}


def _scored(*rows: awards.AwardRow) -> dict[tuple[int, int], awards.SeasonAccolade]:
    return {(a.player_id, a.season_id): a for a in awards.score(rows)}


def test_multiple_second_tier_awards_do_not_stack() -> None:
    """Five event MVPs in a season cannot out-credit a genuine first-team season."""
    credits = _credits((1, 19, 2022, "event_mvp"), (1, 19, 2022, "mode_best"))
    assert credits[(1, 19)].points == pytest.approx(awards.SECOND_TIER_POINTS)


def test_top_and_second_tier_stack_within_one_season() -> None:
    credits = _credits((1, 19, 2022, "first_team"), (1, 19, 2022, "event_mvp"))
    assert credits[(1, 19)].points == pytest.approx(
        awards.TOP_TIER_POINTS + awards.SECOND_TIER_POINTS
    )


def test_roty_only_fires_once_on_the_earliest_qualifying_season() -> None:
    credits = _credits((1, 19, 2022, "roty"), (1, 20, 2023, "roty"))
    assert credits.get((1, 19)) is not None
    assert credits.get((1, 20)) is None


def test_an_unmapped_award_credits_nothing() -> None:
    assert awards.credits([(1, 19, 2022, "unmapped")]) == []


def test_accolade_is_the_seasons_share_of_its_own_year() -> None:
    """One first team and one event MVP in a year: 8 and 4 of a 12-point year."""
    scored = _scored((1, 19, 2022, "first_team"), (2, 19, 2022, "event_mvp"))
    assert scored[(1, 19)].accolade == pytest.approx(8.0 / 12.0)
    assert scored[(2, 19)].accolade == pytest.approx(4.0 / 12.0)
    assert scored[(1, 19)].year_credit == pytest.approx(12.0)


def test_the_denominator_is_the_year_and_not_the_season() -> None:
    """Two seasons inside one year share one budget, so a year with more
    honours in it is worth less per honour."""
    scored = _scored(
        (1, 19, 2022, "first_team"),
        (2, 20, 2022, "first_team"),
        (3, 21, 2023, "first_team"),
    )
    assert scored[(1, 19)].accolade == pytest.approx(0.5)
    assert scored[(3, 21)].accolade == pytest.approx(1.0)


def test_a_year_with_no_season_level_honour_is_silenced() -> None:
    """2013 through 2015 hold event MVPs and nothing else. Dividing by one
    would hand that player the whole year, so the year credits nothing."""
    scored = _scored((1, 19, 2014, "event_mvp"), (2, 20, 2015, "first_team"))
    assert scored[(1, 19)].accolade == pytest.approx(0.0)
    assert scored[(1, 19)].credit == pytest.approx(awards.SECOND_TIER_POINTS)
    assert scored[(2, 20)].accolade == pytest.approx(1.0)


def test_thin_years_reads_the_award_kinds_and_not_the_count() -> None:
    rows: list[awards.AwardRow] = [
        (1, 19, 2013, "event_mvp"),
        (2, 19, 2013, "event_mvp"),
        (3, 19, 2013, "event_mvp"),
        (4, 20, 2016, "first_team"),
    ]
    assert awards.thin_years(rows) == {2013}


def test_the_raw_credit_survives_a_thin_year() -> None:
    """A silenced year loses its share, never its record: the numerator is
    still published so a reader re-normalising differently has it."""
    scored = _scored((1, 19, 2014, "event_mvp"))
    assert scored[(1, 19)].credit == pytest.approx(awards.SECOND_TIER_POINTS)
    assert scored[(1, 19)].year_credit == pytest.approx(0.0)


def test_load_award_rows_carries_the_year() -> None:
    conn = FakeConn([(1, 19, 2022, "first_team")])
    assert awards.load_award_rows(as_conn(conn)) == [(1, 19, 2022, "first_team")]


def test_density_counts_the_unresolved_rows_beside_the_credited_ones() -> None:
    rows: list[awards.AwardRow] = [(1, 19, 2022, "first_team"), (2, 19, 2022, "event_mvp")]
    table = awards.density(rows, [(2022, "unmapped"), (2023, "first_team")])
    by_year = {row["year"]: row for row in table}
    assert by_year[2022]["credited_seasons"] == 2
    assert by_year[2022]["year_credit"] == pytest.approx(12.0)
    assert by_year[2022]["max_stack"] == pytest.approx(8.0)
    assert by_year[2022]["unresolved_rows"] == 1
    assert by_year[2022]["thin"] is False
    # A year whose only award reaches nobody still appears, with no credit.
    assert by_year[2023]["credited_seasons"] == 0
    assert by_year[2023]["unresolved_rows"] == 1


def test_there_is_no_way_to_add_award_credit_to_a_score() -> None:
    """`apply` is retired. ACCOLADE is a component with its own weight, and a
    module-level helper that folds it into a performance number is exactly the
    thing Phase D removed."""
    assert not hasattr(awards, "apply")
