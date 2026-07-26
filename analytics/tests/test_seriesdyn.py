"""Fixture and simulation tests for series dynamics. No database required.

Two kinds of test live here. The first are the ordinary invariants: a race
enumerates to one, a scoreline is classified the way the docs say, a series
whose maps do not add up to its result is refused.

The second kind is the one that matters. This module publishes a null — that
the previous map's result adds nothing once the teams' quality is accounted for
— and a null is only worth as much as the estimator behind it. So the fit is
run on simulated leagues where the answer is known: one with real carryover,
where it has to find it, and one with none but with quality the rating never
saw, where the naive regression finds an effect that is not there and the
sequence model has to refuse it. Those two together are the argument for
believing the number on the site.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import numpy as np

from cdlhub_analytics.regress import fit_logistic_l2
from cdlhub_analytics.seriesdyn import (
    EVENTS,
    WINS_NEEDED,
    Frozen,
    MapRow,
    Sequences,
    Series,
    SeriesMap,
    _swing_pp,
    design,
    expected_events,
    expected_events_latent,
    fit_mixed,
    fit_specs,
    latent_quality,
    map_rows,
    path_events,
    race_paths,
    sequences,
    series_loglik,
)

DAY0 = datetime(2018, 1, 1)
ROTATION_5 = (
    "hardpoint",
    "search-and-destroy",
    "capture-the-flag",
    "hardpoint",
    "search-and-destroy",
)


def build_series(
    path: list[bool],
    *,
    sid: int = 1,
    day: int = 0,
    team1: int = 1,
    team2: int = 2,
    title: str = "WWII",
    year: int = 2018,
) -> Series:
    return Series(
        id=sid,
        team1=team1,
        team2=team2,
        played_at=DAY0 + timedelta(days=day),
        event_id=1,
        title=title,
        year=year,
        wins_needed=WINS_NEEDED,
        maps=tuple(
            SeriesMap(ordinal=i + 1, mode=ROTATION_5[i], team1_won=won)
            for i, won in enumerate(path)
        ),
    )


def frozen_for(series: list[Series], ps: list[float] | None = None) -> Frozen:
    """Every series seen at the same fixed probabilities, so a test can isolate
    the thing it is about from the walk-forward rating."""
    fixed = ps or [0.5] * 5
    return Frozen(
        rotation_ps={s.id: tuple(fixed) for s in series},
        played_ps={s.id: tuple(fixed[: len(s.maps)]) for s in series},
        n_no_rotation=0,
    )


# ===== the race =====


def test_a_race_enumerates_to_one() -> None:
    paths = race_paths([0.6, 0.4, 0.55, 0.5, 0.45])
    assert abs(sum(p for _, p in paths) - 1.0) < 1e-12
    # Three ways to go 3-0, and every path is a legal best-of-five length.
    assert all(3 <= len(path) <= 5 for path, _ in paths)
    assert sum(1 for path, _ in paths if len(path) == 3) == 2


def test_an_even_race_is_symmetric() -> None:
    events = expected_events([0.5] * 5)
    assert abs(events["team1_won"] - 0.5) < 1e-12
    # Between identical teams a 1-0 lead is worth exactly 11/16.
    assert abs(events["map1_winner_won"] - 0.6875) < 1e-12
    assert abs(events["sweep"] - 0.25) < 1e-12
    assert abs(events["decider"] - 0.375) < 1e-12


def test_a_lopsided_race_sweeps_more_and_goes_the_distance_less() -> None:
    even = expected_events([0.5] * 5)
    wide = expected_events([0.8] * 5)
    assert wide["sweep"] > even["sweep"]
    assert wide["decider"] < even["decider"]
    assert wide["map1_winner_won"] > even["map1_winner_won"]


def test_scorelines_are_classified_the_way_the_docs_say() -> None:
    sweep = path_events([True, True, True])
    assert sweep == {
        "team1_won": 1.0,
        "map1_winner_won": 1.0,
        "sweep": 1.0,
        "decider": 0.0,
        "reverse_sweep": 0.0,
    }
    reverse = path_events([False, False, True, True, True])
    assert reverse["reverse_sweep"] == 1.0
    assert reverse["decider"] == 1.0
    assert reverse["map1_winner_won"] == 0.0
    # Losing map 1 but not both openers is a comeback in the weak sense only,
    # and the strong indicator has to say so.
    assert path_events([False, True, True, True])["reverse_sweep"] == 0.0


def test_the_map1_indicator_does_not_care_which_side_won() -> None:
    for path in ([True, True, True], [False, False, False], [True, False, True, True]):
        mirrored = [not x for x in path]
        assert path_events(path)["map1_winner_won"] == path_events(mirrored)["map1_winner_won"]
        assert path_events(path)["sweep"] == path_events(mirrored)["sweep"]


def test_unmeasured_quality_raises_the_sweep_rate_with_no_memory_at_all() -> None:
    """The whole reason the second benchmark exists: spread the teams further
    apart than the rating said and every 'momentum' signature appears."""
    flat = expected_events_latent([0.0] * 5, a=1.0, sigma=0.0)
    spread = expected_events_latent([0.0] * 5, a=1.0, sigma=1.0)
    assert spread["sweep"] > flat["sweep"] + 0.05
    assert spread["decider"] < flat["decider"]
    assert spread["map1_winner_won"] > flat["map1_winner_won"] + 0.03
    # And it stays a fair coin on who wins, because the offset is symmetric.
    assert abs(spread["team1_won"] - 0.5) < 1e-9


# ===== loading and rows =====


def test_map_rows_carry_the_previous_result_and_the_running_lead() -> None:
    series = [build_series([True, False, False, True, True])]
    rows = map_rows(series, frozen_for(series))
    assert [r.ordinal for r in rows] == [2, 3, 4, 5]
    assert [r.prev for r in rows] == [1.0, -1.0, -1.0, 1.0]
    assert [r.lead for r in rows] == [1.0, 0.0, -1.0, 0.0]


def test_the_design_matrix_is_the_columns_the_spec_names() -> None:
    rows = [MapRow(series_id=1, ordinal=2, strength=0.3, prev=1.0, lead=1.0, team1_won=1.0)]
    assert design(rows, "strength_only").shape == (1, 1)
    assert design(rows, "strength_prev").shape == (1, 2)
    assert design(rows, "strength_prev")[0].tolist() == [0.3, 1.0]


def test_sequences_pad_series_of_different_lengths() -> None:
    series = [
        build_series([True, True, True], sid=1),
        build_series([False, True, False, True, True], sid=2),
    ]
    seqs = sequences(series, frozen_for(series))
    assert seqs.x.shape == (2, 5)
    assert seqs.mask[0].tolist() == [1.0, 1.0, 1.0, 0.0, 0.0]
    assert seqs.mask[1].tolist() == [1.0] * 5
    # The lag column is zero on map 1 — there is no previous map to carry.
    assert seqs.prev[0][0] == 0.0 and seqs.prev[1][0] == 0.0
    assert seqs.head(3).mask.sum() == 6.0


# ===== the likelihood =====


def test_with_no_latent_spread_the_likelihood_is_plain_logistic() -> None:
    series = [
        build_series([True, True, True], sid=1),
        build_series([False, False, True, False], sid=2),
    ]
    seqs = sequences(series, frozen_for(series, [0.6, 0.4, 0.55, 0.5, 0.45]))
    a = 0.9
    direct = 0.0
    for i in range(seqs.n_series):
        for j in range(seqs.x.shape[1]):
            if seqs.mask[i, j] == 0.0:
                continue
            p = 1.0 / (1.0 + np.exp(-a * seqs.x[i, j]))
            direct += np.log(p if seqs.y[i, j] == 1.0 else 1.0 - p)
    assert abs(series_loglik(seqs, a=a, sigma=0.0, gamma=0.0) - direct) < 1e-9


def test_the_quadrature_has_converged_at_the_published_node_count() -> None:
    rng = np.random.default_rng(7)
    seqs = simulate(rng, n_series=200, a=1.0, sigma=0.8, gamma=0.3)
    coarse = series_loglik(seqs, a=1.0, sigma=0.8, gamma=0.3)
    fine = _loglik_with_nodes(seqs, a=1.0, sigma=0.8, gamma=0.3, nodes=64)
    assert abs(coarse - fine) < 1e-4


def _loglik_with_nodes(seqs: Sequences, a: float, sigma: float, gamma: float, nodes: int) -> float:
    """The same integral at a different resolution, written out here so the
    convergence check does not depend on the module's own default."""
    z, w = np.polynomial.hermite_e.hermegauss(nodes)
    w = w / np.sqrt(2.0 * np.pi)
    eta = (a * seqs.x + gamma * seqs.prev)[:, :, None] + sigma * z[None, None, :]
    ll_map = seqs.y[:, :, None] * eta - np.logaddexp(0.0, eta)
    ll_series = (ll_map * seqs.mask[:, :, None]).sum(axis=1)
    return float(np.log((np.exp(ll_series) * w[None, :]).sum(axis=1)).sum())


