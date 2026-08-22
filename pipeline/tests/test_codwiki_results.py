from datetime import date

from cdlhub_pipeline.codwiki.results import (
    RANKING_TYPE,
    TIER_NUMBER,
    TIER_TYPES,
    _in_window,
    _places,
    _pool_usd,
    _prize,
    award_kind,
)


def test_a_shared_place_keeps_both_ends() -> None:
    assert _places("1") == (1, 1)
    assert _places("3-4") == (3, 4)
    assert _places("T5-8") == (5, 8)


def test_a_place_that_is_not_a_number_is_no_place() -> None:
    assert _places("DQ") is None
    assert _places("-") is None
    assert _places("") is None


def test_the_window_starts_in_2013_and_ends_before_2017() -> None:
    assert _in_window("2013-01-01")
    assert _in_window("2016-12-31")
    assert not _in_window("2012-12-31")
    assert not _in_window("2017-01-01")
    assert not _in_window("")


def test_a_ranking_list_is_told_from_an_award_by_its_leading_rank() -> None:
    assert RANKING_TYPE.match("#4 BO6 Top 20")
    assert RANKING_TYPE.match("#20 MWII Chall. 20")
    assert not RANKING_TYPE.match("Event MVP")
    assert not RANKING_TYPE.match("CWL All-Star")


def test_the_wiki_award_names_fold_onto_the_kinds_already_stored() -> None:
    assert award_kind("Event MVP") == "event_mvp"
    assert award_kind("Grand Finals MVP") == "fmvp"
    assert award_kind("CWL All-Star") == "first_team"
    assert award_kind("CDL Second Team") == "second_team"
    assert award_kind("Rookie of the Year") == "roty"


def test_an_unrecognised_award_is_named_not_bucketed() -> None:
    assert award_kind("Mayor of Verdansk") == "unmapped"
    assert award_kind("Stream Moment of the Year") == "unmapped"


def test_a_prize_reads_through_its_thousands_separator() -> None:
    assert _prize({"Prize USD": "6,000"}) == 6000.0
    assert _prize({"Prize USD": ""}) is None
    assert _prize({}) is None


def test_the_window_bounds_are_the_ones_the_owner_set() -> None:
    assert _in_window(date(2015, 6, 1).isoformat())


# ------------------------------------------------------- prize pools and tiers


def test_a_pool_in_dollars_reads_as_published() -> None:
    assert _pool_usd("$ 1,000,000") == (1_000_000.0, None)
    assert _pool_usd("$ 5,000") == (5_000.0, None)


def test_a_pool_in_another_currency_is_converted_and_not_read_as_dollars() -> None:
    """A pound taken for a dollar understates an event by a third."""
    pounds, reason = _pool_usd("£ 6,000")
    assert reason is None
    assert pounds is not None
    assert pounds > 6_000.0

    aussie, reason = _pool_usd("A$ 60,000")
    assert reason is None
    assert aussie is not None
    assert aussie < 60_000.0


def test_a_pool_that_is_not_money_is_unknown_rather_than_zero() -> None:
    """`MLG X Games Invitational 2014` pays Medals; nothing is worth zero."""
    pool, reason = _pool_usd("Medals")
    assert pool is None
    assert reason is not None


def test_no_pool_published_is_unknown_and_says_so() -> None:
    assert _pool_usd("") == (None, "no pool published")


def test_only_premier_and_major_take_a_numeric_tier() -> None:
    """The title rule reads `events.tier`, so a word it does not admit deletes
    a title. Minor has no number here and keeps its word in `source_tier`."""
    assert TIER_NUMBER["Premier"] == "1"
    assert TIER_NUMBER["Major"] == "2"
    assert "Minor" not in TIER_NUMBER
    assert "Qualifier" not in TIER_NUMBER
    assert TIER_TYPES["Qualifier"] == "Qualifier"
