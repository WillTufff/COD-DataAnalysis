"""The VALUE backbone: the scale map, the declared weight, and the missing half."""

from __future__ import annotations

import pytest

from cdlhub_analytics.career_rank import breadth, value_backbone


def season(
    player_id: int, season_id: int, score: float, sd: float | None = 4.0
) -> breadth.SeasonBreadth:
    return breadth.SeasonBreadth(
        player_id=player_id,
        season_id=season_id,
        score=score,
        sd=sd,
        n_slices=1,
        n_stats=10,
        maps=40,
        families=("volume",),
    )


def _cohort(season_id: int, scores: list[float]) -> list[breadth.SeasonBreadth]:
    return [season(i, season_id, s) for i, s in enumerate(scores, start=1)]


def test_the_weight_is_the_declared_one() -> None:
    """Fixed in the pre-registration before the run and not revisited after."""
    assert value_backbone.BREADTH_WEIGHT == 0.75
    assert value_backbone.VALUE_WEIGHT == 0.25
    assert value_backbone.BREADTH_WEIGHT + value_backbone.VALUE_WEIGHT == 1.0


def test_value_is_mapped_onto_the_breadth_scale_before_it_is_blended() -> None:
    """VALUE lives on its own scale; blended raw it would swamp or vanish.

    Here VALUE ranks the field the same way breadth does but on a scale a
    hundred times smaller. After the map, agreement means the blend changes
    nothing, which is what a corroborant agreeing has to look like.
    """
    rows = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0])
    values = {(i, 19): 0.1 * row.score for i, row in enumerate(rows, start=1)}
    out = value_backbone.blend(rows, values)
    for original, blended in zip(rows, out, strict=True):
        assert blended.score == pytest.approx(original.score)


def test_a_disagreeing_value_moves_the_score_by_a_quarter_of_the_gap() -> None:
    rows = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0])
    # VALUE ranks the field in the exact reverse of breadth.
    values = {(i, 19): -0.1 * row.score for i, row in enumerate(rows, start=1)}
    out = value_backbone.blend(rows, values)
    field_mean = 50.0
    for original, blended in zip(rows, out, strict=True):
        mirrored = field_mean - (original.score - field_mean)
        assert blended.score == pytest.approx(0.75 * original.score + 0.25 * mirrored)


def test_a_season_with_no_value_row_is_scored_on_breadth_alone() -> None:
    """The missing half renormalizes; it is never a zero inside the mean.

    The rest of the field still blends, so this is the one season carrying its
    breadth score unchanged while its neighbours move.
    """
    rows = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0, 20.0])
    # VALUE ranks the field in reverse, so a blended season has to move.
    values = {(i, 19): -0.1 * row.score for i, row in enumerate(rows, start=1) if i != 3}
    out = value_backbone.blend(rows, values)
    missing = next(row for row in out if row.player_id == 3)
    assert missing.value_scaled is None
    assert missing.score == pytest.approx(50.0)
    assert all(row.score != pytest.approx(row.breadth) for row in out if row.player_id != 3)


def test_a_field_too_small_to_standardize_is_scored_on_breadth_alone() -> None:
    rows = _cohort(19, [10.0, 90.0])
    values = {(1, 19): 1.0, (2, 19): 9.0}
    out = value_backbone.blend(rows, values)
    assert all(row.value_scaled is None for row in out)
    assert [row.score for row in out] == [10.0, 90.0]


def test_a_field_with_no_value_spread_is_scored_on_breadth_alone() -> None:
    """Every season on the same VALUE has no scale to map onto, and dividing by
    that spread would be a division by zero rather than a reading."""
    rows = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0])
    values = {(i, 19): 5.0 for i in range(1, 6)}
    out = value_backbone.blend(rows, values)
    assert all(row.value_scaled is None for row in out)
    assert [row.score for row in out] == [10.0, 30.0, 50.0, 70.0, 90.0]


def test_the_map_never_moves_a_season_against_another_era() -> None:
    """Both moments are the season's own, so a second season's field cannot
    reach the first — the same property the shrinkage has."""
    first = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0])
    second = _cohort(20, [10.0, 30.0, 50.0, 70.0, 90.0])
    values = {(i, 19): 0.1 * row.score for i, row in enumerate(first, start=1)}
    values.update({(i, 20): 500.0 + 90.0 * row.score for i, row in enumerate(second, start=1)})
    alone = value_backbone.blend(first, values)
    together = [row for row in value_backbone.blend(first + second, values) if row.season_id == 19]
    assert [row.score for row in alone] == [row.score for row in together]


def test_the_carried_width_is_the_breadth_halfs_own_width_scaled() -> None:
    rows = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0])
    values = {(i, 19): 0.1 * row.score for i, row in enumerate(rows, start=1)}
    out = value_backbone.blend(rows, values)
    assert all(row.sd == pytest.approx(4.0 * 0.75) for row in out)


def test_coverage_counts_the_seasons_the_value_half_reached() -> None:
    rows = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0, 20.0])
    values = {(i, 19): 0.1 * row.score for i, row in enumerate(rows, start=1) if i != 3}
    report = value_backbone.coverage(value_backbone.blend(rows, values))
    assert report["n_seasons"] == 6
    assert report["n_with_value"] == 5
    assert report["n_breadth_only"] == 1


def test_a_field_below_the_cohort_floor_scores_no_season_on_value() -> None:
    """Four rated seasons is not a field to standardize against.

    The floor is `MIN_SHRINK_COHORT`, the same one the shrinkage reads, for the
    same reason: a location and scale taken from four numbers is noise with two
    moments attached.
    """
    rows = _cohort(19, [10.0, 30.0, 50.0, 70.0, 90.0])
    values = {(i, 19): 0.1 * row.score for i, row in enumerate(rows, start=1) if i != 3}
    assert len(values) < breadth.MIN_SHRINK_COHORT
    out = value_backbone.blend(rows, values)
    assert all(row.value_scaled is None for row in out)
