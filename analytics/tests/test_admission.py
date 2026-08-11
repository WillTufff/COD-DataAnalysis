"""The three gates, on fixtures where the answer is known.

No database. Each test pins one gate's verdict on a column built to fail it, so
a gate that stops discriminating fails here rather than in a run where nobody
reads the artifact.
"""

from datetime import date

from cdlhub_analytics.maprows import MODE_HARDPOINT, MapRow
from cdlhub_analytics.ratings.admission import (
    LEAKY_ACCURACY,
    _denominator_failures,
    verdicts,
)
from cdlhub_analytics.ratings.player_rating import ELIGIBLE_BOTH, Cohort, Feature


def row(player_id: int, team_id: int, game_id: int, **values: float) -> MapRow:
    return MapRow(
        player_id=player_id,
        team_id=team_id,
        game_id=game_id,
        series_id=game_id,
        season_id=1,
        mode_id=1,
        mode_slug=MODE_HARDPOINT,
        title="WWII",
        event_id=1,
        played_at=date(2018, 1, 1),
        duration_s=600.0,
        winner_team_id=1,
        values=dict(values),
        team_kills=0.0,
        team_hill_time=0.0,
    )


def feature(key: str, *sources: str) -> Feature:
    return Feature(
        key=key,
        label=key,
        numerator=lambda r: r.get(sources[0]),
        denominator=lambda r: r.get(sources[-1]),
        denom_kind="shots",
        sources=sources,
        eligibility=ELIGIBLE_BOTH,
    )


def test_a_side_with_no_denominator_is_counted_against_its_column() -> None:
    """Gate 1's other half: the column is populated, and one side of the map
    still cannot form the rate — which drops the whole map from the design."""
    shooter = feature("accuracy", "hits", "shots")
    cohort = Cohort(
        season_id=1, mode_id=1, mode_slug=MODE_HARDPOINT, title="WWII", features=(shooter,)
    )
    rows = [
        row(1, 1, 10, hits=4.0, shots=10.0, kills=1.0, deaths=1.0),
        row(2, 2, 10, hits=0.0, shots=0.0, kills=1.0, deaths=1.0),
    ]
    failures = _denominator_failures(rows, {(1, 1): cohort})
    assert failures[(1, 1)]["accuracy"] == 1


def test_a_column_both_sides_can_form_costs_nothing() -> None:
    shooter = feature("accuracy", "hits", "shots")
    cohort = Cohort(
        season_id=1, mode_id=1, mode_slug=MODE_HARDPOINT, title="WWII", features=(shooter,)
    )
    rows = [
        row(1, 1, 10, hits=4.0, shots=10.0, kills=1.0, deaths=1.0),
        row(2, 2, 10, hits=3.0, shots=12.0, kills=1.0, deaths=1.0),
    ]
    assert _denominator_failures(rows, {(1, 1): cohort}) == {}


def test_the_leaky_threshold_is_below_a_win_condition_and_above_a_coin_flip() -> None:
    """The line the artifact reports against. Captures decide Capture the Flag
    and score 1.00; a column with no signal sits at 0.50."""
    assert 0.5 < LEAKY_ACCURACY < 1.0


def cohort_row(
    title: str,
    direction: int | None = 1,
    sign_accuracy: float | None = 0.6,
    sides_without_denominator: int = 0,
) -> dict[str, object]:
    return {
        "year": 2018,
        "title": title,
        "mode": "Hardpoint",
        "n_maps": 100,
        "sign_accuracy": sign_accuracy,
        "sign_n": 100,
        "direction": direction,
        "sides_without_denominator": sides_without_denominator,
    }


def test_a_column_pointing_two_ways_across_titles_is_not_portable() -> None:
    """Gate 3, on the shape that made it worth having: hill defends points at
    hill time one way on the CWL titles and the other way on Black Ops 4."""
    v = verdicts([cohort_row("IW"), cohort_row("WWII"), cohort_row("BO4", direction=-1)])
    assert v["portable"] is False
    assert v["direction_by_title"] == {"BO4": [-1], "IW": [1], "WWII": [1]}


def test_one_direction_across_every_title_travels() -> None:
    v = verdicts([cohort_row("IW"), cohort_row("WWII"), cohort_row("BO4")])
    assert v["portable"] is True
    assert v["titles"] == ["BO4", "IW", "WWII"]


def test_a_column_disagreeing_with_itself_inside_one_title_is_not_portable() -> None:
    """The `headshot_rate` case: three Infinite Warfare cohorts, two signs. The
    verdict has to read cohorts rather than titles, or this passes as portable."""
    v = verdicts([cohort_row("IW"), cohort_row("IW", direction=-1)])
    assert v["portable"] is False
    assert v["direction_by_title"] == {"IW": [-1, 1]}


def test_cohorts_the_sign_rule_had_no_opinion_on_are_not_evidence() -> None:
    """A cohort where every map tied on the column scores no direction. Counting
    it would let a null vote decide portability."""
    v = verdicts([cohort_row("IW"), cohort_row("BO4", direction=None, sign_accuracy=None)])
    assert v["portable"] is True
    assert v["direction_by_title"] == {"IW": [1]}
    assert v["titles"] == ["IW"]
    assert v["n_cohorts"] == 2  # still reported, just not as direction evidence


def test_the_leaky_verdict_reads_the_worst_cohort_not_the_average() -> None:
    """A column that knew the winner in one cohort is leaky, even if it looked
    harmless in five others."""
    rows = [cohort_row("IW", sign_accuracy=0.52) for _ in range(5)]
    assert verdicts(rows)["leaky"] is False
    rows.append(cohort_row("WWII", sign_accuracy=0.99))
    v = verdicts(rows)
    assert v["leaky"] is True
    assert v["max_sign_accuracy"] == 0.99


def test_denominator_cost_sums_across_cohorts() -> None:
    v = verdicts(
        [
            cohort_row("WWII", sides_without_denominator=8),
            cohort_row("BO6", sides_without_denominator=1),
        ]
    )
    assert v["sides_without_denominator"] == 9
