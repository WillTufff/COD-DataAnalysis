"""Walk-forward evaluation: Brier, log loss, accuracy, calibration bins.

Predictions are strictly pre-update (the model never sees the result it is
predicting). Published to the backtests table and rendered on /methodology —
educational model evaluation only, never wager framing.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date

import psycopg


@dataclass
class Prediction:
    p: float  # P(team1 wins), computed before the update
    won: bool  # team1 actually won
    when: date


@dataclass
class Report:
    window_from: date
    window_to: date
    n: int
    brier: float
    log_loss: float
    accuracy: float
    calibration: list[dict[str, float | int]]


_EPS = 1e-12


def evaluate(preds: list[Prediction], n_bins: int = 10) -> Report:
    if not preds:
        raise ValueError("no predictions to evaluate")
    n = len(preds)
    brier = sum((p.p - (1.0 if p.won else 0.0)) ** 2 for p in preds) / n
    ll = (
        -sum(math.log(max(p.p, _EPS)) if p.won else math.log(max(1.0 - p.p, _EPS)) for p in preds)
        / n
    )
    acc = sum(1 for p in preds if (p.p >= 0.5) == p.won) / n

    bins: list[dict[str, float | int]] = []
    for i in range(n_bins):
        lo, hi = i / n_bins, (i + 1) / n_bins
        inbin = [p for p in preds if lo <= p.p < hi or (i == n_bins - 1 and p.p == 1.0)]
        if not inbin:
            bins.append({"lo": lo, "hi": hi, "n": 0})
            continue
        bins.append(
            {
                "lo": lo,
                "hi": hi,
                "n": len(inbin),
                "mean_pred": sum(p.p for p in inbin) / len(inbin),
                "frac_won": sum(1 for p in inbin if p.won) / len(inbin),
            }
        )
    return Report(
        window_from=min(p.when for p in preds),
        window_to=max(p.when for p in preds),
        n=n,
        brier=brier,
        log_loss=ll,
        accuracy=acc,
        calibration=bins,
    )


def write(conn: psycopg.Connection[tuple[object, ...]], run_id: int, report: Report) -> None:
    conn.execute(
        "INSERT INTO backtests (run_id, window_from, window_to, n_predictions, brier,"
        " log_loss, accuracy, calibration) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (
            run_id,
            report.window_from,
            report.window_to,
            report.n,
            report.brier,
            report.log_loss,
            report.accuracy,
            json.dumps(report.calibration),
        ),
    )
