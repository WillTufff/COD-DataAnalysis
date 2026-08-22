"""Opponent adjustment for the box score. Spec: /methodology#opponent.

A player's cohort z-score treats a stat line farmed against the bottom of an
open bracket identically to one earned against the eventual champion. Nothing in
the published stack corrects for that: `era.py` and `player_rating.py` contain no
strength-of-opposition term, and the CoD community's own critique of K/D names
this as the first thing wrong with it.

**What this module adjusts, and what it does not.** The plus-minus already
conditions on opposition — the opposing four *are* the −1 columns of its design —
so nothing here touches it. What is adjusted is the **box score**: the per-map
rates that feed every published per-player statistic and, later, the prior that
predicts plus-minus from the box score. Reading this as "the plus-minus was
opponent-adjusted" would double-count the correction.

**The unit is the (numerator, denominator) pair, not the rate.** The metric layer
and the rating both sum numerators and denominators across maps and divide once,
never averaging per-map ratios. So an observation here is one player-map's
numerator over its denominator, weighted by that denominator, and an adjusted
season value is Σ adjusted numerator / Σ denominator. Exposure weighting falls
out of that rather than being bolted on: a map that ended early carries less of
the fit because it carries a smaller denominator.

**A ladder, not a choice.** Four rungs of increasing cost, each measured against
the one below, adopting the cheapest after which the leaderboard stops moving:

1. `team_rating` — residualize on the opposing *team's* walk-forward Glicko-2.
   Cheap, interpretable, and the baseline everything else has to beat.
2. `lineup_fe`   — own-player and opposing-*lineup* fixed effects. Where a
   substitute played, the team rating rates a lineup that was not on the server;
   30% of team-map sides are not their team-season's modal lineup, so this is
   not a rare correction.
3. `pooled_context` — the same design under a ridge, with teammate composition
   entering as its own block rather than being averaged over.
4. `shrunk` — empirical-Bayes shrinkage of the adjusted season values.

**Two identification facts, stated here rather than discovered later.**

*The coefficients are not identified; the adjustment is.* Every row carries one
own-player column and `side` opponent columns, so adding c to every own effect
and subtracting c/side from every opponent effect leaves every fitted value
unchanged — the same minimum-norm shift the plus-minus has, one level down. The
adjustment subtracts the opponent contribution *centred on the cohort*, and a
uniform shift moves every row's contribution by the same constant, so it cancels.
Coefficients are therefore published under an explicit sum-to-zero convention and
the adjustment is invariant to it.

*Kills are zero-sum and the two blocks are jointly determined.* A kill I take is
a death you take, so the own block and the opponent block are estimated from the
same events. The consequence is circularity rather than bias: player p's own
lines help estimate the opponent effects that p is then adjusted for. That is
what cross-fitting removes — the opponent block is estimated over five folds cut
on whole series, and every line is adjusted by effects fitted without its own
fold. The in-sample version is fitted alongside so the size of the circularity is
published instead of assumed away.

**Admission governs columns, never rows.** A player thin in a cohort does not get
an opponent column of their own — 2017's median player has seven maps and every
one of its 128 players is under twenty, so a career-scale threshold would pool a
whole season — and their slot joins a pooled replacement bucket instead. Their
own line is still adjusted. No map is ever dropped for thinness: dropping it
would discard a real result and bias the fit toward teams whose opponents
happened to be established.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from numpy.typing import NDArray

from ..maprows import MapRow
from ..regress import FloatArray
from . import player_rating as pr

MODEL = "opponent_adjust"
VERSION = "1.0.0"

# The ladder, cheapest first. `raw` is the unadjusted baseline every rung is
# measured against and is not itself an adjustment.
RUNG_RAW = "raw"
RUNG_TEAM = "team_rating"
RUNG_LINEUP = "lineup_fe"
RUNG_CONTEXT = "pooled_context"
RUNG_SHRUNK = "shrunk"
LADDER: tuple[str, ...] = (RUNG_RAW, RUNG_TEAM, RUNG_LINEUP, RUNG_CONTEXT, RUNG_SHRUNK)

# Maps a player needs inside a cohort to hold a column of their own. Below it
# their slot joins the replacement bucket. Set at the value the project already
# publishes qualification at, so "qualified" means one thing across the site.
ADMISSION_MAPS = 8

# A cohort smaller than this is not fitted at any rung above the first: the
# two-way design would carry more columns than the schedule can separate.
MIN_COHORT_ROWS = 200

# The two-way rung is solved unpenalized, on the pseudo-inverse. The blocks are
# jointly unidentified — 31 of a typical cohort's 115 columns carry no separable
# direction — and a small ridge is the worst of both worlds there: it leaves
# those directions in the solution at an eigenvalue near zero, so the inverse
# carries entries near a million and anything built on it explodes. The
# pseudo-inverse drops them instead, which is the minimum-norm convention this
# module publishes under and which the adjustment is invariant to anyway.
FE_RIDGE = 0.0

# Ridge for the pooled rung, before GCV moves it. The grid is geometric and
# wide: the right amount of pooling for 2017's seven-map players and 2025's
# sixty-map ones are two orders of magnitude apart.
POOL_GRID: tuple[float, ...] = tuple(float(10.0**k) for k in range(-3, 4))


@dataclass(frozen=True)
class Line:
    """One player's line on one map, with who else was on the server."""

    player_id: int
    team_id: int
    game_id: int
    series_id: int
    event_id: int
    season_id: int
    mode_id: int
    duration_s: float
    # <source_uid>#<ordinal>. Every ordering and every seed in this module reads
    # this rather than `game_id` or `series_id`, which the loader renumbers on
    # any reload that deletes and recreates rows.
    map_key: str
    opponents: tuple[int, ...]
    teammates: tuple[int, ...]
    # The opposing team's Glicko-2 rating as it stood before this series. None
    # where the series was never rated; still at the 1500 prior on 8.55% of
    # rows, where it carries no information about the opponent at all.
    opp_rating: float | None
    # feature key -> (numerator, denominator), denominators strictly positive.
    values: dict[str, tuple[float, float]]

    @property
    def team_season(self) -> tuple[int, int]:
        """The cluster standard errors are grouped by."""
        return (self.team_id, self.season_id)

    @property
    def series_key(self) -> str:
        """The series' own natural key, which the map key is built on."""
        return self.map_key.rsplit("#", 1)[0]


@dataclass(frozen=True)
class Panel:
    """One cohort's admitted lines and the feature set its title supports."""

    season_id: int
    mode_id: int
    mode_slug: str
    title: str
    side: int  # players per side on this cohort's maps
    lines: tuple[Line, ...]
    features: tuple[pr.Feature, ...]

    @property
    def key(self) -> tuple[int, int]:
        return (self.season_id, self.mode_id)

    @property
    def n_maps(self) -> int:
        return len({line.game_id for line in self.lines})


def _side_size(sizes: Mapping[int, int]) -> int:
    """The size both sides of a map agreed on, or 0 if they did not."""
    distinct = set(sizes.values())
    return distinct.pop() if len(distinct) == 1 else 0


def build_panels(
    rows: Sequence[MapRow],
    cohorts: Mapping[tuple[int, int], pr.Cohort],
    opp_rating: Mapping[tuple[int, int], float],
) -> dict[tuple[int, int], Panel]:
    """Group admitted player-map lines into one panel per cohort.

    `opp_rating` is keyed (series_id, team_id) and holds that team's rating as
    it stood before the series, so the value read for a line is the *opposing*
    team's entry. A map whose two sides are unequal or which does not carry two
    distinguishable teams is skipped, matching the lineup rule the plus-minus
    applies: a half-observed map is not an observation.
    """
    per_game: dict[str, list[MapRow]] = defaultdict(list)
    for row in rows:
        cohort = cohorts.get((row.season_id, row.mode_id))
        if cohort is not None and cohort.accepts(row) and row.map_key:
            per_game[row.map_key].append(row)

    lines: dict[tuple[int, int], list[Line]] = defaultdict(list)
    sides: dict[tuple[int, int], list[int]] = defaultdict(list)
    # Maps in natural-key order, so nothing downstream inherits an ordering the
    # loader assigned.
    for map_key in sorted(per_game):
        members = per_game[map_key]
        by_team: dict[int, list[MapRow]] = defaultdict(list)
        for member in members:
            by_team[member.team_id].append(member)
        if len(by_team) != 2:
            continue
        size = _side_size({team: len(rs) for team, rs in by_team.items()})
        if size == 0:
            continue
        cohort = cohorts[(members[0].season_id, members[0].mode_id)]
        for team in sorted(by_team):
            own = by_team[team]
            other = next(t for t in by_team if t != team)
            opponents = tuple(sorted(r.player_id for r in by_team[other]))
            for row in sorted(own, key=lambda r: r.player_id):
                values = {}
                for feature in cohort.features:
                    denominator = feature.denominator(row)
                    if denominator > 0.0:
                        values[feature.key] = (feature.numerator(row), denominator)
                if not values:
                    continue
                lines[cohort.key].append(
                    Line(
                        player_id=row.player_id,
                        team_id=team,
                        game_id=row.game_id,
                        series_id=row.series_id,
                        map_key=map_key,
                        event_id=row.event_id,
                        season_id=row.season_id,
                        mode_id=row.mode_id,
                        duration_s=row.duration_s,
                        opponents=opponents,
                        teammates=tuple(
                            sorted(r.player_id for r in own if r.player_id != row.player_id)
                        ),
                        opp_rating=opp_rating.get((row.series_id, other)),
                        values=values,
                    )
                )
            sides[cohort.key].append(size)

    out: dict[tuple[int, int], Panel] = {}
    for key, group in lines.items():
        cohort = cohorts[key]
        counts = sides[key]
        out[key] = Panel(
            season_id=cohort.season_id,
            mode_id=cohort.mode_id,
            mode_slug=cohort.mode_slug,
            title=cohort.title,
            side=max(set(counts), key=counts.count) if counts else 0,
            lines=tuple(group),
            features=cohort.features,
        )
    return out


