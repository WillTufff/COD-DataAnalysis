"""The box-score prior, on targets whose answer is known. No database required.

Four things here can go wrong silently, which is why each has a test rather than
a diagnostic. The fold can leak — a walk-forward fit that sees the season it
predicts looks better and is worthless. The design can move between folds, if a
feature that only some seasons report is admitted. The shrinkage diagnostic can
become a tautology, if the columns it regresses on are also the columns it
fitted. And a collapsed variance component can be read as a small positive one,
which is the failure `hierarchical.py` documents at length.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from cdlhub_analytics.ratings import hierarchical, prior
from cdlhub_analytics.ratings import player_rating as pr


def _feature(key: str) -> pr.Feature:
    return pr.Feature(
        key=key,
        label=key,
        numerator=lambda row: 1.0,
        denominator=lambda row: 1.0,
        denom_kind="maps",
        sources=(),
        eligibility="skill",
    )


def _cohort(season_id: int, mode_id: int, keys: tuple[str, ...]) -> pr.Cohort:
    return pr.Cohort(
        season_id=season_id,
        mode_id=mode_id,
        mode_slug=f"m{mode_id}",
        title="Modern Warfare",
        features=tuple(_feature(k) for k in keys),
    )


def _design(
    n: int = 240, seed: int = 5, noise: float = 0.05, claimed_se: float | None = None
) -> prior.Design:
    """Seven seasons, a signal in the first two columns and nothing in the rest.

    `claimed_se` reports a standard error larger than the spread the targets were
    actually drawn with, which is the shape a penalized estimator produces: the
    ridge pulls the point estimates together while the standard error stays
    honest about how little the maps decided.
    """
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 4))
    year = np.array([2020 + i % 7 for i in range(n)], dtype=float)
    truth = 0.4 * x[:, 0] - 0.3 * x[:, 1]
    se = np.full(n, claimed_se if claimed_se is not None else noise)
    y = truth + rng.standard_normal(n) * noise
    targets = tuple(
        prior.Target(
            player_id=i,
            season_id=int(year[i]),
            year=int(year[i]),
            coef=float(y[i]),
            se=float(se[i]),
            maps=20 + i % 40,
            teammate_concentration=0.5,
        )
        for i in range(n)
    )
    return prior.Design(
        targets=targets,
        columns=("a", "b", "c", "d"),
        x=x,
        y=y,
        se=se,
        year=year,
        exposure=np.column_stack([rng.standard_normal(n), rng.standard_normal(n)]),
        dropped={},
    )


# ----------------------------------------------------------------- the columns


def test_a_feature_missing_from_one_season_is_not_admitted() -> None:
    """Otherwise a training fold's design differs from the fold it predicts."""
    cohorts = {
        (1, 1): _cohort(1, 1, ("kills_pm", "damage_pm")),
        (2, 1): _cohort(2, 1, ("kills_pm",)),
        (3, 1): _cohort(3, 1, ("kills_pm", "damage_pm")),
    }
    admitted, excluded = prior.stable_columns(cohorts, [1, 2, 3])
    assert admitted == {1: ("kills_pm",)}
    assert excluded["n"] == 1
    assert excluded["columns"][0]["feature"] == "damage_pm"
    assert excluded["columns"][0]["seasons_reporting"] == 2


def test_a_mode_played_in_only_some_seasons_keeps_its_stable_features() -> None:
    """Control arrives in 2021 and is gone by 2026; dropping it costs five seasons."""
    cohorts = {
        (1, 1): _cohort(1, 1, ("kills_pm",)),
        (2, 1): _cohort(2, 1, ("kills_pm",)),
        (2, 5): _cohort(2, 5, ("kills_pm", "deaths_pm")),
    }
    admitted, excluded = prior.stable_columns(cohorts, [1, 2])
    assert admitted[5] == ("deaths_pm", "kills_pm")
    assert excluded["n"] == 0


# ----------------------------------------------------------------- the weights


def test_the_weights_are_inverse_posterior_variance_not_inverse_se_squared() -> None:
    design = _design()
    w, tau2 = prior.weights(design)
    assert np.allclose(w, 1.0 / (design.se**2 + tau2))


def test_a_target_whose_spread_is_smaller_than_its_error_reads_as_collapsed() -> None:
    """The measurement this record actually produces, and the naive test for it.

    An EM can stop at 2e-07 rather than landing on zero, and `tau2 > 0` would
    call that a between-player variance. Read against the observation variance
    instead, the way the cohort model reads its own.
    """
    design = _design(noise=0.02, claimed_se=1.0)
    signal = prior.target_signal(design)
    assert signal["collapsed"] is True
    assert signal["distinguishable"] is False
    assert "does not establish that these players differ" in signal["reading"]


def test_a_target_with_real_spread_is_not_called_collapsed() -> None:
    design = _design(noise=0.01)
    assert prior.target_signal(design)["collapsed"] is False


