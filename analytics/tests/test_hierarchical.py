"""Fixture tests for the hierarchical player-season model.

Two families. The first is recovery: simulate a cohort whose variance components
are known, and check the fit finds them — the only way to know an EM loop is
estimating the thing its docstring claims. The second is identity: the posterior
mean must reproduce the m/(m+k) shrinkage it replaces, exactly, because the whole
argument for this model is that the old rule was a special case of it and not a
separate correction. No database required.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from cdlhub_analytics.era import MIN_MAPS
from cdlhub_analytics.ratings import hierarchical as hier
from cdlhub_analytics.ratings import player_rating as pr
from cdlhub_analytics.ratings.hierarchical import (
    FALLBACK_K,
    CohortModel,
    _em,
    _loglik,
    calibrate,
    compare_estimators,
    compute_ratings,
    fit_cohort,
    pooled_map_variance,
    posterior,
)
from cdlhub_analytics.regress import LogisticFit

UNIT = (np.array([0.0]), np.array([1.0]), np.array([1.0]))  # feat_mu, feat_sd, weights


def one_feature_cohort(
    n_players: int, maps: int, sigma: float, tau: float, seed: int = 11, mu: float = 0.0
) -> list[pr.PlayerModeAgg]:
    """A cohort with one feature that *is* the score: true skills drawn N(mu, tau²),
    per-map reads drawn N(skill, sigma²). Denominators are 1, so the season profile
    is the mean of the player's maps and every quantity below is analytic."""
    rng = np.random.default_rng(seed)
    out = []
    for pid in range(n_players):
        skill = mu + tau * rng.standard_normal()
        obs = skill + sigma * rng.standard_normal(maps)
        num = obs.reshape(-1, 1)
        den = np.ones((maps, 1))
        out.append(
            pr.PlayerModeAgg(
                player_id=pid,
                season_id=1,
                mode_id=1,
                maps=maps,
                feats=np.asarray(num.sum(axis=0) / den.sum(axis=0)),
                numerators=num,
                denominators=den,
            )
        )
    return out


def ragged_cohort(
    counts: list[int], sigma: float = 2.0, tau: float = 1.0, seed: int = 12
) -> list[pr.PlayerModeAgg]:
    """The same, with a different map count per player — the case the estimator
    exists for, since real seasons run from one map to a few hundred."""
    rng = np.random.default_rng(seed)
    out = []
    for pid, maps in enumerate(counts):
        skill = tau * rng.standard_normal()
        num = (skill + sigma * rng.standard_normal(maps)).reshape(-1, 1)
        den = np.ones((maps, 1))
        out.append(
            pr.PlayerModeAgg(
                player_id=pid,
                season_id=1,
                mode_id=1,
                maps=maps,
                feats=np.asarray(num.sum(axis=0) / den.sum(axis=0)),
                numerators=num,
                denominators=den,
            )
        )
    return out


def scale_for(members: list[pr.PlayerModeAgg]) -> pr.CohortScale:
    """A CohortScale that leaves the score alone, so the test reads raw units."""
    return pr.CohortScale(
        feat_mu=np.array([0.0]),
        feat_sd=np.array([1.0]),
        score_mu=0.0,
        score_sd=1.0,
        shrink_maps=FALLBACK_K,
        within_var=0.0,
        between_var=0.0,
        n_players=len(members),
        n_maps=sum(a.maps for a in members),
        shrink_estimated=False,
    )


def unit_fit() -> pr.ModeFit:
    return pr.ModeFit(
        n_games=100,
        mu=np.array([0.0]),
        sd=np.array([1.0]),
        fit=LogisticFit(intercept=0.0, weights=np.array([1.0]), converged=True, n_iter=1),
    )


# ------------------------------------------------------------------ recovery


def test_fit_recovers_known_variance_components() -> None:
    members = one_feature_cohort(400, 40, sigma=2.0, tau=1.0)
    model = fit_cohort(members, scale_for(members), unit_fit())
    assert model is not None and model.estimated
    assert model.sigma == pytest.approx(2.0, rel=0.05)
    assert model.tau == pytest.approx(1.0, rel=0.15)
    # k = σ²/τ² is what the shrinkage step used to assert at 15.
    assert model.implied_k == pytest.approx(4.0, rel=0.35)