# ------------------------------------------------------------------ columns


REPLACEMENT = -1  # the pooled column a thin player's slot joins


@dataclass(frozen=True)
class Columns:
    """Which player holds which column, per block, for one panel.

    Each block indexes into the same design matrix. `own` is the line's own
    player, `opp` the players on the other side, `mate` the rest of their own
    side. A player thin in this cohort maps to the block's replacement column,
    which several players share.
    """

    own: dict[int, int]
    opp: dict[int, int]
    mate: dict[int, int]
    width: int
    admitted: frozenset[int]
    pooled: frozenset[int]

    def own_index(self, player_id: int) -> int:
        return self.own.get(player_id, self.own[REPLACEMENT])

    def opp_index(self, player_id: int) -> int:
        return self.opp.get(player_id, self.opp[REPLACEMENT])

    def mate_index(self, player_id: int) -> int:
        return self.mate.get(player_id, self.mate[REPLACEMENT])


def build_columns(panel: Panel, *, teammates: bool) -> Columns:
    """Assign columns, pooling players below the admission threshold.

    Column zero is the intercept. Every block carries a replacement column
    whether or not anyone lands in it, so the design's width is a function of
    the admitted set alone and two runs over the same data cannot differ in
    shape because a boundary player moved.
    """
    played: dict[int, int] = defaultdict(int)
    for line in panel.lines:
        played[line.player_id] += 1
    admitted = frozenset(p for p, n in played.items() if n >= ADMISSION_MAPS)
    pooled = frozenset(played) - admitted

    ordered = sorted(admitted)
    cursor = 1  # 0 is the intercept
    own: dict[int, int] = {}
    for player in ordered:
        own[player] = cursor
        cursor += 1
    own[REPLACEMENT] = cursor
    cursor += 1
    opp: dict[int, int] = {}
    for player in ordered:
        opp[player] = cursor
        cursor += 1
    opp[REPLACEMENT] = cursor
    cursor += 1
    mate: dict[int, int] = {}
    if teammates:
        for player in ordered:
            mate[player] = cursor
            cursor += 1
        mate[REPLACEMENT] = cursor
        cursor += 1
    return Columns(
        own=own,
        opp=opp,
        mate=mate,
        width=cursor,
        admitted=admitted,
        pooled=pooled,
    )


def design(panel: Panel, columns: Columns) -> FloatArray:
    """The two-way (or three-way) incidence matrix, one row per line.

    Opponent and teammate entries accumulate rather than being set, so a slot
    filled twice by the replacement column counts twice — which is what "two of
    the four opposing players were below the threshold" means.
    """
    matrix = np.zeros((len(panel.lines), columns.width), dtype=np.float64)
    matrix[:, 0] = 1.0
    for i, line in enumerate(panel.lines):
        matrix[i, columns.own_index(line.player_id)] += 1.0
        for opponent in line.opponents:
            matrix[i, columns.opp_index(opponent)] += 1.0
        if columns.mate:
            for mate in line.teammates:
                matrix[i, columns.mate_index(mate)] += 1.0
    return matrix


def response(panel: Panel, feature_key: str) -> tuple[FloatArray, FloatArray, FloatArray]:
    """(rate, weight, mask) for one feature over the panel's lines.

    The mask marks lines carrying this feature at all; a line whose denominator
    was zero never entered `values` and is excluded from that feature's fit
    while still belonging to the panel.
    """
    n = len(panel.lines)
    rate = np.zeros(n, dtype=np.float64)
    weight = np.zeros(n, dtype=np.float64)
    mask = np.zeros(n, dtype=bool)
    for i, line in enumerate(panel.lines):
        pair = line.values.get(feature_key)
        if pair is None:
            continue
        numerator, denominator = pair
        rate[i] = numerator / denominator
        weight[i] = denominator
        mask[i] = True
    return rate, weight, mask


# ------------------------------------------------------------------- solving

# Glicko-2's starting rating. A team still sitting on it has played nothing the
# rating could learn from, so its "rating" is the prior rather than a
# measurement, and residualizing on it would read "unknown" as "average".
RATING_PRIOR = 1500.0


@dataclass(frozen=True)
class Wls:
    """A weighted ridge fit, with what the jackknife and the sandwich need."""

    beta: FloatArray
    inv: FloatArray  # (XᵀWX + λI)⁻¹
    residual: FloatArray
    trace_hat: float
    n: int


def solve_wls(
    x: FloatArray, y: FloatArray, w: FloatArray, ridge: float, *, penalize_intercept: bool = False
) -> Wls:
    """Minimize Σ wᵢ(yᵢ − xᵢβ)² + λ‖β‖², with the intercept left unpenalized."""
    xw = x * w[:, None]
    gram = x.T @ xw
    penalty = np.eye(gram.shape[0]) * ridge
    if not penalize_intercept:
        penalty[0, 0] = 0.0
    inv = np.linalg.pinv(gram + penalty, hermitian=True)
    beta = inv @ (xw.T @ y)
    return Wls(
        beta=beta,
        inv=inv,
        residual=y - x @ beta,
        trace_hat=float(np.trace(inv @ gram)),
        n=len(y),
    )


def gcv(fit: Wls, w: FloatArray) -> float:
    """Generalized cross-validation for a weighted ridge, ∞ once df reaches n."""
    weighted_rss = float(np.sum(w * fit.residual**2)) / max(float(np.sum(w)), 1e-12)
    slack = 1.0 - fit.trace_hat / fit.n
    return weighted_rss / (slack * slack) if slack > 1e-9 else math.inf


def cluster_cov(x: FloatArray, w: FloatArray, fit: Wls, clusters: Sequence[Any]) -> FloatArray:
    """Cluster-robust covariance, grouped by whatever `clusters` labels rows with.

    Maps within a series share a lineup, a day, a patch and an opponent, and a
    team's season shares all of that plus a roster, so the residuals are not
    independent at either level. Grouping the score contributions by team-season
    is the coarser of the two and the one the ladder reports.
    """
    groups: dict[Any, list[int]] = defaultdict(list)
    for i, label in enumerate(clusters):
        groups[label].append(i)
    meat = np.zeros((x.shape[1], x.shape[1]), dtype=np.float64)
    for rows in groups.values():
        idx = np.asarray(rows, dtype=np.int64)
        score = x[idx].T @ (w[idx] * fit.residual[idx])
        meat += np.outer(score, score)
    scale = len(groups) / max(len(groups) - 1, 1)
    covariance: FloatArray = scale * (fit.inv @ meat @ fit.inv)
    return covariance


# How many folds the opponent block is cross-fitted over. Each fold's lines are
# adjusted by effects estimated on the other four, so no line contributes to the
# opponent quality it is adjusted for.
#
# Leave-one-series-out was the first design and it is not usable on this record:
# the incidence design is already rank deficient — 31 of 115 columns carry no
# identified direction in a typical CDL Hardpoint cohort, because thin players
# and once-seen lineups pin nothing — and removing a single series makes more
# columns unidentified still. The exact downdate then divides by a singular
# matrix and the correction inflates by a factor of forty. Five folds keep every
# fit full-sized and well-posed while removing the same circularity.
CROSSFIT_FOLDS = 5


