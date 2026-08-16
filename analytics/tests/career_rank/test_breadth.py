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
