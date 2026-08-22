"""Breadth score: slice de-dup, the coverage weight, and the disagreement SD."""

from __future__ import annotations

import pytest

from cdlhub_analytics.career_rank import breadth as breadth

HP: int | None = 1  # mode ids are opaque to breadth.py
SND: int | None = 2


def point(
    player_id: int, season_id: int, mode_id: int | None, metric: str, pctl: float
) -> breadth.MetricPoint:
    return breadth.MetricPoint(player_id, season_id, mode_id, metric, pctl)


# --------------------------------------------------------------- de-dup


def test_per_map_metric_is_dropped_when_its_per_10_twin_is_present() -> None:
    keys = ["kills_pm", "kills_p10", "deaths_pm"]
    assert breadth._redundant_per_map(keys) == {"kills_pm"}


def test_a_per_map_metric_with_no_twin_survives() -> None:
    keys = ["kills_pm", "deaths_pm"]
    assert breadth._redundant_per_map(keys) == set()


# ------------------------------------------------------------- slice floor


def test_a_slice_below_the_minimum_stat_count_does_not_score() -> None:
    points = [point(1, 19, HP, "obj_a", 0.9)]  # only one stat
    slice_maps = {(1, 19, HP): 10}
    out = breadth.build(points, slice_maps)
    assert out == []


def test_a_slice_at_the_minimum_stat_count_scores() -> None:
    points = [point(1, 19, HP, "obj_a", 0.9), point(1, 19, HP, "obj_b", 0.7)]
    slice_maps = {(1, 19, HP): 10}
    out = breadth.build(points, slice_maps)
    assert len(out) == 1
    assert out[0].score == pytest.approx(80.0)


# --------------------------------------------------------- coverage weight


def test_season_score_is_weighted_by_each_modes_share_of_maps() -> None:
    points = [
        point(1, 19, HP, "a", 1.0),
        point(1, 19, HP, "b", 1.0),  # HP slice: 100
        point(1, 19, SND, "c", 0.0),
        point(1, 19, SND, "d", 0.0),  # SND slice: 0
    ]
    # HP played 3x as much as SND this season.
    slice_maps = {(1, 19, HP): 30, (1, 19, SND): 10}
    out = breadth.build(points, slice_maps)
    assert len(out) == 1
    assert out[0].score == pytest.approx(75.0)


def test_a_mode_only_played_once_gets_a_floor_weight_of_one() -> None:
    """A slice missing from slice_maps must not zero out its own weight."""
    points = [point(1, 19, HP, "a", 1.0), point(1, 19, HP, "b", 1.0)]
    out = breadth.build(points, {})
    assert out[0].score == pytest.approx(100.0)


# ----------------------------------------------------------------------- sd


def test_sd_is_zero_when_the_baskets_metrics_fully_agree() -> None:
    points = [point(1, 19, HP, "a", 0.5), point(1, 19, HP, "b", 0.5)]
    out = breadth.build(points, {(1, 19, HP): 10})
    assert out[0].sd == pytest.approx(0.0)


def test_sd_is_positive_when_the_baskets_metrics_disagree() -> None:
    points = [point(1, 19, HP, "a", 0.9), point(1, 19, HP, "b", 0.1)]
    out = breadth.build(points, {(1, 19, HP): 10})
    assert out[0].sd is not None
    assert out[0].sd > 0.0


def test_more_disagreement_produces_a_wider_sd() -> None:
    tight = breadth.build(
        [point(1, 19, HP, "a", 0.55), point(1, 19, HP, "b", 0.45)], {(1, 19, HP): 10}
    )
    wide = breadth.build(
        [point(1, 19, HP, "a", 0.95), point(1, 19, HP, "b", 0.05)], {(1, 19, HP): 10}
    )
    assert tight[0].sd is not None
    assert wide[0].sd is not None
    assert wide[0].sd > tight[0].sd


# --------------------------------------------------------- map-count shrinkage


def season(player_id: int, season_id: int, score: float, maps: int) -> breadth.SeasonBreadth:
    return breadth.SeasonBreadth(
        player_id=player_id,
        season_id=season_id,
        score=score,
        sd=4.0,
        n_slices=1,
        n_stats=10,
        maps=maps,
    )


def _cohort(season_id: int, scores: list[float], maps: int) -> list[breadth.SeasonBreadth]:
    return [season(i, season_id, s, maps) for i, s in enumerate(scores, start=1)]


def test_a_short_season_is_pulled_further_toward_its_seasons_mean() -> None:
    """The whole reason the shrinkage exists: a 40-map season is a noisier
    estimate than a 124-map one, so left alone it reaches further out."""
    short = breadth.shrink(_cohort(1, [90.0, 50.0, 50.0, 50.0, 50.0], 40))
    long = breadth.shrink(_cohort(2, [90.0, 50.0, 50.0, 50.0, 50.0], 124))
    assert short[0].score < long[0].score
    assert short[0].score > 50.0


def test_the_shrinkage_never_moves_a_season_past_its_seasons_mean() -> None:
    rows = breadth.shrink(_cohort(1, [90.0, 10.0, 50.0, 50.0, 50.0], 20))
    mean = 50.0
    assert mean < rows[0].score < 90.0
    assert 10.0 < rows[1].score < mean


def test_a_season_with_no_maps_collapses_onto_the_mean() -> None:
    rows = breadth.shrink(_cohort(1, [90.0, 50.0, 50.0, 50.0, 50.0], 0))
    assert all(row.score == pytest.approx(58.0) for row in rows)


def test_sd_narrows_with_the_deviation_it_measures() -> None:
    rows = breadth.shrink(_cohort(1, [90.0, 50.0, 50.0, 50.0, 50.0], 40))
    assert rows[0].sd is not None
    assert rows[0].sd < 4.0


def test_a_cohort_too_small_to_have_a_field_is_left_alone() -> None:
    rows = breadth.shrink(_cohort(1, [90.0, 50.0], 40))
    assert [row.score for row in rows] == [90.0, 50.0]


def test_the_shrinkage_reads_only_its_own_seasons_field() -> None:
    """A season is moved against the players it played, never against another
    era's mean, so admitting an era cannot move the era already there."""
    alone = breadth.shrink(_cohort(1, [90.0, 50.0, 50.0, 50.0, 50.0], 40))
    beside = breadth.shrink(
        _cohort(1, [90.0, 50.0, 50.0, 50.0, 50.0], 40) + _cohort(2, [10.0] * 5, 200)
    )
    assert beside[0].score == pytest.approx(alone[0].score)


def test_the_refit_recovers_a_planted_sampling_variance() -> None:
    """`estimate_shrink_k` is published beside every run to make drift from the
    frozen constant visible, so it has to recover a K it was never told."""
    import random

    rng = random.Random(11)
    planted = 30.0
    true_sd = 10.0
    rows: list[breadth.SeasonBreadth] = []
    player = 0
    for season_id, maps in enumerate([10, 20, 40, 80, 160, 320], start=1):
        noise = true_sd * (planted / maps) ** 0.5
        for _ in range(400):
            player += 1
            rows.append(
                season(
                    player, season_id, 50.0 + rng.gauss(0.0, true_sd) + rng.gauss(0.0, noise), maps
                )
            )
    fit = breadth.estimate_shrink_k(rows)
    assert fit["fitted"] == 1
    assert planted * 0.6 < fit["k"] < planted * 1.6
