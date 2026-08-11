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


# Mantissa bits kept when hashing a stream's contents, of float64's 52. What is
# dropped is roughly the last eight decimal digits of each value.
#
# A seed read from the full 52 bits is a hash of exact bytes, so a last-bit
# difference in the data produces an unrelated digest, an unrelated set of draws,
# and an interval that moves by its own sampling spread. Last-bit differences are
# not hypothetical: `Generator.normal(loc, scale)` scales its draws with a fused
# multiply-add on arm64 and without one on x86_64, so the same seed gives values
# one ulp apart on the two architectures, and every number computed from them
# inherits that. The point estimate absorbs it — it is a smooth function of the
# data and moves by ~1e-16 — while the interval around it jumps, which is the
# signature this constant exists to remove.
#
# Truncating to a bit boundary is exact integer arithmetic on the float's own
# representation, so it introduces no rounding of its own. Two values still land
# on opposite sides of a boundary if they straddle one, but that now takes a
# specific alignment rather than any perturbation at all: a one-ulp difference
# crosses a boundary with probability 2**-(52 - KEEP) rather than certainty.
KEEP_MANTISSA_BITS = 28


def _coarse(part: object) -> NDArray[np.float64]:
    """`part` as float64 with the low mantissa bits cleared."""
    values = np.ascontiguousarray(part, dtype=np.float64)
    dropped = np.uint64(52 - KEEP_MANTISSA_BITS)
    mask = ~((np.uint64(1) << dropped) - np.uint64(1))
    truncated: NDArray[np.float64] = (values.view(np.uint64) & mask).view(np.float64)
    return truncated.reshape(values.shape)


def stream(seed: int, *content: object) -> np.random.Generator:
    """A generator for one group, seeded from that group's own contents.

    The alternative — one generator advanced through a loop over groups — makes
    every group after the first depend on the iteration order, so a renumbered
    key moves an interval for a group whose data never changed. Here a group's
    draws are a function of its data and the run's seed, and of nothing else:
    reorder the groups, drop one, add one, and the rest are untouched.

    `content` is anything array-like the group is made of. It is hashed as
    float64 contents, so the digest does not move with a dtype or a stride —
    and at reduced precision, so it does not move with a last-bit difference
    either. See `_coarse`.

    Pass distinct parts. The parts are combined with an exclusive or, which is
    blind to their order and annihilates any pair of them that is identical:
    `stream(s, x, x)` seeds as if no content had been given at all. Nothing
    published reaches that today — the two-part callers pass arrays that cannot
    be equal to each other — but the caller with room for it is `seriesdyn`,
    which folds in five 0/1 indicator columns over the same series, and a cohort
    narrow enough to produce two all-zero indicators would cancel them against
    each other.

    What that costs is discrimination rather than correctness: the draws stay
    uniform, the bootstrap stays valid, and the seed stays a function of the
    contents and the run's seed alone, so the surrogate-key independence this
    module exists for is not at stake. Two groups that ought to hold separate
    streams would share one. Combining the parts in sequence instead would close
    it, at the price of moving every published interval a second time to fix
    something that has never produced a wrong number — the exact unexplained
    movement described at the top of this file. Left as is, deliberately.
    """
    digest = 0
    for part in content:
        digest ^= int(matrix_hash(_coarse(part)), 16)
    # Two 64-bit words rather than one: `default_rng` takes the whole sequence
    # into the seed, so the run's seed stays legible in the stream's identity.
    return np.random.default_rng([seed, digest % (2**64)])
