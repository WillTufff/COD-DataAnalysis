"""What a published finding's number is worth once the search behind it is counted.

The findings feed publishes a few hundred claims across sixteen kinds, most of
them the extreme of some scan. Scanning a league and reporting the maxima
produces confident-looking claims from noise at a rate nobody had quantified,
and nothing in this project quantified it until this module.

**A finding is only a test when its sentence claims something the record
estimates.** That line decides everything else here. A player's season K/D is a
noisy read on how good they were, and the era model already ships an error bar
for it, so the sentence generalises, so it can be wrong, so it can be tested.
League-wide engagement pace across a season is computed over every map that
season contains: it estimates nothing, and a null for it would have to be
invented. Four classes fall out, and `CLASS_OF` is the whole partition:

  testable      a latent quantity, and an error for it in the database.
  uncorrected   a latent quantity, and no error anywhere to test it with.
  descriptive   a statement about the record. No latent quantity, no null.
  self_tested   a declared test that already publishes its own interval.

**The null is the claim's own boundary, not zero.** A finding that says "at
least two standard deviations from the cohort" is tested against a true z of 2.
Testing it against a true z of 0 would ask whether the player differs from the
league average at all, which is known false before the data is seen — players
differ — so every such finding survives any correction and the exercise
controls nothing.

**The p-value is conditional on the screen that selected the claim.** The set of
claims was chosen by looking, so a selected subject's statistic is biased upward
against its own true value. Conditioning on the selection removes exactly that
bias:

    p = P(statistic >= observed | statistic >= screen, true value = boundary)

For a continuous statistic whose screen sits at the null value this is twice the
plain tail. For a binomial it is a ratio of two survival functions and is
computed as one. `conditional` is the only place either form is applied.

**Both step-up procedures ship.** Benjamini-Hochberg controls FDR under
independence or positive dependence; these families overlap, since one
player-season can reach several of them, so BH is the optimistic bound.
Benjamini-Yekutieli is valid under arbitrary dependence and costs power. A
reader gets both and can see where they disagree.

Neither procedure is imported. Both are a dozen lines of sorting and a running
minimum, they are exactly reproducible in place, and a step-up procedure hidden
behind a dependency is harder to audit than the four lines it replaces.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import psycopg
from numpy.typing import NDArray
from scipy.stats import binom, norm

FloatArray = NDArray[np.float64]

MODEL = "error_control"
VERSION = "1.0.0"

# A finding above this on the BH column is retracted. Declared before any
# q-value was computed.
Q_THRESHOLD = 0.10

TESTABLE = "testable"
UNCORRECTED = "uncorrected"
DESCRIPTIVE = "descriptive"
SELF_TESTED = "self_tested"

# The partition, and the single place it is written down. Anything absent is a
# kind nobody classified, which `unclassified` reports and the gate refuses.
CLASS_OF: dict[str, str] = {
    "outlier": TESTABLE,
    "trend": TESTABLE,
    "h2h_edge": TESTABLE,
    "clutch_milestone": TESTABLE,
    "trade_asymmetry": TESTABLE,
    "profile_extreme": UNCORRECTED,
    "intangible_outlier": UNCORRECTED,
    "team_style": UNCORRECTED,
    "milestone": DESCRIPTIVE,
    "rating_top": DESCRIPTIVE,
    "what_wins": DESCRIPTIVE,
    "era_context": DESCRIPTIVE,
    "meta_shift": DESCRIPTIVE,
    "model_null": SELF_TESTED,
    "mode_null": SELF_TESTED,
    "series_dynamics": SELF_TESTED,
}

# Why each uncorrected family is uncorrected, published beside the findings.
UNCORRECTED_REASON = (
    "player_metric_season stores a value, a denominator, a z and a percentile for an "
    "arbitrary metric and no standard error, so a threshold test on one of these would "
    "need an error bar invented for it"
)


def class_of(kind: str) -> str:
    """The declared class of a finding kind, or 'testable' for an unknown one."""
    return CLASS_OF.get(kind, TESTABLE)


def unclassified(kinds: Sequence[str]) -> list[str]:
    """Kinds present in a run that the partition does not name."""
    return sorted({k for k in kinds if k not in CLASS_OF})


# ---------------------------------------------------------------- the p-values


def conditional(tail_at_observed: float, tail_at_screen: float) -> float:
    """A tail probability divided by the tail the screen already guaranteed."""
    if tail_at_screen <= 0.0:
        return 1.0
    return min(1.0, tail_at_observed / tail_at_screen)


def threshold_normal(observed: float, boundary: float, se: float) -> float | None:
    """P(statistic >= observed) under a true value at the boundary, given the screen.

    The screen admits everything at or past the boundary, and the boundary is
    the null, so the conditioning tail is exactly one half.
    """
    if se is None or se <= 0.0 or not math.isfinite(se):
        return None
    t = (abs(observed) - boundary) / se
    return conditional(float(norm.sf(t)), 0.5)


def threshold_binomial(successes: int, trials: int, rate: float, screen: float) -> float | None:
    """P(X >= successes) under a true rate, conditioned on X clearing the screen.

    `screen` is the success rate the generator required, so the conditioning
    tail starts at the smallest integer count that reaches it.
    """
    if trials <= 0 or not 0.0 < rate < 1.0:
        return None
    floor = math.ceil(screen * trials - 1e-9)
    return conditional(
        float(binom.sf(successes - 1, trials, rate)),
        float(binom.sf(floor - 1, trials, rate)),
    )


def ordering(n_seasons: int) -> float | None:
    """P(a player's season values arrive in monotone order) with no true trajectory.

    Two of the n! orderings are monotone. Nothing is conditioned away here: the
    pattern is the whole statistic, so the rarity of the pattern is the p-value.
    """
    if n_seasons < 3:
        return None
    return min(1.0, 2.0 / math.factorial(n_seasons))


# ------------------------------------------------------- the step-up procedures


def _step_up(pvalues: FloatArray, multiplier: float) -> FloatArray:
    """Benjamini's step-up, with 1 for BH and the harmonic sum for BY."""
    n = pvalues.size
    if n == 0:
        return np.empty(0, dtype=float)
    order = np.argsort(pvalues, kind="stable")
    ranked = pvalues[order]
    raw = ranked * n * multiplier / np.arange(1, n + 1, dtype=float)
    # Monotone from the largest p down, so a small p never adjusts above a
    # larger one.
    adjusted = np.minimum.accumulate(raw[::-1])[::-1]
    out = np.empty(n, dtype=float)
    out[order] = np.clip(adjusted, 0.0, 1.0)
    return out


def bh(pvalues: Sequence[float]) -> FloatArray:
    """Benjamini-Hochberg q-values, in the order the p-values arrived."""
    return _step_up(np.asarray(pvalues, dtype=float), 1.0)


def by(pvalues: Sequence[float]) -> FloatArray:
    """Benjamini-Yekutieli q-values, valid under arbitrary dependence."""
    p = np.asarray(pvalues, dtype=float)
    if p.size == 0:
        return np.empty(0, dtype=float)
    harmonic = float(np.sum(1.0 / np.arange(1, p.size + 1, dtype=float)))
    return _step_up(p, harmonic)


# ------------------------------------------------------------------ the run


@dataclass(frozen=True)
class Finding:
    """One row of `insights`, reduced to what the correction needs."""

    insight_id: int
    kind: str
    p_value: float | None


@dataclass(frozen=True)
class Corrected:
    """The two q-values a finding earned, and the verdict on it."""

    insight_id: int
    q_bh: float
    q_by: float
    retracted: bool


def load(conn: psycopg.Connection[Any], run_id: int) -> list[Finding]:
    """Every finding of one insights run, in a fixed order."""
    rows = conn.execute(
        "SELECT id, kind, p_value FROM insights WHERE run_id = %s ORDER BY kind, id",
        (run_id,),
    ).fetchall()
    return [
        Finding(insight_id=int(r[0]), kind=str(r[1]), p_value=None if r[2] is None else float(r[2]))
        for r in rows
    ]


def correct(findings: Sequence[Finding]) -> list[Corrected]:
    """BH and BY per family, over the testable findings of each kind."""
    out: list[Corrected] = []
    by_kind: dict[str, list[Finding]] = {}
    for f in findings:
        if class_of(f.kind) == TESTABLE and f.p_value is not None:
            by_kind.setdefault(f.kind, []).append(f)
    for kind in sorted(by_kind):
        family = sorted(by_kind[kind], key=lambda f: (f.p_value or 0.0, f.insight_id))
        pvalues = [f.p_value for f in family if f.p_value is not None]
        q_bh, q_by = bh(pvalues), by(pvalues)
        for f, qb, qy in zip(family, q_bh, q_by, strict=True):
            out.append(
                Corrected(
                    insight_id=f.insight_id,
                    q_bh=float(qb),
                    q_by=float(qy),
                    retracted=bool(qb > Q_THRESHOLD),
                )
            )
    return out


def write(conn: psycopg.Connection[Any], corrected: Sequence[Corrected]) -> None:
    """Store the q-values and the retraction verdict on the findings."""
    if not corrected:
        return
    conn.cursor().executemany(
        "UPDATE insights SET q_bh = %s, q_by = %s, retracted = %s WHERE id = %s",
        [(c.q_bh, c.q_by, c.retracted, c.insight_id) for c in corrected],
    )


# Thresholds the survivor count is published at. The declared one decides
# retraction; the rest are there so a reader can see what the choice cost.
SENSITIVITY = (0.05, 0.10, 0.20, 0.33, 0.50)


def survivors_by_threshold(corrected: Sequence[Corrected]) -> list[dict[str, Any]]:
    """How many findings survive at each threshold, the declared one included."""
    return [
        {
            "q": q,
            "kept": sum(1 for c in corrected if c.q_bh <= q),
            "declared": q == Q_THRESHOLD,
        }
        for q in sorted({*SENSITIVITY, Q_THRESHOLD})
    ]


def _quantiles(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    return {
        "min": float(arr.min()),
        "p25": float(np.quantile(arr, 0.25)),
        "median": float(np.quantile(arr, 0.5)),
        "p75": float(np.quantile(arr, 0.75)),
        "max": float(arr.max()),
    }


def artifact(
    findings: Sequence[Finding],
    corrected: Sequence[Corrected],
    insights_run_id: int | None = None,
) -> dict[str, Any]:
    """The gate's payload: the q distribution per family and what fails the threshold."""
    q_of = {c.insight_id: c for c in corrected}
    classes: dict[str, int] = {}
    families: list[dict[str, Any]] = []
    by_kind: dict[str, list[Finding]] = {}
    for f in findings:
        classes[class_of(f.kind)] = classes.get(class_of(f.kind), 0) + 1
        by_kind.setdefault(f.kind, []).append(f)

    for kind in sorted(by_kind):
        rows = by_kind[kind]
        cls = class_of(kind)
        block: dict[str, Any] = {
            "kind": kind,
            "class": cls,
            "published": len(rows),
        }
        if cls == UNCORRECTED:
            block["reason"] = UNCORRECTED_REASON
        if cls == TESTABLE:
            tested = [r for r in rows if r.insight_id in q_of]
            block["tested"] = len(tested)
            block["untested"] = len(rows) - len(tested)
            block["p"] = _quantiles([r.p_value for r in tested if r.p_value is not None])
            block["q_bh"] = _quantiles([q_of[r.insight_id].q_bh for r in tested])
            block["q_by"] = _quantiles([q_of[r.insight_id].q_by for r in tested])
            block["fails_threshold"] = sum(1 for r in tested if q_of[r.insight_id].retracted)
            block["by_disagrees"] = sum(
                1
                for r in tested
                if q_of[r.insight_id].q_by > Q_THRESHOLD >= q_of[r.insight_id].q_bh
            )
        families.append(block)

    retracted = sum(1 for c in corrected if c.retracted)
    return {
        "available": bool(findings),
        "insights_run_id": insights_run_id,
        "q_threshold": Q_THRESHOLD,
        "procedure": "benjamini-hochberg, retraction; benjamini-yekutieli, published beside it",
        "n_findings": len(findings),
        "n_tested": len(corrected),
        "n_retracted": retracted,
        "by_class": classes,
        "unclassified": unclassified([f.kind for f in findings]),
        "sensitivity": survivors_by_threshold(corrected),
        # Two screens the generator applies after a finding is built, and which
        # no p-value here conditions on: best_per_season keeps a season's most
        # extreme mode slice, and cap_per_subject keeps a subject's two highest
        # scoring. Both select on the same statistic being tested, so the
        # p-values below are optimistic by an amount this run does not measure.
        "unmodelled_selection": ["best_per_season", "cap_per_subject"],
        "families": families,
        "statement": statement(len(findings), len(corrected), retracted),
    }


def statement(n_findings: int, n_tested: int, n_retracted: int) -> str:
    """One line for the run log."""
    if not n_tested:
        return f"{n_findings} findings, none testable"
    return (
        f"{n_findings} findings, {n_tested} tested, {n_retracted} retracted at q <= {Q_THRESHOLD:g}"
    )


def build(conn: psycopg.Connection[Any], run_id: int) -> tuple[list[Corrected], dict[str, Any]]:
    """Correct one insights run and return the rows to store beside its payload."""
    findings = load(conn, run_id)
    corrected = correct(findings)
    return corrected, artifact(findings, corrected, insights_run_id=run_id)


def params() -> dict[str, Any]:
    """What this run was configured with."""
    return {"q_threshold": Q_THRESHOLD, "procedures": ["fdr_bh", "fdr_by"]}
