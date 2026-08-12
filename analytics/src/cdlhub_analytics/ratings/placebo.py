"""Negative controls: what the machinery says when there is nothing to find.

A model that passes its own gate and fails a placebo has not passed. Three
things are checked, each aimed at a specific way this stack could be reporting
structure it invented:

**Shuffled sides.** Reassign the players of each map to the two sides at random,
keeping the sides the size they were played at, and refit the plus-minus. No
player now has any consistent association with winning, so the coefficients
should be noise and their intervals should cover zero at about the nominal rate.
A rate far under 95% means the published standard errors are too narrow, which
would make every resolved coefficient in the leaderboard suspect.

**A duplicated column.** Give one player an exact copy — a synthetic player on
precisely their maps, on precisely their side — and confirm the identification
diagnostic reports the rank deficiency rather than returning two plausible
coefficients for one person. This is the mirror of the failure PD item 6 found
in the record itself, where one career sat in two columns; there the split was
real and invisible, here the copy is planted and has to be visible.

**Permuted seasons.** Shuffle which season each player's transition points at,
so the predictor and the target belong to different players' careers, and
confirm the persistence correlations collapse toward zero. A test that still
reports persistence on permuted labels is measuring the population, not the
player.

The venue permutation the plan also asks for — permute `is_lan` and confirm the
venue effect vanishes — is **declared and not run**: no model in the stack
estimates a venue effect yet. It arrives with P2b, which is where the effect it
would falsify arrives.

The placebos prove the machinery finds nothing where there is nothing. They are
not sufficient alone — a maximally shrunk estimator passes every one of them —
so the pre-flight's positive control, which puts a known value in and asks for it
back, is reported beside them rather than instead of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import numpy as np

from ..regress import fit_logistic_l2
from ..resample import stream
from . import evalspec, preflight, rapm
from .rapm import AdmittedMap

# Replicate shuffles per placebo. The spread across replicates is published
# beside the mean, so a reader can see whether more would have changed anything.
REPLICATES = 8

# Nominal coverage of a ±1.96 SE interval. What the shuffle should recover.
NOMINAL = 0.95


def _shuffled_sides(games: Sequence[AdmittedMap], rng: np.random.Generator) -> list[AdmittedMap]:
    """Every map's players reassigned to its two sides, sizes preserved."""
    out = []
    for game in games:
        players = list(game.players)
        rng.shuffle(players)
        cut = len(game.home_players)
        out.append(
            replace(
                game,
                home_players=tuple(sorted(players[:cut])),
                away_players=tuple(sorted(players[cut:])),
            )
        )
    return out


def _design(games: Sequence[AdmittedMap]) -> tuple[np.ndarray[Any, Any], np.ndarray[Any, Any]]:
    players = sorted({p for game in games for p in game.players})
    col = {pid: i for i, pid in enumerate(players)}
    x = np.zeros((len(games), len(players)), dtype=float)
    y = np.zeros(len(games), dtype=float)
    for i, game in enumerate(games):
        for pid in game.home_players:
            x[i, col[pid]] = 1.0
        for pid in game.away_players:
            x[i, col[pid]] = -1.0
        y[i] = 1.0 if game.home_won else 0.0
    return x, y


def shuffled_sides(games: Sequence[AdmittedMap], replicates: int = REPLICATES) -> dict[str, Any]:
    """Refit the plus-minus on randomized sides; the intervals should cover zero."""
    if len(games) < 100:
        return {"available": False, "reason": "too few admitted maps"}
    covered: list[float] = []
    resolved: list[int] = []
    for i in range(replicates):
        rng = stream(evalspec.BOOTSTRAP_SEED + i, np.array([float(i)]))
        x, y = _design(_shuffled_sides(games, rng))
        result = fit_logistic_l2(x, y, l2=rapm.L2)
        coefs = np.asarray(result.weights)
        logit = result.intercept + x @ coefs
        p = 1.0 / (1.0 + np.exp(-np.clip(logit, -35.0, 35.0)))
        ses = rapm.standard_errors(x, p, rapm.L2)
        keep = ses > 0
        z = np.abs(coefs[keep] / ses[keep])
        covered.append(float(np.mean(z < 1.959964)))
        resolved.append(int(np.sum(z >= 1.959964)))
    return {
        "available": True,
        "what": (
            "players reassigned to the two sides of each map at random, the plus-minus "
            "refitted, and the share of coefficients whose interval covers zero"
        ),
        "replicates": replicates,
        "nominal_coverage": NOMINAL,
        "coverage_mean": round(float(np.mean(covered)), 4),
        "coverage_min": round(float(np.min(covered)), 4),
        "resolved_median": float(np.median(resolved)),
        "columns": int(_design(games)[0].shape[1]),
        "passes": bool(np.min(covered) >= 0.90),
    }


