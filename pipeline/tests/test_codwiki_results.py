from datetime import date

from cdlhub_pipeline.codwiki.results import RANKING_TYPE, _in_window, _places, _prize, award_kind


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
