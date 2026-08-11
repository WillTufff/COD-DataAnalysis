"""Is the gap between two backtest rows a result? Spec: /methodology#validation.

The series-level backtest table has published three models a few thousandths of
Brier apart, and judged those gaps by eye. That is almost certainly the right
call — the momentum null this project leads with is that recent form adds nothing
— but "published nulls are results" cannot rest on visual inspection. The first
objection to any null is that the test was underpowered, and until now there was
no answer to it.

Two things live here:

**Paired intervals.** Every model predicts the same 1,310 series, so the gaps are
paired data, not two independent samples. Per series, the difference in squared
error is one observation; its mean is the Brier gap. A percentile bootstrap over
series gives the interval, and the Diebold-Mariano statistic — the same mean over
its standard error — gives the test. Resampling once per draw and reading every
pair off that draw keeps the pairing intact.

**Power.** The interval says what this archive could not rule out; power says what
it could have found. For the momentum question that is a concrete quantity: how
large would a form effect have to be for 1,310 series to detect it? Adding
beta x form_diff to the Glicko-2 logit gives a one-parameter family of models
that all reduce to Glicko-2 at beta = 0, so the smallest detectable beta can be
found by scanning it — and then restated as the win-probability swing it implies
for a team on a 10-0 run against one on 0-10.

Nothing here selects a model. It reports what the archive can and cannot resolve.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

import numpy as np

from ..backtest import Prediction
from ..regress import FloatArray
from .winprob import SeriesFeatures

BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20190818  # CWL Champs 2019 finals; any fixed seed works

# Two-sided 5% test at 80% power: z(0.975) + z(0.80). The convention, stated
# rather than assumed, because every "detectable" number below is scaled by it.
Z_ALPHA = 1.959964
Z_POWER = 0.841621
POWER_FACTOR = Z_ALPHA + Z_POWER

# The form swing the power statement is quoted at: form_diff spans -1 to +1, so
# 1.0 is a team on a 10-0 run meeting one on 0-10 — the largest momentum gap the
# feature can express, and the most favourable case for detecting it.
FORM_SWING = 1.0


def _sq_err(p: Prediction) -> float:
    return (p.p - (1.0 if p.won else 0.0)) ** 2


def _hit(p: Prediction) -> float:
    return 1.0 if (p.p >= 0.5) == p.won else 0.0


def _stable_order(named: dict[str, dict[Any, Prediction]], keys: set[Any]) -> list[Any]:
    """The observations, ordered by what they contain rather than by their key.

    The bootstrap below draws row indices, so an observation's identity is its
    position in this list. Ordering by the caller's key — a game id or a series
    id — makes those positions depend on surrogate keys, and a reload that
    deletes and recreates a handful of rows renumbers the rest. The published
    interval then moves while every point estimate stays put, which is what the
    metric-diff harness caught: a number that changed with no data behind it.

    Sorting on the predictions themselves fixes the positions to the evidence.
    Ties order arbitrarily and harmlessly — two observations carrying identical
    errors are interchangeable, so no resample mean can tell them apart — and
    the key breaks them only to keep the sort itself reproducible.
    """
    models = sorted(named)

    def content(key: Any) -> tuple[Any, ...]:
        return (
            tuple(_sq_err(named[name][key]) for name in models),
            tuple(_hit(named[name][key]) for name in models),
            str(key),
        )

    return sorted(keys, key=content)


def _percentile_ci(draws: Sequence[float]) -> tuple[float, float]:
    lo, hi = np.percentile(np.asarray(draws, dtype=float), [2.5, 97.5])
    return (float(lo), float(hi))


def _paired_stats(d: FloatArray, idx: np.ndarray[Any, Any]) -> dict[str, Any]:
    """One paired difference vector, as an interval and as a test.

    `mde80` is the smallest true difference this many observations of this
    variance would detect at 80% power — the number that answers "underpowered".
    It is a property of the sample size and the spread, not of the observed gap,
    so it is reported whether or not the gap clears it.
    """
    n = len(d)
    mean = float(d.mean())
    se = float(d.std(ddof=1) / math.sqrt(n))
    lo, hi = _percentile_ci([float(d[row].mean()) for row in idx])
    return {
        "delta": round(mean, 6),
        "lo": round(lo, 6),
        "hi": round(hi, 6),
        "excludes_zero": bool(lo > 0.0 or hi < 0.0),
        # Diebold-Mariano on the loss differential, normal reference.
        "dm_t": round(mean / se, 3) if se > 0 else None,
        "dm_p": round(2.0 * (1.0 - _phi(abs(mean / se))), 4) if se > 0 else None,
        "se": round(se, 6),
        "mde80": round(POWER_FACTOR * se, 6),
    }


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def paired_gaps(named: dict[str, dict[Any, Prediction]], unit: str = "series") -> dict[str, Any]:
    """Paired Brier and accuracy comparisons across models keyed by anything.

    Every pair is scored on the units both members predicted; a model that
    skipped some would otherwise be compared against a different sample than the
    row above it in the table. The key can be a series id or a map id — the
    arithmetic is the same, and keeping one implementation means the map-level
    table cannot quietly diverge from the series-level one it is read beside.
    """
    named = {k: v for k, v in named.items() if v}
    if len(named) < 2:
        return {"available": False, "reason": f"need two models with {unit} ids"}
    shared = set.intersection(*(set(v) for v in named.values()))
    if len(shared) < 50:
        return {"available": False, "reason": f"too few commonly predicted {unit}s"}
    common = _stable_order(named, shared)

    err = {
        name: np.array([_sq_err(preds[s]) for s in common], dtype=float)
        for name, preds in named.items()
    }
    hit = {
        name: np.array([_hit(preds[s]) for s in common], dtype=float)
        for name, preds in named.items()
    }

    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, len(common), size=(BOOTSTRAP_B, len(common)))

    order = list(named)
    pairs = []
    for i, a in enumerate(order):
        for b in order[i + 1 :]:
            entry = {"a": a, "b": b, "what": f"Brier({a}) − Brier({b})"}
            entry.update(_paired_stats(np.asarray(err[a] - err[b]), idx))
            acc = _paired_stats(np.asarray(hit[a] - hit[b]), idx)
            entry["accuracy_delta"] = acc["delta"]
            entry["accuracy_lo"] = acc["lo"]
            entry["accuracy_hi"] = acc["hi"]
            entry["accuracy_excludes_zero"] = acc["excludes_zero"]
            pairs.append(entry)

    per_model = {}
    for name in order:
        brier_draws = [float(err[name][row].mean()) for row in idx]
        acc_draws = [float(hit[name][row].mean()) for row in idx]
        blo, bhi = _percentile_ci(brier_draws)
        alo, ahi = _percentile_ci(acc_draws)
        per_model[name] = {
            "brier": round(float(err[name].mean()), 6),
            "brier_lo": round(blo, 6),
            "brier_hi": round(bhi, 6),
            "accuracy": round(float(hit[name].mean()), 4),
            "accuracy_lo": round(alo, 4),
            "accuracy_hi": round(ahi, 4),
        }

    return {
        "available": True,
        "n": len(common),
        "unit": unit,
        "bootstrap_b": BOOTSTRAP_B,
        "method": f"paired percentile bootstrap over per-{unit} squared-error differences,"
        " with Diebold-Mariano on the same differences",
        "power_alpha": 0.05,
        "power_target": 0.8,
        "models": per_model,
        "pairs": pairs,
    }


def model_gaps(models: dict[str, list[Prediction]]) -> dict[str, Any]:
    """`paired_gaps` over the series-level table, keyed by series id.

    Keeps `n_series` in the payload: the published /methodology page reads that
    name, and renaming a shipped artifact field to match an internal refactor
    would break the page for no gain.
    """
    out = paired_gaps(
        {k: {p.series_id: p for p in v if p.series_id is not None} for k, v in models.items()},
        unit="series",
    )
    if out.get("available"):
        out["n_series"] = out["n"]
    return out


def form_power(trace: Sequence[SeriesFeatures]) -> dict[str, Any]:
    """How large a form effect this archive could have detected.

    Suppose momentum is real and the true series probability is Glicko-2's logit
    plus beta x form_diff. Then the expected Brier gain from knowing beta, and the
    variance of that gain, are both available in closed form — no simulation.

    Writing d for the per-series loss differential between the baseline p0 and the
    truth pb, d = (p0 − y)² − (pb − y)² = (p0 − pb)(p0 + pb − 2y). With y drawn
    from the truth, E[d] = (p0 − pb)² and Var[d] = 4 (p0 − pb)² pb (1 − pb). So a
    beta is detectable when the mean gain over the archive's own matchups and form
    states clears 80% power at a two-sided 5% level:

        mean_i (p0_i − pb_i)²  ≥  (z_.975 + z_.80) · sqrt(Σ_i Var[d_i]) / n

    Sweeping beta and reporting the first value that clears it turns "the null
    might be underpowered" into a number. It is a statement about *this* archive:
    the same matchups, the same distribution of form gaps, 1,310 series.
    """
    if not trace:
        return {"available": False, "reason": "no feature trace"}
    base_logit = np.array([f.get("glicko_logit") for f in trace], dtype=float)
    form = np.array([f.get("form_diff") for f in trace], dtype=float)
    n = len(trace)
    p0 = _expit(base_logit)

    curve: list[dict[str, Any]] = []
    detectable: dict[str, Any] | None = None
    # Up to 3 logits. A coefficient past that would make a 10-0 run worth more
    # than the gap between a 50/50 and a 95% favourite, which nobody claims.
    for beta in [round(0.05 * i, 2) for i in range(1, 61)]:
        pb = _expit(base_logit + beta * form)
        gap = p0 - pb
        gain = float((gap**2).mean())
        se = float(np.sqrt((4.0 * gap**2 * pb * (1.0 - pb)).sum()) / n)
        point: dict[str, Any] = {
            "beta": beta,
            "brier_gain": round(gain, 6),
            "mde80": round(POWER_FACTOR * se, 6),
            "detectable": bool(se > 0.0 and gain >= POWER_FACTOR * se),
        }
        curve.append(point)
        if detectable is None and point["detectable"]:
            detectable = point

    beta_star = detectable["beta"] if detectable else None
    return {
        "available": True,
        "n_series": n,
        "swing": FORM_SWING,
        "criterion": "smallest beta on form_diff whose expected paired Brier gain reaches"
        " 80% power at a two-sided 5% level",
        "beta_detectable": beta_star,
        "brier_gain_at_detectable": detectable["brier_gain"] if detectable else None,
        # What that coefficient would mean at the table: an even matchup where one
        # team arrives 10-0 over its last ten and the other 0-10.
        "swing_pp": (
            round(100.0 * (_sigmoid(beta_star * FORM_SWING) - 0.5), 2)
            if beta_star is not None
            else None
        ),
        "curve": curve,
    }


def _expit(z: FloatArray) -> FloatArray:
    return np.asarray(1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0))))


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))
