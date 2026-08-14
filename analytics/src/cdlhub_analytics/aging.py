"""Aging curves, and the selection problem that makes one curve a lie.

Spec: /methodology#aging.

A curve fitted on observed player-seasons is biased upward at the tail, and the
mechanism is not subtle: players who decline leave the league and stop
contributing seasons, so the players still measured at 28 are the ones who did
not decline. This is the best-documented defect in the baseball aging literature
and it applies here with more force, because a roster is four players and the
attrition is brutal. **The answer is not a better single fit. It is three fits
whose disagreement is the measurement.**

- `naive` — every observed player-season, level on level, pooled. The biased
  one. It is published because the size of the bias is only visible against it.
- `delta` — paired consecutive seasons of the same player, so each observation
  is a within-player change and the between-player differences cancel. It
  carries its own version of the same bias, pushed down one level: pairing
  conditions on *having* a next season, and a player who declines and is
  dropped contributes no pair.
- `retention` — the same pairs, weighted by the inverse of a fitted probability
  of being retained at that age. It corrects what the delta method conditions
  on, at the cost of trusting the retention model.

None of the three is the answer, and the gate refuses to let one of them be
published alone. **The peak age is an interval spanning all three**, and the
spread between them is reported as the size of the problem rather than hidden
inside one confidence band.

**No survival library is used, and that is a decision rather than an omission.**
The plan named `lifelines`. What is actually needed here is the probability that
a player observed at age *a* appears in the next league season — a discrete-time
retention rate over at most ten periods, with no censoring beyond the final
season and no covariates. That is a ratio of counts with a shrinkage prior, it
is fifteen lines, and it is exactly reproducible and readable in place. Adding an
untyped dependency, a `mypy` override and lock churn to compute it would trade
the project's transparency for nothing.

**The two-component test rides along.** Slaying and objective contribution are
fitted separately, on the same population and through the same three fits, with
the plan's prediction that their peaks differ. It is reported whichever way it
lands.

**Two populations, because they buy different things.** The box score offers ten
seasons and 530 consecutive-season pairs and can locate a peak. The plus-minus
offers seven seasons and 263 pairs and is the quantity anyone actually argues
about. Publishing one without the other would either answer the wrong question
or answer the right one with no power.

**Age is known for 439 of 815 players.** A player without a birthdate is fitted
on their career-season index instead, in a separate population that never mixes
with the age one: the two x-axes are different quantities and averaging them
would produce a curve of neither.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import numpy as np
import psycopg
from numpy.typing import NDArray

from . import career, resample
from .ratings import statespace
from .ratings.preflight import Season, load_seasons

FloatArray = NDArray[np.float64]

MODEL = "aging"
VERSION = "1.0.0"

NAIVE = "naive"
DELTA = "delta"
RETENTION = "retention"
FITS = (NAIVE, DELTA, RETENTION)

OVERALL = "overall"
SLAYING = "slaying"
OBJECTIVE = "objective"

COMPOSITE = career.COMPOSITE
PLUS_MINUS = career.PLUS_MINUS

# How many player-seasons an age needs before a curve is drawn through it.
#
# This replaced a declared window of 17 to 32, which was wrong in the way the
# comment on it warned against. The record holds one season at 17 and five in
# total across 29 to 32, so a quadratic drawn to the declared edges showed a
# steep post-28 decline resting on almost nothing, and the drawn shape looked
# like the strongest claim on the page. The window is now measured from the
# population being fitted: the widest run of consecutive ages that each clear
# this floor. Fits still use every observation. Only the drawn range and the
# range a peak may be published in are restricted.
MIN_AGE_SUPPORT = 10

# A window narrower than this cannot carry a quadratic worth drawing.
MIN_WINDOW_YEARS = 4.0

# Inset from the window's edges for a publishable peak. A vertex outside the
# supported range is reported as "no interior peak" instead of being clipped to
# the edge, because a clipped peak is an extrapolation wearing a measurement's
# clothes.
PEAK_INSET = 1.0

# Cluster bootstrap over players. Seeded per population from the population's own
# contents, never from a surrogate key, per the resample policy.
BOOTSTRAP_B = 1000
BOOTSTRAP_SEED = 20260814

# Shrinkage on the retention rate: a pseudo-count of players split between
# staying and leaving, added to every age bin. An age observed four times would
# otherwise be able to report a retention rate of exactly zero, and its inverse
# weight would be infinite.
RETENTION_PRIOR = 4.0

# The inverse-probability weight is capped. An age whose retention rate is
# genuinely near zero would otherwise let two or three players carry the whole
# fit, which converts a correction for selection into a different selection.
MAX_WEIGHT = 5.0

# Below this many observations a fit is not attempted at all.
MIN_OBSERVATIONS = 30


@dataclass(frozen=True)
class Observation:
    """One player-season on one axis, at one x."""

    player_id: int
    season_position: int
    x: float
    value: float


@dataclass(frozen=True)
class Curve:
    """A fitted shape over the published x window."""

    fit: str
    x: FloatArray
    y: FloatArray
    peak: float | None
    peak_lo: float | None
    peak_hi: float | None
    n_observations: int
    n_players: int


@dataclass(frozen=True)
class CurveRow:
    """One stored point of one player's fitted trajectory."""

    player_id: int
    population: str
    fit: str
    component: str
    x_is_age: bool
    age_or_seq: float
    fitted: float
    lo95: float | None
    hi95: float | None


