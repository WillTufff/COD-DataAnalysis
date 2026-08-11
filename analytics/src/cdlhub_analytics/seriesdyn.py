"""Series dynamics. Spec: /methodology#series-dynamics.

A Call of Duty series is a race to three maps, and the commentary around it is
made of claims about the race itself: a 1-0 lead is worth more than arithmetic,
teams ride momentum through a series, a reverse sweep is a collapse rather than
a coin landing the same way twice. This module measures all three against the
only thing they have to beat — the same maps, played by the same teams, with no
memory between them.

**The null this tests is conditional independence.** Take each series' two
teams, freeze their map-level Elo *before the first map is played*, and take the
league's published mode rotation as the maps they will play. That gives five
independent per-map win probabilities and an exact enumeration of the race:
every scoreline the series could have reached, with its probability. Summed over
the archive, that says how often a 3-0 should happen, how often a series should
go the distance, how often the map-1 winner should go on to win, and how often
someone should come back from 0-2 — with no carryover of any kind. What is
published is the gap between that and what happened.

Doing it this way is what separates the interesting question from the obvious
one. Raw `P(win series | won map 1)` is 0.759, and it is *supposed* to be high:
winning map 1 is a third of the way to the series result, and the team that won
it was on average the better team to begin with. Both of those are mechanical,
both are in the enumeration, and neither is momentum. Only the residual is.

**And the residual has a rival explanation that is not momentum either.** The
ratings' own map-1 calibration slope comes out above 1, which is the archive
saying the true gaps between these teams are wider than the ratings knew — so
the map-1 winner is, on the evidence of having won it, better than its rating
said, and goes on to win map 2 more often with nothing carrying over at all.
Every rate here is therefore stated against a second benchmark: the same
enumeration at the strength gap that best explains these results with no
carryover, and the section on unmeasured quality below is how that gap is
fitted. What separates the two is *shape* — quality is shared by every map of a
series in any order, carryover attaches to adjacency — and the sequence model
there is what estimates them apart. The plain regression is kept beside it,
unregularized and frozen at the series start, because the distance between the
two answers is itself the finding.

**A null is only as good as its power**, so every coefficient is reported with
the smallest one this archive could have resolved at 80% power, translated into
points of win probability between a team that just won a map and one that just
lost it. "No momentum" and "no momentum this archive could see" are different
claims and only the second is ever made.

Scope is the 1,272 best-of-five series whose maps reconstruct their scoreline
exactly. Best-of-one and best-of-three shapes exist in the archive — 34 of them,
almost all forfeits — and are counted and excluded rather than pooled into a
format they are not.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import numpy as np
import psycopg

from .ratings.maplevel import BLEND_K, ROTATION, K, State
from .regress import FloatArray, fit_logistic_l2
from .resample import order as content_order
from .resample import stream as resample_stream

MODEL = "series_dynamics"
VERSION = "1.0.0"

# The published format. Everything else in the archive is a forfeit shape or a
# one-off final; both are reported as counts and neither is analysed.
WINS_NEEDED = 3

# No penalty on the momentum fit. See the module docstring: ridge shrinks toward
# zero, and zero is the null hypothesis, so a penalised coefficient would be
# evidence for the null manufactured by the estimator.
L2 = 0.0

BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20190811  # CWL Champs 2019 grand final; any fixed seed works

# Two-sided 5% at 80% power, same convention as ratings.significance.
Z_ALPHA = 1.959964
Z_POWER = 0.841621
POWER_FACTOR = Z_ALPHA + Z_POWER

# A (title, year) cohort enters the per-era table with this many series. Below
# it the rates are one event's worth of bracket and say nothing.
MIN_ERA_SERIES = 50


def params() -> dict[str, Any]:
    return {
        "wins_needed": WINS_NEEDED,
        "strength": "map_elo blend arm, frozen before map 1",
        "k": K,
        "blend_k": BLEND_K,
        "l2": L2,
        "bootstrap_b": BOOTSTRAP_B,
        "min_era_series": MIN_ERA_SERIES,
    }


SERIES_SQL = """
SELECT s.id, s.team1_id, s.team2_id, s.team1_score, s.team2_score, s.played_at,
       s.event_id, t.short_name, se.year, g.ordinal, gm.slug, g.winner_team_id
FROM series s
JOIN games g       ON g.series_id = s.id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN titles t      ON t.id = se.title_id
WHERE s.team1_id IS NOT NULL AND s.team2_id IS NOT NULL
  AND s.team1_score IS NOT NULL AND s.team2_score IS NOT NULL
  AND s.team1_score <> s.team2_score
