import numpy as np

import cdlhub_analytics.gates as gates
import cdlhub_analytics.validation as validation


def season(
    player_id: int,
    year: int,
    value: float | None = 1.0,
    skill: float | None = 0.0,
    external: float | None = 1.0,
    handle: str = "p",
) -> validation.SeasonRow:
    return validation.SeasonRow(
        player_id=player_id,
        season_id=year,
        year=year,
        handle=f"{handle}{player_id}",
        maps=40,
        value=value,
        skill=skill,
        external=external,
        external_maps=40,
        unrated_maps=0,
    )


def test_mid_ranks_do_not_order_ties_by_position() -> None:
    ranks = validation._ranks(np.asarray([1.0, 2.0, 2.0, 3.0], dtype=np.float64))
    assert list(ranks) == [1.0, 2.5, 2.5, 4.0]


def test_a_perfect_agreement_is_spearman_one() -> None:
    rows = [season(i, 2024, value=float(i), external=float(i)) for i in range(1, 12)]
    art = validation.convergent(rows)
    assert art["axes"][0]["spearman"] == 1.0
    assert art["disagreement_count"] == 0


def test_a_reversed_ordering_is_named_as_a_disagreement() -> None:
    rows = [season(i, 2024, value=float(i), external=float(12 - i)) for i in range(1, 12)]
    art = validation.convergent(rows)
    assert art["axes"][0]["spearman"] == -1.0
    assert art["disagreement_count"] > 0


def test_no_disagreement_row_carries_an_undeclared_field() -> None:
    rows = [season(i, 2024, value=float(i), external=float(12 - i)) for i in range(1, 12)]
    art = validation.convergent(rows)
    allowed = set(validation.DISAGREEMENT_FIELDS)
    for row in art["disagreements"]:
        assert set(row) <= allowed


def test_the_external_value_never_reaches_the_payload() -> None:
    # the licence forbids redistribution, so the gate closes the schema
    rows = [season(i, 2024, value=float(i), external=float(12 - i)) for i in range(1, 12)]
    art = validation.convergent(rows)
    assert art["disagreements"], "the fixture is meant to produce disagreements"
    assert "their_rating" not in validation.DISAGREEMENT_FIELDS
    assert all("external" not in key for row in art["disagreements"] for key in row)


def test_an_unrated_map_is_not_a_rating_of_zero() -> None:
    assert validation.UNRATED == 0.0


def test_the_selection_bar_reads_the_season_its_own_team_size() -> None:
    rows = [season(i, 2020, value=float(100 - i)) for i in range(1, 40)]
    awards = [validation.Award("first_team", f"p{i}", 2020, i) for i in range(1, 6)]
    art = validation.face_validity(rows, awards)
    assert art["by_season"][0]["team_size"] == 5
    assert art["by_season"][0]["in_top_n"] == 5


def test_an_award_whose_player_is_unresolved_is_named_not_dropped() -> None:
    rows = [season(i, 2024, value=float(100 - i)) for i in range(1, 40)]
    awards = [validation.Award("first_team", "ghost", 2024, None)] + [
        validation.Award("first_team", f"p{i}", 2024, i) for i in range(1, 4)
    ]
    art = validation.face_validity(rows, awards)
    assert art["population"]["awards_excluded"] == 1
    assert art["excluded"][0]["handle"] == "ghost"


def test_a_rookie_carries_the_count_of_seasons_before_the_award() -> None:
    rows = [season(1, 2023, value=2.0), season(1, 2024, value=2.0)]
    rows += [season(i, 2024, value=1.0) for i in range(2, 6)]
    art = validation.face_validity(rows, [validation.Award("roty", "p1", 2024, 1)])
    assert art["ranked_awards"][0]["prior_rated_seasons"] == 1


def test_common_swaps_keeps_only_what_both_axes_can_score() -> None:
    rows = [season(1, 2024, skill=None), season(2, 2024), season(3, 2024)]
    swaps = [_swap(9, 1, 2), _swap(9, 2, 3)]
    assert len(validation.common_swaps(rows, swaps)) == 1


def _swap(team_id: int, departed: int, arrived: int) -> validation.Swap:
    return validation.Swap(
        team_id=team_id,
        year=2024,
        departed=departed,
        arrived=arrived,
        before_win_rate=0.5,
        after_win_rate=0.5,
        before_maps=16,
        after_maps=16,
    )


def test_a_null_says_whether_the_design_could_have_resolved_it() -> None:
    rows = [season(i, 2024, value=float(i % 3)) for i in range(1, 40)]
    swaps = [_swap(t, (t % 30) + 1, ((t + 7) % 30) + 1) for t in range(1, 30)]
    art = validation.roster_shock(rows, swaps, "value")
    assert art["informative"] is not None
    assert art["detectable_slope"] is not None


def test_the_gate_fails_a_test_with_no_population() -> None:
    bad = {"validation_convergent": {"verdict": "something", "attribution": "x"}}
    assert any("population" in line for line in gates.validation_failures(bad))


def test_the_gate_fails_a_disagreement_carrying_an_undeclared_field() -> None:
    bad = {
        "validation_convergent": {
            "verdict": "something",
            "attribution": "x",
            "population": {"n": 1},
            "disagreements": [{"handle": "p1", "their_rating": 1.23}],
        }
    }
    assert any("undeclared" in line for line in gates.validation_failures(bad))


def test_the_gate_fails_a_one_sided_violation() -> None:
    bad = {
        "validation_retrodiction": {
            "verdict": "something",
            "population": {"n": 1},
            "one_sided_violations": 3,
        }
    }
    assert any("one-sided" in line for line in gates.validation_failures(bad))