def params() -> dict[str, Any]:
    return {
        "fits": list(FITS),
        "min_age_support": MIN_AGE_SUPPORT,
        "min_window_years": MIN_WINDOW_YEARS,
        "bootstrap_b": BOOTSTRAP_B,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "retention_prior": RETENTION_PRIOR,
        "max_weight": MAX_WEIGHT,
        "survival_library": "none: a discrete-time retention rate over ten periods",
    }


# MARK: loading


def _age_at(birthdate: date, season: Season) -> float:
    """Age in years at the midpoint of the season's year.

    A season is a year, not a day, so an age to the day would be false
    precision. The midpoint is used rather than the start so a player born in
    December is not credited with a whole extra year of youth.
    """
    return (date(season.year, 7, 1) - birthdate).days / 365.25


def load_birthdates(conn: psycopg.Connection[tuple[object, ...]]) -> dict[int, date]:
    rows = conn.execute("SELECT id, birthdate FROM players WHERE birthdate IS NOT NULL").fetchall()
    return {cast(int, r[0]): cast(date, r[1]) for r in rows}


_ALL_MODES_SQL = """
SELECT player_id, season_id, rating, kd_z
FROM player_season_adjusted
WHERE run_id = %s AND mode_id IS NULL AND maps_played >= %s
ORDER BY player_id, season_id
"""

# Objective contribution has no all-modes row and cannot have one: hill time,
# zone captures and bomb plants are three different quantities and the season
# table stores each against its own mode. The season figure is therefore built
# here, as the maps-weighted mean of the per-mode z-scores — a player who played
# mostly Hardpoint is asked mostly about Hardpoint.
_BY_MODE_OBJECTIVE_SQL = """
SELECT player_id, season_id,
       sum(obj_z * maps_played) / sum(maps_played) AS obj_z
FROM player_season_adjusted
WHERE run_id = %s AND mode_id IS NOT NULL AND obj_z IS NOT NULL AND maps_played > 0
GROUP BY player_id, season_id
HAVING sum(maps_played) >= %s
ORDER BY player_id, season_id
"""


def load_composite(
    conn: psycopg.Connection[tuple[object, ...]], rating_run_id: int, era_run_id: int
) -> dict[str, list[tuple[int, int, float]]]:
    """The three box-score components, keyed by component name.

    `rating` comes from the rating run and the z-scores from the era run, which
    is the same split `evaluate` reads them under: they are written by different
    stages and a single run id would silently return nothing for one of them.
    """
    out: dict[str, list[tuple[int, int, float]]] = {OVERALL: [], SLAYING: [], OBJECTIVE: []}
    for run_id, component, column in (
        (rating_run_id, OVERALL, 2),
        (era_run_id, SLAYING, 3),
    ):
        for row in conn.execute(_ALL_MODES_SQL, (run_id, career.QUALIFIED_MAPS)):
            if row[column] is not None:
                out[component].append(
                    (cast(int, row[0]), cast(int, row[1]), cast(float, row[column]))
                )
    for row in conn.execute(_BY_MODE_OBJECTIVE_SQL, (era_run_id, career.QUALIFIED_MAPS)):
        out[OBJECTIVE].append((cast(int, row[0]), cast(int, row[1]), cast(float, row[2])))
    return out


