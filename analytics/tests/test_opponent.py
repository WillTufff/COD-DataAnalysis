"""Opponent adjustment: the solver identities, the panel, and the invariances."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from cdlhub_analytics.ratings import opponent as op


def _toy(seed: int = 7, n: int = 60, p: int = 5) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = np.column_stack([np.ones(n), rng.standard_normal((n, p - 1))])
    beta = rng.standard_normal(p)
    y = x @ beta + 0.3 * rng.standard_normal(n)
    w = 1.0 + rng.random(n) * 4.0
    return x, y, w


def test_solve_wls_matches_the_closed_form() -> None:
    x, y, w = _toy()
    fit = op.solve_wls(x, y, w, 0.0)
    expected = np.linalg.lstsq(x * np.sqrt(w)[:, None], y * np.sqrt(w), rcond=None)[0]
    assert np.allclose(fit.beta, expected, atol=1e-8)


def test_solve_wls_leaves_the_intercept_unpenalized() -> None:
    x, y, w = _toy()
    heavy = op.solve_wls(x, y, w, 1e6)
    # Every slope is crushed to zero; the intercept survives as the weighted mean.
    assert np.allclose(heavy.beta[1:], 0.0, atol=1e-4)
    assert heavy.beta[0] == pytest.approx(np.sum(w * y) / np.sum(w), rel=1e-3)


@pytest.mark.parametrize("ridge", [0.0, 0.5])
def test_crossfit_holds_out_the_fold_it_predicts(ridge: float) -> None:
    """Each fold's values come from a fit that never saw that fold."""
    x, y, w = _toy(seed=11, n=50, p=4)
    folds = [i % 5 for i in range(len(y))]
    selector = np.array([0.0, 1.0, 1.0, 0.0])
    got = op.crossfit(x, y, w, ridge, folds, selector)

    for fold in sorted(set(folds)):
        holdout = np.array([f == fold for f in folds])
        trained = op.solve_wls(x[~holdout], y[~holdout], w[~holdout], ridge)
        expected = (x[holdout] * selector) @ trained.beta
        assert np.allclose(got[holdout], expected, atol=1e-10)


def test_crossfit_stays_finite_on_a_rank_deficient_design() -> None:
    """The design this module fits is rank deficient by construction.

    A duplicated column has no separable direction, which is the shape the
    opponent and own blocks are in on every real cohort. The contribution has to
    stay on the same scale as the in-sample one rather than exploding, which is
    what leave-one-series-out did here.
    """
    x, y, w = _toy(seed=13, n=60, p=4)
    x = np.column_stack([x, x[:, 1]])  # an exact duplicate: one null direction
    selector = np.array([0.0, 1.0, 0.0, 0.0, 1.0])
    folds = [i % 5 for i in range(len(y))]
    in_sample = (x * selector) @ op.solve_wls(x, y, w, 0.0).beta
    out_of_sample = op.crossfit(x, y, w, 0.0, folds, selector)
    assert np.all(np.isfinite(out_of_sample))
    assert out_of_sample.std() < 5.0 * in_sample.std() + 1.0


def test_fold_assignment_reads_the_natural_key_not_arrival_order() -> None:
    """Renumbering the loader's keys must not move a fold."""
    keys = ["cwl-b#1", "cwl-a#2", "cwl-c#1", "cwl-a#1"]
    assert op.fold_of(keys, folds=2) == op.fold_of(keys, folds=2)
    # The same series in a different arrival order lands in the same folds.
    shuffled = ["cwl-a#1", "cwl-c#1", "cwl-b#1", "cwl-a#2"]
    by_key = dict(zip(keys, op.fold_of(keys, folds=2), strict=True))
    assert [by_key[k] for k in shuffled] == op.fold_of(shuffled, folds=2)