# ===== simulation: does the estimator earn the null? =====


def simulate(
    rng: np.random.Generator,
    n_series: int,
    a: float,
    sigma: float,
    gamma: float,
    strength_sd: float = 0.5,
) -> Sequences:
    """A league where the answer is known.

    Each series draws a rating-visible strength gap and a latent offset the
    rating never sees, then plays a best-of-five whose maps depend on the
    previous result exactly as much as `gamma` says and no more. The race stops
    at three wins, so the simulated data has the same ragged shape as the
    archive's.
    """
    width = 2 * WINS_NEEDED - 1
    x = np.zeros((n_series, width))
    y = np.zeros((n_series, width))
    prev = np.zeros((n_series, width))
    mask = np.zeros((n_series, width))
    for i in range(n_series):
        strength = rng.normal(0.0, strength_sd, size=width)
        u = rng.normal(0.0, sigma)
        w1 = w2 = 0
        last = 0.0
        for j in range(width):
            if w1 == WINS_NEEDED or w2 == WINS_NEEDED:
                break
            eta = a * strength[j] + u + gamma * last
            won = rng.random() < 1.0 / (1.0 + np.exp(-eta))
            x[i, j], y[i, j], prev[i, j], mask[i, j] = strength[j], float(won), last, 1.0
            last = 1.0 if won else -1.0
            w1, w2 = (w1 + 1, w2) if won else (w1, w2 + 1)
    return Sequences(x=x, y=y, prev=prev, mask=mask)