def load_plus_minus(
    conn: psycopg.Connection[tuple[object, ...]], season_rapm_run_id: int
) -> list[tuple[int, int, float]]:
    """Season-resolution one-sided coefficients only.

    An era-resolution row repeats one estimate across three seasons. Fitting a
    curve through three copies of one number would report a flat stretch that
    nothing measured, so the CWL era contributes no observations here — it is
    absent from the age curve, and the artifact says so rather than letting a
    reader infer coverage from the axis.
    """
    rows = conn.execute(
        "SELECT player_id, season_id, coef FROM player_rapm"
        " WHERE run_id = %s AND scope = %s AND resolution = %s AND season_id IS NOT NULL"
        " ORDER BY player_id, season_id",
        (season_rapm_run_id, statespace.FILTERED, statespace.SEASON),
    ).fetchall()
    return [(cast(int, r[0]), cast(int, r[1]), cast(float, r[2])) for r in rows]


def observations(
    rows: Sequence[tuple[int, int, float]],
    seasons: dict[int, Season],
    birthdates: dict[int, date],
    use_age: bool,
) -> list[Observation]:
    """Rows to observations on one x-axis.

    `use_age` selects the population rather than filtering one: a player with a
    birthdate is fitted on age and a player without is fitted on their career
    index, and the two never appear in the same fit.
    """
    order = {
        season.season_id: i
        for i, season in enumerate(sorted(seasons.values(), key=lambda s: (s.year, s.season_id)))
    }
    first_season: dict[int, int] = {}
    for player_id, season_id, _ in rows:
        position = order[season_id]
        if player_id not in first_season or position < first_season[player_id]:
            first_season[player_id] = position

    out: list[Observation] = []
    for player_id, season_id, value in rows:
        has_age = player_id in birthdates
        if has_age != use_age:
            continue
        position = order[season_id]
        x = (
            _age_at(birthdates[player_id], seasons[season_id])
            if use_age
            else float(position - first_season[player_id] + 1)
        )
        out.append(Observation(player_id=player_id, season_position=position, x=x, value=value))
    return out


# MARK: the three fits


def _quadratic(x: FloatArray, y: FloatArray, w: FloatArray) -> FloatArray | None:
    """Weighted least squares on [1, x, x²]. Returns the coefficients."""
    if x.size < 3:
        return None
    design = np.column_stack([np.ones_like(x), x, x * x])
    root = np.sqrt(w)[:, None]
    try:
        beta, *_ = np.linalg.lstsq(design * root, y * np.sqrt(w), rcond=None)
    except np.linalg.LinAlgError:
        return None
    return cast(FloatArray, beta)


def _line(x: FloatArray, y: FloatArray, w: FloatArray) -> FloatArray | None:
    """Weighted least squares on [1, x]."""
    if x.size < 2:
        return None
    design = np.column_stack([np.ones_like(x), x])
    root = np.sqrt(w)[:, None]
    try:
        beta, *_ = np.linalg.lstsq(design * root, y * np.sqrt(w), rcond=None)
    except np.linalg.LinAlgError:
        return None
    return cast(FloatArray, beta)


def _vertex(beta: FloatArray, bounds: tuple[float, float]) -> float | None:
    """The interior maximum of a quadratic, or None if it does not have one."""
    if beta[2] >= 0.0:
        return None
    peak = -beta[1] / (2.0 * beta[2])
    return peak if bounds[0] <= peak <= bounds[1] else None


def _zero_crossing(beta: FloatArray, bounds: tuple[float, float]) -> float | None:
    """Where a fitted rate of change passes from positive to negative."""
    if beta[1] >= 0.0:
        return None
    crossing = -beta[0] / beta[1]
    return crossing if bounds[0] <= crossing <= bounds[1] else None


def pairs(obs: Sequence[Observation]) -> list[tuple[int, float, float]]:
    """Consecutive-season pairs of the same player: (player, midpoint x, change).

    Consecutive means consecutive *league* seasons. A player who sat a year out
    and came back has no pair across the gap, because the change over two years
    is not the change over one and averaging them would flatten the curve.
    """
    by_player: dict[int, dict[int, Observation]] = defaultdict(dict)
    for row in obs:
        by_player[row.player_id][row.season_position] = row
    out: list[tuple[int, float, float]] = []
    for player_id, seasons in sorted(by_player.items()):
        for position, row in sorted(seasons.items()):
            nxt = seasons.get(position + 1)
            if nxt is None:
                continue
            out.append((player_id, (row.x + nxt.x) / 2.0, nxt.value - row.value))
    return out


