"""Hyperparameter sensitivity for the two team rating systems.

Elo's K, Glicko-2's tau, and the rating-period granularity were asserted
constants in a project whose stated rule is that a model ships with its
backtest. This module scores each setting on the same walk-forward evaluation
the published runs use, and stores the grid as an artifact.

**The sweep does not choose the published settings.** Picking the argmin on the
same 1,310 series the scores are reported over would be selection on the test
set — the reported Brier would then be the best of twenty draws rather than an
estimate of anything. The defaults stay declared constants, and this grid is
published as sensitivity analysis: its job is to show how much the choice
matters, which on this archive is very little. A setting is worth changing when
the grid moves the metric by more than the spread between the three models,
and that case is stated explicitly rather than applied silently.
"""

from __future__ import annotations

from typing import Any

from ..backtest import evaluate
from .fit import PERIODS, SeriesRow, elo_walk_forward, glicko2_walk_forward

ELO_KS = (8.0, 12.0, 16.0, 20.0, 24.0, 28.0, 32.0, 40.0, 48.0, 64.0)
GLICKO_TAUS = (0.2, 0.3, 0.5, 0.8, 1.2)


def _score(preds: list[Any]) -> dict[str, float | int]:
    report = evaluate(preds)
    return {
        "n": report.n,
        "brier": round(report.brier, 5),
        "log_loss": round(report.log_loss, 5),
        "accuracy": round(report.accuracy, 4),
    }


def sweep(
    series: list[SeriesRow],
    lineage: dict[int, int] | None = None,
    elo_k: float = 32.0,
    glicko_tau: float = 0.5,
    glicko_period: str = "event",
) -> dict[str, Any]:
    """Score every setting on the full walk-forward record.

    Every cell predicts the same series, so the grid is internally comparable;
    `published` marks the declared defaults so a reader can see where they sit
    in the grid rather than having to trust that they are reasonable.
    """
    elo = [{"k": k, **_score(elo_walk_forward(series, k, lineage)[0])} for k in ELO_KS]
    glicko = [
        {
            "tau": tau,
            "period": period,
            **_score(glicko2_walk_forward(series, tau, lineage, period)[0]),
        }
        for period in PERIODS
        for tau in GLICKO_TAUS
    ]

    def best(grid: list[dict[str, Any]]) -> dict[str, Any]:
        return min(grid, key=lambda c: c["brier"])

    return {
        "note": (
            "Sensitivity analysis, not model selection: the published settings are "
            "fixed constants and are not chosen from this grid."
        ),
        "published": {
            "elo_k": elo_k,
            "glicko_tau": glicko_tau,
            "glicko_period": glicko_period,
        },
        "elo": elo,
        "glicko2": glicko,
        "best_by_brier": {"elo": best(elo), "glicko2": best(glicko)},
    }