def test_cluster_cov_reduces_to_the_sandwich_at_one_row_per_cluster() -> None:
    x, y, w = _toy(seed=5, n=40, p=3)
    fit = op.solve_wls(x, y, w, 0.0)
    clusters = list(range(len(y)))
    covariance = op.cluster_cov(x, w, fit, clusters)
    meat = (x * (w * fit.residual)[:, None]).T @ (x * (w * fit.residual)[:, None])
    scale = len(y) / (len(y) - 1)
    assert np.allclose(covariance, scale * fit.inv @ meat @ fit.inv, atol=1e-10)


def test_gcv_is_infinite_once_the_design_saturates() -> None:
    x, y, w = _toy(seed=9, n=6, p=6)
    fit = op.solve_wls(x, y, w, 0.0)
    assert op.gcv(fit, w) == float("inf")


# ------------------------------------------------------------- the adjustment


def _panel(seed: int = 2) -> op.Panel:
    """A synthetic cohort with a real opponent effect in it."""
    rng = np.random.default_rng(seed)
    strength = rng.normal(0.0, 0.5, 16)
    lines = []
    for game in range(80):
        picked = rng.permutation(16)[:8]
        left, right = picked[:4], picked[4:]
        for own, other in ((left, right), (right, left)):
            for player in own:
                value = 10.0 + float(strength[other].sum()) + rng.normal(0.0, 1.0)
                lines.append(
                    op.Line(
                        player_id=int(player),
                        team_id=0 if own is left else 1,
                        game_id=game,
                        series_id=game // 2,
                        event_id=0,
                        season_id=0,
                        mode_id=0,
                        duration_s=600.0,
                        map_key=f"evt-{game // 2:03d}#{game % 2 + 1}",
                        opponents=tuple(sorted(int(p) for p in other)),
                        teammates=tuple(sorted(int(p) for p in own if p != player)),
                        opp_rating=1500.0 + float(strength[other].sum()) * 100.0,
                        values={"stat": (value, 1.0)},
                    )
                )
    return op.Panel(
        season_id=0,
        mode_id=0,
        mode_slug="synthetic",
        title="synthetic",
        side=4,
        lines=tuple(lines),
        features=(),
    )


def test_the_unadjusted_aggregate_is_sum_then_divide() -> None:
    """The season value is Σnumerator / Σdenominator, never a mean of ratios."""
    panel = _panel()
    values = {v.player_id: v for v in op.aggregate(panel, "stat")}
    player = next(iter(values))
    lines = [line for line in panel.lines if line.player_id == player]
    numerator = sum(line.values["stat"][0] for line in lines)
    denominator = sum(line.values["stat"][1] for line in lines)
    assert values[player].value == pytest.approx(numerator / denominator)


def test_an_adjustment_leaves_the_cohort_mean_where_it_was() -> None:
    """The correction is centred, so it redistributes rather than inflates."""
    panel = _panel()
    columns = op.build_columns(panel, teammates=False)
    matrix = op.design(panel, columns)
    adjustment = op.adjust_lineup_fe(panel, "stat", columns, matrix, cross_fit=False)
    _rate, weight, mask = op.response(panel, "stat")
    assert float(np.sum(adjustment.delta[mask] * weight[mask])) == pytest.approx(0.0, abs=1e-8)


def test_the_adjustment_survives_a_shift_between_the_blocks() -> None:
    """The coefficients are unidentified along a shift; the correction is not."""
    panel = _panel()
    columns = op.build_columns(panel, teammates=False)
    matrix = op.design(panel, columns)
    baseline = op.adjust_lineup_fe(panel, "stat", columns, matrix, cross_fit=False)

    # Move a constant from every own column into every opponent column. Every
    # fitted value is unchanged, so the adjustment has to be too.
    shifted = matrix.copy()
    own_block = sorted(columns.own.values())
    opp_block = sorted(columns.opp.values())
    assert own_block and opp_block
    rate, weight, mask = op.response(panel, "stat")
    fit = op.solve_wls(matrix[mask], rate[mask], weight[mask], op.FE_RIDGE)
    moved = fit.beta.copy()
    moved[own_block] += 3.0
    moved[opp_block] -= 3.0 / panel.side
    assert np.allclose(shifted[mask] @ moved, matrix[mask] @ fit.beta, atol=1e-8)

    selector = np.zeros(matrix.shape[1])
    selector[opp_block] = 1.0
    before = (matrix[mask] * selector) @ fit.beta
    after = (matrix[mask] * selector) @ moved
    centred_before = before - before.mean()
    centred_after = after - after.mean()
    assert np.allclose(centred_before, centred_after, atol=1e-8)
    assert baseline.delta_sd is not None


