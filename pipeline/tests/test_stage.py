"""Series stage: the round-label classes and the league-event rule."""

from __future__ import annotations

import pytest

from cdlhub_pipeline import stage
from cdlhub_pipeline.stage import BRACKET, FINAL, GROUP, LEAGUE, REGULAR, League


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        # Call of Duty League prose.
        ("Major Qualifier", REGULAR),
        ("Week 3", REGULAR),
        ("Group Play A Winners Round 1", GROUP),
        ("Group Stage", GROUP),
        ("Play-In", GROUP),
        ("Winners Round 2", BRACKET),
        ("Elimination Finals", BRACKET),
        ("Semifinals", BRACKET),
        ("Grand Finals", FINAL),
        ("Finals", FINAL),
        # Short codes, from Cito and the CoD wiki's schedule.
        ("GF", FINAL),
        ("GF2", FINAL),
        ("QF", BRACKET),
        ("LR1", BRACKET),
        ("LR11", BRACKET),
        ("WR4", BRACKET),
        ("OWR4", BRACKET),
        ("OLR10", BRACKET),
        ("R16", BRACKET),
        ("WF", BRACKET),
        ("3RD", BRACKET),
        ("13TH", BRACKET),
        ("Relegation", BRACKET),
        ("Group B", GROUP),
        ("Pool A", GROUP),
        ("Day 4", REGULAR),
        # CWL archive slugs.
        ("champs-grand-finals-0", FINAL),
        ("champs-winners-1-2", BRACKET),
        ("champs-losers-3-1", BRACKET),
        ("plq-bracket-lr2-1", BRACKET),
        ("rel-winners-1-0", BRACKET),
        ("pool-B-4", GROUP),
        ("champs-pool-A-0", GROUP),
        ("pro1-a1-7", REGULAR),
        ("pro-w10-3", REGULAR),
    ],
)
def test_every_vocabulary_reaches_a_class(label: str, expected: str) -> None:
    assert stage.classify_label(label) == expected


def test_the_losers_final_is_not_the_grand_final() -> None:
    """Cito carries LF beside WF and GF at the same event."""
    assert stage.classify_label("LF") == BRACKET


def test_an_unknown_label_decides_nothing() -> None:
    assert stage.classify_label("Unknown Round") is None
    assert stage.classify_label("") is None
    assert stage.classify_label(None) is None


LEAGUE_ONLY = League(playoffs=False, reason="test")
WITH_PLAYOFFS = League(playoffs=True, reason="test")


def test_a_league_event_is_league_play_whatever_its_labels_say() -> None:
    """The 2020 home series labelled each weekend as a tournament."""
    for label in ("Group Play A Winners Round 1", "Winners Round 1", "Grand Finals", None):
        assert stage.derive(LEAGUE_ONLY, label) == LEAGUE


def test_a_league_with_its_own_playoffs_keeps_the_bracket() -> None:
    assert stage.derive(WITH_PLAYOFFS, "pro1-a1-7") == LEAGUE
    assert stage.derive(WITH_PLAYOFFS, "Group Stage") == LEAGUE
    assert stage.derive(WITH_PLAYOFFS, "pro1-winners-1-0") == BRACKET
    assert stage.derive(WITH_PLAYOFFS, "pro1-grand-finals-0") == FINAL


def test_an_event_has_no_league_play() -> None:
    """A round-robin day at an event is group play, and no label means no stage."""
    assert stage.derive(None, "Day 2") == GROUP
    assert stage.derive(None, "Group A") == GROUP
    assert stage.derive(None, "OWR4") == BRACKET
    assert stage.derive(None, "GF") == FINAL
    assert stage.derive(None, None) is None


def test_the_league_list_loads_and_names_each_reason() -> None:
    leagues = stage.Leagues.load()
    assert leagues.get("CDL Week 2: London") == League(
        playoffs=False,
        reason="2020 home series weekend: league play, labelled as a small tournament",
    )
    assert all(entry.reason for entry in leagues.events.values())
    assert leagues.get("CDL Championship") is None