def retention_rates(obs: Sequence[Observation], last_position: int) -> dict[int, float]:
    """P(a player observed at x is observed again next season), by integer x.

    The final season is excluded from the denominator entirely. A player active
    in it has not failed to return; the record simply stops, and counting them
    as a departure would report the end of the data as a wave of retirements at
    every age at once.
    """
    seen: dict[int, set[int]] = defaultdict(set)
    for row in obs:
        seen[row.player_id].add(row.season_position)
    stayed: dict[int, float] = defaultdict(float)
    total: dict[int, float] = defaultdict(float)
    for row in obs:
        if row.season_position >= last_position:
            continue
        bucket = int(round(row.x))
        total[bucket] += 1.0
        if row.season_position + 1 in seen[row.player_id]:
            stayed[bucket] += 1.0
    if not total:
        return {}
    pooled = sum(stayed.values()) / sum(total.values())
    return {
        bucket: (stayed[bucket] + RETENTION_PRIOR * pooled) / (count + RETENTION_PRIOR)
        for bucket, count in total.items()
    }


def retention_weights(
    paired: Sequence[tuple[int, float, float]], rates: dict[int, float]
) -> FloatArray:
    """Inverse probability of being retained, capped.

    A pair exists only because the player was retained, so it stands for the
    players at that age who were not. The weight is 1/r, normalised to a mean of
    one so the weighted fit keeps the same effective scale as the unweighted
    one, and capped so a thin age cannot take the fit over.
    """
    if not paired:
        return np.zeros(0, dtype=float)
    pooled = float(np.mean(list(rates.values()))) if rates else 1.0
    raw = np.array(
        [1.0 / max(rates.get(int(round(x)), pooled), 1e-3) for _, x, _ in paired],
        dtype=float,
    )
    capped = np.minimum(raw, MAX_WEIGHT)
    mean = float(capped.mean())
    return cast(FloatArray, capped / mean if mean > 0.0 else capped)


def support_window(obs: Sequence[Observation]) -> tuple[float, float] | None:
    """The widest run of consecutive ages that each carry MIN_AGE_SUPPORT seasons.

    Contiguous by construction, so the window can never bridge an age the record
    does not cover. Returns None when no such run is long enough to draw, which
    is a population that has no publishable curve and is reported as one.
    """
    counts: Counter[int] = Counter(int(round(row.x)) for row in obs)
    supported = sorted(age for age, n in counts.items() if n >= MIN_AGE_SUPPORT)
    if not supported:
        return None
    best: tuple[int, int] | None = None
    start = supported[0]
    for previous, age in zip(supported, supported[1:], strict=False):
        if age != previous + 1:
            if best is None or previous - start > best[1] - best[0]:
                best = (start, previous)
            start = age
    last = supported[-1]
    if best is None or last - start > best[1] - best[0]:
        best = (start, last)
    if best[1] - best[0] < MIN_WINDOW_YEARS:
        return None
    return float(best[0]), float(best[1])


def _grid(window: tuple[float, float]) -> FloatArray:
    return np.arange(window[0], window[1] + 0.5, 0.5, dtype=float)


def _level_curve(beta: FloatArray, grid: FloatArray) -> FloatArray:
    """A fitted level, centred on its own mean like the integrated curves.

    The centring is what makes the three fits comparable at all. A level fit
    carries the population mean in its intercept and the two delta fits cannot
    carry a level, so plotting the raw level beside them puts one curve near
    +0.97 and the other two near zero. On a shared axis the naive curve becomes
    a flat line pinned at the top and the comparison this phase exists to make
    is invisible. Centring drops only the intercept, which no fit here claims to
    estimate.

    Amplitude is left alone. The naive curve's span is about a sixth of the
    delta curve's, and that gap is a second reading of the same survivorship:
    the survivors at each age flatten the level curve as well as moving its
    peak.
    """
    fitted = beta[0] + beta[1] * grid + beta[2] * grid * grid
    return cast(FloatArray, fitted - float(fitted.mean()))


