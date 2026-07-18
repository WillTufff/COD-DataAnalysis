"""winprob_v1: series win probability as a learned correction to Glicko-2.
Spec: /methodology#winprob.

Glicko-2 is the strongest baseline in the backtest table, so instead of a
new rating system this model asks a sharper question: *given* the ratings,
does anything else — recent form, head-to-head history, rating uncertainty,
the Elo signal — carry additional information about who wins a series?

Features per series, all computed strictly before it is played:

  glicko_logit  logit of the walk-forward Glicko-2 win probability
  elo_logit     logit of the walk-forward Elo win probability
  rd_sum        combined Glicko rating deviation, scaled by 1/350
  form_diff     difference in win rate over each team's last 10 series
                (unplayed slots count 0.5, so new teams sit at even form)
  h2h_edge      shrunken prior head-to-head record, (w+2)/(n+4) − 0.5

The model is L2 logistic regression refit on an expanding window every
REFIT_EVERY series. Until MIN_TRAIN prior series exist it predicts with the
identity coefficients (1.0 on glicko_logit, 0 elsewhere), i.e. it *is*
Glicko-2 — so the backtest covers the same series as the baselines and any
Brier improvement is attributable to the added features, not a different
evaluation window. The final refit's coefficients are stored as an artifact:
a near-zero form_diff weight is a published test of the momentum narrative.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from typing import Any

import numpy as np
import psycopg

from ..backtest import Prediction
from ..regress import fit_logistic_l2
from .elo import Elo, expected
from .fit import SeriesRow
from .glicko2 import Glicko2

FEATURES = ("glicko_logit", "elo_logit", "rd_sum", "form_diff", "h2h_edge")

L2 = 1.0
MIN_TRAIN = 200
REFIT_EVERY = 50
FORM_WINDOW = 10
ELO_K = 32.0
GLICKO_TAU = 0.5


def _logit(p: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-min(max(z, -35.0), 35.0)))


class _FeatureState:
    """Everything the model is allowed to know before a series starts."""

    def __init__(self) -> None:
        self.elo = Elo(k=ELO_K)
        self.glicko = Glicko2(tau=GLICKO_TAU)
        self.form: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=FORM_WINDOW))
        self.h2h: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])  # [t1 wins, n]

    def features(self, s: SeriesRow) -> list[float]:
        a, b = self.glicko.state(s.team1), self.glicko.state(s.team2)
        form1 = sum(self.form[s.team1]) + 0.5 * (FORM_WINDOW - len(self.form[s.team1]))
        form2 = sum(self.form[s.team2]) + 0.5 * (FORM_WINDOW - len(self.form[s.team2]))
        key = (min(s.team1, s.team2), max(s.team1, s.team2))
        wins_low, n = self.h2h[key]
        wins1 = wins_low if s.team1 == key[0] else n - wins_low
        return [
            _logit(self.glicko.predict(s.team1, s.team2)),
            _logit(expected(self.elo.rating(s.team1), self.elo.rating(s.team2))),
            (a.rd + b.rd) / 350.0,
            (form1 - form2) / FORM_WINDOW,
            (wins1 + 2.0) / (n + 4.0) - 0.5,
        ]

    def record(self, s: SeriesRow) -> None:
        self.elo.update(s.team1, s.team2, s.team1_won)
        self.glicko.update(s.team1, s.team2, s.team1_won)
        self.form[s.team1].append(1.0 if s.team1_won else 0.0)
        self.form[s.team2].append(0.0 if s.team1_won else 1.0)
        key = (min(s.team1, s.team2), max(s.team1, s.team2))
        rec = self.h2h[key]
        rec[1] += 1
        if s.team1_won == (s.team1 == key[0]):
            rec[0] += 1


_IDENTITY = np.array([1.0, 0.0, 0.0, 0.0, 0.0])  # pure Glicko-2 pass-through


def fit_walk_forward(series: list[SeriesRow]) -> tuple[list[Prediction], dict[str, Any]]:
    """Predict every decided series in order, refitting on an expanding window.

    Returns the walk-forward predictions and the coefficient artifact from
    the final refit (trained on all but the last partial block).
    """
    state = _FeatureState()
    xs: list[list[float]] = []
    ys: list[float] = []
    preds: list[Prediction] = []
    intercept, weights = 0.0, _IDENTITY
    last_refit = {"n_train": 0}

    for i, s in enumerate(series):
        if i >= MIN_TRAIN and i % REFIT_EVERY == 0:
            fit = fit_logistic_l2(np.array(xs), np.array(ys), l2=L2)
            intercept, weights = fit.intercept, fit.weights
            last_refit = {"n_train": len(xs)}
        x = state.features(s)
        p = _sigmoid(intercept + float(np.dot(weights, x)))
        preds.append(Prediction(p=p, won=s.team1_won, when=s.played_at.date()))
        xs.append(x)
        ys.append(1.0 if s.team1_won else 0.0)
        state.record(s)

    artifact = {
        "features": list(FEATURES),
        "l2": L2,
        "min_train": MIN_TRAIN,
        "refit_every": REFIT_EVERY,
        "form_window": FORM_WINDOW,
        "final_intercept": round(float(intercept), 4),
        "final_weights": {f: round(float(w), 4) for f, w in zip(FEATURES, weights, strict=True)},
        "n_train_at_final_refit": last_refit["n_train"],
    }
    return preds, artifact


def write_artifact(
    conn: psycopg.Connection[tuple[object, ...]], run_id: int, artifact: dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, "coefficients", json.dumps(artifact)),
    )
