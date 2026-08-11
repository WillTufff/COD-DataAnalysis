"""Fixture tests for the paired-gap and power layer. No database required."""

from datetime import date, timedelta

import numpy as np

from cdlhub_analytics.backtest import Prediction
from cdlhub_analytics.ratings.significance import (
    POWER_FACTOR,
    form_power,
    model_gaps,
    paired_gaps,
)
from cdlhub_analytics.ratings.winprob import FEATURES, SeriesFeatures

DAY0 = date(2018, 1, 1)


def preds(ps: list[float], wins: list[bool], offset: int = 0) -> list[Prediction]:
    return [
        Prediction(p=p, won=w, when=DAY0 + timedelta(days=i), series_id=i + offset)
        for i, (p, w) in enumerate(zip(ps, wins, strict=True))
    ]


def coin_flip_outcomes(n: int, seed: int = 3) -> list[bool]:
    rng = np.random.default_rng(seed)
    return [bool(b) for b in rng.integers(0, 2, size=n)]


def test_a_real_gap_excludes_zero() -> None:
    """One model calls every result exactly right, the other guesses 0.5 forever.
    An interval that cannot separate those is not measuring anything."""
    wins = coin_flip_outcomes(400)
    sharp = preds([0.99 if w else 0.01 for w in wins], wins)
    flat = preds([0.5] * 400, wins)

    out = model_gaps({"sharp": sharp, "flat": flat})
    (pair,) = out["pairs"]
    assert out["n_series"] == 400
    assert pair["delta"] < 0  # sharp has the lower Brier
    assert pair["hi"] < 0 and pair["excludes_zero"]
    assert pair["dm_p"] is not None and pair["dm_p"] < 0.001


def test_two_copies_of_one_model_have_a_zero_gap() -> None:
    """The paired interval has to collapse when the differences are all zero —
    an unpaired test on the same data would report a wide one."""
    wins = coin_flip_outcomes(300)
    ps = [0.7 if w else 0.4 for w in wins]
    out = model_gaps({"a": preds(ps, wins), "b": preds(list(ps), wins)})
    (pair,) = out["pairs"]
    assert pair["delta"] == 0.0
    assert (pair["lo"], pair["hi"]) == (0.0, 0.0)
    assert not pair["excludes_zero"]


def test_a_tiny_gap_does_not_exclude_zero_and_reports_what_would() -> None:
    """The case the backtest table is actually in: a gap far below what this
    many series can resolve. The finding is the mde, not the gap."""
    wins = coin_flip_outcomes(500)
    a = preds([0.6 if w else 0.4 for w in wins], wins)
    b = preds([0.601 if w else 0.401 for w in wins], wins)
    (pair,) = model_gaps({"a": a, "b": b})["pairs"]
    assert not pair["excludes_zero"]
    assert pair["mde80"] > abs(pair["delta"])


def test_gaps_pair_on_series_id_not_position() -> None:
    """Glicko-2 iterates rating periods, so its predictions do not arrive in
    series order. Pairing by position would silently compare different games."""
    wins = coin_flip_outcomes(200)
    ordered = preds([0.9 if w else 0.1 for w in wins], wins)
    shuffled = list(reversed(preds([0.9 if w else 0.1 for w in wins], wins)))
    (pair,) = model_gaps({"a": ordered, "b": shuffled})["pairs"]
    assert pair["delta"] == 0.0, "same predictions, differently ordered"


def test_gaps_score_every_pair_on_the_series_all_models_reached() -> None:
    wins = coin_flip_outcomes(300)
    full = preds([0.6 if w else 0.4 for w in wins], wins)
    partial = full[:120]
    out = model_gaps({"a": full, "b": partial, "c": list(full)})
    assert out["n_series"] == 120
    assert len(out["pairs"]) == 3
    assert set(out["models"]) == {"a", "b", "c"}


