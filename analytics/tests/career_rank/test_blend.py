"""Career blend: the five components, their scaling, and the season sum beside them."""

from __future__ import annotations

from typing import Any

import pytest

from cdlhub_analytics.career_rank import blend as blend
from cdlhub_analytics.ratings.preflight import Season


@pytest.fixture(autouse=True)
def _isolated_spans_path(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Every test in this module fits its spans fresh unless it opts into a
    pin: the package's own `spans.json`, once a base run has actually been
    frozen, would otherwise leak into every other test here and pin a cohort
    none of them chose."""
    path = tmp_path / "spans.json"
    monkeypatch.setattr(blend, "SPANS_PATH", path)
    return path


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


def test_season_total_is_the_sum_of_season_scores() -> None:
    rows = [score(1, 19, 50.0), score(1, 20, 60.0)]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[1].season_total == pytest.approx(110.0)


def test_peak_and_the_season_sum_can_disagree() -> None:
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
    assert out[2].season_total > out[1].season_total


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
    top = payload["top_ten_by_season_total"][0]
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


def test_a_finish_only_season_moves_coverage_and_never_the_season_sum() -> None:
    scored = [score(1, 19, 50.0), score(1, 20, 60.0), score(1, 21, 70.0)]
    out = {r.player_id: r for r in blend.build(scored, SEASONS)}[1]
    with_finish = [*scored, finish_only(1, 1)]
    after = {r.player_id: r for r in blend.build(with_finish, SEASONS)}[1]

    assert after.season_total == pytest.approx(out.season_total)
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


# ------------------------------------------------------------------ the blend


def full_career(player_id: int, level: float) -> list[blend.SeasonScore]:
    """Three consecutive CDL seasons at one level, so a cohort can be built
    without every test restating one."""
    return [score(player_id, season_id, level) for season_id in (19, 20, 21)]


def test_the_blend_weights_are_the_pre_registered_five() -> None:
    assert blend.CAREER_COMPONENT_WEIGHTS == {
        blend.PEAK: 20.0,
        blend.PRIME: 25.0,
        blend.LONGEVITY: 20.0,
        blend.RESUME: 25.0,
        blend.ACCOLADE: 10.0,
    }


def test_three_of_the_five_weights_read_one_input() -> None:
    assert blend.CAREER_AXIS_WEIGHTS == {
        blend.PERFORMANCE: 65.0,
        blend.RESUME: 25.0,
        blend.ACCOLADE: 10.0,
    }
    assert sum(blend.CAREER_AXIS_WEIGHTS.values()) == sum(blend.CAREER_COMPONENT_WEIGHTS.values())


def test_total_is_the_blend_and_not_the_season_sum() -> None:
    rows = full_career(1, 80.0) + full_career(2, 40.0)
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    # The p1/p99 span over a two-career cohort sits just inside [40, 80], so
    # the better career reads a little above 100 on PEAK and PRIME rather
    # than exactly 100, and the blend is those two weights over the five:
    # (20 + 25) / 100 of that scaled value. The season sums are 240 and 120
    # and say nothing about it.
    assert out[1].total == pytest.approx(45.45918367346939)
    assert out[2].total == pytest.approx(-0.4591836734693876)
    assert out[1].season_total == pytest.approx(240.0)


def test_resume_enters_the_ranking() -> None:
    """The whole point of the phase: a career that finished better outranks an
    equal performer, which a sum of season scores could never say."""
    plain = full_career(1, 50.0)
    winner = [
        blend.SeasonScore(2, season_id, {blend.PERFORMANCE: 50.0, blend.RESUME: 0.5}, 2.0)
        for season_id in (19, 20, 21)
    ]
    out = {r.player_id: r for r in blend.build(plain + winner, SEASONS)}
    assert out[2].total > out[1].total
    assert out[1].season_total == pytest.approx(out[2].season_total)


def test_a_component_the_archive_cannot_see_renormalizes_away() -> None:
    """A career inside a year that named no honour has no award axis. It is
    scored on the four components it has, not charged a zero for the fifth."""
    rows = full_career(1, 50.0) + full_career(2, 90.0)
    covered = {r.player_id: r for r in blend.build(rows, SEASONS, accolade_years={2020})}
    absent = {r.player_id: r for r in blend.build(rows, SEASONS, accolade_years={1999})}
    assert blend.ACCOLADE in covered[1].career_components
    assert blend.ACCOLADE not in absent[1].career_components
    assert sum(blend.CAREER_COMPONENT_WEIGHTS[n] for n in absent[1].career_components) == 90.0


def test_never_winning_an_award_is_a_zero_and_not_an_absence() -> None:
    """A career alongside a first team it did not win is charged for it. This
    is the difference the coverage rule exists to keep."""
    empty = full_career(1, 50.0)
    decorated = [
        blend.SeasonScore(2, season_id, {blend.PERFORMANCE: 50.0, blend.ACCOLADE: 0.2}, 2.0)
        for season_id in (19, 20, 21)
    ]
    out = {r.player_id: r for r in blend.build(empty + decorated, SEASONS, accolade_years={2020})}
    # The p1/p99 span over [0.0, 0.2] sits just inside that range, so the
    # zero reads a shade below 0 and the win a shade above 100 rather than
    # landing exactly on the endpoints.
    assert out[1].career_components[blend.ACCOLADE] == pytest.approx(-1.0204081632653061)
    assert out[2].career_components[blend.ACCOLADE] == pytest.approx(101.02040816326533)
    assert out[2].total > out[1].total


def test_longevity_is_measured_above_the_seasons_replacement_level() -> None:
    rows = full_career(1, 60.0)
    out = {r.player_id: r for r in blend.build(rows, SEASONS, {19: 40.0, 20: 40.0, 21: 50.0})}
    assert out[1].longevity == pytest.approx(20.0 + 20.0 + 10.0)


def test_a_season_below_replacement_contributes_zero_and_never_a_negative() -> None:
    """Only a season under the map floor can fall below a floor built from
    seasons that cleared it, and it cannot take a career backwards."""
    rows = full_career(1, 60.0) + [score(1, 22, 10.0)]
    out = {
        r.player_id: r for r in blend.build(rows, SEASONS, dict.fromkeys((19, 20, 21, 22), 40.0))
    }
    assert out[1].longevity == pytest.approx(60.0)


def test_a_season_with_no_replacement_level_contributes_nothing() -> None:
    rows = full_career(1, 60.0)
    out = {r.player_id: r for r in blend.build(rows, SEASONS, {19: 40.0})}
    assert out[1].longevity == pytest.approx(20.0)


def test_the_scales_are_fitted_on_the_qualified_cohort_alone() -> None:
    """A career below the season floor is not ranked, so it cannot set the top
    of a scale the ranked careers are read against."""
    rows = full_career(1, 50.0) + full_career(2, 70.0) + [score(3, 19, 99.0)]
    built = blend.build(rows, SEASONS)
    out = {r.player_id: r for r in built}
    assert out[3].qualified is False
    # The p1/p99 span over [50, 70] sits just inside that range (50.2, 69.8),
    # so the top of the qualified cohort reads a shade above 100 rather than
    # landing exactly on it.
    assert out[2].career_components[blend.PEAK] == pytest.approx(101.02040816326533)
    # The unranked career scales off the top of the cohort rather than being
    # clamped into it.
    assert out[3].career_components[blend.PEAK] > 100.0
    assert blend.scales(built)[blend.PEAK] == pytest.approx((50.2, 69.8))


def test_a_career_with_no_three_season_window_has_no_prime() -> None:
    """The window is three seasons of the league and a gap costs what it cost,
    so two seasons three years apart still have one. A career with a single
    season has none, and PRIME is absent rather than zero."""
    rows = full_career(1, 50.0) + [score(2, 22, 50.0)]
    out = {r.player_id: r for r in blend.build(rows, SEASONS)}
    assert out[2].best_three is None
    assert blend.PRIME not in out[2].career_components


def test_the_artifact_publishes_the_weights_and_the_coverage() -> None:
    rows = full_career(1, 50.0) + full_career(2, 70.0)
    built = blend.build(rows, SEASONS, accolade_years={1999})
    art = blend.artifact(built, spans=blend.scales(built))
    assert art["career_component_weights"][blend.RESUME] == 25.0
    assert art["component_coverage"][blend.ACCOLADE] == 0
    assert art["n_renormalized"] == 2
    assert art["component_scale"][blend.PEAK] == {"low": 50.2, "high": 69.8}
    assert art["span_provenance"]["mode"] == blend.FITTED


EARLY = {
    31: Season(31, 2013, "2013-2016"),
    32: Season(32, 2014, "2013-2016"),
    33: Season(33, 2015, "2013-2016"),
}
WITH_EARLY = {**EARLY, **SEASONS}


def test_the_prime_window_covers_the_pre_league_era() -> None:
    """`career._season_order` drops 2013-2016 because the plus-minus axis has
    no replacement scale there. This board's season unit is a percentile inside
    the season's own field, so the window is every published season and PRIME
    is measurable in all three eras."""
    rows = [score(1, season_id, 50.0) for season_id in (31, 32, 33)]
    out = {r.player_id: r for r in blend.build(rows, WITH_EARLY)}
    assert out[1].best_three == pytest.approx(150.0)
    assert out[1].best_three_start_season_id == 31
    assert blend.PRIME in out[1].career_components


def test_a_window_spanning_the_league_change_is_one_sequence() -> None:
    rows = [score(1, 33, 50.0), score(1, 1, 50.0), score(1, 2, 50.0)]  # 2015-2017-2018
    out = {r.player_id: r for r in blend.build(rows, WITH_EARLY)}
    assert out[1].best_three == pytest.approx(150.0)
    assert out[1].best_three_start_season_id == 33


# ------------------------------------------------------------- span fit


def test_q_reproduces_the_reference_quantile_on_a_known_vector() -> None:
    """The reference implementation from the pre-registration, against a
    hand-computed vector: `numpy.percentile`'s linear interpolation method."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert blend._q(values, 0.0) == pytest.approx(10.0)
    assert blend._q(values, 1.0) == pytest.approx(50.0)
    assert blend._q(values, 0.5) == pytest.approx(30.0)
    # i = 0.25 * 4 = 1.0 exactly -> values[1]
    assert blend._q(values, 0.25) == pytest.approx(20.0)
    # i = 0.1 * 4 = 0.4 -> interpolate between values[0] and values[1]
    assert blend._q(values, 0.1) == pytest.approx(14.0)


def test_a_value_above_the_p99_fit_scales_above_100_unclamped() -> None:
    """A career whose PEAK sits above the qualified cohort's p99 fit is not
    pulled back to 100 — the whole point of the robust fit replacing min-max
    is that the endpoints stop being one career's own number, not that the
    board stops reporting a career past them."""
    # A wide, roughly uniform cohort plus one outlier well past its own p99.
    rows = [full_career(p, float(10 * p)) for p in range(1, 10)]
    flat = [s for career in rows for s in career]
    outlier = full_career(99, 500.0)
    out = {r.player_id: r for r in blend.build(flat + outlier, SEASONS)}
    assert out[99].career_components[blend.PEAK] > 100.0


# ------------------------------------------------------------- span pinning


def test_pinned_spans_are_used_in_preference_to_fitting(_isolated_spans_path: Any) -> None:
    base_rows = full_career(1, 80.0) + full_career(2, 40.0)
    built = blend.build(base_rows, SEASONS)
    pointer = blend.refit_spans(built, "cr-spans-test", base_run=1731)
    assert pointer["base_run"] == 1731
    assert _isolated_spans_path.exists()

    fresh = {r.player_id: r for r in blend.build(base_rows, SEASONS)}
    assert blend.span_provenance([r for r in built])["mode"] == blend.PINNED

    # A cohort that would fit a very different span if refit from scratch
    # still reads against the pinned one, and produces identical output to a
    # separately-built run over the *same* pinned span — the point of pinning
    # is that the scale no longer depends on which careers happen to be in
    # the cohort this run.
    different_cohort = full_career(1, 80.0) + full_career(2, 40.0) + full_career(3, 10.0)
    out_a = {r.player_id: r for r in blend.build(base_rows, SEASONS)}
    out_b = {r.player_id: r for r in blend.build(different_cohort, SEASONS)}
    assert out_a[1].career_components[blend.PEAK] == pytest.approx(
        out_b[1].career_components[blend.PEAK]
    )
    assert out_a[1].total == pytest.approx(fresh[1].total)


def test_absent_spans_json_falls_back_to_fitting(_isolated_spans_path: Any) -> None:
    assert not _isolated_spans_path.exists()
    rows = full_career(1, 80.0) + full_career(2, 40.0)
    built = blend.build(rows, SEASONS)
    assert blend.span_provenance(built)["mode"] == blend.FITTED


def test_a_refreeze_preserves_the_prior_entry_in_history(_isolated_spans_path: Any) -> None:
    rows_a = full_career(1, 80.0) + full_career(2, 40.0)
    built_a = blend.build(rows_a, SEASONS)
    first = blend.refit_spans(built_a, "cr-spans-a", base_run=1000)

    rows_b = full_career(1, 90.0) + full_career(2, 30.0)
    built_b = blend.build(rows_b, SEASONS)
    second = blend.refit_spans(built_b, "cr-spans-b", base_run=2000)

    assert second["supersedes"] == "cr-spans-a"
    assert len(second["history"]) == 1
    assert second["history"][0]["cut"] == "cr-spans-a"
    assert second["history"][0]["base_run"] == 1000
    assert second["history"][0]["sha256"] == first["sha256"]

    with pytest.raises(ValueError):
        blend.refit_spans(built_b, "cr-spans-a", base_run=3000)


def test_a_partial_refit_carries_the_other_spans_over(_isolated_spans_path: Any) -> None:
    built_a = blend.build(full_career(1, 80.0) + full_career(2, 40.0), SEASONS)
    first = blend.refit_spans(built_a, "cr-spans-a", base_run=1000)

    built_b = blend.build(full_career(1, 90.0) + full_career(2, 30.0), SEASONS)
    second = blend.refit_spans(built_b, "cr-spans-b", base_run=2000, only=[blend.PRIME])

    fresh = blend.refit_spans(built_b, "cr-spans-c", base_run=3000)
    for name, span in second["spans"].items():
        expected = fresh["spans"][name] if name == blend.PRIME else first["spans"][name]
        assert span == pytest.approx(expected)
    assert second["base_runs"][blend.PRIME] == 2000
    assert second["base_runs"][blend.PEAK] == 1000


def test_a_partial_refit_refuses_an_unknown_component(_isolated_spans_path: Any) -> None:
    built = blend.build(full_career(1, 80.0) + full_career(2, 40.0), SEASONS)
    blend.refit_spans(built, "cr-spans-a", base_run=1000)
    with pytest.raises(ValueError):
        blend.refit_spans(built, "cr-spans-b", base_run=2000, only=["PERFORMANCE"])


def test_a_partial_refit_needs_a_pin(_isolated_spans_path: Any) -> None:
    built = blend.build(full_career(1, 80.0) + full_career(2, 40.0), SEASONS)
    with pytest.raises(ValueError):
        blend.refit_spans(built, "cr-spans-a", base_run=1000, only=[blend.PEAK])
