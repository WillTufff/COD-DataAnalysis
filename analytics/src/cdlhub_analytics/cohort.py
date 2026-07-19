"""Cohort-relative scoring shared by the era adjustment and the metric layer."""

from __future__ import annotations

import math

import numpy as np

MIN_COHORT = 2


def z_and_pctl(values: dict[int, float], cohort_ids: list[int]) -> dict[int, tuple[float, float]]:
    """Z-score and percentile for every id in `values`, measured against the
    distribution formed by `cohort_ids` only.

    Returns an empty mapping when the cohort is too small or has no spread,
    which callers store as NULL z/pctl.
    """
    cohort_values = [values[i] for i in cohort_ids if i in values and math.isfinite(values[i])]
    if len(cohort_values) < MIN_COHORT:
        return {}
    cohort = np.array(cohort_values, dtype=float)
    sd = float(cohort.std(ddof=1))
    if sd == 0.0 or not math.isfinite(sd):
        return {}
    mean = float(cohort.mean())
    sorted_cohort = np.sort(cohort)
    out: dict[int, tuple[float, float]] = {}
    for entity_id, v in values.items():
        if not math.isfinite(v):
            continue
        z = (v - mean) / sd
        pctl = float(np.searchsorted(sorted_cohort, v, side="right")) / len(sorted_cohort)
        out[entity_id] = (z, min(max(pctl, 0.0), 1.0))
    return out
