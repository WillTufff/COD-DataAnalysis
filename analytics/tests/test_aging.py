"""Aging: the three fits, the pairing rule, the retention weights, the interval."""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from cdlhub_analytics import aging
from cdlhub_analytics.ratings.preflight import Season

SEASONS = {i: Season(i, 2017 + i, "CDL" if 2017 + i >= 2020 else "CWL") for i in range(10)}


def obs(player_id: int, position: int, x: float, value: float) -> aging.Observation:
    return aging.Observation(player_id=player_id, season_position=position, x=x, value=value)


def quadratic_population(peak: float, n_players: int = 120) -> list[aging.Observation]:
    """A population whose true curve peaks where it is told to.

    Sized so every age in 18-27 clears `MIN_AGE_SUPPORT`, because the drawn
    window is measured from the population and a thinner one would not have a
    curve to check.
    """
    out: list[aging.Observation] = []
    for player_id in range(n_players):
        start = 18 + player_id % 6
        for step in range(5):
            x = float(start + step)
            out.append(obs(player_id, step, x, -((x - peak) ** 2) / 10.0))
    return out


# --------------------------------------------------------------------- the age


def test_age_is_taken_at_the_middle_of_the_season_year() -> None:
    born = date(2000, 1, 1)
    assert aging._age_at(born, Season(1, 2020, "CDL")) == pytest.approx(20.5, abs=0.05)


def test_a_player_without_a_birthdate_is_fitted_on_a_career_index() -> None:
    rows = [(1, 3, 5.0), (1, 4, 6.0), (2, 3, 1.0)]
    seasons = {3: Season(3, 2020, "CDL"), 4: Season(4, 2021, "CDL")}
    out = aging.observations(rows, seasons, {}, use_age=False)
    assert sorted((o.player_id, o.x) for o in out) == [(1, 1.0), (1, 2.0), (2, 1.0)]


def test_the_two_x_axes_never_appear_in_the_same_population() -> None:
    rows = [(1, 3, 5.0), (2, 3, 1.0)]
    seasons = {3: Season(3, 2020, "CDL")}
    births = {1: date(2000, 1, 1)}
    by_age = aging.observations(rows, seasons, births, use_age=True)
    by_index = aging.observations(rows, seasons, births, use_age=False)
    assert [o.player_id for o in by_age] == [1]
    assert [o.player_id for o in by_index] == [2]


# ------------------------------------------------------------------ the pairing


def test_a_pair_needs_consecutive_league_seasons() -> None:
    """A player who sat a year out has no pair across the gap: two years of
    change is not one year of change."""
    rows = [obs(1, 0, 20.0, 1.0), obs(1, 2, 22.0, 3.0), obs(1, 3, 23.0, 4.0)]
    assert [(p, round(x, 1), round(d, 1)) for p, x, d in aging.pairs(rows)] == [(1, 22.5, 1.0)]


def test_a_single_season_contributes_no_pair() -> None:
    assert aging.pairs([obs(1, 0, 20.0, 1.0)]) == []


# ---------------------------------------------------------------- the retention


def test_the_final_season_is_not_counted_as_a_departure() -> None:
    """Otherwise the end of the record reads as a wave of retirements at every
    age at once."""
    rows = [obs(1, 0, 20.0, 1.0), obs(1, 1, 21.0, 1.0), obs(2, 1, 25.0, 1.0)]
    rates = aging.retention_rates(rows, last_position=1)
    assert 25 not in rates
    assert rates[20] > 0.5


def test_a_departure_lowers_the_rate_at_the_age_it_happened() -> None:
    rows = [obs(p, 0, 28.0, 1.0) for p in range(20)]
    rows += [obs(p, 0, 20.0, 1.0) for p in range(20, 40)]
    rows += [obs(p, 1, 21.0, 1.0) for p in range(20, 40)]
    rates = aging.retention_rates(rows, last_position=1)
    assert rates[28] < rates[20]


def test_weights_are_capped_and_centred_on_one() -> None:
    paired = [(1, 20.0, 0.0), (2, 28.0, 0.0)]
    weights = aging.retention_weights(paired, {20: 0.9, 28: 0.01})
    assert float(np.mean(weights)) == pytest.approx(1.0)
    assert float(weights.max() / weights.min()) <= aging.MAX_WEIGHT / (1.0 / 0.9) + 1e-9


def test_an_age_with_no_rate_falls_back_rather_than_dividing_by_zero() -> None:
    weights = aging.retention_weights([(1, 19.0, 0.0)], {})
    assert weights.size == 1
    assert np.isfinite(weights).all()


# -------------------------------------------------------------------- the fits


def test_the_naive_fit_recovers_a_planted_peak() -> None:
    curves = aging.fit_curves(quadratic_population(peak=24.0))
    assert curves[aging.NAIVE].peak == pytest.approx(24.0, abs=0.5)


def test_the_delta_fit_recovers_the_same_planted_peak() -> None:
    """A within-player change crosses zero at the vertex of the level curve."""
    curves = aging.fit_curves(quadratic_population(peak=24.0))
    assert curves[aging.DELTA].peak == pytest.approx(24.0, abs=0.5)


def _monotone_population() -> list[aging.Observation]:
    return [
        obs(player_id, step, float(18 + step), float(18 + step))
        for player_id in range(120)
        for step in range(5)
    ]


