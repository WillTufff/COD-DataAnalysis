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
evaluation window.

That last sentence is only true if the Glicko-2 inside this module is the same
one the baseline row publishes, and for a while it was not: this module advanced
Glicko-2 per series while the published run had moved to rating periods over the
whole roster. The identity phase was then exactly a Glicko-2 that appeared
nowhere on the site, and the backtest table compared two different fits while
crediting the difference to these features. The settings that decide a fit —
rating period, lineage map, K, tau — are now all arguments, passed by the caller
from the same values it gives the published runs. `test_winprob` pins the
identity phase against `fit.glicko2_walk_forward` prediction by prediction, so
the two cannot drift apart again without a test failing.

The final refit's coefficients are stored as an artifact. Their interpretation
is a question for the backtest, not for this docstring.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import psycopg

from ..backtest import Prediction
from ..regress import fit_logistic_l2
from .elo import Elo, expected
from .fit import DEFAULT_PERIOD, SeriesRow, group_periods
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
    """Everything the model is allowed to know before a series starts.

    Each rating here is advanced on the same clock as the published run of that
    same model, because a feature fitted differently from the model it is being
    compared against makes the comparison meaningless:

    * **Glicko-2** moves once per rating period, over every rated lineage, via
      `advance` — the paper's semantics and what `fit.glicko2_walk_forward`
      does. Results accumulate in `_pending` during a period and land at
      `close_period`.
    * **Elo** moves per series, because that is what Elo is and what
      `fit.elo_walk_forward` does.

    Form and head-to-head are plain counters over completed series with no
    period semantics of their own, so they advance per series too. Freezing them
    for the length of an event would be stricter than necessary — they read only
    finished results either way — and it would blind the model to exactly the
    within-event streak the momentum question is about.

    All state is keyed on the lineage, matching the published runs, so a rebrand
    carries its rating, form and head-to-head across instead of starting over.
    """

    def __init__(
        self,
        lineage: dict[int, int] | None = None,
        elo_k: float = ELO_K,
        glicko_tau: float = GLICKO_TAU,
    ) -> None:
        self.lin = lineage or {}
        self.elo = Elo(k=elo_k)
        self.glicko = Glicko2(tau=glicko_tau)
        self.form: dict[int, deque[float]] = defaultdict(lambda: deque(maxlen=FORM_WINDOW))
        self.h2h: dict[tuple[int, int], list[int]] = defaultdict(lambda: [0, 0])  # [low wins, n]
        self._pending: dict[int, list[tuple[int, float]]] = {}

    def lineage_of(self, team: int) -> int:
        return self.lin.get(team, team)

    def features(self, s: SeriesRow) -> list[float]:
        l1, l2 = self.lineage_of(s.team1), self.lineage_of(s.team2)
        a, b = self.glicko.state(l1), self.glicko.state(l2)
        form1 = sum(self.form[l1]) + 0.5 * (FORM_WINDOW - len(self.form[l1]))
        form2 = sum(self.form[l2]) + 0.5 * (FORM_WINDOW - len(self.form[l2]))
        key = (min(l1, l2), max(l1, l2))
        wins_low, n = self.h2h[key]
        wins1 = wins_low if l1 == key[0] else n - wins_low
        return [
            _logit(self.glicko.predict(l1, l2)),
            _logit(expected(self.elo.rating(l1), self.elo.rating(l2))),
            (a.rd + b.rd) / 350.0,
            (form1 - form2) / FORM_WINDOW,
            (wins1 + 2.0) / (n + 4.0) - 0.5,
        ]

    def record(self, s: SeriesRow) -> None:
        """Everything that advances per series. Glicko-2 results are only banked
        here; they take effect when the period closes."""
        l1, l2 = self.lineage_of(s.team1), self.lineage_of(s.team2)
        score = 1.0 if s.team1_won else 0.0
        self.elo.update(l1, l2, s.team1_won)
        self._pending.setdefault(l1, []).append((l2, score))
        self._pending.setdefault(l2, []).append((l1, 1.0 - score))
        self.form[l1].append(score)
        self.form[l2].append(1.0 - score)
        key = (min(l1, l2), max(l1, l2))
        rec = self.h2h[key]
        rec[1] += 1
        if s.team1_won == (l1 == key[0]):
            rec[0] += 1

    def close_period(self) -> None:
        """Close the Glicko-2 rating period over every lineage rated so far.

        The roster is `self.glicko.teams`, which `features` has already populated
        for anyone appearing in this period — the same roster, in the same order
        of operations, as `fit.glicko2_walk_forward`. Teams yet to debut are left
        at 1500 ± 350 rather than inflated past "nothing is known".
        """
        self.glicko.advance(self._pending, list(self.glicko.teams))
        self._pending = {}


