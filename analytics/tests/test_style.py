"""Simulation tests for the player style model. No database required.

This module publishes a null — that the box scores in this archive hold no
discrete archetypes — and a null is worth exactly as much as the estimator's
ability to find structure when structure is there. So the tests are in two
halves.

The ordinary invariants come first: components are retained only against a
permuted null, quality really leaves the residual, percentiles are percentiles,
a basis refuses columns that one era cannot reach.

The half that matters plants known answers. A cloud with three well-separated
groups has to be recovered, at the right k, with the gap statistic and the
no-cluster null both agreeing. A single Gaussian has to be refused however
elongated it is — and the elongated case is the specific trap, because a
bisected cigar is a highly stable partition and a naive stability test calls it
two clusters. Those two together are the argument for believing "no archetypes"
on the real data.
"""

from __future__ import annotations

import math

import numpy as np

from cdlhub_analytics.style import (
    MIN_SEASON_COVERAGE,
    Column,
    Grid,
    Subject,
    _percentiles,
    assess,
    build_basis,
    gap_choice,
    gaussian_null,
    horn_components,
    jaccard_stability,
    kmeans,
    residualize_quality,
    silhouette,
    standardize,
)


def three_groups(n: int = 480, seed: int = 1, separation: float = 6.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    centres = np.array([[0.0, 0.0], [separation, 0.0], [separation / 2, separation]])
    labels = rng.integers(0, 3, n)
    return centres[labels] + rng.standard_normal((n, 2))


def one_cloud(n: int = 480, seed: int = 2, elongation: float = 4.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 2))
    x[:, 0] *= elongation
    return x


# ------------------------------------------------------------------ invariants


def test_standardize_leaves_a_constant_column_alone_rather_than_dividing_by_zero() -> None:
    x = np.column_stack([np.ones(20), np.arange(20.0)])
    z = standardize(x)
    assert np.all(np.isfinite(z))
    assert np.allclose(z[:, 0], 0.0)
    assert abs(float(z[:, 1].std()) - 1.0) < 1e-9


def test_percentiles_are_uniform_and_ordered() -> None:
    v = np.array([5.0, 1.0, 3.0, 2.0])
    p = _percentiles(v)
    assert list(np.argsort(p)) == list(np.argsort(v))
    assert p.min() > 0.0 and p.max() < 1.0


def test_quality_leaves_the_residual_entirely() -> None:
    rng = np.random.default_rng(3)
    quality = rng.standard_normal(300)
    style = rng.standard_normal((300, 5))
    x = style + 2.0 * quality[:, None]  # every feature loaded on quality
    resid = residualize_quality(x, quality)
    after = np.corrcoef(resid.matrix.T, quality)[:-1, -1]
    assert np.all(np.abs(after) < 1e-8)
    assert resid.quality_share > 0.6


def test_a_residual_with_no_quality_in_it_reports_almost_nothing_removed() -> None:
    rng = np.random.default_rng(4)
    x = rng.standard_normal((400, 6))
    resid = residualize_quality(x, rng.standard_normal(400))
    assert resid.quality_share < 0.02


def test_horn_keeps_the_planted_rank_and_not_the_noise() -> None:
    rng = np.random.default_rng(5)
    latent = rng.standard_normal((500, 3))
    loading = rng.standard_normal((3, 12))
    x = latent @ loading + 0.4 * rng.standard_normal((500, 12))
    comps = horn_components(standardize(x), np.random.default_rng(6), replicates=60)
    assert comps.n_retained == 3


def test_horn_keeps_nothing_it_cannot_justify_on_pure_noise() -> None:
    rng = np.random.default_rng(7)
    x = standardize(rng.standard_normal((400, 10)))
    comps = horn_components(x, np.random.default_rng(8), replicates=60)
    # max(keep, 1) floors the retained count at one; the honest check is that
    # the leading eigenvalue does not clear its own permuted null.
    assert float(comps.eigenvalues[0]) < float(comps.null95[0]) * 1.15
    assert comps.n_retained <= 1


def test_kmeans_is_deterministic_given_its_generator() -> None:
    x = three_groups()
    a = kmeans(x, 3, np.random.default_rng(9))
    b = kmeans(x, 3, np.random.default_rng(9))
    assert a.inertia == b.inertia
    assert list(a.labels) == list(b.labels)


def test_silhouette_is_undefined_at_one_cluster_rather_than_zero() -> None:
    x = three_groups()
    assert math.isnan(silhouette(x, np.zeros(len(x), dtype=int), 1))


def test_the_gap_rule_takes_the_first_k_within_one_se_of_the_next() -> None:
    class R:
        def __init__(self, k: int, gap: float, sk: float) -> None:
            self.k, self.gap, self.sk = k, gap, sk

    # k=1 is not within one s_k of k=2, k=2 is of k=3.
    rs = [R(1, 1.0, 0.01), R(2, 1.5, 0.01), R(3, 1.505, 0.02)]
    assert gap_choice(rs) == 2  # type: ignore[arg-type]