# ------------------------------------------------------------ the walk-forward


def test_a_fold_is_never_trained_on_the_season_it_predicts() -> None:
    """The leak that would make every number here worthless, tested by moving one.

    Corrupting the last season's features must not move the predictions for the
    seasons before it. Asserting on `n_train` alone would pass for a fit that
    trained on everything and counted correctly.

    The perturbation is on `x` rather than on `y` deliberately: the draws are
    seeded from the targets' contents, so moving `y` would reseed every draw and
    the test would fail for a reason that is not leakage.
    """
    design = _design()
    before = {
        (p.player_id, p.year): p.mean
        for p in prior.walk_forward(design, prior.weights(design)[0], draws=8)
    }

    last = design.years[-1]
    corrupted = design.x.copy()
    corrupted[design.year == last] += 50.0
    moved = dataclasses.replace(design, x=corrupted)
    after = prior.walk_forward(moved, prior.weights(design)[0], draws=8)

    assert any(p.year < last for p in after)
    for p in after:
        if p.year < last:
            assert p.mean == before[(p.player_id, p.year)]


def test_the_earliest_season_is_predicted_by_nothing_and_is_not_predicted() -> None:
    design = _design()
    predictions = prior.walk_forward(design, prior.weights(design)[0], draws=8)
    assert design.years[0] not in {p.year for p in predictions}
    assert all(p.n_train > 0 for p in predictions)


def test_the_row_order_is_decided_by_contents_and_not_by_player_id() -> None:
    """A reload that renumbers players must not move a published interval.

    The draws are added positionally, so the row order decides which target gets
    which draw. That order therefore has to be a function of what the rows
    contain — and the guarantee is that the loader produces the same order from
    the same targets however they arrived, not that any order gives the same
    draws, which cannot be true of a positional draw.
    """
    design = _design()
    arrived = list(design.targets)
    renumbered = [dataclasses.replace(t, player_id=t.player_id + 1000) for t in reversed(arrived)]
    assert [(t.year, t.coef, t.se) for t in prior.order_targets(arrived)] == [
        (t.year, t.coef, t.se) for t in prior.order_targets(renumbered)
    ]


def test_the_draws_are_reproducible_from_the_targets_alone() -> None:
    design = _design()
    assert np.array_equal(prior._draw_targets(design, 4), prior._draw_targets(design, 4))


# -------------------------------------------------------------- the diagnostic


def test_the_shrinkage_diagnostic_reports_the_target_beside_the_prior() -> None:
    """The number that says whether an exposure loading is the fit's or the target's."""
    design = _design()
    out = prior.exposure_loading(
        design, prior.walk_forward(design, prior.weights(design)[0], draws=8)
    )
    assert out["available"]
    assert out["ratio"] is not None
    assert out["superseded_threshold"]["declared_max"] == prior.SUPERSEDED_SHRINKAGE_R2_MAX


def test_a_fit_that_amplifies_the_targets_exposure_loading_fails() -> None:
    """The concern the threshold was reaching for, stated as the ratio it became."""
    design = _design()
    predictions = prior.walk_forward(design, prior.weights(design)[0], draws=8)
    index = {(t.player_id, t.season_id): i for i, t in enumerate(design.targets)}
    take = np.asarray([index[(p.player_id, p.season_id)] for p in predictions], dtype=int)
    planted = [
        dataclasses.replace(p, mean=float(design.exposure[i, 0]))
        for p, i in zip(predictions, take, strict=True)
    ]
    out = prior.exposure_loading(design, planted)
    assert out["prior_r2"] > out["target_r2"]
    assert out["passes"] is False


def test_the_superseded_threshold_says_what_moved_it() -> None:
    """A threshold re-cut after the result is worthless unless the re-cut is recorded."""
    assert prior.THRESHOLD_HISTORY
    entry = prior.THRESHOLD_HISTORY[0]
    assert entry["value"] == prior.SUPERSEDED_SHRINKAGE_R2_MAX
    assert "faithful" in entry["because"]


# ------------------------------------------------------------------- the ridge


def test_a_noisier_target_earns_a_heavier_penalty() -> None:
    quiet = _design(noise=0.01)
    loud = _design(noise=1.0)
    assert (
        prior.fit_ridge(loud.x, loud.y, prior.weights(loud)[0]).lam
        >= prior.fit_ridge(quiet.x, quiet.y, prior.weights(quiet)[0]).lam
    )


def test_the_ridge_recovers_a_signal_it_was_given() -> None:
    """A positive control: the fit finds structure where structure was planted."""
    design = _design(noise=0.01)
    fit = prior.fit_ridge(design.x, design.y, prior.weights(design)[0])
    assert fit.beta[0] > 0.2
    assert fit.beta[1] < -0.1
    assert abs(fit.beta[2]) < 0.1