def crossfit(
    x: FloatArray,
    y: FloatArray,
    w: FloatArray,
    ridge: float,
    folds: Sequence[int],
    selector: FloatArray,
) -> FloatArray:
    """Per-row `selector`-weighted contribution, fitted without that row's fold.

    `selector` marks the columns whose contribution is wanted — the opponent
    block — so the returned value is what the opposition was worth on each line,
    estimated from lines the row itself had no part in.
    """
    out = np.zeros(len(y), dtype=np.float64)
    groups = sorted(set(folds))
    labels = np.asarray(folds)
    for fold in groups:
        holdout = labels == fold
        if not holdout.any() or holdout.all():
            continue
        trained = solve_wls(x[~holdout], y[~holdout], w[~holdout], ridge)
        out[holdout] = (x[holdout] * selector) @ trained.beta
    return out


def fold_of(keys: Sequence[str], folds: int = CROSSFIT_FOLDS) -> list[int]:
    """Assign each row a fold from its series' natural key, in sorted order."""
    order_of = {key: index for index, key in enumerate(sorted(set(keys)))}
    return [order_of[key] % folds for key in keys]


# ------------------------------------------------------------------- rungs


@dataclass(frozen=True)
class Adjustment:
    """What one rung did to one feature in one cohort.

    `delta` is what the rung subtracts from each line's rate — the opponent
    contribution centred on the cohort, so a line against exactly average
    opposition is left alone and the cohort's weighted mean is unchanged by
    construction. Lines the rung could not reach carry a delta of zero and are
    counted rather than dropped.
    """

    rung: str
    feature: str
    key: tuple[int, int]
    delta: FloatArray
    fitted: FloatArray  # bool mask: lines that entered this rung's fit
    reached: int  # lines carrying a non-zero delta
    ridge: float | None = None
    slope: float | None = None  # rate units per rating point, rung 1 only
    slope_se: float | None = None
    delta_sd: float | None = None
    trace_hat: float | None = None
    criterion: float | None = None
    circularity: float | None = None  # sd of (in-sample delta − jackknifed delta)


def _weighted_mean(values: FloatArray, weights: FloatArray) -> float:
    total = float(np.sum(weights))
    return float(np.sum(values * weights) / total) if total > 0.0 else 0.0


def _usable_rating(line: Line) -> bool:
    """A rating that measured something. The prior is not a measurement."""
    return line.opp_rating is not None and abs(line.opp_rating - RATING_PRIOR) > 1e-9


def adjust_team_rating(panel: Panel, feature_key: str) -> Adjustment:
    """Rung 1: residualize the rate on the opposing team's pre-series rating."""
    rate, weight, mask = response(panel, feature_key)
    rated = np.array([_usable_rating(line) for line in panel.lines], dtype=bool)
    fitted = mask & rated
    delta = np.zeros(len(rate), dtype=np.float64)
    if int(fitted.sum()) < 2:
        return Adjustment(
            rung=RUNG_TEAM,
            feature=feature_key,
            key=panel.key,
            delta=delta,
            fitted=fitted,
            reached=0,
        )
    ratings = np.array(
        [line.opp_rating if line.opp_rating is not None else 0.0 for line in panel.lines]
    )
    centre = _weighted_mean(ratings[fitted], weight[fitted])
    x = np.column_stack([np.ones(int(fitted.sum())), ratings[fitted] - centre])
    fit = solve_wls(x, rate[fitted], weight[fitted], 0.0)
    clusters = [line.team_season for line, keep in zip(panel.lines, fitted, strict=True) if keep]
    covariance = cluster_cov(x, weight[fitted], fit, clusters)
    delta[fitted] = fit.beta[1] * (ratings[fitted] - centre)
    return Adjustment(
        rung=RUNG_TEAM,
        feature=feature_key,
        key=panel.key,
        delta=delta,
        fitted=fitted,
        reached=int(np.count_nonzero(delta)),
        slope=float(fit.beta[1]),
        slope_se=float(math.sqrt(max(covariance[1, 1], 0.0))),
        delta_sd=float(np.std(delta[fitted])),
    )


def _fe_adjustment(
    panel: Panel,
    feature_key: str,
    columns: Columns,
    matrix: FloatArray,
    rung: str,
    ridge: float,
    *,
    cross_fit: bool,
) -> Adjustment:
    """Fit the incidence design and centre the opponent block's contribution."""
    rate, weight, mask = response(panel, feature_key)
    delta = np.zeros(len(rate), dtype=np.float64)
    if int(mask.sum()) < matrix.shape[1] + 2:
        return Adjustment(
            rung=rung, feature=feature_key, key=panel.key, delta=delta, fitted=mask, reached=0
        )
    x, y, w = matrix[mask], rate[mask], weight[mask]
    fit = solve_wls(x, y, w, ridge)

    opp_columns = np.zeros(matrix.shape[1], dtype=np.float64)
    for index in columns.opp.values():
        opp_columns[index] = 1.0
    contribution = (x * opp_columns) @ fit.beta

    circularity: float | None = None
    if cross_fit:
        series = [line.series_key for line, keep in zip(panel.lines, mask, strict=True) if keep]
        out_of_sample = crossfit(x, y, w, ridge, fold_of(series), opp_columns)
        circularity = float(np.std(contribution - out_of_sample))
        contribution = out_of_sample

    centred = contribution - _weighted_mean(contribution, w)
    delta[mask] = centred
    return Adjustment(
        rung=rung,
        feature=feature_key,
        key=panel.key,
        delta=delta,
        fitted=mask,
        reached=int(np.count_nonzero(delta)),
        ridge=ridge,
        delta_sd=float(np.std(centred)),
        trace_hat=fit.trace_hat,
        criterion=gcv(fit, w),
        circularity=circularity,
    )


def adjust_lineup_fe(
    panel: Panel,
    feature_key: str,
    columns: Columns,
    matrix: FloatArray,
    *,
    cross_fit: bool = True,
) -> Adjustment:
    """Rung 2: own-player and opposing-lineup fixed effects, barely penalized."""
    return _fe_adjustment(
        panel, feature_key, columns, matrix, RUNG_LINEUP, FE_RIDGE, cross_fit=cross_fit
    )


def adjust_pooled(
    panel: Panel,
    feature_key: str,
    columns: Columns,
    matrix: FloatArray,
    *,
    cross_fit: bool = True,
    grid: Sequence[float] = POOL_GRID,
) -> Adjustment:
    """Rung 3: the same design plus teammates, pooled at a ridge GCV chooses."""
    rate, weight, mask = response(panel, feature_key)
    if int(mask.sum()) < matrix.shape[1] + 2:
        return Adjustment(
            rung=RUNG_CONTEXT,
            feature=feature_key,
            key=panel.key,
            delta=np.zeros(len(rate)),
            fitted=mask,
            reached=0,
        )
    x, y, w = matrix[mask], rate[mask], weight[mask]
    best = min(grid, key=lambda ridge: gcv(solve_wls(x, y, w, ridge), w))
    return _fe_adjustment(
        panel, feature_key, columns, matrix, RUNG_CONTEXT, best, cross_fit=cross_fit
    )


# ------------------------------------------------------- season aggregation

# Maps a player needs in a cohort to appear on that cohort's leaderboard. The
# same floor the era adjustment and the metric layer publish at.
QUALIFY_MAPS = 8

# How many places the top-of-table churn is measured over.
TOP_N = 10


@dataclass(frozen=True)
class SeasonValue:
    """One player's adjusted season rate for one feature in one cohort."""

    player_id: int
    key: tuple[int, int]
    feature: str
    value: float
    denom: float
    maps: int


def aggregate(panel: Panel, feature_key: str, delta: FloatArray | None = None) -> list[SeasonValue]:
    """Sum numerators and denominators per player, then divide once.

    With `delta` given, each line's numerator is its adjusted rate times its own
    denominator, so the season value is the rate the player would have posted
    against cohort-average opposition at the same exposure.
    """
    rate, weight, mask = response(panel, feature_key)
    shift = np.zeros(len(rate)) if delta is None else delta
    numerator: dict[int, float] = defaultdict(float)
    denominator: dict[int, float] = defaultdict(float)
    maps: dict[int, int] = defaultdict(int)
    for i, line in enumerate(panel.lines):
        if not mask[i]:
            continue
        numerator[line.player_id] += (rate[i] - shift[i]) * weight[i]
        denominator[line.player_id] += weight[i]
        maps[line.player_id] += 1
    return [
        SeasonValue(
            player_id=player,
            key=panel.key,
            feature=feature_key,
            value=numerator[player] / denominator[player],
            denom=denominator[player],
            maps=maps[player],
        )
        for player in sorted(numerator)
        if denominator[player] > 0.0
    ]


