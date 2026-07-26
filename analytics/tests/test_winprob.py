"""winprob_v1 hygiene: the identity phase reproduces the *published* Glicko-2
exactly, the walk-forward never sees the future, and learned predictions stay
calibrated on synthetic data with known team strengths."""

from datetime import datetime, timedelta

import numpy as np

from cdlhub_analytics.ratings.fit import SeriesRow, glicko2_walk_forward
from cdlhub_analytics.ratings.winprob import (
    GLICKO_TAU,
    MIN_TRAIN,
    _FeatureState,
    fit_walk_forward,
)


def synthetic_series(n: int = 400, seed: int = 9, series_per_event: int = 1) -> list[SeriesRow]:
    """Six teams with fixed latent strength; higher id is stronger.

    `series_per_event` groups consecutive series into shared events, which is
    what makes an event-length rating period differ from a per-series one — at
    the default of 1 the two are the same thing and prove nothing.
    """
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
                event_id=i // series_per_event,
            )
        )
    return out


def test_identity_phase_matches_the_published_glicko_prediction_for_prediction() -> None:
    """The claim the backtest table rests on: before the model has learned
    anything, winprob *is* the published Glicko-2 — not a Glicko-2 fitted some
    other way. Run at an event-length period, where a per-series fit would give
    visibly different numbers."""
    series = synthetic_series(series_per_event=12)
    preds, _, _ = fit_walk_forward(series, period="event")
    baseline, _rows = glicko2_walk_forward(series, tau=GLICKO_TAU, period="event")
    for pred, base in zip(preds[:MIN_TRAIN], baseline[:MIN_TRAIN], strict=False):
        assert abs(pred.p - base.p) < 1e-9


def test_identity_phase_tracks_whatever_period_it_is_given() -> None:
    """Same equality at every period length, so the pairing is structural rather
    than a coincidence of the default."""
    series = synthetic_series(series_per_event=12)
    for period in ("series", "event", "week", "month"):
        preds, _, _ = fit_walk_forward(series, period=period)
        baseline, _rows = glicko2_walk_forward(series, tau=GLICKO_TAU, period=period)
        gaps = [
            abs(p.p - b.p) for p, b in zip(preds[:MIN_TRAIN], baseline[:MIN_TRAIN], strict=False)
        ]
        assert max(gaps) < 1e-9, period


def test_the_period_actually_changes_the_predictions() -> None:
    """Guards the two tests above from passing vacuously: if period grouping were
    ignored, every period would give the same predictions and the equalities
    would hold for the wrong reason."""
    series = synthetic_series(series_per_event=12)
    per_series, _, _ = fit_walk_forward(series, period="series")
    per_event, _, _ = fit_walk_forward(series, period="event")
    gaps = [abs(a.p - b.p) for a, b in zip(per_series, per_event, strict=True)]
    assert max(gaps) > 1e-3


def test_lineage_merges_feature_state() -> None:
    """A rebrand carries its rating, form and head-to-head across, as it does in
    the published runs — the rated entity is the org, not the brand."""
    series = synthetic_series(n=40, series_per_event=8)
    merged, _, _ = fit_walk_forward(series, lineage={6: 5})
    plain, _, _ = fit_walk_forward(series)
    assert any(abs(a.p - b.p) > 1e-6 for a, b in zip(merged, plain, strict=True))
    state = _FeatureState(lineage={6: 5})
    assert state.lineage_of(6) == 5
    assert state.lineage_of(1) == 1


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
    preds, artifact, _ = fit_walk_forward(series)
    assert len(preds) == len(series)
    late = preds[MIN_TRAIN:]
    assert all(0.0 < p.p < 1.0 for p in late)
    brier = float(np.mean([(p.p - (1.0 if p.won else 0.0)) ** 2 for p in late]))
    assert brier < 0.25, "must beat an uninformed 0.5-forever predictor"
    assert set(artifact["final_weights"]) == set(artifact["features"])
    assert artifact["n_train_at_final_refit"] >= MIN_TRAIN


def test_record_updates_the_per_series_state_and_banks_glicko() -> None:
    state = _FeatureState()
    s = synthetic_series(n=1)[0]
    state.record(s)
    feats = state.features(s)
    assert feats[1] != 0.0  # elo logit: Elo moves per series
    assert feats[3] != 0.0  # form windows now differ
    assert feats[4] != 0.0  # h2h has a record
    # Glicko-2 has not moved: the period is still open and the result is banked.
    assert feats[0] == 0.0
    assert state._pending
    state.close_period()
    assert state.features(s)[0] != 0.0
    assert not state._pending
