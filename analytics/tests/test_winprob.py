"""winprob_v1 hygiene: the identity phase reproduces Glicko-2 exactly, the
walk-forward never sees the future, and learned predictions stay calibrated
on synthetic data with known team strengths."""

from datetime import datetime, timedelta

import numpy as np

from cdlhub_analytics.ratings.fit import SeriesRow
from cdlhub_analytics.ratings.glicko2 import Glicko2
from cdlhub_analytics.ratings.winprob import GLICKO_TAU, MIN_TRAIN, _FeatureState, fit_walk_forward


def synthetic_series(n: int = 400, seed: int = 9) -> list[SeriesRow]:
    """Six teams with fixed latent strength; higher id is stronger."""
    rng = np.random.default_rng(seed)
    t0 = datetime(2018, 1, 1)
    out: list[SeriesRow] = []
    for i in range(n):
        a, b = map(int, rng.choice(6, size=2, replace=False) + 1)
        p_a = 1.0 / (1.0 + np.exp(-(a - b) * 0.5))
        out.append(
            SeriesRow(
                id=i,
                team1=a,
                team2=b,
                team1_won=bool(rng.random() < p_a),
                played_at=t0 + timedelta(hours=6 * i),
            )
        )
    return out


def test_identity_phase_is_exactly_glicko() -> None:
    series = synthetic_series()
    preds, _ = fit_walk_forward(series)
    glicko = Glicko2(tau=GLICKO_TAU)
    for s, pred in zip(series[:MIN_TRAIN], preds[:MIN_TRAIN], strict=False):
        expected = glicko.predict(s.team1, s.team2)
        assert abs(pred.p - expected) < 1e-9
        glicko.update(s.team1, s.team2, s.team1_won)


def test_first_series_features_are_neutral() -> None:
    state = _FeatureState()
    s = synthetic_series(n=1)[0]
    feats = state.features(s)
    assert abs(feats[0]) < 1e-9  # glicko logit: both unrated
    assert abs(feats[1]) < 1e-9  # elo logit
    assert feats[3] == 0.0  # form diff: empty windows sit at 0.5 each
    assert feats[4] == 0.0  # h2h edge: (0+2)/(0+4) - 0.5


def test_learned_phase_stays_calibrated() -> None:
    series = synthetic_series()
    preds, artifact = fit_walk_forward(series)
    assert len(preds) == len(series)
    late = preds[MIN_TRAIN:]
    assert all(0.0 < p.p < 1.0 for p in late)
    brier = float(np.mean([(p.p - (1.0 if p.won else 0.0)) ** 2 for p in late]))
    assert brier < 0.25, "must beat an uninformed 0.5-forever predictor"
    assert set(artifact["final_weights"]) == set(artifact["features"])
    assert artifact["n_train_at_final_refit"] >= MIN_TRAIN


def test_record_updates_all_feature_state() -> None:
    state = _FeatureState()
    s = synthetic_series(n=1)[0]
    state.record(s)
    feats2 = state.features(s)
    assert feats2[0] != 0.0  # ratings moved
    assert feats2[3] != 0.0  # form windows now differ
    assert feats2[4] != 0.0  # h2h has a record