def leaderboard(values: Sequence[SeasonValue]) -> dict[int, float]:
    """Qualified players' values as z-scores within their cohort.

    The unqualified are excluded from the moments rather than merely from the
    published table: a cohort's spread is what its regular players did, and
    letting a four-map line widen it would flatten everyone else's z-score.
    """
    qualified = [v for v in values if v.maps >= QUALIFY_MAPS]
    if len(qualified) < 2:
        return {}
    raw = np.array([v.value for v in qualified])
    spread = float(raw.std(ddof=1))
    if spread <= 0.0:
        return {}
    centre = float(raw.mean())
    return {v.player_id: (v.value - centre) / spread for v in qualified}


def _spearman(left: Sequence[float], right: Sequence[float]) -> float | None:
    """Rank correlation, None where either side has no spread to rank."""
    if len(left) < 3:
        return None
    a = np.argsort(np.argsort(np.asarray(left, dtype=float))).astype(float)
    b = np.argsort(np.argsort(np.asarray(right, dtype=float))).astype(float)
    if a.std() <= 0.0 or b.std() <= 0.0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


@dataclass(frozen=True)
class Movement:
    """How far one rung moved the leaderboard against the rung below it."""

    n_players: int
    spearman: float | None
    mean_abs_dz: float
    p95_abs_dz: float
    max_abs_dz: float
    top_n_churn: int
    biggest: tuple[tuple[int, float], ...]  # (player_id, Δz), largest first


def movement(before: Mapping[int, float], after: Mapping[int, float]) -> Movement:
    """Compare two leaderboards over the players both of them rank."""
    shared = sorted(set(before) & set(after))
    if not shared:
        return Movement(0, None, 0.0, 0.0, 0.0, 0, ())
    left = [before[p] for p in shared]
    right = [after[p] for p in shared]
    deltas = np.array(right) - np.array(left)
    top_before = {p for p, _ in sorted(before.items(), key=lambda kv: -kv[1])[:TOP_N]}
    top_after = {p for p, _ in sorted(after.items(), key=lambda kv: -kv[1])[:TOP_N]}
    order = sorted(zip(shared, deltas, strict=True), key=lambda kv: -abs(kv[1]))
    return Movement(
        n_players=len(shared),
        spearman=_spearman(left, right),
        mean_abs_dz=float(np.mean(np.abs(deltas))),
        p95_abs_dz=float(np.percentile(np.abs(deltas), 95)),
        max_abs_dz=float(np.max(np.abs(deltas))),
        top_n_churn=len(top_before - top_after),
        biggest=tuple((int(p), float(d)) for p, d in order[:5]),
    )


# ------------------------------------------------------------------ loading

_RATING_SQL = """
SELECT tr.series_id, tr.team_id, tr.rating_pre
FROM team_ratings tr
WHERE tr.run_id = (
  SELECT id FROM model_runs WHERE model = 'glicko2' ORDER BY id DESC LIMIT 1
)
"""


def load_ratings(conn: Any) -> dict[tuple[int, int], float]:
    """Each team's Glicko-2 rating as it stood before each series it played."""
    return {(int(r[0]), int(r[1])): float(r[2]) for r in conn.execute(_RATING_SQL).fetchall()}


# ------------------------------------------------------- rung 4: shrinkage


def pooled_within_variance(panel: Panel, feature_key: str, delta: FloatArray) -> tuple[float, int]:
    """σ̂², the per-map spread of the adjusted rate around each player's own mean.

    Pooled with the usual (mᵢ − 1) weighting, so a full season contributes more
    than three maps. Returns (σ̂², players with replication).
    """
    rate, _weight, mask = response(panel, feature_key)
    adjusted = rate - delta
    by_player: dict[int, list[float]] = defaultdict(list)
    for i, line in enumerate(panel.lines):
        if mask[i]:
            by_player[line.player_id].append(adjusted[i])
    ss = 0.0
    df = 0
    replicated = 0
    for series in by_player.values():
        if len(series) < 2:
            continue
        replicated += 1
        values = np.asarray(series)
        ss += float(((values - values.mean()) ** 2).sum())
        df += len(values) - 1
    return (ss / df if df > 0 else 0.0, replicated)


@dataclass(frozen=True)
class Shrunk:
    """Rung 4's output: the adjusted season values pulled toward their cohort."""

    values: tuple[SeasonValue, ...]
    mu: float
    tau2: float
    sigma2: float
    n_replicated: int
    mean_shrinkage: float


def shrink(panel: Panel, feature_key: str, delta: FloatArray) -> Shrunk | None:
    """Rung 4: empirical-Bayes shrinkage of one cohort's adjusted season values.

    A player's season is a noisy read of what they were worth, and the noise is
    larger for the player who played eight maps than for the one who played
    sixty. Shrinking each toward the cohort by its own sampling variance is the
    schedule-adjusted version of the same partial pooling the composite rating
    already publishes one level up.
    """
    from .hierarchical import empirical_bayes

    values = [v for v in aggregate(panel, feature_key, delta) if v.maps >= QUALIFY_MAPS]
    if len(values) < 2:
        return None
    sigma2, replicated = pooled_within_variance(panel, feature_key, delta)
    if sigma2 <= 0.0:
        return None
    x = np.array([v.value for v in values])
    v_i = np.array([sigma2 / max(v.maps, 1) for v in values])
    mu, tau2, posterior_means = empirical_bayes(x, v_i)
    shrinkage = tau2 / (tau2 + v_i) if tau2 > 0.0 else np.zeros_like(v_i)
    return Shrunk(
        values=tuple(
            SeasonValue(
                player_id=v.player_id,
                key=v.key,
                feature=v.feature,
                value=float(posterior_means[i]),
                denom=v.denom,
                maps=v.maps,
            )
            for i, v in enumerate(values)
        ),
        mu=float(mu),
        tau2=float(tau2),
        sigma2=float(sigma2),
        n_replicated=replicated,
        mean_shrinkage=float(np.mean(shrinkage)),
    )


# ------------------------------------------------------------- diagnostics