def test_fit_recovers_the_population_mean() -> None:
    members = one_feature_cohort(400, 20, sigma=1.5, tau=1.0, mu=3.0)
    model = fit_cohort(members, scale_for(members), unit_fit())
    assert model is not None
    assert model.mu == pytest.approx(3.0, abs=0.15)


def test_fit_handles_unbalanced_map_counts() -> None:
    """Half the cohort plays 4 maps, half plays 60. A single pooled variance would
    be dominated by whichever group is larger; the per-player v_i is the point."""
    counts = [4] * 150 + [60] * 150
    members = ragged_cohort(counts, sigma=2.0, tau=1.0)
    model = fit_cohort(members, scale_for(members), unit_fit())
    assert model is not None and model.estimated
    assert model.sigma == pytest.approx(2.0, rel=0.06)
    assert model.tau == pytest.approx(1.0, rel=0.2)


def test_em_is_monotone_in_the_likelihood() -> None:
    """EM's guarantee, checked rather than cited: every step raises the marginal
    likelihood, which is why this loop needs no line search and no restarts."""
    rng = np.random.default_rng(3)
    x = rng.normal(0.0, 1.5, size=200)
    v = rng.uniform(0.05, 2.0, size=200)
    tau2 = 0.01
    mu = float(x.mean())
    last = _loglik(x, v, mu, tau2)
    for _ in range(25):
        w = 1.0 / (tau2 + v)
        mu = float((w * x).sum() / w.sum())
        b = tau2 / (tau2 + v)
        tau2 = float((b * v + (mu + b * (x - mu) - mu) ** 2).mean())
        now = _loglik(x, v, mu, tau2)
        assert now >= last - 1e-9
        last = now


def test_em_converges_and_reports_it() -> None:
    rng = np.random.default_rng(4)
    x = rng.normal(0.0, 1.0, size=300)
    v = np.full(300, 0.25)
    _mu, tau2, iterations, converged = _em(x, v, 0.5)
    assert converged and iterations < 500
    # With equal v the answer is analytic: τ² = Var(x) − v.
    assert tau2 == pytest.approx(float(x.var()) - 0.25, rel=0.05)


def test_pooled_variance_ignores_players_without_replication() -> None:
    members = ragged_cohort([1, 1, 1, 30, 30], sigma=2.0, tau=1.0)
    sigma2, replicated, n_maps = pooled_map_variance(members, *UNIT)
    assert replicated == 2
    assert n_maps == 63
    assert math.sqrt(sigma2) == pytest.approx(2.0, rel=0.2)


def test_calibration_is_one_when_the_profile_really_is_a_mean() -> None:
    """These fixtures have unit denominators, so the season profile *is* the mean
    of the player's maps and σ²/m is exactly right. The calibration must therefore
    come back at 1.0 — including on four-map seasons, where the bootstrap's own
    (m−1)/m bias would otherwise show up as a 12% correction the data never asked
    for."""
    for counts in ([4] * 200, [40] * 200, [4] * 100 + [40] * 100):
        members = ragged_cohort(counts, sigma=2.0, tau=1.0, seed=61)
        sigma2, _replicated, _n = pooled_map_variance(members, *UNIT)
        factor, stats = calibrate(members, scale_for(members), unit_fit(), sigma2)
        assert stats["ratio"] == pytest.approx(1.0, abs=0.05), counts
        assert factor == pytest.approx(1.0, abs=0.1), counts


# ------------------------------------------------------------- the posterior


def test_posterior_mean_is_the_shrinkage_it_replaces() -> None:
    """The identity the whole change rests on: the posterior mean is the old
    m/(m+k) rule with k = σ²/τ², so nothing about the point estimate is new."""
    model = CohortModel(
        mu=0.0,
        tau2=1.0,
        sigma2=16.0,
        origin=0.0,
        n_players=50,
        n_maps=500,
        n_replicated=50,
        iterations=3,
        converged=True,
        estimated=True,
        fallback=None,
        loglik=None,
    )
    assert model.implied_k == pytest.approx(16.0)
    for maps in (1, 8, 40, 200):
        post = posterior(2.0, maps, model)
        assert post.z == pytest.approx(pr._shrink(2.0, maps, model.implied_k))
        assert post.shrinkage == pytest.approx(maps / (maps + 16.0))