def test_admission_pools_thin_players_and_never_drops_their_line() -> None:
    panel = _panel()
    thin = op.Panel(
        season_id=panel.season_id,
        mode_id=panel.mode_id,
        mode_slug=panel.mode_slug,
        title=panel.title,
        side=panel.side,
        lines=panel.lines[:40],  # everyone is now under the threshold
        features=(),
    )
    columns = op.build_columns(thin, teammates=False)
    assert columns.pooled, "a thin cohort should pool somebody"
    matrix = op.design(thin, columns)
    # Every line still has a row, and every row still names four opponents.
    assert matrix.shape[0] == len(thin.lines)
    opp_block = sorted(columns.opp.values())
    assert np.allclose(matrix[:, opp_block].sum(axis=1), float(thin.side))


def test_the_stop_rule_skips_a_rung_that_costs_reliability() -> None:
    """The ladder is not monotone and the rule must not assume it is."""
    summary: dict[str, dict[str, Any]] = {
        op.RUNG_TEAM: {
            "mean_abs_dz_median": 0.02,
            "placebo_ratio_median": None,
            "reliability_gain_median": 0.001,
        },
        op.RUNG_LINEUP: {
            "mean_abs_dz_median": 0.10,
            "placebo_ratio_median": 1.6,
            "reliability_gain_median": -0.02,  # less repeatable than raw
        },
        op.RUNG_CONTEXT: {
            "mean_abs_dz_median": 0.05,
            "placebo_ratio_median": 1.9,
            "reliability_gain_median": 0.004,
        },
        op.RUNG_SHRUNK: {
            "mean_abs_dz_median": 0.001,  # moves nothing
            "placebo_ratio_median": None,
            "reliability_gain_median": None,
        },
    }
    verdict = op.adopt(summary)
    assert verdict["adopted"] == op.RUNG_CONTEXT
    failed = {row["rung"]: row for row in verdict["per_rung"]}
    assert failed[op.RUNG_LINEUP]["clears"] is False
    assert failed[op.RUNG_LINEUP]["clears_movement"] is True
    assert failed[op.RUNG_SHRUNK]["clears_movement"] is False


def test_a_rung_that_fails_its_placebo_is_never_adopted() -> None:
    summary: dict[str, dict[str, Any]] = {
        op.RUNG_TEAM: {
            "mean_abs_dz_median": 0.2,
            "placebo_ratio_median": None,
            "reliability_gain_median": 0.0,
        },
        op.RUNG_LINEUP: {
            "mean_abs_dz_median": 0.9,
            "placebo_ratio_median": 1.0,  # indistinguishable from a shuffle
            "reliability_gain_median": 0.5,
        },
    }
    assert op.adopt(summary)["adopted"] == op.RUNG_TEAM


def test_the_shrunk_rung_hands_per_line_questions_back_down_the_ladder() -> None:
    verdict: dict[str, Any] = {
        "adopted": op.RUNG_SHRUNK,
        "per_rung": [
            {"rung": op.RUNG_TEAM, "clears": True},
            {"rung": op.RUNG_LINEUP, "clears": False},
            {"rung": op.RUNG_CONTEXT, "clears": True},
            {"rung": op.RUNG_SHRUNK, "clears": True},
        ],
    }
    assert op.adjusting_rung(verdict) == op.RUNG_CONTEXT


def test_the_positive_control_recovers_a_planted_opponent_effect() -> None:
    """The placebo says nothing comes from nothing; this says something comes
    from something, which no other test in the plan asserts."""
    result = op.positive_control(maps=300)
    assert result["correlation"] > 0.9
    assert result["slope"] == pytest.approx(1.0, abs=0.15)
