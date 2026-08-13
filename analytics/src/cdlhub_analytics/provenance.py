"""The reproducibility record every model run stores in its params.

A run's numbers can only be reproduced from the seeds that produced them, the
dependency set that was resolved, and the interpreter that ran it. All three go
into `model_runs.params` under a `provenance` key, alongside the git SHA
`writeback` already records.

`matrix_hash` fingerprints a design matrix so a refit that produces different
numbers can be told apart from a refit that was handed different data.
"""

from __future__ import annotations

import hashlib
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np

from . import roundwp, seriesdyn, style
from .metricdiff import evalpop
from .ratings import (
    evalspec,
    holdout,
    maplevel,
    opponent,
    player_rating,
    prior,
    significance,
    simleague,
    statespace,
)
from .regress import matrix_hash

__all__ = ["SEEDS", "block", "environment", "lock_sha256", "matrix_hash"]

ANALYTICS_ROOT = Path(__file__).resolve().parents[2]
LOCKFILE = ANALYTICS_ROOT / "uv.lock"

# Every fixed seed in the package, by the module that owns it. A stochastic
# stage whose seed is missing here is a stage whose output cannot be reproduced,
# which is what `tests/test_provenance.py` asserts against.
SEEDS: dict[str, int] = {
    "player_rating": player_rating.BOOTSTRAP_SEED,
    "holdout": holdout.BOOTSTRAP_SEED,
    "significance": significance.BOOTSTRAP_SEED,
    "maplevel": maplevel.PERMUTATION_SEED,
    "roundwp": roundwp.BOOTSTRAP_SEED,
    "seriesdyn": seriesdyn.BOOTSTRAP_SEED,
    "style": style.SEED,
    "simleague": simleague.SEED,
    "statespace": statespace.BOOTSTRAP_SEED,
    "opponent": opponent.PLACEBO_SEED,
    "evalspec": evalspec.BOOTSTRAP_SEED,
    "prior": prior.SEED,
}


def lock_sha256() -> str | None:
    """The resolved dependency set, hashed. None when the lockfile is absent."""
    try:
        return hashlib.sha256(LOCKFILE.read_bytes()).hexdigest()
    except OSError:
        return None


def environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "implementation": sys.implementation.name,
        "numpy": np.__version__,
        "platform": platform.platform(terse=True),
        "machine": platform.machine(),
    }


def block() -> dict[str, Any]:
    """The provenance object attached to every run."""
    return {
        "seeds": dict(SEEDS),
        "lock_sha256": lock_sha256(),
        "environment": environment(),
        "evaluation_set": evalpop.stamp(),
    }
