"""Cohort-relative scoring shared by the era adjustment and the metric layer."""

from __future__ import annotations

import math

import numpy as np

MIN_COHORT = 2

# A percentile and a z-score do not need the same amount of company. A
# percentile is a rank statement: with a handful of peers it is coarse but it
# is not wrong. A z-score divides by an estimated SD and invites the reader to
# treat the result as standard deviations from a distribution — on four
# qualified players that number is noise wearing a distribution's clothes, and
# the site colors bars and fires "N standard deviations" findings off it.
#
# So the two thresholds are separated: below MIN_Z_COHORT the percentile still
# publishes and the z-score comes back None, which every caller already stores
# as NULL. Nothing is hidden that was previously shown; a claim the sample
# cannot support is simply not made.
MIN_Z_COHORT = 15


def cohort_spread(values: dict[int, float], cohort_ids: list[int]) -> tuple[float, float] | None:
    """(mean, sd) of the qualified cohort, or None if it cannot be measured.

    Exposed separately because a caller that wants to express an uncertainty in
    z units — an SE in raw units divided by this sd — needs the same denominator
    the z-score used, and re-deriving it would let the two drift apart.
    """
    cohort_values = [values[i] for i in cohort_ids if i in values and math.isfinite(values[i])]
    if len(cohort_values) < MIN_COHORT:
        return None
    cohort = np.array(cohort_values, dtype=float)
    sd = float(cohort.std(ddof=1))
    if sd == 0.0 or not math.isfinite(sd):
        return None
    return float(cohort.mean()), sd


def z_and_pctl(
    values: dict[int, float],
    cohort_ids: list[int],
    min_z_cohort: int = MIN_Z_COHORT,
) -> dict[int, tuple[float | None, float]]:
    """Z-score and percentile for every id in `values`, measured against the
    distribution formed by `cohort_ids` only.

    Returns an empty mapping when the cohort is too small or has no spread,
    which callers store as NULL z/pctl. Cohorts smaller than `min_z_cohort`
    return a percentile with a None z-score.
    """
    cohort_values = [values[i] for i in cohort_ids if i in values and math.isfinite(values[i])]
    spread = cohort_spread(values, cohort_ids)
    if spread is None:
        return {}
    mean, sd = spread
    cohort = np.array(cohort_values, dtype=float)
    sorted_cohort = np.sort(cohort)
    publish_z = len(cohort_values) >= min_z_cohort
    out: dict[int, tuple[float | None, float]] = {}
    for entity_id, v in values.items():
        if not math.isfinite(v):
            continue
        z = (v - mean) / sd if publish_z else None
        pctl = float(np.searchsorted(sorted_cohort, v, side="right")) / len(sorted_cohort)
        out[entity_id] = (z, min(max(pctl, 0.0), 1.0))
    return out