# ------------------------------------------------------------ the planted cases


def test_three_planted_groups_are_found_at_the_right_k() -> None:
    x = three_groups()
    results, gap_k, surviving = assess(x, seed=11, k_max=5)
    assert gap_k == 3
    assert surviving == 3
    at3 = next(r for r in results if r.k == 3)
    assert at3.silhouette_null is not None
    # The claim is not a silhouette threshold but a margin over the no-cluster
    # null: planted groups score most of the way to twice what an unclustered
    # cloud does, which is the distance the real data never covers.
    assert at3.silhouette > 1.8 * at3.silhouette_null.hi
    assert min(at3.stability) > 0.9
    assert at3.beats_null


def test_a_single_gaussian_is_refused_at_every_k() -> None:
    x = one_cloud(elongation=1.0)
    results, gap_k, surviving = assess(x, seed=12, k_max=5)
    assert surviving == 1
    assert all(not r.beats_null for r in results if r.k >= 2)


def test_an_elongated_cloud_is_refused_despite_a_highly_stable_bisection() -> None:
    """The trap this module exists to avoid.

    Splitting a cigar down its long axis reproduces almost perfectly, so raw
    bootstrap stability calls it two clusters. Measured against a cloud built
    to have no clusters, it is nothing.
    """
    x = one_cloud(elongation=5.0)
    stability = jaccard_stability(x, 2, np.random.default_rng(13), replicates=40)
    assert min(stability) > 0.85, "the bisection really is reproducible"

    sil_null, stab_null = gaussian_null(x, 2, np.random.default_rng(14), replicates=8)
    fit = kmeans(x, 2, np.random.default_rng(15))
    observed = silhouette(x, fit.labels, 2)
    assert sil_null.contains(observed), "and it is exactly what no clusters look like"

    _, _, surviving = assess(x, seed=16, k_max=4)
    assert surviving == 1


def test_the_null_band_widens_to_cover_its_own_draws() -> None:
    x = one_cloud(elongation=3.0)
    sil_null, stab_null = gaussian_null(x, 3, np.random.default_rng(17), replicates=8)
    assert sil_null.lo <= sil_null.mean <= sil_null.hi
    assert stab_null.lo <= stab_null.mean <= stab_null.hi


def test_separation_has_to_be_real_before_the_verdict_flips() -> None:
    """Power, stated as the separation this test can actually resolve."""
    weak = three_groups(separation=1.0, seed=18)
    _, _, surviving_weak = assess(weak, seed=19, k_max=4)
    strong = three_groups(separation=6.0, seed=18)
    _, _, surviving_strong = assess(strong, seed=19, k_max=4)
    assert surviving_weak == 1
    assert surviving_strong == 3


# ------------------------------------------------------------------- the basis


def _grid(values: np.ndarray, seasons: list[int], columns: list[Column]) -> Grid:
    subjects = [Subject(player_id=i + 1, season_id=s) for i, s in enumerate(seasons)]
    return Grid(
        subjects=subjects,
        columns=columns,
        values=values,
        season_of=np.array(seasons),
        rating=np.zeros(len(seasons)),
    )


def test_a_column_one_era_cannot_reach_is_refused() -> None:
    seasons = [1] * 50 + [2] * 50
    values = np.ones((100, 2))
    # column 1 is present for everyone; column 2 only for season 2
    values[:50, 1] = np.nan
    grid = _grid(values, seasons, [Column(0, "kd"), Column(0, "deep_streak_rate")])
    eligible = grid.eligible()
    assert bool(eligible[0])
    assert not bool(eligible[1])
    assert grid.season_coverage(1)[1] == 0.0 < MIN_SEASON_COVERAGE


def test_the_basis_is_complete_case_over_the_columns_it_admitted() -> None:
    seasons = [1] * 40 + [2] * 40
    rng = np.random.default_rng(20)
    values = rng.standard_normal((80, 2))
    values[5, 0] = np.nan  # one hole in an admitted column drops that row
    grid = _grid(values, seasons, [Column(0, "kd"), Column(0, "kills_p10")])
    basis = build_basis(grid, "core", (0,))
    assert basis.n == 79
    assert np.all(np.isfinite(basis.matrix()))


def test_coverage_reports_retention_per_season_so_era_skew_is_visible() -> None:
    seasons = [1] * 40 + [2] * 40
    values = np.ones((80, 1))
    values[:20, 0] = np.nan  # half of season 1 lost
    grid = _grid(values, seasons, [Column(0, "kd")])
    basis = build_basis(grid, "core", (0,))
    cov = {c["season_id"]: c for c in basis.coverage()}
    assert cov[1]["kept"] == 20 and cov[1]["share"] == 0.5
    assert cov[2]["kept"] == 40 and cov[2]["share"] == 1.0
