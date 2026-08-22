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
    points = [point(1, 19, HP, "kills_pm", 0.9)]  # only one stat
    slice_maps = {(1, 19, HP): 10}
    out = breadth.build(points, slice_maps)
    assert out == []


def test_a_slice_at_the_minimum_stat_count_scores() -> None:
    points = [point(1, 19, HP, "kills_pm", 0.9), point(1, 19, HP, "deaths_pm", 0.7)]
    slice_maps = {(1, 19, HP): 10}
    out = breadth.build(points, slice_maps)
    assert len(out) == 1
    assert out[0].score == pytest.approx(80.0)


# --------------------------------------------------------- coverage weight


def test_season_score_is_weighted_by_each_modes_share_of_maps() -> None:
    points = [
        point(1, 19, HP, "kills_pm", 1.0),
        point(1, 19, HP, "deaths_pm", 1.0),  # HP slice: 100
        point(1, 19, SND, "snd_kpr", 0.0),
        point(1, 19, SND, "snd_dpr", 0.0),  # SND slice: 0
    ]
    # HP played 3x as much as SND this season.
    slice_maps = {(1, 19, HP): 30, (1, 19, SND): 10}
    out = breadth.build(points, slice_maps)
    assert len(out) == 1
    assert out[0].score == pytest.approx(75.0)


def test_a_mode_only_played_once_gets_a_floor_weight_of_one() -> None:
    """A slice missing from slice_maps must not zero out its own weight."""
    points = [point(1, 19, HP, "kills_pm", 1.0), point(1, 19, HP, "deaths_pm", 1.0)]
    out = breadth.build(points, {})
    assert out[0].score == pytest.approx(100.0)


# ----------------------------------------------------------------------- sd


def test_sd_is_zero_when_the_baskets_metrics_fully_agree() -> None:
    points = [point(1, 19, HP, "kills_pm", 0.5), point(1, 19, HP, "deaths_pm", 0.5)]
    out = breadth.build(points, {(1, 19, HP): 10})
    assert out[0].sd == pytest.approx(0.0)


def test_sd_is_positive_when_the_baskets_metrics_disagree() -> None:
    points = [point(1, 19, HP, "kills_pm", 0.9), point(1, 19, HP, "deaths_pm", 0.1)]
    out = breadth.build(points, {(1, 19, HP): 10})
    assert out[0].sd is not None
    assert out[0].sd > 0.0


