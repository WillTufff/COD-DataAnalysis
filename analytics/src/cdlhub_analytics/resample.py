"""Fixing a resample's population to its contents rather than to its keys.

A seeded bootstrap or permutation draws *positions*. Whatever sits at position
zero is decided long before the draw, and in this project it was usually decided
by a surrogate key: rows arrived in `player_id` order, or clusters in the order
a `series_id` first appeared, or groups in the order a dictionary was filled.
Surrogate keys are assigned by the loader. Delete and recreate a few hundred
rows — which every reload of a source does — and the keys underneath renumber,
the population permutes, the same seed lands on different observations, and a
published interval moves while every point estimate it surrounds stays exactly
where it was.

That is not sampling error being reported honestly; it is a number that changed
with no data behind it, and it is invisible in review because the estimate it
brackets does not move. The metric-diff harness found it in two places and the
class it belongs to is this one.

Two shapes of the defect, and one function each:

- The population's *row order* comes from a key. `order` sorts the rows by what
  they contain instead. Ties are rows carrying identical numbers, which no
  resample statistic can tell apart, so leaving them in arrival order is
  harmless — but a caller may pass a final tie-break column to keep the sort
  itself reproducible.
- A *single* generator is threaded through many groups, so each group's draws
  depend on how many groups were reached before it — that is, on the iteration
  order of a dictionary keyed by a surrogate. `stream` gives a group its own
  generator, seeded from the group's contents, so its draws do not depend on
  when it was reached or on whether it was reached at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from .regress import matrix_hash

__all__ = ["order", "stream"]

IntArray = NDArray[np.int64]


def order(columns: Sequence[Sequence[float] | NDArray[np.float64]]) -> IntArray:
    """A permutation sorting rows by their contents, first column primary.

    Given the aligned columns of a population, returns the index order to put it
    in before any draw is taken. `numpy.lexsort` reads its keys last-first, which
    is the opposite of how a sort key is written and read, so the reversal
    happens here rather than at every call site.
    """
    if not columns:
        return np.zeros(0, dtype=np.int64)
    keys = tuple(np.asarray(c, dtype=float) for c in reversed(list(columns)))
    return np.asarray(np.lexsort(keys), dtype=np.int64)


def stream(seed: int, *content: object) -> np.random.Generator:
    """A generator for one group, seeded from that group's own contents.

    The alternative — one generator advanced through a loop over groups — makes
    every group after the first depend on the iteration order, so a renumbered
    key moves an interval for a group whose data never changed. Here a group's
    draws are a function of its data and the run's seed, and of nothing else:
    reorder the groups, drop one, add one, and the rest are untouched.

    `content` is anything array-like the group is made of. It is hashed as
    float64 contents, so the digest does not move with a dtype or a stride.
    """
    digest = 0
    for part in content:
        digest ^= int(matrix_hash(np.asarray(part, dtype=float)), 16)
    # Two 64-bit words rather than one: `default_rng` takes the whole sequence
    # into the seed, so the run's seed stays legible in the stream's identity.
    return np.random.default_rng([seed, digest % (2**64)])