def _integrated_curve(beta: FloatArray, grid: FloatArray) -> FloatArray:
    """A rate of change, integrated back into a shape.

    The delta fits estimate dy/dx. The level they integrate to is unidentified,
    because a within-player change says nothing about where the player started.
    The curve is centred on its own mean, the only anchor the observations
    support, and `_level_curve` is centred the same way so the three can be read
    on one axis.
    """
    shape = beta[0] * grid + beta[1] * grid * grid / 2.0
    return cast(FloatArray, shape - float(shape.mean()))


def fit_curves(obs: Sequence[Observation]) -> dict[str, Curve]:
    """All three fits over one population, with a cluster bootstrap on the peak.

    Every observation enters every fit. The measured window governs where the
    result is drawn and where a peak may be published, so a thin tail informs
    the fit without being presented as a shape anyone measured.
    """
    out: dict[str, Curve] = {}
    n_players = len({row.player_id for row in obs})
    if len(obs) < MIN_OBSERVATIONS:
        return out
    window = support_window(obs)
    if window is None:
        return out
    bounds = (window[0] + PEAK_INSET, window[1] - PEAK_INSET)
    grid = _grid(window)

    x = np.array([row.x for row in obs], dtype=float)
    y = np.array([row.value for row in obs], dtype=float)
    beta = _quadratic(x, y, np.ones_like(x))
    if beta is not None:
        lo, hi = _bootstrap_peak(
            [row.player_id for row in obs], x, y, np.ones_like(x), _quadratic, _vertex, bounds
        )
        out[NAIVE] = Curve(
            fit=NAIVE,
            x=grid,
            y=_level_curve(beta, grid),
            peak=_vertex(beta, bounds),
            peak_lo=lo,
            peak_hi=hi,
            n_observations=len(obs),
            n_players=n_players,
        )

    paired = pairs(obs)
    if len(paired) >= MIN_OBSERVATIONS:
        px = np.array([p[1] for p in paired], dtype=float)
        py = np.array([p[2] for p in paired], dtype=float)
        clusters = [p[0] for p in paired]
        last = max(row.season_position for row in obs)
        rates = retention_rates(obs, last)
        for name, weights in (
            (DELTA, np.ones_like(px)),
            (RETENTION, retention_weights(paired, rates)),
        ):
            fit = _line(px, py, weights)
            if fit is None:
                continue
            lo, hi = _bootstrap_peak(clusters, px, py, weights, _line, _zero_crossing, bounds)
            out[name] = Curve(
                fit=name,
                x=grid,
                y=_integrated_curve(fit, grid),
                peak=_zero_crossing(fit, bounds),
                peak_lo=lo,
                peak_hi=hi,
                n_observations=len(paired),
                n_players=len(set(clusters)),
            )
    return out


def _bootstrap_peak(
    clusters: Sequence[int],
    x: FloatArray,
    y: FloatArray,
    w: FloatArray,
    fit: Any,
    peak_of: Any,
    bounds: tuple[float, float],
) -> tuple[float | None, float | None]:
    """A percentile interval on the peak, resampling players rather than rows.

    Rows of the same player are not independent, so a row bootstrap would report
    an interval narrower than the data supports. The population is ordered by
    its own contents and the generator is seeded from them, so neither the
    interval nor the draws depend on any surrogate key.
    """
    if x.size == 0:
        return None, None
    position = resample.order([x, y, w, np.asarray(clusters, dtype=float)])
    x, y, w = x[position], y[position], w[position]
    ordered = [clusters[i] for i in position]
    by_player: dict[int, list[int]] = defaultdict(list)
    for index, player_id in enumerate(ordered):
        by_player[player_id].append(index)
    # Players are ordered by what their rows contain, not by their id, so a
    # renumbered database draws the same players in the same order.
    players = sorted(
        by_player, key=lambda p: (float(x[by_player[p][0]]), float(y[by_player[p][0]]), p)
    )
    rng = resample.stream(BOOTSTRAP_SEED, x, y, w)
    peaks: list[float] = []
    for _ in range(BOOTSTRAP_B):
        drawn = rng.integers(0, len(players), len(players))
        taken = np.concatenate([by_player[players[int(i)]] for i in drawn])
        beta = fit(x[taken], y[taken], w[taken])
        if beta is None:
            continue
        peak = peak_of(beta, bounds)
        if peak is not None:
            peaks.append(peak)
    # An interval is reported only when most draws found an interior peak at
    # all. Below that the fit does not locate a peak and saying so is the
    # result; a percentile of the draws that happened to succeed would describe
    # a conditional population nobody asked about.
    if len(peaks) < BOOTSTRAP_B // 2:
        return None, None
    return float(np.percentile(peaks, 2.5)), float(np.percentile(peaks, 97.5))