def test_more_disagreement_produces_a_wider_sd() -> None:
    tight = breadth.build(
        [point(1, 19, HP, "kills_pm", 0.55), point(1, 19, HP, "deaths_pm", 0.45)], {(1, 19, HP): 10}
    )
    wide = breadth.build(
        [point(1, 19, HP, "kills_pm", 0.95), point(1, 19, HP, "deaths_pm", 0.05)], {(1, 19, HP): 10}
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


def test_the_pooled_slice_is_ignored_beside_a_qualifying_mode_slice() -> None:
    """Beside a mode row the pooled row is the same maps counted twice.

    It used to enter as a seventh slice at the `max(1, 0)` floor, weighing one
    map against modes weighing hundreds. Here the pooled row would drag a
    strong season down if it counted; the score has to read the mode slice
    alone.
    """
    points = [
        breadth.MetricPoint(1, 10, 2, "kill_share", 0.90),
        breadth.MetricPoint(1, 10, 2, "kills_p10", 0.90),
        breadth.MetricPoint(1, 10, None, "kill_share", 0.10),
        breadth.MetricPoint(1, 10, None, "kills_p10", 0.10),
    ]
    rows = breadth.build(points, {(1, 10, 2): 40, (1, 10, None): 40})
    assert len(rows) == 1
    assert rows[0].n_slices == 1
    assert rows[0].score == pytest.approx(90.0)
    assert rows[0].maps == 40


def test_the_pooled_slice_carries_a_season_no_mode_qualifies() -> None:
    """A season spread thin across modes is measured, not missing.

    Each mode falls below the surviving-stat floor while the pooled row clears
    it, which is coverage and not performance. Dropping the pooled row here
    removed the season from the board for having been spread out; it scores on
    the pooled reading instead, at the season's own map count so the shrinkage
    weighs it honestly.
    """
    points = [
        breadth.MetricPoint(1, 10, 2, "kill_share", 0.90),
        breadth.MetricPoint(1, 10, 3, "kill_share", 0.90),
        breadth.MetricPoint(1, 10, None, "kill_share", 0.80),
        breadth.MetricPoint(1, 10, None, "kills_p10", 0.80),
    ]
    rows = breadth.build(points, {(1, 10, 2): 20, (1, 10, 3): 20, (1, 10, None): 40})
    assert len(rows) == 1
    assert rows[0].n_slices == 1
    assert rows[0].score == pytest.approx(80.0)
    assert rows[0].maps == 40


def test_the_maps_query_answers_for_the_pooled_slice() -> None:
    """The pooled key needs its own grouping set.

    `games.mode_id` is never null, so grouping by it alone gave the pooled key
    no count and sent it to the `max(1, ...)` floor — a weight of one map, and
    a shrinkage that pulls the season almost entirely onto its cohort mean.
    """
    assert "GROUPING SETS" in breadth._MAPS_SQL


def test_a_slice_with_no_map_count_is_floored_and_not_dropped() -> None:
    """The floor is a guard on a real mode, not a weight of its own.

    A mode the map join cannot answer for still has to score something, or one
    unanswerable slice takes the whole season to zero. It weighs one map, which
    is the smallest a mode can weigh, and never the whole season.
    """
    points = [
        breadth.MetricPoint(1, 10, 2, "kill_share", 0.90),
        breadth.MetricPoint(1, 10, 2, "kills_p10", 0.90),
        breadth.MetricPoint(1, 10, 3, "kill_share", 0.10),
        breadth.MetricPoint(1, 10, 3, "kills_p10", 0.10),
    ]
    # Mode 2 is answered for with 99 maps; mode 3 is not answered for at all.
    rows = breadth.build(points, {(1, 10, 2): 99})
    assert len(rows) == 1
    assert rows[0].score > 88.0


# ------------------------------------------------------------------ families


def test_every_basket_metric_is_assigned_a_family() -> None:
    """The one guard that keeps the authored table honest.

    The families are authored metric by metric because the catalog's own
    `category` is twelve mode-shaped labels and not them. A gold metric added
    later with no family would otherwise reach `build` and raise there; this
    fails first, and it fails before a run rather than during one.
    """
    basket = set(breadth.gold_basket())
    assert basket - set(breadth.FAMILY) == set()
    assert set(breadth.FAMILY) - basket == set()


def test_every_family_has_at_least_one_metric() -> None:
    assigned = set(breadth.FAMILY.values())
    assert assigned == set(breadth.FAMILIES)


def test_a_family_with_many_metrics_does_not_outweigh_one_with_few() -> None:
    """The whole point of the change: a slice is worth what it measured.

    Five slaying-volume metrics agreeing at 1.0 against one discipline metric
    at 0.0 used to score 83.3, because volume happened to be measured five
    ways. The families each carry half.
    """
    points = [
        point(1, 19, HP, key, 1.0)
        for key in ("kills_pm", "ekia_p10", "damage_pm", "kill_share", "snd_kpr")
    ] + [point(1, 19, HP, "clean_kill_rate", 0.0)]
    out = breadth.build(points, {(1, 19, HP): 10})
    assert out[0].score == pytest.approx(50.0)


def test_an_absent_family_is_not_a_zero() -> None:
    """A family with no surviving metric leaves the mean rather than entering it.

    Two families agreeing at 0.8 score 80 whether or not the other four exist.
    Scoring the absent four as zero would read a 2013-2016 season, which
    carries three families at most, as a bad season instead of a thin one.
    """
    points = [point(1, 19, HP, "kills_pm", 0.8), point(1, 19, HP, "clean_kill_rate", 0.8)]
    out = breadth.build(points, {(1, 19, HP): 10})
    assert out[0].score == pytest.approx(80.0)
    assert out[0].families == ("volume", "discipline")


def test_family_coverage_is_the_union_across_the_slices_that_scored() -> None:
    points = [
        point(1, 19, HP, "kills_pm", 0.5),
        point(1, 19, HP, "hill_time_pm", 0.5),
        point(1, 19, SND, "snd_kpr", 0.5),
        point(1, 19, SND, "snd_fb_rate", 0.5),
    ]
    out = breadth.build(points, {(1, 19, HP): 10, (1, 19, SND): 10})
    assert out[0].families == ("volume", "objective", "opening")


def test_families_are_reported_in_a_fixed_order() -> None:
    """Order comes from `FAMILIES`, never from what the slice happened to hold,
    so two seasons with the same coverage compare equal as written."""
    forward = breadth.build(
        [point(1, 19, HP, "kills_pm", 0.5), point(1, 19, HP, "clean_kill_rate", 0.5)],
        {(1, 19, HP): 10},
    )
    reverse = breadth.build(
        [point(1, 19, HP, "clean_kill_rate", 0.5), point(1, 19, HP, "kills_pm", 0.5)],
        {(1, 19, HP): 10},
    )
    assert forward[0].families == reverse[0].families
