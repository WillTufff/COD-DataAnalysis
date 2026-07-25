from typing import Any

from cdlhub_analytics.insights import (
    Atom,
    _ordinal,
    best_per_season,
    cap_per_subject,
)


def test_ordinal() -> None:
    assert _ordinal(1) == "1st"
    assert _ordinal(2) == "2nd"
    assert _ordinal(3) == "3rd"
    assert _ordinal(4) == "4th"
    assert _ordinal(11) == "11th"
    assert _ordinal(12) == "12th"
    assert _ordinal(13) == "13th"
    assert _ordinal(21) == "21st"
    assert _ordinal(91) == "91st"
    assert _ordinal(100) == "100th"


def atom(
    kind: str,
    subject_id: int,
    score: float,
    headline: str = "h",
    subject_type: str = "player",
    **detail: Any,
) -> Atom:
    return Atom(subject_type, subject_id, kind, headline, dict(detail), score)


# ---------- best_per_season ----------


def test_best_per_season_keeps_only_the_strongest_mode_slice() -> None:
    """One season's all-modes row plus its per-mode rows are one finding."""
    atoms = [
        atom("outlier", 1, 0.60, "2019 all modes", season_year=2019, mode=None),
        atom("outlier", 1, 0.80, "2019 Hardpoint", season_year=2019, mode="Hardpoint"),
        atom("outlier", 1, 0.55, "2019 S&D", season_year=2019, mode="Search & Destroy"),
    ]
    kept = best_per_season(atoms)
    assert len(kept) == 1
    assert kept[0].headline == "2019 Hardpoint"


def test_best_per_season_keeps_separate_seasons() -> None:
    """Two strong seasons are two findings, not one."""
    atoms = [
        atom("outlier", 1, 0.8, "2018", season_year=2018),
        atom("outlier", 1, 0.7, "2019", season_year=2019),
    ]
    assert len(best_per_season(atoms)) == 2


def test_best_per_season_keeps_separate_players() -> None:
    atoms = [
        atom("outlier", 1, 0.8, "a", season_year=2019),
        atom("outlier", 2, 0.7, "b", season_year=2019),
    ]
    assert len(best_per_season(atoms)) == 2


def test_best_per_season_passes_through_seasonless_atoms() -> None:
    """Career milestones and team peaks carry no season and must survive."""
    atoms = [
        atom("milestone", 1, 0.5, "career maps"),
        atom("outlier", 1, 0.8, "2019", season_year=2019),
    ]
    kept = best_per_season(atoms)
    assert {a.headline for a in kept} == {"career maps", "2019"}


def test_best_per_season_is_order_independent() -> None:
    a = atom("outlier", 1, 0.8, "high", season_year=2019)
    b = atom("outlier", 1, 0.8, "also", season_year=2019)
    assert best_per_season([a, b])[0].headline == best_per_season([b, a])[0].headline


# ---------- cap_per_subject ----------


def test_cap_per_subject_limits_one_subject_to_the_best_two() -> None:
    atoms = [atom("profile_extreme", 1, s, f"h{s}") for s in (0.9, 0.8, 0.7, 0.6)]
    kept = cap_per_subject(atoms, limit=2)
    assert [a.score for a in kept] == [0.9, 0.8]


def test_cap_per_subject_is_per_kind_and_per_subject() -> None:
    atoms = [
        atom("profile_extreme", 1, 0.9),
        atom("profile_extreme", 1, 0.8),
        atom("profile_extreme", 1, 0.7),  # dropped: third of this kind
        atom("outlier", 1, 0.7),  # kept: different kind
        atom("profile_extreme", 2, 0.7),  # kept: different subject
    ]
    kept = cap_per_subject(atoms, limit=2)
    assert len(kept) == 4
    assert sum(1 for a in kept if a.kind == "profile_extreme" and a.subject_id == 1) == 2


def test_cap_per_subject_separates_players_from_teams_with_the_same_id() -> None:
    """subject_id is only unique within subject_type."""
    atoms = [
        atom("team_style", 1, 0.9, subject_type="team"),
        atom("team_style", 1, 0.8, subject_type="team"),
        atom("team_style", 1, 0.7, subject_type="player"),
    ]
    assert len(cap_per_subject(atoms, limit=2)) == 3


def test_cap_per_subject_leaves_uncapped_kinds_alone() -> None:
    """Per-cohort model summaries are one fact each, however many share a subject."""
    atoms = [atom("what_wins", 1, s) for s in (0.9, 0.8, 0.7, 0.6)]
    assert len(cap_per_subject(atoms, limit=2)) == 4


def test_cap_per_subject_is_deterministic_across_orderings() -> None:
    atoms = [atom("outlier", 1, 0.5, f"h{i}") for i in range(5)]
    assert [a.headline for a in cap_per_subject(atoms, limit=2)] == [
        a.headline for a in cap_per_subject(list(reversed(atoms)), limit=2)
    ]