def _naive_prev_beta(seqs: Sequences) -> float:
    """The coefficient a plain regression on strength and the previous result
    finds — the estimate the sequence model exists to correct."""
    rows = seqs.mask.astype(bool) & (seqs.prev != 0.0)
    x = np.column_stack([seqs.x[rows], seqs.prev[rows]])
    fit = fit_logistic_l2(x, seqs.y[rows], l2=0.0)
    return float(fit.weights[1])


def test_a_league_with_real_carryover_is_found() -> None:
    rng = np.random.default_rng(11)
    seqs = simulate(rng, n_series=2500, a=1.0, sigma=0.5, gamma=0.4)
    fit = fit_mixed(seqs)
    assert abs(fit.gamma - 0.4) < 0.12
    assert abs(fit.sigma - 0.5) < 0.15
    result = latent_quality(seqs)
    assert result["excludes_zero"]
    assert result["full"]["gamma_lo"] > 0.0


def test_a_league_with_no_carryover_but_unseen_quality_is_refused() -> None:
    """The bias this module is built around: with a latent per-series offset and
    no memory whatsoever, the naive regression reports momentum. The sequence
    model has to put the same data back at zero."""
    rng = np.random.default_rng(23)
    seqs = simulate(rng, n_series=2500, a=1.0, sigma=0.9, gamma=0.0)

    naive = _naive_prev_beta(seqs)
    assert naive > 0.1, "the simulation should reproduce the spurious effect"

    fit = fit_mixed(seqs)
    assert abs(fit.gamma) < 0.1
    result = latent_quality(seqs)
    assert not result["excludes_zero"]
    # And it recovers the quality that caused the illusion rather than dropping it.
    assert abs(result["full"]["sigma"] - 0.9) < 0.15