def test_posterior_sd_shrinks_with_maps_and_exceeds_the_bootstrap() -> None:
    model = CohortModel(
        mu=0.0,
        tau2=1.0,
        sigma2=16.0,
        origin=0.0,
        n_players=50,
        n_maps=500,
        n_replicated=50,
        iterations=3,
        converged=True,
        estimated=True,
        fallback=None,
        loglik=None,
    )
    short, long = posterior(1.0, 8, model), posterior(1.0, 120, model)
    assert short.sd > long.sd
    # Resampling the shrunk estimate gives B·√v; the posterior is √(B·v), larger
    # by √B — the reason the published band was too tight, not a rescaling.
    for maps in (8, 120):
        post = posterior(1.0, maps, model)
        v = model.sigma2 / maps
        assert post.sd == pytest.approx(math.sqrt(post.shrinkage * v))
        assert post.sd > post.shrinkage * math.sqrt(v)
    # A season with no evidence keeps the prior's whole width.
    assert posterior(1.0, 10**9, model).sd < 1e-3


def test_posterior_sd_is_calibrated_in_simulation() -> None:
    """Coverage, on data where the truth is known: a 95% credible interval should
    contain the true skill about 95% of the time. This is the claim the site makes
    when it draws the band, so it is the claim the tests should check."""
    rng = np.random.default_rng(7)
    sigma, tau, maps, n = 2.0, 1.0, 12, 3000
    skills = tau * rng.standard_normal(n)
    x = skills + (sigma / math.sqrt(maps)) * rng.standard_normal(n)
    model = CohortModel(
        mu=0.0,
        tau2=tau**2,
        sigma2=sigma**2,
        origin=0.0,
        n_players=n,
        n_maps=n * maps,
        n_replicated=n,
        iterations=5,
        converged=True,
        estimated=True,
        fallback=None,
        loglik=None,
    )
    covered = 0
    for xi, truth in zip(x, skills, strict=True):
        post = posterior(float(xi), maps, model)
        lo, hi = post.z - 1.96 * post.sd, post.z + 1.96 * post.sd
        covered += int(lo <= truth / tau <= hi)
    assert 0.93 <= covered / n <= 0.97


def test_origin_puts_the_qualified_cohort_at_zero() -> None:
    """1.00 still means "average among players the site will rank", even though
    the fit reads the fringe too."""
    members = ragged_cohort([2] * 60 + [40] * 60, sigma=2.0, tau=1.0, seed=21)
    # Push the short seasons well below the rest, so an origin taken over
    # everyone would sit somewhere the qualified cohort is not.
    for a in members[:60]:
        a.numerators = a.numerators - 5.0
        a.feats = np.asarray(a.numerators.sum(axis=0) / a.denominators.sum(axis=0))
    model = fit_cohort(members, scale_for(members), unit_fit())
    assert model is not None
    zs = [posterior(float(a.feats[0]), a.maps, model).z for a in members if a.maps >= MIN_MAPS]
    assert float(np.mean(zs)) == pytest.approx(0.0, abs=1e-9)


# ------------------------------------------------------------------ fallback


def test_cohort_without_replication_falls_back_to_the_constant() -> None:
    members = ragged_cohort([1] * 40, sigma=2.0, tau=1.0)
    model = fit_cohort(members, scale_for(members), unit_fit())
    assert model is not None
    assert not model.estimated
    assert model.fallback is not None and "replication" in model.fallback
    assert model.implied_k == pytest.approx(FALLBACK_K)


def test_cohort_with_no_real_spread_says_so_in_its_intervals() -> None:
    """Every player identical, all the observed spread noise. The ratings are
    still expressed in τ units, so the cohort does not visibly flatten — what has
    to happen instead is that every interval swallows the difference. A rating of
    0.7τ with an interval of ±0.9τ is the model saying it found nothing, and it
    is the reason the interval ships with the number rather than beside it."""
    members = one_feature_cohort(80, 20, sigma=2.0, tau=0.0, seed=31)
    model = fit_cohort(members, scale_for(members), unit_fit())
    assert model is not None
    assert model.tau2 > 0.0  # never divide by zero, however degenerate the cohort
    assert model.implied_k > 50.0  # almost nothing of a 20-map season survives
    post = posterior(1.0, 20, model)
    assert post.sd > abs(post.z), "an interval that fails to cover zero would be a claim"


