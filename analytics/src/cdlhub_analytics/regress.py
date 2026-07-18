"""L2-regularized logistic regression, fit by iteratively reweighted least
squares. Spec: /methodology#player-rating.

Deliberately implemented directly in numpy: ~40 lines, no black box, so the
methodology page can show exactly what is being minimized —

    L(w) = -sum_i [ y_i log p_i + (1-y_i) log(1-p_i) ] + (l2/2) ||w||^2

with the intercept unpenalized. Newton steps until the gradient norm is
below tol. Inputs are expected standardized; l2 is on that scale.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]


@dataclass
class LogisticFit:
    intercept: float
    weights: FloatArray  # one per feature column
    converged: bool
    n_iter: int

    def predict(self, x: FloatArray) -> FloatArray:
        """P(y=1) for rows of x (n × k)."""
        return _sigmoid(self.intercept + x @ self.weights)


def _sigmoid(z: FloatArray) -> FloatArray:
    out: FloatArray = 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))
    return out


def fit_logistic_l2(
    x: FloatArray,
    y: FloatArray,
    l2: float = 1.0,
    max_iter: int = 100,
    tol: float = 1e-8,
) -> LogisticFit:
    """Fit y ∈ {0,1} on x (n × k). Returns intercept + per-feature weights.

    Penalty applies to feature weights only, never the intercept. The ridge
    term also makes the Newton system positive definite, so collinear or
    tiny cohorts (Uplink) degrade toward zero weights instead of blowing up.
    """
    n, k = x.shape
    xd = np.hstack([np.ones((n, 1)), x])
    w = np.zeros(k + 1)
    penalty = np.full(k + 1, l2)
    penalty[0] = 0.0  # unpenalized intercept

    converged = False
    it = 0
    while it < max_iter:
        it += 1
        p = _sigmoid(xd @ w)
        grad = xd.T @ (y - p) - penalty * w
        if float(np.linalg.norm(grad)) < tol * n:
            converged = True
            break
        wt = np.maximum(p * (1.0 - p), 1e-9)
        hess = (xd * wt[:, None]).T @ xd + np.diag(penalty)
        w = w + np.linalg.solve(hess, grad)
    return LogisticFit(intercept=float(w[0]), weights=w[1:], converged=converged, n_iter=it)