def test_the_null_fit_pins_gamma_at_zero_and_still_finds_the_spread() -> None:
    rng = np.random.default_rng(5)
    seqs = simulate(rng, n_series=1500, a=1.0, sigma=0.7, gamma=0.0)
    null = fit_mixed(seqs, with_gamma=False)
    assert null.gamma == 0.0
    assert abs(null.sigma - 0.7) < 0.2
    full = fit_mixed(seqs, with_gamma=True, start=null)
    # Adding a free parameter can never fit worse, up to optimizer tolerance.
    assert full.loglik >= null.loglik - 1e-6


def test_the_profile_interval_covers_the_truth_and_excludes_a_far_value() -> None:
    rng = np.random.default_rng(101)
    seqs = simulate(rng, n_series=2000, a=1.0, sigma=0.6, gamma=0.25)
    result = latent_quality(seqs)
    lo, hi = result["full"]["gamma_lo"], result["full"]["gamma_hi"]
    assert lo < 0.25 < hi
    assert hi < 0.25 + 0.5


def test_the_balanced_first_three_arm_agrees_with_the_full_sequence() -> None:
    rng = np.random.default_rng(31)
    seqs = simulate(rng, n_series=2000, a=1.0, sigma=0.6, gamma=0.3)
    result = latent_quality(seqs)
    arm = result["first_three"]
    assert arm["n_maps"] == int(seqs.head(3).mask.sum())
    assert abs(arm["gamma"] - result["full"]["gamma"]) < 0.15


# ===== reporting =====


def test_a_coefficient_reads_out_in_points_of_win_probability() -> None:
    assert _swing_pp(0.0) == 0.0
    assert _swing_pp(-0.3) == -_swing_pp(0.3)
    # Half a logit either way is about 25 points between the two states.
    assert 20.0 < _swing_pp(0.5) < 30.0


def _mixed_league(rng: np.random.Generator, n: int = 300) -> list[MapRow]:
    """Series of assorted lengths at assorted probabilities, which is what the
    regression needs to be given: a constant column has no coefficient."""
    rows: list[MapRow] = []
    for i in range(n):
        path = [bool(rng.random() < 0.55) for _ in range(3)]
        if sum(path) == 2:
            path.append(bool(rng.random() < 0.55))
        series = [build_series(path, sid=i + 1, day=i)]
        base = 0.35 + 0.3 * rng.random()
        rows.extend(map_rows(series, frozen_for(series, [base] * 5)))
    return rows


def test_the_regression_reports_every_spec_it_was_asked_for() -> None:
    rng = np.random.default_rng(3)
    rows = _mixed_league(rng)
    out: dict[str, Any] = fit_specs(rows, "test", rng, specs=("strength_prev",))
    assert out["available"]
    assert [s["spec"] for s in out["specs"]] == ["strength_prev"]
    assert [t["term"] for t in out["specs"][0]["terms"]] == ["strength", "prev"]
    assert out["n_series"] == 300


def test_too_few_maps_is_reported_rather_than_fitted() -> None:
    rng = np.random.default_rng(1)
    series = [build_series([True, True, True], sid=1)]
    out = fit_specs(map_rows(series, frozen_for(series)), "test", rng)
    assert out["available"] is False
    assert out["n_maps"] == 2


def test_a_column_that_never_varies_is_refused_rather_than_inverted() -> None:
    """With no ridge there is nothing holding up a singular design, so the fit
    has to check rather than raise out of numpy."""
    rng = np.random.default_rng(9)
    series = [build_series([True, True, True], sid=i + 1, day=i) for i in range(200)]
    rows = map_rows(series, frozen_for(series))  # every strength identical, every prev +1
    out = fit_specs(rows, "test", rng, specs=("strength_prev",))
    assert out["available"] is False
    assert "constant" in out["reason"]


def test_every_published_event_has_an_expectation() -> None:
    events = expected_events([0.5] * 5)
    assert set(events) == set(EVENTS)
    assert set(path_events([True, True, True])) == set(EVENTS)