def duplicated_player(games: Sequence[AdmittedMap]) -> dict[str, Any]:
    """Plant an exact copy of one player and confirm the diagnostic sees it."""
    if len(games) < 100:
        return {"available": False, "reason": "too few admitted maps"}
    counts: dict[int, int] = {}
    for game in games:
        for pid in game.players:
            counts[pid] = counts.get(pid, 0) + 1
    # The busiest player, so the copy is a column with real support behind it
    # rather than one the penalty would have flattened anyway.
    target = max(sorted(counts), key=lambda pid: counts[pid])
    clone = max(counts) + 1

    planted = []
    for game in games:
        if target in game.home_players:
            planted.append(replace(game, home_players=(*game.home_players, clone)))
        elif target in game.away_players:
            planted.append(replace(game, away_players=(*game.away_players, clone)))
        else:
            planted.append(game)

    before = preflight.career_spectrum(games)
    after = preflight.career_spectrum(planted)
    return {
        "available": True,
        "what": (
            "a synthetic player on exactly one existing player's maps and side: a column "
            "identical to theirs, which the design cannot separate from it"
        ),
        "duplicated_player_maps": counts[target],
        "columns_before": before.columns,
        "columns_after": after.columns,
        "rank_before": before.rank,
        "rank_after": after.rank,
        "deficiency_before": before.deficiency,
        "deficiency_after": after.deficiency,
        # The copy adds a column and no direction: rank must not move.
        "passes": bool(after.columns == before.columns + 1 and after.rank == before.rank),
    }


def permuted_seasons(
    predictor: Sequence[float], target: Sequence[float], replicates: int = REPLICATES
) -> dict[str, Any]:
    """Break the pairing between a predictor and its target; persistence should go."""
    x = np.asarray(predictor, dtype=float)
    y = np.asarray(target, dtype=float)
    if len(x) < 50:
        return {"available": False, "reason": "too few transitions"}
    observed = float(np.corrcoef(x, y)[0, 1])
    drawn = []
    for i in range(replicates):
        rng = stream(evalspec.BOOTSTRAP_SEED + i, x, y)
        drawn.append(float(np.corrcoef(x, rng.permutation(y))[0, 1]))
    return {
        "available": True,
        "what": (
            "the target reassigned to a different player's transition, so the predictor "
            "points at a career that is not the one it measured"
        ),
        "replicates": replicates,
        "observed_r": round(observed, 4),
        "permuted_r_mean": round(float(np.mean(drawn)), 4),
        "permuted_r_max_abs": round(float(np.max(np.abs(drawn))), 4),
        "passes": bool(np.max(np.abs(drawn)) < abs(observed)),
    }


DEFERRED = {
    "venue_permutation": (
        "permute is_lan and confirm the venue effect vanishes: declared, not run. No model "
        "in the stack estimates a venue effect, so there is nothing for the permutation to "
        "falsify until P2b fits one"
    )
}


def suite(
    games: Sequence[AdmittedMap], predictor: Sequence[float], target: Sequence[float]
) -> dict[str, Any]:
    """Every placebo, and the ones deliberately not run."""
    blocks = {
        "shuffled_sides": shuffled_sides(games),
        "duplicated_player": duplicated_player(games),
        "permuted_seasons": permuted_seasons(predictor, target),
    }
    ran = [b for b in blocks.values() if b.get("available")]
    return {
        "what": "negative controls: what the machinery reports when there is nothing there",
        "placebos": blocks,
        "deferred": DEFERRED,
        "n_run": len(ran),
        "n_failed": sum(1 for b in ran if not b.get("passes")),
        "passes": all(b.get("passes") for b in ran),
    }
