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
    return blend.SeasonScore(player_id, season_id, s, sd)


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