def test_two_player_cohort_is_still_fittable_and_a_lone_player_is_not() -> None:
    assert fit_cohort(ragged_cohort([10]), scale_for([]), unit_fit()) is None
    model = fit_cohort(ragged_cohort([10, 10]), scale_for([]), unit_fit())
    assert model is not None


# -------------------------------------------------------------- the ratings


def rating_setup() -> tuple[
    list[pr.PlayerModeAgg],
    dict[tuple[int, int], pr.ModeFit],
    dict[tuple[int, int], pr.CohortScale],
    dict[tuple[int, int], CohortModel],
]:
    members = ragged_cohort([6] * 40 + [50] * 40, sigma=2.0, tau=1.0, seed=41)
    scales = {(1, 1): scale_for(members)}
    fits = {(1, 1): unit_fit()}
    models = hier.build_models(members, fits, scales)
    return members, fits, scales, models


def test_ratings_carry_an_interval_on_every_row() -> None:
    members, fits, scales, models = rating_setup()
    ratings = compute_ratings(members, fits, scales, models)
    assert len(ratings) == 2 * len(members)  # one mode row + one blended row each
    assert all(r.rating_sd is not None and r.rating_sd > 0.0 for r in ratings)
    # The old estimator left every per-mode row's interval empty.
    assert all(r.rating_sd is None for r in pr.compute_ratings(members, fits, scales) if r.mode_id)


def test_short_seasons_get_wider_intervals_and_flatter_ratings() -> None:
    members, fits, scales, models = rating_setup()
    blended = {
        r.player_id: r for r in compute_ratings(members, fits, scales, models) if not r.mode_id
    }
    short = [blended[a.player_id] for a in members if a.maps == 6]
    long = [blended[a.player_id] for a in members if a.maps == 50]
    assert np.mean([r.rating_sd for r in short]) > np.mean([r.rating_sd for r in long])
    assert np.std([r.rating for r in short]) < np.std([r.rating for r in long])


def test_blended_variance_adds_the_modes_in_quadrature() -> None:
    """Two modes, hand-checked: the blend is maps-weighted and its variance is
    Σ w² V, which is the arithmetic a reader can redo from the published rows."""
    members = ragged_cohort([20, 20], sigma=2.0, tau=1.0, seed=51)
    second = ragged_cohort([10, 10], sigma=2.0, tau=1.0, seed=52)
    for a in second:
        a.mode_id = 2
    everyone = members + second
    scales = {(1, 1): scale_for(members), (1, 2): scale_for(second)}
    fits = {(1, 1): unit_fit(), (1, 2): unit_fit()}
    models = hier.build_models(everyone, fits, scales)
    ratings = compute_ratings(everyone, fits, scales, models)

    by_key = {(r.player_id, r.mode_id): r for r in ratings}
    for pid in (0, 1):
        mode_rows = [by_key[(pid, 1)], by_key[(pid, 2)]]
        share = [r.maps / 30.0 for r in mode_rows]
        blend = by_key[(pid, None)]
        expected = 1.0 + sum(w * (r.rating - 1.0) for w, r in zip(share, mode_rows, strict=True))
        assert blend.rating == pytest.approx(expected)
        sds = [r.rating_sd for r in mode_rows]
        assert all(sd is not None for sd in sds)
        var = sum((w * sd) ** 2 for w, sd in zip(share, sds, strict=True) if sd is not None)
        assert blend.rating_sd == pytest.approx(math.sqrt(var))


def test_comparison_reports_movement_and_the_interval_ratio() -> None:
    members, fits, scales, models = rating_setup()
    new = compute_ratings(members, fits, scales, models)
    old = pr.compute_ratings(members, fits, scales)
    out = compare_estimators(new, old)
    assert out["available"]
    assert out["n_player_seasons"] == len(members)
    assert out["spearman"] is not None and out["spearman"] > 0.9  # a rescale, not a reshuffle
    # The posterior interval is wider than the bootstrap one it replaces, for
    # every player, because it answers a different question.
    assert out["interval"]["median"] is not None and out["interval"]["median"] > 1.0