ORDER BY s.played_at, s.id, g.ordinal
"""


@dataclass(frozen=True)
class SeriesMap:
    ordinal: int
    mode: str
    team1_won: bool


@dataclass(frozen=True)
class Series:
    """One decided series whose maps reconstruct its scoreline exactly."""

    id: int
    team1: int
    team2: int
    played_at: datetime
    event_id: int
    title: str
    year: int
    wins_needed: int
    maps: tuple[SeriesMap, ...]

    @property
    def era(self) -> str:
        return f"{self.year} {self.title}"

    @property
    def path(self) -> tuple[bool, ...]:
        """The race as seen by team 1: True where team 1 won that map."""
        return tuple(m.team1_won for m in self.maps)


def load(
    conn: psycopg.Connection[tuple[object, ...]],
) -> tuple[list[Series], dict[str, int]]:
    """Every decided series, with the ones this analysis cannot use counted.

    A series is kept only when its maps *are* its result: no undecided map, no
    gap in the ordinals, and map wins that add up to the archived scoreline. The
    race enumeration reads the order of results, so a series missing its third
    map is not a shorter series — it is an unknown one, and it is dropped and
    counted rather than reconstructed.
    """
    rows = conn.execute(SERIES_SQL).fetchall()
    grouped: dict[int, list[tuple[object, ...]]] = defaultdict(list)
    for row in rows:
        grouped[cast(int, row[0])].append(row)

    dropped: dict[str, int] = defaultdict(int)
    out: list[Series] = []
    for sid, maps in grouped.items():
        head = maps[0]
        t1, t2 = cast(int, head[1]), cast(int, head[2])
        s1, s2 = cast(int, head[3]), cast(int, head[4])
        wins_needed = max(s1, s2)

        if any(row[11] is None for row in maps):
            dropped["undecided_map"] += 1
            continue
        ordinals = [cast(int, row[9]) for row in maps]
        if ordinals != list(range(1, len(ordinals) + 1)):
            dropped["ordinal_gap"] += 1
            continue
        won = [cast(int, row[11]) == t1 for row in maps]
        if (sum(won), len(won) - sum(won)) != (s1, s2):
            dropped["score_mismatch"] += 1
            continue
        if wins_needed != WINS_NEEDED:
            dropped[f"best_of_{2 * wins_needed - 1}"] += 1
            continue

        out.append(
            Series(
                id=sid,
                team1=t1,
                team2=t2,
                played_at=cast(datetime, head[5]),
                event_id=cast(int, head[6]),
                title=cast(str, head[7]),
                year=cast(int, head[8]),
                wins_needed=wins_needed,
                maps=tuple(
                    SeriesMap(
                        ordinal=cast(int, row[9]),
                        mode=cast(str, row[10]),
                        team1_won=w,
                    )
                    for row, w in zip(maps, won, strict=True)
                ),
            )
        )
    out.sort(key=lambda s: (s.played_at, s.id))
    return out, dict(dropped)


# ------------------------------------------------------------- the race


# Every quantity this module publishes about a scoreline, computed the same way
# for a series that happened and for one the enumeration imagined.
EVENTS: tuple[str, ...] = ("team1_won", "map1_winner_won", "sweep", "decider", "reverse_sweep")


def path_events(path: Sequence[bool], wins_needed: int = WINS_NEEDED) -> dict[str, float]:
    """The indicators a single race path carries, from team 1's side.

    `reverse_sweep` is the strong form of a comeback: the eventual winner lost
    the first `wins_needed - 1` maps, so in a best-of-five it is 0-2 down and
    won. The weak form — trailing at any point — is every series that goes the
    distance, which is `decider`, and reporting it as a comeback would dress up
    arithmetic as drama.
    """
    w1 = sum(1 for x in path if x)
    team1_won = w1 == wins_needed
    winner_side = team1_won
    opening = path[: wins_needed - 1]
    return {
        "team1_won": float(team1_won),
        # Who won map 1 is path[0]; the map-1 winner took the series exactly
        # when that side is also the winning side.
        "map1_winner_won": float(bool(path[0]) == winner_side),
        "sweep": float(min(w1, len(path) - w1) == 0),
        "decider": float(len(path) == 2 * wins_needed - 1),
        "reverse_sweep": float(all(x != winner_side for x in opening)),
    }


def race_paths(
    ps: Sequence[float], wins_needed: int = WINS_NEEDED
) -> list[tuple[tuple[bool, ...], float]]:
    """Every scoreline the race can reach, with its probability.

    `ps[i]` is team 1's win probability on the i-th map of the rotation. The
    maps are enumerated rather than collapsed into one probability because they
    are different modes with different probabilities — a Hardpoint team meeting
    a Search team is the case that makes a race asymmetric, and averaging it
    away would erase it.
    """
    if len(ps) < 2 * wins_needed - 1:
        raise ValueError("rotation is shorter than the longest possible race")
    out: list[tuple[tuple[bool, ...], float]] = []

    def walk(i: int, w1: int, w2: int, prob: float, path: list[bool]) -> None:
        if w1 == wins_needed or w2 == wins_needed:
            out.append((tuple(path), prob))
            return
        p = ps[i]
        walk(i + 1, w1 + 1, w2, prob * p, [*path, True])
        walk(i + 1, w1, w2 + 1, prob * (1.0 - p), [*path, False])

    walk(0, 0, 0, 1.0, [])
    return out


def expected_events(ps: Sequence[float], wins_needed: int = WINS_NEEDED) -> dict[str, float]:
    """What each indicator averages to when the maps carry no memory."""
    totals = dict.fromkeys(EVENTS, 0.0)
    for path, prob in race_paths(ps, wins_needed):
        for key, value in path_events(path, wins_needed).items():
            totals[key] += prob * value
    return totals


def expected_events_latent(
    logits: Sequence[float],
    a: float,
    sigma: float,
    wins_needed: int = WINS_NEEDED,
) -> dict[str, float]:
    """The same expectation, allowing for quality the rating did not have.

    The rating-only column below is the wrong benchmark on its own, and in a
    knowable direction: two teams further apart than their ratings say produce
    more sweeps and fewer deciders than independence at those ratings predicts,
    without anything carrying over between maps. So the same race is enumerated
    again at probabilities `sigmoid(a·strength + u)` and averaged over `u`, with
    `a` and `sigma` taken from the sequence model fitted with no momentum term
    at all. Whatever survives *this* column is not the rating being modest.
    """
    nodes, weights = _gauss_hermite()
    totals = dict.fromkeys(EVENTS, 0.0)
    x = np.asarray(logits, dtype=float)
    for node, weight in zip(nodes, weights, strict=True):
        ps = 1.0 / (1.0 + np.exp(-(a * x + sigma * node)))
        for key, value in expected_events(ps.tolist(), wins_needed).items():
            totals[key] += float(weight) * value
    return totals


# ------------------------------------------------------- frozen strength


@dataclass
class Frozen:
    """One walk-forward pass: what each series looked like before it started.

    `rotation_ps` is team 1's win probability on each map of the title's
    rotation and `played_ps` on each map actually played, both from ratings that
    have seen every earlier series and nothing of this one. The distinction
    matters: the rotation is a league rule and known in advance, while which
    maps got played is a function of the result.
    """

    rotation_ps: dict[int, tuple[float, ...]]
    played_ps: dict[int, tuple[float, ...]]
    n_no_rotation: int


def freeze(
    series: Sequence[Series],
    lineage: dict[int, int] | None = None,
    k: float = K,
    blend_k: float = BLEND_K,
) -> Frozen:
    """Walk the archive, recording each series' opening probabilities.

    The rating state advances exactly as `maplevel.walk_forward` advances it —
    same K, same blend, one update per decided map in play order — but every
    probability this module reads is taken before the series' first update. A
    rating that moved with map 1 would carry the map-1 result into the control
    variable, and the momentum coefficient would be measuring its own control.
    """
    lin = lineage or {}
    state = State(k=k, blend_k=blend_k)
    rotation_ps: dict[int, tuple[float, ...]] = {}
    played_ps: dict[int, tuple[float, ...]] = {}
    n_no_rotation = 0

    for s in series:
        l1, l2 = lin.get(s.team1, s.team1), lin.get(s.team2, s.team2)
        rotation = ROTATION.get(s.title)
        if rotation is None:
            n_no_rotation += 1
        else:
            rotation_ps[s.id] = tuple(state.predict("blend", l1, l2, mode) for mode in rotation)
        played_ps[s.id] = tuple(state.predict("blend", l1, l2, m.mode) for m in s.maps)
        for m in s.maps:
            state.update(l1, l2, m.mode, m.team1_won)

    return Frozen(rotation_ps=rotation_ps, played_ps=played_ps, n_no_rotation=n_no_rotation)


def _logit(p: float) -> float:
    q = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(q / (1.0 - q))


# --------------------------------------------------------- the descriptive


def _wilson(k: float, n: float) -> tuple[float, float]:
    """Score interval for a proportion. Behaves at 0 and 1, where the normal
    approximation returns a zero-width interval and lies about it."""
    if n <= 0:
        return (0.0, 1.0)
    z = Z_ALPHA
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (max(centre - half, 0.0), min(centre + half, 1.0))


# The two benchmarks every observed rate is stated against, weakest first.
BENCHMARKS: tuple[str, ...] = ("rating", "quality")


def _paired_gap(o: FloatArray, e: FloatArray, idx: np.ndarray[Any, Any]) -> dict[str, Any]:
    """Observed minus expected, resampled over series.

    The two rates are computed on the same series, so their difference is
    paired; treating them as two independent proportions would widen the
    interval by roughly the square root of two and weaken a real gap.
    """
    diff = o - e
    draws = np.array([diff[row].mean() for row in idx])
    lo, hi = np.percentile(draws, [2.5, 97.5])
    se = float(draws.std(ddof=1))
    return {
        "expected": round(float(e.mean()), 4),
        "delta": round(float(diff.mean()), 4),
        "lo": round(float(lo), 4),
        "hi": round(float(hi), 4),
        "excludes_zero": bool(lo > 0.0 or hi < 0.0),
        "mde80": round(POWER_FACTOR * se, 4) if se > 0 else None,
    }


def _rates(
    observed: dict[str, FloatArray],
    expected: dict[str, dict[str, FloatArray]],
    idx: np.ndarray[Any, Any],
) -> list[dict[str, Any]]:
    """Every scoreline rate against both benchmarks."""
    out: list[dict[str, Any]] = []
    for key in EVENTS:
        o = observed[key]
        n = len(o)
        lo, hi = _wilson(float(o.sum()), float(n))
        out.append(
            {
                "event": key,
                "n_series": n,
                "observed": round(float(o.mean()), 4),
                "observed_lo": round(lo, 4),
                "observed_hi": round(hi, 4),
                "vs": {b: _paired_gap(o, expected[b][key], idx) for b in BENCHMARKS},
            }
        )
    return out


def _indicator_arrays(
    series: Sequence[Series], frozen: Frozen, quality: tuple[float, float]
) -> tuple[list[Series], dict[str, FloatArray], dict[str, dict[str, FloatArray]]]:
    """Per-series observed and expected indicators, aligned row for row.

    `quality` is the (a, sigma) of the sequence model fitted with no momentum
    term — the second benchmark's parameters, taken from a fit that was given
    every chance to explain these results without carryover.
    """
    a, sigma = quality
    usable = [s for s in series if s.id in frozen.rotation_ps]
    observed: dict[str, list[float]] = {k: [] for k in EVENTS}
    expected: dict[str, dict[str, list[float]]] = {b: {k: [] for k in EVENTS} for b in BENCHMARKS}
    for s in usable:
        rot = frozen.rotation_ps[s.id]
        obs = path_events(s.path, s.wins_needed)
        exp = {
            "rating": expected_events(rot, s.wins_needed),
            "quality": expected_events_latent([_logit(p) for p in rot], a, sigma, s.wins_needed),
        }
        for key in EVENTS:
            observed[key].append(obs[key])
            for b in BENCHMARKS:
                expected[b][key].append(exp[b][key])
    return (
        usable,
        {k: np.array(v, dtype=float) for k, v in observed.items()},
        {b: {k: np.array(v, dtype=float) for k, v in cols.items()} for b, cols in expected.items()},
    )


def _conditional_map1(
    observed: dict[str, FloatArray],
    expected: dict[str, dict[str, FloatArray]],
    idx: np.ndarray[Any, Any],
) -> dict[str, Any]:
    """P(win the series | won map 1), the headline number and the one most
    easily misread.

    Because the map-1 winner is whichever side won it, this is the same
    quantity as the `map1_winner_won` rate — stated separately because it is the
    form the claim is usually made in, and stated beside three benchmarks
    because on its own it says nothing. Between identical teams a 1-0 lead is
    already worth the coin-flip column; between these teams, at these ratings,
    it is worth the rating column; and allowing for what the ratings did not
    know, the quality column.
    """
    o = observed["map1_winner_won"]
    return {
        "observed": round(float(o.mean()), 4),
        "coin_flip": round(_coin_flip_map1(), 4),
        "vs": {b: _paired_gap(o, expected[b]["map1_winner_won"], idx) for b in BENCHMARKS},
        "note": "the coin-flip column is what a 1-0 lead is worth between two"
        " identical teams — the purely arithmetic part of the number",
    }


def _coin_flip_map1(wins_needed: int = WINS_NEEDED) -> float:
    return expected_events([0.5] * (2 * wins_needed - 1), wins_needed)["map1_winner_won"]


def _strength_check(series: Sequence[Series], frozen: Frozen) -> dict[str, Any]:
    """Are the frozen probabilities calibrated on the one map with no selection?

    Everything expected rests on those probabilities, and an overconfident
    rating would inflate the expected sweep rate and understate the gap. Map 1
    is the honest place to check: every series plays it, so the set is not
    conditioned on any result. Maps 4 and 5 exist only in series that were
    close, which is why they are not used here.
    """
    ps: list[float] = []
    ys: list[float] = []
    for s in series:
        rot = frozen.rotation_ps.get(s.id)
        if rot is None:
            continue
        ps.append(rot[0])
        ys.append(1.0 if s.maps[0].team1_won else 0.0)
    if not ps:
        return {"available": False, "reason": "no series with a known rotation"}
    p = np.array(ps)
    y = np.array(ys)
    # Slope of observed on predicted in logit space: 1.0 is calibrated, below
    # 1.0 is overconfident. Fitted with no penalty for the same reason the
    # momentum fit has none.
    fit = fit_logistic_l2(np.array([[_logit(v)] for v in ps]), y, l2=0.0)
    return {
        "available": True,
        "n_maps": len(ps),
        "mean_predicted": round(float(p.mean()), 4),
        "observed": round(float(y.mean()), 4),
        "brier": round(float(np.mean((p - y) ** 2)), 5),
        "calibration_slope": round(float(fit.weights[0]), 3),
        "note": "map 1 only — every series plays it, so this set is not conditioned"
        " on a result the way maps 4 and 5 are",
    }


# --------------------------------------------- unmeasured quality, and the fit
#
# The regression below has one serious rival explanation, and it is not a
# technicality — it is the whole difficulty of the question.
#
# A team that wins map 1 is, on the evidence of having won it, better than its
# rating said. The rating is a noisy summary of a roster that changes, plays
# through illness, prepares harder for a rival; the archive's own map-1
# calibration slope comes out above 1, which is the rating saying its own
# spreads are too narrow. So the map-1 winner goes on to win map 2 more often
# than a *rating-based* independence calculation predicts, with no memory of any
# kind between the maps. Every quantity on this page has that problem, and a
# regression on the rating alone cannot tell the two apart: both put a positive
# coefficient on the previous map.
#
# What separates them is *shape*, not size. Quality a rating missed is a
# property of the series: it is shared by every map equally, no matter which
# order they are played in. Carryover is a property of adjacency: it links map 2
# to map 1 and map 3 to map 2, and it is what remains once the shared part is
# removed. So the model is fitted with both — a per-series latent offset drawn
# from a normal, and a lag-1 term — over the whole sequence of maps at once:
#
#     P(team 1 wins map m) = sigmoid(a · strength_m + u_series + gamma · prev_m)
#     u_series ~ Normal(0, sigma²)
#
# with u integrated out by Gauss-Hermite quadrature. `sigma` is everything the
# rating did not know about these two teams on this day; `gamma` is what is left
# for carryover, and it is the number this module exists to report.
#
# Two details make the estimate honest rather than merely fashionable:
#
#   * **The first map is modelled, not conditioned on.** Fitting a lag model to
#     maps 2 and later while treating map 1 as given is the initial-conditions
#     trap: map 1's result is itself informative about `u`, so conditioning on
#     it feeds the latent quality straight into the lag term. The likelihood
#     here covers the whole sequence from map 1, so the correlation between the
#     first result and `u` is part of the model instead of a bias in it.
#   * **The stopping rule is ignorable.** A series ends when a side reaches
#     three wins, and that is a deterministic function of results already in the
#     likelihood, so truncating the sequence there is not selection.

# Nodes for the quadrature over the latent offset. The integrand is a smooth
# bounded function of a normal, which is what Gauss-Hermite is exact on; 24
# nodes moves the fitted parameters by under 1e-6 against 48.
GH_NODES = 24

# Nelder-Mead, on three parameters. Written out rather than imported because the
# package depends on numpy and psycopg and nothing else, and a simplex on three
# smooth parameters is thirty lines.
NM_MAX_ITER = 2000
NM_TOL = 1e-9

# 95% for a likelihood-ratio interval on one parameter: the profile drops by
# half a chi-square(1) quantile at the bounds.
CHI2_1_95 = 3.841459

# How finely the profile bound is bracketed, in logits.
PROFILE_TOL = 1e-3


@dataclass(frozen=True)
class Sequences:
    """Every series' maps as one padded block, ready for the quadrature.

    Rows are series, columns are maps in play order, and `mask` marks the maps
    a series actually played — a best-of-five stops at three, four or five, and
    padding is how one array holds all three shapes.
    """

    x: FloatArray  # strength logit per map
    y: FloatArray  # 1 where team 1 won
    prev: FloatArray  # +1/−1 for the previous map's winner, 0 on map 1
    mask: FloatArray  # 1 where the map was played

    @property
    def n_series(self) -> int:
        return int(self.x.shape[0])

    def take(self, rows: np.ndarray[Any, Any]) -> Sequences:
        return Sequences(x=self.x[rows], y=self.y[rows], prev=self.prev[rows], mask=self.mask[rows])

    def head(self, n_maps: int) -> Sequences:
        """The first `n_maps` maps of every series.

        At three this is the one perfectly balanced panel the archive contains:
        a best-of-five needs three wins, so every series plays maps 1, 2 and 3
        and none of them is present because of how the earlier ones went. Maps 4
        and 5 exist only in series that were close, and while the stopping rule
        is ignorable for the likelihood, a fit that never touches it cannot be
        argued with on those grounds at all.
        """
        return Sequences(
            x=self.x[:, :n_maps],
            y=self.y[:, :n_maps],
            prev=self.prev[:, :n_maps],
            mask=self.mask[:, :n_maps],
        )


def sequences(series: Sequence[Series], frozen: Frozen) -> Sequences:
    usable = [s for s in series if s.id in frozen.played_ps]
    width = max(len(s.maps) for s in usable)
    n = len(usable)
    x = np.zeros((n, width))
    y = np.zeros((n, width))
    prev = np.zeros((n, width))
    mask = np.zeros((n, width))
    for i, s in enumerate(usable):
        ps = frozen.played_ps[s.id]
        for j, m in enumerate(s.maps):
            x[i, j] = _logit(ps[j])
            y[i, j] = 1.0 if m.team1_won else 0.0
            mask[i, j] = 1.0
            if j > 0:
                prev[i, j] = 1.0 if s.maps[j - 1].team1_won else -1.0
    return Sequences(x=x, y=y, prev=prev, mask=mask)


def _gauss_hermite(n: int = GH_NODES) -> tuple[FloatArray, FloatArray]:
    """Nodes and weights for E[f(u)], u ~ Normal(0, 1)."""
    nodes, weights = np.polynomial.hermite_e.hermegauss(n)
    return np.asarray(nodes, dtype=float), np.asarray(weights / math.sqrt(2.0 * math.pi))


def series_loglik(seqs: Sequences, a: float, sigma: float, gamma: float) -> float:
    """Log-likelihood of every series, latent quality integrated out.

    Each series contributes one term: the average over the latent offset of the
    probability of its whole sequence of results. That is what makes the series
    the independent unit here — no clustering correction is bolted on afterwards
    because none is needed.
    """
    nodes, weights = _gauss_hermite()
    # (series, map, node)
    eta = (a * seqs.x + gamma * seqs.prev)[:, :, None] + abs(sigma) * nodes[None, None, :]
    # log Bernoulli, written through logaddexp so a confident node cannot
    # overflow before the mask has a chance to zero it out.
    ll_map = seqs.y[:, :, None] * eta - np.logaddexp(0.0, eta)
    ll_series = (ll_map * seqs.mask[:, :, None]).sum(axis=1)
    mixed = np.log(np.maximum((np.exp(ll_series) * weights[None, :]).sum(axis=1), 1e-300))
    return float(mixed.sum())


def _nelder_mead(
    objective: Any,
    start: Sequence[float],
    step: float = 0.25,
    max_iter: int = NM_MAX_ITER,
    tol: float = NM_TOL,
) -> tuple[FloatArray, float]:
    """Minimize `objective` over a small vector. Returns (argmin, min)."""
    k = len(start)
    simplex = [np.array(start, dtype=float)]
    for i in range(k):
        point = np.array(start, dtype=float)
        point[i] += step if point[i] == 0.0 else step * abs(point[i])
        simplex.append(point)
    values = [float(objective(p)) for p in simplex]

    for _ in range(max_iter):
        order = np.argsort(values)
        simplex = [simplex[i] for i in order]
        values = [values[i] for i in order]
        if abs(values[-1] - values[0]) <= tol * (abs(values[0]) + tol):
            break
        centroid = np.mean(simplex[:-1], axis=0)
        reflected = centroid + (centroid - simplex[-1])
        f_r = float(objective(reflected))
        if f_r < values[0]:
            expanded = centroid + 2.0 * (centroid - simplex[-1])
            f_e = float(objective(expanded))
            simplex[-1], values[-1] = (expanded, f_e) if f_e < f_r else (reflected, f_r)
        elif f_r < values[-2]:
            simplex[-1], values[-1] = reflected, f_r
        else:
            contracted = centroid + 0.5 * (simplex[-1] - centroid)
            f_c = float(objective(contracted))
            if f_c < values[-1]:
                simplex[-1], values[-1] = contracted, f_c
            else:
                for i in range(1, k + 1):
                    simplex[i] = simplex[0] + 0.5 * (simplex[i] - simplex[0])
                    values[i] = float(objective(simplex[i]))
    best = int(np.argmin(values))
    return simplex[best], values[best]


@dataclass(frozen=True)
class Mixed:
    a: float
    sigma: float
    gamma: float
    loglik: float


def fit_mixed(seqs: Sequences, with_gamma: bool = True, start: Mixed | None = None) -> Mixed:
    """Maximum likelihood for (a, sigma, gamma), or for (a, sigma) with gamma
    pinned at zero — the null the momentum term is tested against."""
    s = start or Mixed(a=1.0, sigma=0.5, gamma=0.0, loglik=0.0)
    if with_gamma:

        def negative(p: FloatArray) -> float:
            return -series_loglik(seqs, float(p[0]), float(p[1]), float(p[2]))

        best, value = _nelder_mead(negative, [s.a, s.sigma, s.gamma])
        return Mixed(
            a=float(best[0]), sigma=abs(float(best[1])), gamma=float(best[2]), loglik=-value
        )

    def negative_null(p: FloatArray) -> float:
        return -series_loglik(seqs, float(p[0]), float(p[1]), 0.0)

    best, value = _nelder_mead(negative_null, [s.a, s.sigma])
    return Mixed(a=float(best[0]), sigma=abs(float(best[1])), gamma=0.0, loglik=-value)


def profile_interval(seqs: Sequences, best: Mixed, which: str = "gamma") -> tuple[float, float]:
    """Likelihood-ratio interval: the values of one parameter whose best fit,
    with the others free, costs half a chi-square(1) quantile of log-likelihood.

    Preferred here over resampling because the likelihood already treats a
    series as one observation, and because a bootstrap of a three-parameter
    simplex fit is thousands of optimizations for an interval this returns from
    about twenty.
    """
    target = best.loglik - CHI2_1_95 / 2.0

    def profile(value: float) -> float:
        if which == "gamma":

            def negative(p: FloatArray) -> float:
                return -series_loglik(seqs, float(p[0]), float(p[1]), value)

            _, neg = _nelder_mead(negative, [best.a, best.sigma])
        else:

            def negative(p: FloatArray) -> float:
                return -series_loglik(seqs, float(p[0]), value, float(p[1]))

            _, neg = _nelder_mead(negative, [best.a, best.gamma])
        return -neg

    centre = best.gamma if which == "gamma" else best.sigma

    def bound(direction: int) -> float:
        step = max(abs(centre), 0.1) * 0.5
        far = centre
        for _ in range(20):
            far = far + direction * step
            if which == "sigma" and far <= 0.0:
                return 0.0
            if profile(far) < target:
                break
            step *= 1.6
        else:
            return far
        lo, hi = (centre, far) if direction > 0 else (far, centre)
        # Bisect to a thousandth of a logit, which is three digits past anything
        # this interval is ever quoted to, and stop — each step is a full refit.
        while hi - lo > PROFILE_TOL:
            mid = 0.5 * (lo + hi)
            if profile(mid) >= target:
                lo, hi = (mid, hi) if direction > 0 else (lo, mid)
            else:
                lo, hi = (lo, mid) if direction > 0 else (mid, hi)
        return hi if direction > 0 else lo

    return bound(-1), bound(+1)


def _arm(seqs: Sequences, label: str) -> dict[str, Any]:
    """One fitted pair of models on one set of sequences."""
    null = fit_mixed(seqs, with_gamma=False)
    full = fit_mixed(seqs, with_gamma=True, start=null)
    lo, hi = profile_interval(seqs, full, "gamma")
    lr = 2.0 * (full.loglik - null.loglik)
    return {
        "arm": label,
        "n_series": seqs.n_series,
        "n_maps": int(seqs.mask.sum()),
        "a": round(full.a, 4),
        "sigma": round(full.sigma, 4),
        "gamma": round(full.gamma, 4),
        "gamma_lo": round(lo, 4),
        "gamma_hi": round(hi, 4),
        "excludes_zero": bool(lo > 0.0 or hi < 0.0),
        "lr_stat": round(lr, 3),
        "p": round(2.0 * (1.0 - _phi(math.sqrt(max(lr, 0.0)))), 4),
        "swing_pp": round(_swing_pp(full.gamma), 2),
        "swing_pp_lo": round(_swing_pp(lo), 2),
        "swing_pp_hi": round(_swing_pp(hi), 2),
    }


def latent_quality(seqs: Sequences) -> dict[str, Any]:
    """Fit the two models and report what separates them.

    The likelihood-ratio statistic is the whole result in one number: how much
    better the sequence of map results is explained once adjacency is allowed,
    over and above a series-wide quality offset that explains the same maps in
    any order.
    """
    null = fit_mixed(seqs, with_gamma=False)
    full = fit_mixed(seqs, with_gamma=True, start=null)
    lo, hi = profile_interval(seqs, full, "gamma")
    s_lo, s_hi = profile_interval(seqs, full, "sigma")
    lr = 2.0 * (full.loglik - null.loglik)
    # One free parameter, so the LR statistic is the square of a z. Reported as
    # a two-sided p on that z rather than by looking up a chi-square, which is
    # the same number and easier to check by hand.
    z = math.sqrt(max(lr, 0.0))
    # What the archive could have resolved: the profile interval's half-width is
    # a likelihood-based standard error, and 80% power needs the usual multiple
    # of it.
    se = (hi - lo) / (2.0 * Z_ALPHA) if hi > lo else 0.0
    return {
        "available": True,
        "n_series": seqs.n_series,
        "n_maps": int(seqs.mask.sum()),
        "quadrature_nodes": GH_NODES,
        "null": {
            "a": round(null.a, 4),
            "sigma": round(null.sigma, 4),
            "loglik": round(null.loglik, 3),
        },
        "full": {
            "a": round(full.a, 4),
            "sigma": round(full.sigma, 4),
            "sigma_lo": round(s_lo, 4),
            "sigma_hi": round(s_hi, 4),
            "gamma": round(full.gamma, 4),
            "gamma_lo": round(lo, 4),
            "gamma_hi": round(hi, 4),
            "loglik": round(full.loglik, 3),
        },
        "excludes_zero": bool(lo > 0.0 or hi < 0.0),
        "lr_stat": round(lr, 3),
        "p": round(2.0 * (1.0 - _phi(z)), 4),
        "swing_pp": round(_swing_pp(full.gamma), 2),
        "swing_pp_lo": round(_swing_pp(lo), 2),
        "swing_pp_hi": round(_swing_pp(hi), 2),
        "mde80": round(POWER_FACTOR * se, 4) if se > 0 else None,
        "mde80_swing_pp": round(_swing_pp(POWER_FACTOR * se), 2) if se > 0 else None,
        # Sigma in the same unit as gamma: the map win probability between a
        # series where the favourite is one standard deviation better than its
        # rating said and one where it is a standard deviation worse.
        "sigma_swing_pp": round(_swing_pp(full.sigma), 2),
        # The same fit on maps 1-3, where a best-of-five has no stopping rule at
        # all: every series plays all three, so nothing about which maps are in
        # the sample depends on how the earlier ones went.
        "first_three": _arm(seqs.head(3), "maps 1-3, a complete panel"),
        "interpretation": "sigma is quality the rating did not have, shared by every map of"
        " the series in any order; gamma is what adjacency adds on top of it, which is the"
        " only part a momentum claim can own",
    }


# ------------------------------------------------------------ the momentum fit


# One row per map played after the first, and the columns every spec draws from.
# `prev` and `lead` are coded from team 1's side so the fit stays symmetric:
# with ±1 coding the intercept sits at zero when neither side is favoured.
@dataclass(frozen=True)
class MapRow:
    series_id: int
    ordinal: int
    strength: float  # logit of the frozen pre-series probability for this map
    prev: float  # +1 team 1 won the previous map, −1 team 2 did
    lead: float  # team 1 map wins minus team 2 map wins, before this map
    team1_won: float


def map_rows(series: Sequence[Series], frozen: Frozen) -> list[MapRow]:
    out: list[MapRow] = []
    for s in series:
        ps = frozen.played_ps.get(s.id)
        if ps is None:
            continue
        w1 = w2 = 0
        for i, m in enumerate(s.maps):
            if i > 0:
                out.append(
                    MapRow(
                        series_id=s.id,
                        ordinal=m.ordinal,
                        strength=_logit(ps[i]),
                        prev=1.0 if s.maps[i - 1].team1_won else -1.0,
                        lead=float(w1 - w2),
                        team1_won=1.0 if m.team1_won else 0.0,
                    )
                )
            if m.team1_won:
                w1 += 1
            else:
                w2 += 1
    return out


# What each spec regresses map outcome on. `prev_only` is the uncontrolled
# version and exists to show how much of the raw association is just the better
# team having won the previous map too.
_SPECS: dict[str, tuple[str, ...]] = {
    "strength_only": ("strength",),
    "prev_only": ("prev",),
    "strength_prev": ("strength", "prev"),
    "strength_lead": ("strength", "lead"),
}


def columns(rows: Sequence[MapRow]) -> dict[str, FloatArray]:
    return {
        "strength": np.array([r.strength for r in rows]),
        "prev": np.array([r.prev for r in rows]),
        "lead": np.array([r.lead for r in rows]),
    }


def design(rows: Sequence[MapRow], spec: str) -> FloatArray:
    cols = columns(rows)
    return np.column_stack([cols[name] for name in _SPECS[spec]])


def _swing_pp(beta: float) -> float:
    """A ±1 coefficient in points of win probability between the two states.

    Between evenly matched teams, `prev = +1` sits at sigmoid(beta) and
    `prev = −1` at sigmoid(−beta), so the whole effect of having won the
    previous map is the distance between them. Quoting beta alone invites
    reading a logit as a probability.
    """
    hi = 1.0 / (1.0 + math.exp(-beta))
    lo = 1.0 / (1.0 + math.exp(beta))
    return 100.0 * (hi - lo)


def _phi(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def fit_specs(
    rows: Sequence[MapRow],
    label: str,
    specs: Sequence[str] = tuple(_SPECS),
) -> dict[str, Any]:
    """Every spec fitted on one set of rows, with clustered bootstrap intervals.

    The resampling unit is the series, not the map. Maps of one series share two
    teams, one day and one venue, and the whole question is whether they also
    share something else; treating three maps as three independent observations
    would shrink the interval by roughly the square root of three and
    manufacture the effect being tested.

    The generator is built here from these rows rather than handed in. A shared
    one would make each call's intervals depend on how much had been drawn from
    it already — so adding a stage above, or reordering two below, would move
    numbers that no data supports.
    """
    if len(rows) < 100:
        return {"available": False, "reason": "too few maps", "n_maps": len(rows)}
    # Nothing is regularising this fit, so a column that never varies has no
    # coefficient to find and would come back out of numpy as a singular matrix.
    # The archive has never produced one; a future season with a single result
    # in it could.
    cols = columns(rows)
    used = {name for spec in specs for name in _SPECS[spec]}
    flat = [name for name in used if float(np.std(cols[name])) == 0.0]
    if flat:
        return {
            "available": False,
            "reason": f"constant column: {', '.join(sorted(flat))}",
            "n_maps": len(rows),
        }

    by_series: dict[int, list[int]] = defaultdict(list)
    for i, r in enumerate(rows):
        by_series[r.series_id].append(i)
    y = np.array([r.team1_won for r in rows], dtype=float)

    # Clusters ordered by what the series contains, not by when its id first
    # appeared in the rows: the draw picks cluster positions, and positions
    # handed out by a surrogate key move whenever the loader renumbers.
    named = sorted(used)

    def cluster_contents(members: list[int]) -> tuple[tuple[float, ...], ...]:
        return tuple(sorted((y[i], *(float(cols[n][i]) for n in named)) for i in members))

    clusters = [np.array(v, dtype=int) for v in sorted(by_series.values(), key=cluster_contents)]

    # Seeded from the same rows in the same content order, or the stream itself
    # would carry the arrival order the clusters were just freed from.
    rows_in_order = content_order([y, *(cols[n] for n in named)])
    rng = resample_stream(
        BOOTSTRAP_SEED, y[rows_in_order], *(cols[n][rows_in_order] for n in named)
    )
    idx = rng.integers(0, len(clusters), size=(BOOTSTRAP_B, len(clusters)))
    resamples = [np.concatenate([clusters[c] for c in row]) for row in idx]

    fitted: list[dict[str, Any]] = []
    for spec in specs:
        names = _SPECS[spec]
        x = design(rows, spec)
        fit = fit_logistic_l2(x, y, l2=L2)
        draws = np.array(
            [fit_logistic_l2(x[take], y[take], l2=L2).weights for take in resamples],
            dtype=float,
        )
        terms: list[dict[str, Any]] = []
        for j, name in enumerate(names):
            beta = float(fit.weights[j])
            col = draws[:, j]
            lo, hi = np.percentile(col, [2.5, 97.5])
            se = float(col.std(ddof=1))
            term: dict[str, Any] = {
                "term": name,
                "beta": round(beta, 4),
                "lo": round(float(lo), 4),
                "hi": round(float(hi), 4),
                "excludes_zero": bool(lo > 0.0 or hi < 0.0),
                "se": round(se, 4),
                "z": round(beta / se, 3) if se > 0 else None,
                "p": round(2.0 * (1.0 - _phi(abs(beta / se))), 4) if se > 0 else None,
                "mde80": round(POWER_FACTOR * se, 4) if se > 0 else None,
            }
            if name in ("prev", "lead"):
                # Only the memory terms get a points-of-win-probability reading;
                # `strength` is a calibration slope on a logit and has no such
                # interpretation.
                term["swing_pp"] = round(_swing_pp(beta), 2)
                if se > 0:
                    term["mde80_swing_pp"] = round(_swing_pp(POWER_FACTOR * se), 2)
            terms.append(term)
        fitted.append(
            {
                "spec": spec,
                "intercept": round(fit.intercept, 4),
                "converged": bool(fit.converged),
                "terms": terms,
            }
        )

    return {
        "available": True,
        "rows": label,
        "n_maps": len(rows),
        "n_series": len(clusters),
        "bootstrap_b": BOOTSTRAP_B,
        "cluster": "series — maps of one series are one cluster, not independent draws",
        "specs": fitted,
    }


# ----------------------------------------------------------------- artifacts


def _by_era(
    series: Sequence[Series],
    observed: dict[str, FloatArray],
    expected: dict[str, dict[str, FloatArray]],
) -> list[dict[str, Any]]:
    """The same table cut by title-season, for eras with enough series to say
    anything. Cohorts below the floor are still counted, never silently
    dropped."""
    order: list[str] = []
    where: dict[str, list[int]] = defaultdict(list)
    for i, s in enumerate(series):
        if s.era not in where:
            order.append(s.era)
        where[s.era].append(i)

    out: list[dict[str, Any]] = []
    for era in sorted(order, key=lambda e: (series[where[e][0]].year, e)):
        rows = np.array(where[era], dtype=int)
        cell: dict[str, Any] = {
            "era": era,
            "n_series": len(rows),
            "qualified": bool(len(rows) >= MIN_ERA_SERIES),
        }
        for key in EVENTS:
            o = observed[key][rows]
            lo, hi = _wilson(float(o.sum()), float(len(rows)))
            cell[key] = {
                "observed": round(float(o.mean()), 4),
                "observed_lo": round(lo, 4),
                "observed_hi": round(hi, 4),
                "expected": {b: round(float(expected[b][key][rows].mean()), 4) for b in BENCHMARKS},
            }
        out.append(cell)
    return out


def build_artifacts(
    conn: psycopg.Connection[tuple[object, ...]],
    lineage: dict[int, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Everything this model publishes, keyed by artifact name."""
    series, dropped = load(conn)
    if not series:
        return {}
    frozen = freeze(series, lineage=lineage)
    seqs = sequences(series, frozen)

    # The sequence model comes first because the descriptive table needs its
    # no-momentum fit: the second benchmark every rate is stated against is
    # "independence, at whatever strength gap best explains these results
    # without carryover", and that gap is exactly (a, sigma) from the null fit.
    quality = latent_quality(seqs)
    null_fit = (float(quality["null"]["a"]), float(quality["null"]["sigma"]))

    usable, observed, expected = _indicator_arrays(series, frozen, null_fit)
    if not usable:
        return {}

    # The series arrive in the order the loader emitted them, and the bootstrap
    # below draws positions in these columns — so every gap's interval was a
    # function of the ids underneath, and a reload that renumbered a few hundred
    # series would move all of them while no observed or expected rate moved.
    # Ordered by what each series produced, on every column at once so the
    # pairing that makes an observed-minus-expected gap legitimate survives it.
    take = content_order(
        [observed[k] for k in EVENTS] + [expected[b][k] for b in BENCHMARKS for k in EVENTS]
    )
    observed = {k: v[take] for k, v in observed.items()}
    expected = {b: {k: v[take] for k, v in cols.items()} for b, cols in expected.items()}

    rng = resample_stream(BOOTSTRAP_SEED, *(observed[k] for k in EVENTS))
    idx = rng.integers(0, len(usable), size=(BOOTSTRAP_B, len(usable)))

    rows = map_rows(usable, frozen)
    map2 = [r for r in rows if r.ordinal == 2]

    dynamics = {
        "scope": f"best-of-{2 * WINS_NEEDED - 1} series whose maps reconstruct their"
        " scoreline exactly; strength is the map-level Elo blend arm frozen before map 1",
        "benchmarks": {
            "coin_flip": "two identical teams — the arithmetic of a race to three",
            "rating": "the same teams at their frozen ratings, playing the league's mode"
            " rotation with no memory between maps, enumerated exactly over every scoreline",
            "quality": "the same enumeration, but at the strength gap that best explains"
            " these results with no carryover at all — the rating's own spreads are too"
            " narrow, and this column is what that alone is worth",
        },
        "n_series": len(usable),
        "n_series_loaded": len(series),
        "dropped": dropped,
        "n_no_rotation": frozen.n_no_rotation,
        "bootstrap_b": BOOTSTRAP_B,
        "map1": _conditional_map1(observed, expected, idx),
        "rates": _rates(observed, expected, idx),
        "by_era": _by_era(usable, observed, expected),
        "min_era_series": MIN_ERA_SERIES,
        "strength_check": _strength_check(usable, frozen),
    }

    momentum = {
        "question": "does the previous map's result predict the next one once the"
        " teams' strength is accounted for — and does it still, once the rating is"
        " allowed to have been wrong about their strength?",
        "coding": "prev is +1 when team 1 won the previous map and −1 when team 2 did,"
        " so the effect of having won it is the distance between sigmoid(beta) and"
        " sigmoid(−beta) between evenly matched teams",
        "l2": L2,
        "penalty_note": "unregularised: a ridge shrinks a coefficient toward zero, and"
        " zero is the hypothesis under test",
        "map2": fit_specs(
            map2,
            "map 2 only — one row per series",
            specs=("strength_only", "prev_only", "strength_prev"),
        ),
        "consecutive": fit_specs(rows, "every map after the first"),
        "quality": quality,
    }
    return {"series_dynamics": dynamics, "series_momentum": momentum}


def headline(dynamics: dict[str, Any], momentum: dict[str, Any]) -> str:
    """One line for the run log, and the shape the findings feed restates."""
    m1 = dynamics["map1"]
    quality = momentum["quality"]
    verdict = "carryover survives" if quality["excludes_zero"] else "no carryover"
    return (
        f"map-1 winner takes {m1['observed']:.1%} of series"
        f" (rating-only independence {m1['vs']['rating']['expected']:.1%},"
        f" with unmeasured quality {m1['vs']['quality']['expected']:.1%});"
        f" adjacency worth {quality['swing_pp']:+.1f} pt"
        f" [{quality['swing_pp_lo']:+.1f}, {quality['swing_pp_hi']:+.1f}] ({verdict})"
    )