def opponent_graph(panel: Panel) -> dict[str, Any]:
    """Whether the schedule linked the players this cohort compares.

    An opponent effect is a difference between columns the schedule connected.
    Where the graph of "played against" falls into pieces, two players in
    different pieces are being compared through a chain that does not exist, and
    an adjustment across that seam is arithmetic rather than evidence.
    """
    from .graphs import graph_stats

    faced: dict[tuple[int, int], int] = defaultdict(int)
    nodes: set[int] = set()
    for line in panel.lines:
        nodes.add(line.player_id)
        for opponent in line.opponents:
            faced[(min(line.player_id, opponent), max(line.player_id, opponent))] += 1
    # Each pair is counted once per side, so the map count is halved back.
    pairs = [(left, right, max(maps // 2, 1)) for (left, right), maps in sorted(faced.items())]
    return graph_stats(pairs, sorted(nodes)).payload()


def split_halves(panel: Panel) -> tuple[list[int], list[int]]:
    """Line indexes split by whole series, alternating in played order.

    Maps inside a series share a lineup, a day, a patch and an opponent, so a
    split that cuts through one puts the same conditions on both sides and
    reports the reliability of a coin rather than of a season.
    """
    ordered = sorted({line.series_key for line in panel.lines})
    parity = {series: index % 2 for index, series in enumerate(ordered)}
    left = [i for i, line in enumerate(panel.lines) if parity[line.series_key] == 0]
    right = [i for i, line in enumerate(panel.lines) if parity[line.series_key] == 1]
    return (left, right)


def _half_values(
    panel: Panel, feature_key: str, delta: FloatArray, keep: Sequence[int]
) -> dict[int, tuple[float, int]]:
    rate, weight, mask = response(panel, feature_key)
    numerator: dict[int, float] = defaultdict(float)
    denominator: dict[int, float] = defaultdict(float)
    maps: dict[int, int] = defaultdict(int)
    for i in keep:
        if not mask[i]:
            continue
        line = panel.lines[i]
        numerator[line.player_id] += (rate[i] - delta[i]) * weight[i]
        denominator[line.player_id] += weight[i]
        maps[line.player_id] += 1
    return {
        player: (numerator[player] / denominator[player], maps[player])
        for player in numerator
        if denominator[player] > 0.0
    }


def reliability(
    panel: Panel, feature_key: str, delta: FloatArray, min_maps: int = 4
) -> float | None:
    """Correlation between a player's two half-season values, split on series.

    A correction that removes signal along with the opponent shows up here as a
    fall against the unadjusted number, which is the cheapest available check
    that the adjustment is not simply adding noise.
    """
    left, right = split_halves(panel)
    a = _half_values(panel, feature_key, delta, left)
    b = _half_values(panel, feature_key, delta, right)
    shared = sorted(p for p in set(a) & set(b) if a[p][1] >= min_maps and b[p][1] >= min_maps)
    if len(shared) < 5:
        return None
    x = np.array([a[p][0] for p in shared])
    y = np.array([b[p][0] for p in shared])
    if x.std() <= 0.0 or y.std() <= 0.0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


# -------------------------------------------------------------- the controls

# Draws for the placebo. The statistic is a standard deviation over thousands of
# lines, so its own sampling error is small and a few dozen shuffles resolve it.
PLACEBO_DRAWS = 24
PLACEBO_SEED = 20170811  # CWL Championship 2017, the first event in the archive


def placebo(
    panel: Panel,
    feature_key: str,
    columns: Columns,
    matrix: FloatArray,
    seed: int = PLACEBO_SEED,
    draws: int = PLACEBO_DRAWS,
    rung: str = RUNG_LINEUP,
) -> dict[str, Any]:
    """Permute which lineup a line faced and refit. The correction should vanish.

    Every lineup keeps its own composition and only which line it stood across
    from is destroyed, so what survives is what this design reports from a
    schedule carrying no information at all. The real correction has to stand
    clear of that to mean anything.

    The population is put in an order fixed by its own contents before any draw
    is taken — map natural key first, then the line's own rate and weight — so a
    reload that renumbers the loader's keys cannot move the result. Ties are
    lines carrying the same numbers on the same map, which no permutation
    statistic can tell apart.
    """
    from ..resample import order, stream

    def fit_once(target: Panel, target_matrix: FloatArray) -> Adjustment:
        if rung == RUNG_CONTEXT:
            return adjust_pooled(target, feature_key, columns, target_matrix, cross_fit=False)
        return adjust_lineup_fe(target, feature_key, columns, target_matrix, cross_fit=False)

    real = fit_once(panel, matrix)
    if real.delta_sd is None or not real.reached:
        return {"available": False, "reason": "the rung did not fit this feature"}

    rate, weight, mask = response(panel, feature_key)
    ranks = {key: index for index, key in enumerate(sorted({m.map_key for m in panel.lines}))}
    fixed = order(
        [
            np.array([float(ranks[line.map_key]) for line in panel.lines]),
            rate,
            weight,
        ]
    )
    generator = stream(seed, rate[mask], weight[mask])
    lineups = [panel.lines[int(i)].opponents for i in fixed]
    sizes: list[float] = []
    for _draw in range(draws):
        permutation = generator.permutation(len(lineups))
        shuffled = [tuple[int, ...]() for _ in panel.lines]
        for slot, donor in enumerate(permutation):
            shuffled[int(fixed[slot])] = lineups[int(donor)]
        fake = Panel(
            season_id=panel.season_id,
            mode_id=panel.mode_id,
            mode_slug=panel.mode_slug,
            title=panel.title,
            side=panel.side,
            lines=tuple(
                Line(
                    player_id=line.player_id,
                    team_id=line.team_id,
                    game_id=line.game_id,
                    series_id=line.series_id,
                    event_id=line.event_id,
                    season_id=line.season_id,
                    mode_id=line.mode_id,
                    duration_s=line.duration_s,
                    map_key=line.map_key,
                    opponents=opponents,
                    teammates=line.teammates,
                    opp_rating=line.opp_rating,
                    values=line.values,
                )
                for line, opponents in zip(panel.lines, shuffled, strict=True)
            ),
            features=panel.features,
        )
        fake_fit = fit_once(fake, design(fake, columns))
        if fake_fit.delta_sd is not None:
            sizes.append(fake_fit.delta_sd)
    if not sizes:
        return {"available": False, "reason": "no placebo draw fitted"}
    return {
        "available": True,
        "rung": rung,
        "draws": len(sizes),
        "real_sd": round(real.delta_sd, 6),
        "placebo_sd_mean": round(float(np.mean(sizes)), 6),
        "placebo_sd_max": round(float(np.max(sizes)), 6),
        # How many times larger the real correction is than shuffled opposition
        # produces. At or below 1 the adjustment is fitting noise.
        "ratio": round(real.delta_sd / float(np.mean(sizes)), 3) if np.mean(sizes) > 0 else None,
    }


def functional_form(panel: Panel, feature_key: str) -> dict[str, Any]:
    """Is rung 1's straight line the right shape, and is one slope enough?

    Three things the cheap rung assumes without testing. *Linearity* — compared
    against a four-bin step function of the same rating, by weighted residual
    sum of squares. *Exposure* — whether map duration carries anything the
    denominator has not already absorbed. *Homogeneity* — whether the slope
    differs between players above and below their cohort's median, which is what
    a role- or level-dependent matchup effect would look like.
    """
    rate, weight, mask = response(panel, feature_key)
    rated = np.array([_usable_rating(line) for line in panel.lines], dtype=bool)
    keep = mask & rated
    if int(keep.sum()) < 100:
        return {"available": False, "reason": "too few rated lines"}
    ratings = np.array([line.opp_rating or 0.0 for line in panel.lines])[keep]
    y, w = rate[keep], weight[keep]
    centre = _weighted_mean(ratings, w)

    linear = solve_wls(np.column_stack([np.ones(len(y)), ratings - centre]), y, w, 0.0)
    edges = np.quantile(ratings, [0.25, 0.5, 0.75])
    bins = np.digitize(ratings, edges)
    binned_x = np.column_stack([np.ones(len(y))] + [(bins == b).astype(float) for b in (1, 2, 3)])
    binned = solve_wls(binned_x, y, w, 0.0)

    def weighted_rss(residual: FloatArray) -> float:
        return float(np.sum(w * residual**2))

    out: dict[str, Any] = {
        "available": True,
        "n": int(keep.sum()),
        "linear_rss": round(weighted_rss(linear.residual), 6),
        "binned_rss": round(weighted_rss(binned.residual), 6),
        # Below 1 the step function fits better, i.e. the straight line is
        # leaving structure on the table.
        "linear_over_binned": round(
            weighted_rss(linear.residual) / max(weighted_rss(binned.residual), 1e-12), 4
        ),
    }

    durations = np.array([line.duration_s for line in panel.lines])[keep]
    if float(durations.std()) > 0.0:
        with_time = solve_wls(
            np.column_stack([np.ones(len(y)), ratings - centre, durations - durations.mean()]),
            y,
            w,
            0.0,
        )
        out["duration_slope"] = round(float(with_time.beta[2]), 8)
        out["rating_slope_without_duration"] = round(float(linear.beta[1]), 8)
        out["rating_slope_with_duration"] = round(float(with_time.beta[1]), 8)

    own = np.array([line.player_id for line in panel.lines])[keep]
    means = {player: float(np.mean(y[own == player])) for player in np.unique(own)}
    median = float(np.median(list(means.values())))
    upper = np.array([means[int(p)] > median for p in own])
    if 20 <= int(upper.sum()) <= len(y) - 20:
        interaction = np.column_stack(
            [
                np.ones(len(y)),
                ratings - centre,
                upper.astype(float),
                (ratings - centre) * upper.astype(float),
            ]
        )
        fit = solve_wls(interaction, y, w, 0.0)
        clusters = [line.team_season for line, k in zip(panel.lines, keep, strict=True) if k]
        covariance = cluster_cov(interaction, w, fit, clusters)
        se = math.sqrt(max(covariance[3, 3], 0.0))
        out["slope_gap_upper_minus_lower"] = round(float(fit.beta[3]), 8)
        out["slope_gap_se"] = round(se, 8)
        out["slope_gap_t"] = round(float(fit.beta[3] / se), 3) if se > 0 else None
    return out


# ------------------------------------------------------------- the ladder

# The stop rule, declared before the ladder is fitted rather than read off it.
#
# A rung is adopted only if it does something its predecessor did not, on both
# of two counts: it has to move the leaderboard by more than a hundredth of a
# cohort standard deviation on average, and its correction has to stand clear of
# what the same design reports from a shuffled schedule. A rung that moves
# numbers without clearing its own placebo is fitting noise, which is the
# failure this phase is most exposed to.
STOP_MEAN_ABS_DZ = 0.01
STOP_PLACEBO_RATIO = 1.5

# Features the bootstrap brackets. The whole ladder is refitted per draw, so the
# interval is bought for the two columns every cohort publishes rather than for
# all of them.
HEADLINE_FEATURES = ("kills_p10", "kills_pm", "deaths_p10", "deaths_pm")

BOOTSTRAP_B = 200
BOOTSTRAP_SEED = 20200124  # the CDL's first match day, as elsewhere in this plan


@dataclass(frozen=True)
class Fitted:
    """Every rung's delta for one feature in one cohort, and its diagnostics."""

    key: tuple[int, int]
    feature: str
    deltas: dict[str, FloatArray]
    adjustments: dict[str, Adjustment]
    boards: dict[str, dict[int, float]]
    reliability: dict[str, float | None]


def fit_feature(
    panel: Panel,
    feature_key: str,
    two_way: tuple[Columns, FloatArray],
    three_way: tuple[Columns, FloatArray],
) -> Fitted:
    """Run one feature up the whole ladder, keeping every rung's output."""
    columns2, matrix2 = two_way
    columns3, matrix3 = three_way
    zero = np.zeros(len(panel.lines), dtype=np.float64)

    adjustments = {
        RUNG_TEAM: adjust_team_rating(panel, feature_key),
        RUNG_LINEUP: adjust_lineup_fe(panel, feature_key, columns2, matrix2),
        RUNG_CONTEXT: adjust_pooled(panel, feature_key, columns3, matrix3),
    }
    deltas = {RUNG_RAW: zero, **{rung: a.delta for rung, a in adjustments.items()}}

    boards = {rung: leaderboard(aggregate(panel, feature_key, d)) for rung, d in deltas.items()}
    # Rung 4 shrinks rung 3's season values rather than the map-level rates, so
    # it has a board but no delta of its own.
    shrunk = shrink(panel, feature_key, deltas[RUNG_CONTEXT])
    boards[RUNG_SHRUNK] = leaderboard(shrunk.values) if shrunk else {}
    if shrunk is not None:
        adjustments[RUNG_SHRUNK] = Adjustment(
            rung=RUNG_SHRUNK,
            feature=feature_key,
            key=panel.key,
            delta=deltas[RUNG_CONTEXT],
            fitted=np.ones(len(panel.lines), dtype=bool),
            reached=len(shrunk.values),
            delta_sd=float(np.std([v.value for v in shrunk.values])) if shrunk.values else None,
            criterion=shrunk.mean_shrinkage,
        )
    return Fitted(
        key=panel.key,
        feature=feature_key,
        deltas=deltas,
        adjustments=adjustments,
        boards=boards,
        reliability={rung: reliability(panel, feature_key, d) for rung, d in deltas.items()},
    )


def cohort_spread(values: Sequence[SeasonValue]) -> float | None:
    """The spread the leaderboard's z-scores are measured in."""
    qualified = [v.value for v in values if v.maps >= QUALIFY_MAPS]
    if len(qualified) < 2:
        return None
    spread = float(np.std(qualified, ddof=1))
    return spread if spread > 0.0 else None


def schedule_softness(
    panel: Panel, feature_key: str, delta: FloatArray, spread: float
) -> dict[str, Any]:
    """How much of each event's box score came from the opposition it faced.

    The adjustment subtracts an amount per line; averaged over an event and
    divided by the cohort's own spread, that amount is what a player-map in that
    event was worth *because of who was across from it*, in the units the
    leaderboard is read in. A positive figure is a soft field.
    """
    _rate, weight, mask = response(panel, feature_key)
    by_event: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for i, line in enumerate(panel.lines):
        if mask[i]:
            by_event[line.event_id].append((delta[i], weight[i]))
    out: dict[str, Any] = {}
    for event_id, pairs in by_event.items():
        deltas = np.array([d for d, _ in pairs])
        weights = np.array([w for _, w in pairs])
        total = float(weights.sum())
        if total <= 0.0:
            continue
        out[str(event_id)] = {
            "lines": len(pairs),
            "mean_delta_z": round(float((deltas * weights).sum() / total) / spread, 4),
        }
    return out


def _round(value: float | None, digits: int) -> float | None:
    """Round where there is something to round."""
    return None if value is None else round(value, digits)


def _percentiles(values: Sequence[float]) -> dict[str, float]:
    if not values:
        return {}
    array = np.asarray(values, dtype=float)
    return {
        "n": int(array.size),
        "median": round(float(np.median(array)), 4),
        "p90": round(float(np.percentile(array, 90)), 4),
        "max": round(float(np.max(array)), 4),
        "mean": round(float(np.mean(array)), 4),
    }


def ladder_rows(
    panels: Mapping[tuple[int, int], Panel],
    seasons: Mapping[int, dict[str, Any]],
    modes: Mapping[int, str],
    *,
    placebo_draws: int = PLACEBO_DRAWS,
) -> tuple[list[dict[str, Any]], dict[tuple[int, int], dict[str, Fitted]]]:
    """Fit every cohort and feature up the ladder; return one row each."""
    rows: list[dict[str, Any]] = []
    fits: dict[tuple[int, int], dict[str, Fitted]] = {}
    for key in sorted(panels, key=lambda k: (seasons[k[0]]["year"], modes.get(k[1], ""))):
        panel = panels[key]
        columns2 = build_columns(panel, teammates=False)
        matrix2 = design(panel, columns2)
        columns3 = build_columns(panel, teammates=True)
        matrix3 = design(panel, columns3)
        fits[key] = {}
        for feature in panel.features:
            fitted = fit_feature(panel, feature.key, (columns2, matrix2), (columns3, matrix3))
            fits[key][feature.key] = fitted
            raw_values = aggregate(panel, feature.key)
            spread = cohort_spread(raw_values)
            row: dict[str, Any] = {
                "season": seasons[key[0]]["year"],
                "title": seasons[key[0]]["title"],
                "mode": modes.get(key[1], str(key[1])),
                "feature": feature.key,
                "lines": int(fitted.deltas[RUNG_RAW].size),
                "players_admitted": len(columns2.admitted),
                "players_pooled": len(columns2.pooled),
                "cohort_spread": round(spread, 6) if spread else None,
            }
            previous = RUNG_RAW
            for rung in LADDER[1:]:
                moved = movement(fitted.boards[previous], fitted.boards[rung])
                adjustment = fitted.adjustments.get(rung)
                row[rung] = {
                    "mean_abs_dz": round(moved.mean_abs_dz, 4),
                    "p95_abs_dz": round(moved.p95_abs_dz, 4),
                    "spearman": round(moved.spearman, 5) if moved.spearman is not None else None,
                    "top_n_churn": moved.top_n_churn,
                    "reached": adjustment.reached if adjustment else 0,
                    "reliability": _round(fitted.reliability.get(rung), 4),
                    "circularity": _round(adjustment.circularity if adjustment else None, 6),
                    "ridge": adjustment.ridge if adjustment else None,
                }
                previous = rung
            row["reliability_raw"] = _round(fitted.reliability[RUNG_RAW], 4)
            rung1 = fitted.adjustments[RUNG_TEAM]
            row["team_rating_slope"] = _round(rung1.slope, 8)
            row["team_rating_slope_se"] = _round(rung1.slope_se, 8)
            if placebo_draws:
                for rung, columns, matrix in (
                    (RUNG_LINEUP, columns2, matrix2),
                    (RUNG_CONTEXT, columns3, matrix3),
                ):
                    result = placebo(
                        panel, feature.key, columns, matrix, draws=placebo_draws, rung=rung
                    )
                    row[rung]["placebo_ratio"] = result.get("ratio")
            rows.append(row)
    return (rows, fits)


# ---------------------------------------------------------------- bootstrap


def bootstrap_correction(
    panel: Panel,
    feature_key: str,
    columns: Columns,
    matrix: FloatArray,
    seed: int = BOOTSTRAP_SEED,
    draws: int = BOOTSTRAP_B,
) -> dict[str, Any]:
    """A percentile interval for how far the adjustment moves the leaderboard.

    The resampling unit is the whole series, everywhere in this plan: maps
    inside one share a lineup, a day, a patch and an opponent, so treating them
    as independent would narrow every interval by an unknown factor.

    Each draw indexes rows of the design already built rather than rebuilding
    one, which is what makes two hundred draws affordable; a drawn series
    appearing twice is two copies of its rows, which is what resampling a
    cluster means.

    The population is put in an order fixed by its own contents before any draw
    — the series' natural key, then its own weighted rate and exposure — and the
    generator is seeded from those contents, so neither the draw nor the
    interval moves when a reload renumbers the loader's keys.
    """
    from ..resample import order, stream

    rate, weight, mask = response(panel, feature_key)
    if not mask.any():
        return {"available": False, "reason": "the feature is absent from this cohort"}

    rows = np.flatnonzero(mask)
    x_all, y_all, w_all = matrix[rows], rate[rows], weight[rows]
    players = np.array([panel.lines[int(i)].player_id for i in rows])
    opp_columns = np.zeros(matrix.shape[1], dtype=np.float64)
    for index in columns.opp.values():
        opp_columns[index] = 1.0

    grouped: dict[str, list[int]] = defaultdict(list)
    for position, line_index in enumerate(rows):
        grouped[panel.lines[int(line_index)].series_key].append(position)
    keys = sorted(grouped)
    if len(keys) < 10:
        return {"available": False, "reason": "fewer than ten series"}

    totals = np.array([float(w_all[grouped[k]].sum()) for k in keys])
    means = np.array(
        [
            float((y_all[grouped[k]] * w_all[grouped[k]]).sum()) / max(total, 1e-12)
            for k, total in zip(keys, totals, strict=True)
        ]
    )
    fixed = order([np.arange(len(keys), dtype=float), means, totals])
    blocks = [np.asarray(grouped[keys[int(i)]], dtype=np.int64) for i in fixed]
    generator = stream(seed, means, totals)

    def board(sample: NDArray[np.int64], delta: FloatArray) -> dict[int, float]:
        """Season values and their z-scores over one drawn sample."""
        numerator: dict[int, float] = defaultdict(float)
        denominator: dict[int, float] = defaultdict(float)
        counted: dict[int, int] = defaultdict(int)
        for position, row in enumerate(sample):
            player = int(players[row])
            numerator[player] += (y_all[row] - delta[position]) * w_all[row]
            denominator[player] += w_all[row]
            counted[player] += 1
        values = [
            SeasonValue(
                player_id=player,
                key=panel.key,
                feature=feature_key,
                value=numerator[player] / denominator[player],
                denom=denominator[player],
                maps=counted[player],
            )
            for player in sorted(numerator)
            if denominator[player] > 0.0
        ]
        return leaderboard(values)

    sizes: list[float] = []
    for _draw in range(draws):
        picked = generator.integers(0, len(blocks), size=len(blocks))
        drawn = np.concatenate([blocks[int(i)] for i in picked])
        x, y, w = x_all[drawn], y_all[drawn], w_all[drawn]
        if len(y) <= matrix.shape[1] + 2:
            continue
        fit = solve_wls(x, y, w, FE_RIDGE)
        contribution = (x * opp_columns) @ fit.beta
        delta = contribution - _weighted_mean(contribution, w)
        moved = movement(board(drawn, np.zeros(len(drawn))), board(drawn, delta))
        sizes.append(moved.mean_abs_dz)
    if not sizes:
        return {"available": False, "reason": "no draw produced a leaderboard"}
    array = np.asarray(sizes)
    return {
        "available": True,
        "draws": len(sizes),
        "n_series": len(keys),
        "mean_abs_dz": round(float(array.mean()), 5),
        "lo": round(float(np.percentile(array, 2.5)), 5),
        "hi": round(float(np.percentile(array, 97.5)), 5),
    }


# --------------------------------------------------- the positive control

CONTROL_SEED = 20190818  # CWL Championship 2019 finals, as elsewhere in the stack


def positive_control(
    seed: int = CONTROL_SEED,
    players: int = 40,
    maps: int = 400,
    side: int = 4,
    noise: float = 1.0,
) -> dict[str, Any]:
    """Recover an opponent effect that was put there on purpose.

    The placebo says the machinery reports nothing from nothing. It does not say
    the machinery reports the right thing from something. Here a league is built
    with known per-player offensive and defensive effects and a schedule that
    pairs teams at random; the rung then has to recover the defensive ones it
    was never told.
    """
    generator = np.random.default_rng(seed)
    own_effect = generator.normal(0.0, 1.0, players)
    opponent_effect = generator.normal(0.0, 0.5, players)

    lines: list[Line] = []
    for game in range(maps):
        picked = generator.permutation(players)[: 2 * side]
        left, right = picked[:side], picked[side:]
        for own, other in ((left, right), (right, left)):
            faced = float(opponent_effect[other].sum())
            for player in own:
                value = own_effect[player] + faced + generator.normal(0.0, noise)
                lines.append(
                    Line(
                        player_id=int(player),
                        team_id=0 if own is left else 1,
                        game_id=game,
                        series_id=game,
                        event_id=0,
                        season_id=0,
                        mode_id=0,
                        duration_s=600.0,
                        map_key=f"sim-{game:05d}#1",
                        opponents=tuple(sorted(int(p) for p in other)),
                        teammates=tuple(sorted(int(p) for p in own if p != player)),
                        opp_rating=None,
                        values={"synthetic": (value, 1.0)},
                    )
                )
    panel = Panel(
        season_id=0,
        mode_id=0,
        mode_slug="synthetic",
        title="synthetic",
        side=side,
        lines=tuple(lines),
        features=(),
    )
    columns = build_columns(panel, teammates=False)
    matrix = design(panel, columns)
    rate, weight, mask = response(panel, "synthetic")
    fit = solve_wls(matrix[mask], rate[mask], weight[mask], FE_RIDGE)

    recovered = np.array([fit.beta[columns.opp_index(p)] for p in range(players)])
    truth = opponent_effect - opponent_effect.mean()
    estimate = recovered - recovered.mean()
    return {
        "seed": seed,
        "players": players,
        "maps": maps,
        "noise_sd": noise,
        "true_effect_sd": round(float(truth.std()), 4),
        "recovered_effect_sd": round(float(estimate.std()), 4),
        "correlation": round(float(np.corrcoef(truth, estimate)[0, 1]), 4),
        # A slope of one means the recovered effect is on the true scale rather
        # than merely correlated with it.
        "slope": round(float(np.polyfit(truth, estimate, 1)[0]), 4),
    }


# ----------------------------------------------------------- the artifact


def adopt(summary: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """Apply the declared stop rule to the ladder's per-rung summary.

    Three criteria, each declared before anything was fitted, and each a
    different way of failing:

    - *movement* — the rung has to change the leaderboard against the rung below
      it by at least a hundredth of a cohort standard deviation. A rung that
      changes nothing is a rung nobody needs.
    - *placebo* — what it changes has to stand clear of what the same design
      reports from a shuffled schedule. A rung that moves numbers without
      clearing this is fitting noise, which is the failure this phase is most
      exposed to.
    - *reliability* — it must not leave the statistic less repeatable than the
      unadjusted one. A correction that removes signal along with the opponent
      is a worse number wearing a better name.

    **The ladder is not monotone and the rule does not assume it is.** Each rung
    is judged on its own, and the adopted rung is the highest that clears all
    three, rather than the last before a failure. On this record that matters:
    the two-way rung overshoots and comes out *less* repeatable than the raw
    number, and the pooled rung above it repairs exactly that. A climb-until-
    failure rule would have stopped at the cheap rung and thrown away the one
    that works.
    """
    reasons: list[dict[str, Any]] = []
    adopted = RUNG_RAW
    for rung in LADDER[1:]:
        row = summary.get(rung, {})
        moved = row.get("mean_abs_dz_median")
        ratio = row.get("placebo_ratio_median")
        gain = row.get("reliability_gain_median")
        clears_movement = moved is not None and moved >= STOP_MEAN_ABS_DZ
        # Only the two fitted rungs have a placebo; the others are judged on the
        # criteria that apply to them rather than given a free pass they did not
        # earn or a failure they could not avoid.
        clears_placebo = ratio is None or ratio >= STOP_PLACEBO_RATIO
        clears_reliability = gain is None or gain >= 0.0
        clears = clears_movement and clears_placebo and clears_reliability
        reasons.append(
            {
                "rung": rung,
                "mean_abs_dz_median": moved,
                "placebo_ratio_median": ratio,
                "reliability_gain_median": gain,
                "clears_movement": clears_movement,
                "clears_placebo": clears_placebo,
                "clears_reliability": clears_reliability,
                "clears": clears,
            }
        )
        if clears:
            adopted = rung
    return {
        "adopted": adopted,
        "thresholds": {
            "mean_abs_dz": STOP_MEAN_ABS_DZ,
            "placebo_ratio": STOP_PLACEBO_RATIO,
            "reliability_gain": 0.0,
        },
        "per_rung": reasons,
    }


def adjusting_rung(verdict: Mapping[str, Any]) -> str:
    """The adopted rung, dropped to the highest one that adjusts a map.

    Rung 4 shrinks season values rather than map rates, so it has no per-line
    correction for anything that reads one. Where it is the adopted rung, the
    per-line questions are answered by the highest rung below it that cleared.
    """
    adopted = str(verdict["adopted"])
    if adopted != RUNG_SHRUNK:
        return adopted
    cleared = [row["rung"] for row in verdict["per_rung"] if row["clears"]]
    for rung in reversed(LADDER[1:-1]):
        if rung in cleared:
            return str(rung)
    return RUNG_RAW


def _summarize(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Collapse the per-cohort ladder into one row per rung."""
    out: dict[str, dict[str, Any]] = {}
    for rung in LADDER[1:]:
        moved = [r[rung]["mean_abs_dz"] for r in rows if rung in r]
        ratios = [
            r[rung]["placebo_ratio"]
            for r in rows
            if rung in r and r[rung].get("placebo_ratio") is not None
        ]
        churn = [r[rung]["top_n_churn"] for r in rows if rung in r]
        gains = [
            r[rung]["reliability"] - r["reliability_raw"]
            for r in rows
            if rung in r
            and r[rung].get("reliability") is not None
            and r.get("reliability_raw") is not None
        ]
        out[rung] = {
            "fits": len(moved),
            "mean_abs_dz_median": round(float(np.median(moved)), 5) if moved else None,
            "mean_abs_dz_p90": round(float(np.percentile(moved, 90)), 5) if moved else None,
            "mean_abs_dz_max": round(float(np.max(moved)), 5) if moved else None,
            "placebo_ratio_median": round(float(np.median(ratios)), 4) if ratios else None,
            "top_n_churn_total": int(np.sum(churn)) if churn else 0,
            "reliability_gain_median": round(float(np.median(gains)), 5) if gains else None,
            "reliability_gain_positive": sum(1 for g in gains if g > 0),
            "reliability_measured": len(gains),
        }
    return out


def schedule_answer(
    panels: Mapping[tuple[int, int], Panel],
    fits: Mapping[tuple[int, int], Mapping[str, Fitted]],
    seasons: Mapping[int, dict[str, Any]],
    events: Mapping[int, str],
    rung: str,
) -> dict[str, Any]:
    """Was any reputation built on soft fields, and whose?

    The gate names the CWL-era open brackets, and this does not take that label
    from anywhere — it measures the thing the label is a proxy for. The amount
    the adjustment removes from a line *is* what the opposition was worth on it,
    so averaging that over an event says how soft the event's fields were, and
    averaging it over a player-season says how much of that player's number came
    from who they played. Both are reported in cohort standard deviations, which
    is the unit the leaderboard is read in.
    """
    per_event: dict[int, list[float]] = defaultdict(list)
    per_player: dict[tuple[int, int], list[float]] = defaultdict(list)
    per_era: dict[str, list[float]] = defaultdict(list)

    for key, panel in panels.items():
        for feature_key, fitted in fits.get(key, {}).items():
            spread = cohort_spread(aggregate(panel, feature_key))
            if not spread:
                continue
            delta = fitted.deltas.get(rung)
            if delta is None:
                continue
            _rate, weight, mask = response(panel, feature_key)
            era = "CWL" if seasons[key[0]]["year"] <= 2019 else "CDL"
            for i, line in enumerate(panel.lines):
                if not mask[i]:
                    continue
                scaled = delta[i] / spread
                per_event[line.event_id].append(scaled)
                per_player[(line.player_id, key[0])].append(scaled)
                per_era[era].append(scaled)

    by_event_mean = {
        event_id: float(np.mean(values))
        for event_id, values in per_event.items()
        if len(values) >= 200
    }
    softest = [
        {
            "event": events.get(event_id, str(event_id)),
            "lines": len(per_event[event_id]),
            "mean_delta_z": round(mean, 4),
        }
        for event_id, mean in sorted(by_event_mean.items(), key=lambda kv: -abs(kv[1]))
    ]
    inflated = sorted(
        (
            {
                "player_id": player,
                "season_id": season,
                "lines": len(values),
                "mean_delta_z": round(float(np.mean(values)), 4),
            }
            for (player, season), values in per_player.items()
            if len(values) >= 40
        ),
        key=lambda row: -abs(float(row["mean_delta_z"])),
    )
    return {
        "rung": rung,
        "by_era": {
            era: {
                "lines": len(values),
                "mean_delta_z": round(float(np.mean(values)), 5),
                "sd_delta_z": round(float(np.std(values)), 5),
                "p95_abs_delta_z": round(float(np.percentile(np.abs(values), 95)), 5),
            }
            for era, values in sorted(per_era.items())
        },
        "events_by_softness": softest[:15],
        "most_inflated_player_seasons": inflated[:15],
    }


def coverage(panels: Mapping[tuple[int, int], Panel]) -> dict[str, Any]:
    """What the cheap rung can and cannot see.

    Rung 1 reads a rating, and a team that has played nothing carries the prior
    rather than a measurement. Counting those lines is the honest statement of
    where residualizing on a team rating has nothing to residualize on.
    """
    total = rated = prior = missing = 0
    blind_seasons: dict[int, int] = defaultdict(int)
    for panel in panels.values():
        for line in panel.lines:
            total += 1
            if line.opp_rating is None:
                missing += 1
                blind_seasons[panel.season_id] += 1
            elif abs(line.opp_rating - RATING_PRIOR) <= 1e-9:
                prior += 1
                blind_seasons[panel.season_id] += 1
            else:
                rated += 1
    return {
        "lines": total,
        "opponent_rated": rated,
        "opponent_at_prior": prior,
        "opponent_missing": missing,
        "blind_share": round((prior + missing) / total, 5) if total else None,
        "blind_by_season": {str(k): v for k, v in sorted(blind_seasons.items())},
    }


def artifact(
    panels: Mapping[tuple[int, int], Panel],
    seasons: Mapping[int, dict[str, Any]],
    modes: Mapping[int, str],
    events: Mapping[int, str],
    *,
    placebo_draws: int = PLACEBO_DRAWS,
    bootstrap_draws: int = BOOTSTRAP_B,
) -> dict[str, Any]:
    """The whole phase as one payload: the ladder, the controls, the answer."""
    rows, fits = ladder_rows(panels, seasons, modes, placebo_draws=placebo_draws)
    summary = _summarize(rows)
    verdict = adopt(summary)
    adopted = adjusting_rung(verdict)

    intervals: list[dict[str, Any]] = []
    if bootstrap_draws:
        for key in sorted(panels, key=lambda k: seasons[k[0]]["year"]):
            panel = panels[key]
            columns = build_columns(panel, teammates=False)
            for feature in panel.features:
                if feature.key not in HEADLINE_FEATURES:
                    continue
                result = bootstrap_correction(
                    panel,
                    feature.key,
                    columns,
                    design(panel, columns),
                    draws=bootstrap_draws,
                )
                if result.get("available"):
                    intervals.append(
                        {
                            "season": seasons[key[0]]["year"],
                            "mode": modes.get(key[1], str(key[1])),
                            "feature": feature.key,
                            **result,
                        }
                    )

    graphs = {}
    for key, panel in panels.items():
        label = f"{seasons[key[0]]['year']} {modes.get(key[1], key[1])}"
        graphs[label] = opponent_graph(panel)

    forms = []
    for key in sorted(panels, key=lambda k: seasons[k[0]]["year"]):
        panel = panels[key]
        for feature in panel.features:
            shape = functional_form(panel, feature.key)
            if shape.get("available"):
                forms.append(
                    {
                        "season": seasons[key[0]]["year"],
                        "mode": modes.get(key[1], str(key[1])),
                        "feature": feature.key,
                        **shape,
                    }
                )

    return {
        "version": VERSION,
        "adjusts": "box-score features, not the plus-minus",
        "rungs": list(LADDER),
        "stop_rule": verdict,
        "ladder": summary,
        "by_cohort": rows,
        "coverage": coverage(panels),
        "controls": {
            "positive": positive_control(),
            "placebo_draws": placebo_draws,
        },
        "functional_form": {
            "fits": len(forms),
            "linear_over_binned": _percentiles(
                [f["linear_over_binned"] for f in forms if "linear_over_binned" in f]
            ),
            "slope_gap_abs_t": _percentiles(
                [abs(f["slope_gap_t"]) for f in forms if f.get("slope_gap_t") is not None]
            ),
            "per_cohort": forms,
        },
        "opponent_graph": graphs,
        "schedule": schedule_answer(panels, fits, seasons, events, adopted)
        if adopted != RUNG_RAW
        else {"rung": RUNG_RAW, "reason": "no rung was adopted"},
        "bootstrap": {
            "unit": "series",
            "draws": bootstrap_draws,
            "seed": BOOTSTRAP_SEED,
            "rung": RUNG_LINEUP,
            "per_cohort": intervals,
        },
        "params": {
            "admission_maps": ADMISSION_MAPS,
            "qualify_maps": QUALIFY_MAPS,
            "crossfit_folds": CROSSFIT_FOLDS,
            "pool_grid": list(POOL_GRID),
            "placebo_seed": PLACEBO_SEED,
            "control_seed": CONTROL_SEED,
        },
    }
