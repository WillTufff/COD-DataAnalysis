"""Career blend: peak / best-three / total over the breadth score, and its SD."""

from __future__ import annotations

import pytest

from cdlhub_analytics.career_rank import blend as blend
from cdlhub_analytics.ratings.preflight import Season

CDL = {
    19: Season(19, 2020, "CDL"),
    20: Season(20, 2021, "CDL"),
    21: Season(21, 2022, "CDL"),
    22: Season(22, 2023, "CDL"),
}
CWL = {1: Season(1, 2017, "CWL"), 2: Season(2, 2018, "CWL"), 12: Season(12, 2019, "CWL")}
SEASONS = {**CWL, **CDL}


def score(player_id: int, season_id: int, s: float, sd: float | None = 2.0) -> blend.SeasonScore:
    return blend.SeasonScore(player_id, season_id, {blend.PERFORMANCE: s}, sd)


def finish_only(player_id: int, season_id: int, share: float = 0.4) -> blend.SeasonScore:
    """A season the box-score archive does not reach: a finish and nothing to
    score it with."""
    return blend.SeasonScore(player_id, season_id, {blend.RESUME: share}, None)


def test_total_is_the_sum_of_season_scores() -> None:
    rows = [score(1, 19, 50.0), score(1, 20, 60.0)]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].total == pytest.approx(110.0)


def test_peak_and_total_can_disagree() -> None:
    rows = [
        score(1, 19, 90.0),
        score(1, 20, 10.0),
        score(1, 21, 10.0),
        score(2, 19, 40.0),
        score(2, 20, 40.0),
        score(2, 21, 40.0),
    ]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].peak > out[2].peak
    assert out[2].total > out[1].total


def test_a_career_below_the_season_floor_is_not_qualified() -> None:
    rows = [score(1, 19, 50.0), score(1, 20, 50.0)]  # 2 seasons, floor is 3
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].qualified is False


def test_a_career_at_the_season_floor_is_qualified() -> None:
    rows = [score(1, 19, 50.0), score(1, 20, 50.0), score(1, 21, 50.0)]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].qualified is True


def test_best_three_needs_three_consecutive_league_seasons() -> None:
    rows = [score(1, 19, 5.0), score(1, 21, 5.0), score(1, 22, 5.0)]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].best_three == pytest.approx(10.0)
    assert out[1].best_three_start_season_id == 19


# --------------------------------------------------------------------- sd


def test_total_sd_compounds_the_season_sds() -> None:
    rows = [score(1, 19, 50.0, sd=3.0), score(1, 20, 50.0, sd=4.0)]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].total_sd == pytest.approx(5.0)  # sqrt(3^2 + 4^2)


def test_a_missing_season_sd_withdraws_the_total_interval() -> None:
    rows = [score(1, 19, 50.0, sd=3.0), score(1, 20, 50.0, sd=None)]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].total_sd is None


def test_the_artifact_carries_total_sd_beside_total() -> None:
    rows = (
        [score(p, 19, float(p) * 10.0, sd=1.0) for p in range(1, 4) for _ in range(1)]
        + [score(p, 20, float(p) * 10.0, sd=1.0) for p in range(1, 4)]
        + [score(p, 21, float(p) * 10.0, sd=1.0) for p in range(1, 4)]
    )
    built = blend.build(rows, SEASONS)
    payload = blend.artifact(built)
    top = payload["top_ten_by_total"][0]
    assert "total_sd" in top
    assert top["total_sd"] == pytest.approx(1.73, abs=0.01)  # rounded sqrt(3)


def test_a_component_the_archive_misses_is_absent_from_the_mean_not_zero() -> None:
    assert blend.renormalize({blend.PERFORMANCE: 50.0}) == pytest.approx(50.0)
    # RESUME carries no season weight yet, so a season holding only a finish
    # has nothing to score and comes back unscored rather than scored zero.
    assert blend.renormalize({blend.RESUME: 0.4}) is None
    assert blend.renormalize({}) is None


def test_renormalization_rescales_the_weights_to_what_is_present() -> None:
    weights = {blend.PERFORMANCE: 0.75, blend.RESUME: 0.25}
    both = blend.renormalize({blend.PERFORMANCE: 80.0, blend.RESUME: 40.0}, weights)
    assert both == pytest.approx(0.75 * 80.0 + 0.25 * 40.0)
    # One component missing does not drag the mean toward zero; the surviving
    # weight is rescaled to 1.
    assert blend.renormalize({blend.PERFORMANCE: 80.0}, weights) == pytest.approx(80.0)


def test_a_finish_only_season_moves_coverage_and_never_the_total() -> None:
    scored = [score(1, 19, 50.0), score(1, 20, 60.0), score(1, 21, 70.0)]
    out = {r.player_id: r for r in blend.build(scored, SEASONS)}[1]
    with_finish = [*scored, finish_only(1, 1)]
    after = {r.player_id: r for r in blend.build(with_finish, SEASONS)}[1]

    assert after.total == pytest.approx(out.total)
    assert after.peak == pytest.approx(out.peak)
    assert after.best_three == pytest.approx(out.best_three)
    assert after.qualified is out.qualified

    assert after.n_seasons == 4
    assert after.seasons_covered == 3
    assert after.coverage_from_year == 2020
    assert after.components_present == (blend.PERFORMANCE, blend.RESUME)


def test_a_career_with_nothing_scorable_is_not_ranked() -> None:
    rows = blend.build([finish_only(1, 19), finish_only(1, 20)], SEASONS)
    assert rows == []


def test_the_artifact_publishes_what_the_board_could_not_see() -> None:
    rows = blend.build(
        [score(1, 19, 50.0), score(1, 20, 60.0), score(1, 21, 70.0), finish_only(1, 1)],
        SEASONS,
    )
    art = blend.artifact(rows, n_unrankable=2)
    assert art["n_unrankable"] == 2
    assert art["n_partial_coverage"] == 1
    assert art["seasons_uncovered"] == 1
    assert art["season_component_weights"] == {blend.PERFORMANCE: 1.0}