@dataclass
class SeriesFeatures:
    """One series as the model saw it, kept so a later analysis can ask what the
    features could have shown without re-deriving the walk.

    The power statement on the momentum null needs exactly this: `glicko_logit`
    is the baseline the model corrects, and `form_diff` is the effect whose
    detectable size is in question. Re-walking the state in a second module to
    recover them would be a second implementation of the walk, free to drift.
    """

    series_id: int
    when: date
    won: bool
    x: tuple[float, ...]  # in FEATURES order

    def get(self, feature: str) -> float:
        return self.x[FEATURES.index(feature)]


_IDENTITY = np.array([1.0, 0.0, 0.0, 0.0, 0.0])  # pure Glicko-2 pass-through


def fit_walk_forward(
    series: list[SeriesRow],
    lineage: dict[int, int] | None = None,
    period: str = DEFAULT_PERIOD,
    elo_k: float = ELO_K,
    glicko_tau: float = GLICKO_TAU,
) -> tuple[list[Prediction], dict[str, Any], list[SeriesFeatures]]:
    """Predict every decided series in order, refitting on an expanding window.

    Two clocks run here and they are independent. The **rating period** decides
    when Glicko-2 state moves, and is the caller's choice so that it matches the
    published Glicko-2 run. The **refit cadence** counts series, not periods: the
    model retrains every REFIT_EVERY series once MIN_TRAIN exist, because that
    cadence is about how much training data has accumulated and has nothing to do
    with how time is sliced.

    Every series is still predicted before it is recorded, and every feature is
    read before the series it describes, so a longer period can only make the
    walk-forward stricter — never looser.

    Returns the walk-forward predictions, the coefficient artifact from the final
    refit (trained on all but the last partial block), and the per-series feature
    trace the significance layer reads.
    """
    state = _FeatureState(lineage, elo_k=elo_k, glicko_tau=glicko_tau)
    xs: list[list[float]] = []
    ys: list[float] = []
    preds: list[Prediction] = []
    trace: list[SeriesFeatures] = []
    intercept, weights = 0.0, _IDENTITY
    last_refit = {"n_train": 0}
    seen = 0

    for block in group_periods(series, period):
        for s in block:
            if seen >= MIN_TRAIN and seen % REFIT_EVERY == 0:
                fit = fit_logistic_l2(np.array(xs), np.array(ys), l2=L2)
                intercept, weights = fit.intercept, fit.weights
                last_refit = {"n_train": len(xs)}
            x = state.features(s)
            p = _sigmoid(intercept + float(np.dot(weights, x)))
            preds.append(Prediction(p=p, won=s.team1_won, when=s.played_at.date(), series_id=s.id))
            trace.append(
                SeriesFeatures(
                    series_id=s.id,
                    when=s.played_at.date(),
                    won=s.team1_won,
                    x=tuple(x),
                )
            )
            xs.append(x)
            ys.append(1.0 if s.team1_won else 0.0)
            state.record(s)
            seen += 1
        state.close_period()

    artifact = {
        "features": list(FEATURES),
        "l2": L2,
        "min_train": MIN_TRAIN,
        "refit_every": REFIT_EVERY,
        "form_window": FORM_WINDOW,
        "period": period,
        "elo_k": elo_k,
        "glicko_tau": glicko_tau,
        "final_intercept": round(float(intercept), 4),
        "final_weights": {f: round(float(w), 4) for f, w in zip(FEATURES, weights, strict=True)},
        "n_train_at_final_refit": last_refit["n_train"],
    }
    return preds, artifact, trace


def write_artifact(
    conn: psycopg.Connection[tuple[object, ...]], run_id: int, artifact: dict[str, Any]
) -> None:
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, "coefficients", json.dumps(artifact)),
    )