def test_gaps_need_series_ids() -> None:
    wins = coin_flip_outcomes(100)
    anon = [Prediction(p=0.5, won=w, when=DAY0) for w in wins]
    assert model_gaps({"a": anon, "b": anon})["available"] is False


def trace(n: int, seed: int = 7) -> list[SeriesFeatures]:
    """Series with a spread of Glicko-2 logits and form gaps, outcomes drawn
    from the ratings alone — the null the power statement is quoted against."""
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        logit = float(rng.normal(0.0, 0.8))
        form = float(rng.uniform(-1.0, 1.0))
        x = [0.0] * len(FEATURES)
        x[FEATURES.index("glicko_logit")] = logit
        x[FEATURES.index("form_diff")] = form
        p = 1.0 / (1.0 + np.exp(-logit))
        out.append(
            SeriesFeatures(
                series_id=i,
                when=DAY0 + timedelta(days=i),
                won=bool(rng.random() < p),
                x=tuple(x),
            )
        )
    return out


def test_form_power_reports_a_detectable_effect_size() -> None:
    out = form_power(trace(1310))
    beta = out["beta_detectable"]
    assert beta is not None and 0.0 < beta <= 3.0
    # The quoted swing is that coefficient at a 10-0 versus 0-10 gap.
    assert out["swing_pp"] > 0.0
    assert out["brier_gain_at_detectable"] >= out["curve"][0]["brier_gain"]


def test_more_series_detect_smaller_effects() -> None:
    """The whole point of a power statement: the threshold is a property of the
    sample size, and has to move with it."""
    small = form_power(trace(300))["beta_detectable"]
    large = form_power(trace(4000))["beta_detectable"]
    assert small is not None and large is not None
    assert large < small


def test_form_power_curve_matches_its_own_criterion() -> None:
    out = form_power(trace(800))
    for point in out["curve"]:
        expected = point["brier_gain"] >= point["mde80"]
        assert point["detectable"] is expected, point
    first = next(p for p in out["curve"] if p["detectable"])
    assert first["beta"] == out["beta_detectable"]
    assert POWER_FACTOR > 2.8  # z(0.975) + z(0.80), stated not assumed


def test_form_power_needs_a_trace() -> None:
    assert form_power([])["available"] is False


# ===== the interval must not move when only the keys move =====


def _two_models(keys: list[int]) -> dict[str, dict[int, Prediction]]:
    """Two models over the same 200 observations, addressed by `keys`.

    The predictions are a fixed list, so relabelling them is exactly the change
    a reload makes when it renumbers game ids: same evidence, different keys.
    """
    rng = np.random.default_rng(11)
    outcomes = [bool(b) for b in rng.integers(0, 2, size=len(keys))]
    sharp = [0.75 if w else 0.25 for w in outcomes]
    blunt = [float(p) for p in rng.uniform(0.35, 0.65, size=len(keys))]
    return {
        name: {
            key: Prediction(p=p, won=w, when=DAY0 + timedelta(days=i), series_id=key)
            for i, (key, p, w) in enumerate(zip(keys, ps, outcomes, strict=True))
        }
        for name, ps in (("sharp", sharp), ("blunt", blunt))
    }


def test_renumbering_the_keys_does_not_move_the_interval() -> None:
    """The bootstrap draws positions, so its ordering has to come from the
    evidence rather than from surrogate ids that a reload renumbers."""
    n = 200
    original = paired_gaps(_two_models(list(range(n))), unit="map")
    # Same observations, keys shuffled into a different sort order.
    shuffled_keys = [(key * 7919) % 100_003 for key in range(n)]
    assert sorted(shuffled_keys) != list(range(n))
    renumbered = paired_gaps(_two_models(shuffled_keys), unit="map")

    assert original["pairs"] and renumbered["pairs"]
    for before, after in zip(original["pairs"], renumbered["pairs"], strict=True):
        for field in ("delta", "lo", "hi", "se", "mde80", "accuracy_lo", "accuracy_hi"):
            assert before[field] == after[field], field