# MARK: per-player trajectories


def player_rows(
    obs: Sequence[Observation],
    curves: dict[str, Curve],
    population: str,
    component: str,
    x_is_age: bool,
) -> list[CurveRow]:
    """One fitted trajectory per player: the population shape, shifted to them.

    A random intercept and a common shape. The shift is the player's mean
    residual against the shape, shrunk toward zero by their own count of
    seasons, so a player with two seasons is not handed a personal level the
    two seasons cannot support. Nothing here fits a personal *shape*: with a
    median of two seasons per player there is no curvature to estimate, and a
    per-player quadratic on three points would interpolate rather than fit.
    """
    if not curves:
        return []
    by_player: dict[int, list[Observation]] = defaultdict(list)
    for row in obs:
        by_player[row.player_id].append(row)
    spread = float(np.std([row.value for row in obs])) if obs else 0.0

    out: list[CurveRow] = []
    for fit_name, curve in sorted(curves.items()):
        for player_id, rows in sorted(by_player.items()):
            xs = np.array([row.x for row in rows], dtype=float)
            values = np.array([row.value for row in rows], dtype=float)
            shape_at = np.interp(xs, curve.x, curve.y)
            residual = float(np.mean(values - shape_at))
            # Shrinkage toward the population: n/(n+1) of the player's own mean.
            shift = residual * len(rows) / (len(rows) + 1.0)
            inside = (curve.x >= xs.min() - 0.5) & (curve.x <= xs.max() + 0.5)
            for x_value, fitted in zip(curve.x[inside], curve.y[inside], strict=True):
                band = spread / math.sqrt(len(rows)) if spread > 0.0 else None
                out.append(
                    CurveRow(
                        player_id=player_id,
                        population=population,
                        fit=fit_name,
                        component=component,
                        x_is_age=x_is_age,
                        age_or_seq=float(x_value),
                        fitted=float(fitted) + shift,
                        lo95=None if band is None else float(fitted) + shift - 1.96 * band,
                        hi95=None if band is None else float(fitted) + shift + 1.96 * band,
                    )
                )
    return out


# MARK: the published object


def build(
    conn: psycopg.Connection[tuple[object, ...]],
    rating_run_id: int,
    era_run_id: int,
    season_rapm_run_id: int,
) -> tuple[list[CurveRow], dict[str, Any]]:
    """Every curve this phase publishes, and what the three fits disagree about."""
    seasons = load_seasons(conn)
    birthdates = load_birthdates(conn)
    components = load_composite(conn, rating_run_id, era_run_id)
    plus_minus = load_plus_minus(conn, season_rapm_run_id)

    sources: list[tuple[str, str, Sequence[tuple[int, int, float]]]] = [
        (COMPOSITE, OVERALL, components[OVERALL]),
        (COMPOSITE, SLAYING, components[SLAYING]),
        (COMPOSITE, OBJECTIVE, components[OBJECTIVE]),
        (PLUS_MINUS, OVERALL, plus_minus),
    ]

    rows: list[CurveRow] = []
    blocks: dict[str, Any] = {}
    for population, component, source in sources:
        obs = observations(source, seasons, birthdates, use_age=True)
        curves = fit_curves(obs)
        rows += player_rows(obs, curves, population, component, x_is_age=True)
        blocks[f"{population}.{component}"] = _block(curves, obs)
        # The players with no birthdate are a population of their own, fitted on
        # the career index. They are stored but not summarised into the peak
        # interval: a peak in career-season units is not an age and combining
        # the two would produce a number in no unit at all.
        by_index = observations(source, seasons, birthdates, use_age=False)
        rows += player_rows(by_index, fit_curves(by_index), population, component, x_is_age=False)

    return rows, artifact(blocks, birthdates, seasons)


