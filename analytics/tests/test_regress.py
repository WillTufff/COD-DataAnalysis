import numpy as np

from cdlhub_analytics.regress import fit_logistic_l2


def test_recovers_known_weights() -> None:
    # Simulated logistic data with known coefficients; small ridge -> close recovery.
    rng = np.random.default_rng(7)
    true_w = np.array([1.2, -0.8, 0.0])
    x = rng.standard_normal((20000, 3))
    p = 1.0 / (1.0 + np.exp(-(x @ true_w)))
    y = (rng.random(20000) < p).astype(float)
    fit = fit_logistic_l2(x, y, l2=0.01)
    assert fit.converged
    assert np.allclose(fit.weights, true_w, atol=0.08)
    assert abs(fit.intercept) < 0.05


def test_ridge_shrinks_toward_zero() -> None:
    rng = np.random.default_rng(11)
    x = rng.standard_normal((500, 2))
    y = (x[:, 0] + rng.standard_normal(500) > 0).astype(float)
    loose = fit_logistic_l2(x, y, l2=0.1)
    tight = fit_logistic_l2(x, y, l2=100.0)
    assert float(np.linalg.norm(tight.weights)) < float(np.linalg.norm(loose.weights))


def test_separable_data_stays_finite() -> None:
    # Perfectly separable data explodes unpenalized logistic; ridge must not.
    x = np.array([[-2.0], [-1.0], [1.0], [2.0]])
    y = np.array([0.0, 0.0, 1.0, 1.0])
    fit = fit_logistic_l2(x, y, l2=1.0)
    assert np.isfinite(fit.weights).all() and np.isfinite(fit.intercept)
    assert fit.weights[0] > 0.0
    p = fit.predict(np.array([[3.0]]))
    assert 0.5 < float(p[0]) < 1.0


def test_collinear_features_split_weight() -> None:
    # Two identical columns: ridge divides the weight evenly instead of failing.
    rng = np.random.default_rng(3)
    base = rng.standard_normal(2000)
    x = np.column_stack([base, base])
    y = (base + 0.5 * rng.standard_normal(2000) > 0).astype(float)
    fit = fit_logistic_l2(x, y, l2=1.0)
    assert fit.converged
    assert np.isclose(fit.weights[0], fit.weights[1], atol=1e-6)