def test_a_curve_with_no_interior_maximum_reports_no_peak() -> None:
    curves = aging.fit_curves(_monotone_population())
    assert curves[aging.NAIVE].peak is None


def test_a_peak_outside_the_supported_window_is_refused_not_clipped() -> None:
    """A clipped peak is an extrapolation wearing a measurement's clothes."""
    beta = np.array([0.0, -2.0 * 60.0, 1.0], dtype=float) * -1.0
    assert aging._vertex(beta, (19.0, 27.0)) is None


# ------------------------------------------------------------- the age window


def test_the_window_is_the_widest_run_of_supported_ages() -> None:
    rows = [
        obs(player_id, 0, float(age), 1.0)
        for age in (20, 21, 22, 23, 24, 25)
        for player_id in range(aging.MIN_AGE_SUPPORT)
    ]
    assert aging.support_window(rows) == (20.0, 25.0)


def test_a_thin_tail_is_left_out_of_the_window() -> None:
    """The defect this rule replaced: a curve drawn to an age the record barely
    covers, where the fitted shape is the most confident thing on the page."""
    rows = [
        obs(player_id, 0, float(age), 1.0)
        for age in (20, 21, 22, 23, 24)
        for player_id in range(aging.MIN_AGE_SUPPORT)
    ]
    rows += [obs(900 + i, 0, 31.0, 1.0) for i in range(2)]
    assert aging.support_window(rows) == (20.0, 24.0)


def test_the_window_never_bridges_an_age_the_record_misses() -> None:
    rows = [
        obs(player_id, 0, float(age), 1.0)
        for age in (19, 20, 21, 22, 23, 27, 28)
        if age != 24
        for player_id in range(aging.MIN_AGE_SUPPORT)
    ]
    assert aging.support_window(rows) == (19.0, 23.0)


def test_a_record_with_no_supported_run_has_no_window_and_no_curve() -> None:
    rows = [obs(player_id, 0, 20.0, 1.0) for player_id in range(60)]
    assert aging.support_window(rows) is None
    assert aging.fit_curves(rows) == {}


def test_a_population_below_the_floor_is_not_fitted_at_all() -> None:
    assert aging.fit_curves([obs(1, 0, 20.0, 1.0)]) == {}


# --------------------------------------------------------------- the interval


def test_the_published_interval_spans_every_fit_that_found_a_peak() -> None:
    curves = aging.fit_curves(quadratic_population(peak=24.0))
    block = aging._block(curves, quadratic_population(peak=24.0))
    interval = block["peak_interval"]
    los = [f["peak_lo"] for f in block["fits"].values() if f["peak_lo"] is not None]
    his = [f["peak_hi"] for f in block["fits"].values() if f["peak_hi"] is not None]
    assert interval["lo"] == pytest.approx(min(los))
    assert interval["hi"] == pytest.approx(max(his))


def test_the_spread_between_the_point_estimates_is_reported() -> None:
    curves = aging.fit_curves(quadratic_population(peak=24.0))
    block = aging._block(curves, quadratic_population(peak=24.0))
    assert block["peak_interval"]["spread"] is not None
    assert block["peak_interval"]["fits_locating_a_peak"] >= 2


def test_no_peak_anywhere_publishes_no_interval_rather_than_a_default() -> None:
    rows = _monotone_population()
    block = aging._block(aging.fit_curves(rows), rows)
    assert block["peak_interval"]["lo"] is None
    assert block["peak_interval"]["point_estimates"] == []


# ------------------------------------------------------------ the bootstrap key


def test_the_bootstrap_does_not_depend_on_player_numbering() -> None:
    """The resample policy, applied here: renumbering the players must not move
    the interval, because the draw is ordered by contents rather than by key."""
    population = quadratic_population(peak=24.0)
    renumbered = [
        aging.Observation(
            player_id=row.player_id + 5_000,
            season_position=row.season_position,
            x=row.x,
            value=row.value,
        )
        for row in population
    ]
    first = aging.fit_curves(population)[aging.NAIVE]
    second = aging.fit_curves(renumbered)[aging.NAIVE]
    assert first.peak_lo == pytest.approx(second.peak_lo)
    assert first.peak_hi == pytest.approx(second.peak_hi)


# ------------------------------------------------------- per-player trajectories


def test_a_player_curve_is_the_population_shape_shifted_to_them() -> None:
    population = quadratic_population(peak=24.0)
    curves = aging.fit_curves(population)
    rows = aging.player_rows(population, curves, aging.COMPOSITE, aging.OVERALL, x_is_age=True)
    assert rows
    assert {row.population for row in rows} == {aging.COMPOSITE}
    assert {row.x_is_age for row in rows} == {True}
    # No player is given a curve outside the seasons they played.
    played: dict[int, list[float]] = {}
    for seen in population:
        played.setdefault(seen.player_id, []).append(seen.x)
    for curve_row in rows[:200]:
        span = played[curve_row.player_id]
        assert min(span) - 1.0 <= curve_row.age_or_seq <= max(span) + 1.0


def test_no_curves_means_no_player_rows() -> None:
    assert aging.player_rows([obs(1, 0, 20.0, 1.0)], {}, aging.COMPOSITE, aging.OVERALL, True) == []