def _block(curves: dict[str, Curve], obs: Sequence[Observation]) -> dict[str, Any]:
    window = support_window(obs)
    peaks = [c.peak for c in curves.values() if c.peak is not None]
    los = [c.peak_lo for c in curves.values() if c.peak_lo is not None]
    his = [c.peak_hi for c in curves.values() if c.peak_hi is not None]
    return {
        "n_observations": len(obs),
        "n_players": len({row.player_id for row in obs}),
        # The ages this population actually covers, measured. Curves are drawn
        # over it and a peak is published only inside it.
        "age_window": None if window is None else [window[0], window[1]],
        "fits": {
            name: {
                "peak": None if curve.peak is None else round(float(curve.peak), 2),
                "peak_lo": None if curve.peak_lo is None else round(float(curve.peak_lo), 2),
                "peak_hi": None if curve.peak_hi is None else round(float(curve.peak_hi), 2),
                "n_observations": curve.n_observations,
                "n_players": curve.n_players,
                "curve": [
                    {"x": round(float(x), 2), "y": round(float(y), 4)}
                    for x, y in zip(curve.x, curve.y, strict=True)
                ],
            }
            for name, curve in sorted(curves.items())
        },
        # The published peak: the union of all three, never one of them. A
        # reader who wants the delta fit's own interval can read it above; what
        # this project claims is the span, because the disagreement between the
        # fits is larger than any one of their standard errors and pretending
        # otherwise is the whole failure mode this phase exists to avoid.
        "peak_interval": {
            "lo": round(float(min(los)), 2) if los else None,
            "hi": round(float(max(his)), 2) if his else None,
            "point_estimates": sorted(round(float(p), 2) for p in peaks),
            "spread": round(float(max(peaks) - min(peaks)), 2) if len(peaks) > 1 else None,
            "fits_locating_a_peak": len(peaks),
        },
    }


def artifact(
    blocks: dict[str, Any], birthdates: dict[int, date], seasons: dict[int, Season]
) -> dict[str, Any]:
    overall = blocks.get(f"{COMPOSITE}.{OVERALL}", {})
    two_component = _two_component(blocks)
    return {
        "available": bool(blocks),
        # There is no single age window any more. Each population reports the
        # one its own record supports, inside its block.
        "min_age_support": MIN_AGE_SUPPORT,
        "players_with_birthdate": len(birthdates),
        "seasons": len(seasons),
        "retention_prior": RETENTION_PRIOR,
        "max_weight": MAX_WEIGHT,
        "populations": blocks,
        "two_component": two_component,
        "statement": _statement(overall, two_component),
    }


def _two_component(blocks: dict[str, Any]) -> dict[str, Any]:
    """Do slaying and objective contribution peak at different ages?

    The plan predicts they do. The test is the gap between the two peak
    intervals: if they overlap, the record does not separate them, and that is
    reported rather than being argued around.
    """
    slaying = blocks.get(f"{COMPOSITE}.{SLAYING}", {}).get("peak_interval", {})
    objective = blocks.get(f"{COMPOSITE}.{OBJECTIVE}", {}).get("peak_interval", {})
    if not slaying.get("point_estimates") or not objective.get("point_estimates"):
        return {"available": False, "reason": "one component located no peak"}
    overlap = (
        slaying["lo"] is not None
        and objective["lo"] is not None
        and slaying["lo"] <= objective["hi"]
        and objective["lo"] <= slaying["hi"]
    )
    return {
        "available": True,
        "slaying": slaying,
        "objective": objective,
        "intervals_overlap": bool(overlap),
        "separated": not overlap,
    }


def _statement(overall: dict[str, Any], two_component: dict[str, Any]) -> str:
    interval = overall.get("peak_interval", {})
    if not interval or interval.get("lo") is None:
        return "no fit located an interior peak; no peak age is published"
    parts = [
        f"peak between {interval['lo']} and {interval['hi']} across "
        f"{interval['fits_locating_a_peak']} fits"
    ]
    if interval.get("spread") is not None:
        parts.append(f"the three point estimates spread {interval['spread']} years")
    if two_component.get("available"):
        parts.append(
            "slaying and objective peaks are separated"
            if two_component["separated"]
            else "slaying and objective peaks are not separated"
        )
    return "; ".join(parts)


def headline(payload: dict[str, Any]) -> str:
    return cast(str, payload.get("statement", "aging curves unavailable"))