# ------------------------------------------------------------------- the ladder


def test_every_arm_sees_the_same_folds_and_the_same_drawn_targets() -> None:
    """A comparison between arms is only one if nothing else varies.

    The ridge is asserted against itself through two entry points rather than
    against another arm, because the arms are supposed to disagree — what must
    not vary is the fold assignment and the noise each fit is handed.
    """
    design = _design()
    w = prior.weights(design)[0]
    through_ladder = prior.walk_forward(design, w, draws=4, arm=prior.RIDGE)
    direct = prior.walk_forward(design, w, draws=4)
    assert [(p.player_id, p.year, p.mean) for p in through_ladder] == [
        (p.player_id, p.year, p.mean) for p in direct
    ]


def test_an_arm_that_cannot_be_imported_is_reported_rather_than_raised() -> None:
    """A dropped dependency has to leave a verdict behind, not a stack trace."""
    ok, reason = prior.arm_available("lightgbm")
    assert isinstance(ok, bool)
    assert ok or reason


def test_the_ladder_publishes_the_ridge_unless_an_arm_beats_it() -> None:
    design = _design(n=120)
    out = prior.ladder(design, prior.weights(design)[0], draws=2)
    assert out["arms"][prior.RIDGE]["available"]
    assert out["published_arm"] in (prior.RIDGE, prior.FOREST, prior.BOOSTED)
    for name in (prior.FOREST, prior.BOOSTED):
        block = out["arms"][name]
        if block["available"]:
            assert block["vs_ridge"]["ships"] is (
                block["vs_ridge"]["excludes_zero"] and block["vs_ridge"]["delta_r"] > 0
            )
        else:
            assert block["reason"]


# -------------------------------------------------------------------- the blend


def _cohort_model(prior_mean: float, prior_var: float, obs_var: float) -> hierarchical.CohortModel:
    """The cohort fit whose posterior is the same algebra `blend` does by hand."""
    return hierarchical.CohortModel(
        mu=prior_mean,
        tau2=prior_var,
        sigma2=obs_var,
        origin=0.0,
        n_players=10,
        n_maps=10,
        n_replicated=10,
        iterations=1,
        converged=True,
        estimated=True,
        fallback=None,
        loglik=None,
    )


def test_the_blend_agrees_with_the_cohort_posterior_it_could_not_call() -> None:
    """Reuse by proof rather than by contortion.

    `hierarchical.posterior` derives its observation variance from a cohort's
    σ²/maps and takes its prior mean from `model.mu`, so P5 — which has a per-row
    prior mean and `se²` already in hand — cannot call it. This is the test that
    keeps the four lines it writes instead from drifting into different algebra.
    """
    coef, se, prior_mean, prior_var = 0.08, 0.12, -0.02, 0.004
    mean, sd, weight = prior.blend(coef, se, prior_mean, prior_var)

    model = _cohort_model(prior_mean, prior_var, se**2)
    reference = hierarchical.posterior(coef, 1, model)
    tau = model.tau
    assert mean == pytest.approx(reference.z * tau, rel=1e-12)
    assert sd == pytest.approx(reference.sd * tau, rel=1e-12)
    # `shrinkage` is the share the *observation* kept, which is the complement of
    # the share the prior supplied.
    assert weight == pytest.approx(1.0 - reference.shrinkage, rel=1e-12)


def test_a_target_noisier_than_the_prior_hands_the_answer_to_the_prior() -> None:
    """The record's own case: se 0.127 against an out-of-fold residual of 0.06."""
    _mean, _sd, weight = prior.blend(0.1, 0.127, 0.0, 0.0039)
    assert weight > 0.75


def test_a_precise_target_keeps_its_own_estimate() -> None:
    _mean, _sd, weight = prior.blend(0.1, 0.01, 0.0, 0.0039)
    assert weight < 0.05


def test_the_prior_variance_is_measured_out_of_fold_and_says_so() -> None:
    design = _design()
    predictions = prior.walk_forward(design, prior.weights(design)[0], draws=8)
    out = prior.prior_variance(design, predictions)
    assert out["available"]
    assert out["used"] == "residual_var"
    assert out["noise_adjusted_var"] <= out["residual_var"]


def test_every_rated_row_carries_the_estimate_it_was_blended_with() -> None:
    """The table is only readable if the inputs travel with the answer."""
    design = _design()
    predictions = prior.walk_forward(design, prior.weights(design)[0], draws=8)
    rated = prior.blend_all(design, predictions, 0.004, prior.RIDGE)
    assert len(rated) == len(predictions)
    by_key = {(t.player_id, t.season_id): t for t in design.targets}
    for row in rated:
        target = by_key[(row.player_id, row.season_id)]
        assert row.coef == target.coef
        assert row.se == target.se
        assert min(row.coef, row.prior_mean) <= row.skill <= max(row.coef, row.prior_mean)
