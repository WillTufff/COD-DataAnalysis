"""The scipy surface this package is allowed to touch, behind typed signatures.

`scipy` arrives with the season-varying plus-minus, which needs three things
numpy does not offer: a Cholesky factorization reused across right-hand sides,
the log-determinant that REML is written in terms of, and the normal quantile
function the rank transform of a censored margin is defined by. Everything that
calls into it calls through here, so `mypy --strict` still holds over the
project's own logic whatever upstream's stubs do or do not declare.

Nothing here has an opinion about ratings. It is arithmetic with a stated
signature, and it is deliberately small: an adapter that grows a model inside it
is not an adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.linalg import cho_factor, cho_solve
from scipy.optimize import minimize
from scipy.special import ndtri

from ..regress import FloatArray


@dataclass(frozen=True)
class Cholesky:
    """A symmetric positive-definite matrix, factored once and reused.

    The generalized ridge solves the same system against several responses and
    needs the diagonal of its inverse for standard errors and its determinant
    for REML. Factoring once and answering all three from the factor is the
    difference between one decomposition and four.
    """

    factor: tuple[FloatArray, bool]
    size: int

    def solve(self, rhs: FloatArray) -> FloatArray:
        out: FloatArray = cho_solve(self.factor, rhs)
        return out

    def inverse(self) -> FloatArray:
        """The full inverse, by solving against the identity.

        Only worth calling where the whole matrix is wanted — the coefficient
        variances need its diagonal and the effective degrees of freedom need
        its product with the Gram matrix.
        """
        return self.solve(np.eye(self.size, dtype=float))

    def log_determinant(self) -> float:
        """log|A|, from twice the log of the triangular factor's diagonal."""
        triangular = self.factor[0]
        return 2.0 * float(np.sum(np.log(np.abs(np.diag(triangular)))))


def cholesky(matrix: FloatArray) -> Cholesky:
    """Factor a symmetric positive-definite matrix.

    The ridge penalty is what guarantees positive definiteness here: the Gram
    matrix of a rank-deficient design is only positive *semi*-definite, and
    every caller adds λ₀I before arriving.
    """
    factor = cho_factor(matrix, lower=True, check_finite=False)
    return Cholesky(
        factor=(np.asarray(factor[0], dtype=float), bool(factor[1])), size=matrix.shape[0]
    )


def normal_quantile(probabilities: FloatArray) -> FloatArray:
    """Φ⁻¹, the inverse standard normal CDF, elementwise.

    What turns a rank into a normal score. Inputs must be strictly inside (0, 1);
    the rank transform's (i / (n + 1)) construction guarantees that.
    """
    out: FloatArray = ndtri(probabilities)
    return out


def minimize_scalar_pair(
    objective: Callable[[FloatArray], float],
    start: Sequence[float],
    bounds: Sequence[tuple[float, float]],
) -> tuple[FloatArray, float, bool]:
    """Minimize a two-parameter criterion over a box. (argmin, value, converged).

    Used for (λ₀, λ_w) by GCV or REML, both of which are smooth in the log of
    each penalty and neither of which has a closed-form minimizer. Nelder-Mead
    rather than a gradient method: the objective costs one Cholesky, the
    derivative would cost several, and two parameters is where a simplex is
    still the cheap answer.
    """
    result = minimize(
        objective,
        np.asarray(start, dtype=float),
        method="Nelder-Mead",
        bounds=list(bounds),
        options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 200},
    )
    return np.asarray(result.x, dtype=float), float(result.fun), bool(result.success)
