"""The one rule that decides whether two people share a career page."""

from cdlhub_pipeline.codwiki.identity import _real_name


def test_the_players_table_names_the_person() -> None:
    wiki = {"Realize (Josh Taylor)": {"NameFull": "Josh Taylor"}}
    assert _real_name("Realize (Josh Taylor)", wiki) == "josh taylor"


def test_a_page_with_no_row_is_named_by_its_own_parenthetical() -> None:
    """`Denz (Denholm Taylor)` is a redirect the box scores still use."""
    assert _real_name("Denz (Denholm Taylor)", {}) == "denholm taylor"


def test_a_plain_page_with_no_row_names_nobody() -> None:
    assert _real_name("Realize", {}) == ""


def test_the_players_table_wins_over_the_parenthetical() -> None:
    wiki = {"Melo (Alexandre Boin)": {"NameFull": "Alexandre Boin"}}
    assert _real_name("Melo (Alexandre Boin)", wiki) == "alexandre boin"


def test_an_empty_real_name_falls_through_to_the_parenthetical() -> None:
    wiki = {"Jake (Jake Wellstead)": {"NameFull": ""}}
    assert _real_name("Jake (Jake Wellstead)", wiki) == "jake wellstead"
