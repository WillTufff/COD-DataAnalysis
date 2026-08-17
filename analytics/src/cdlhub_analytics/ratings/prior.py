"""The box score, fitted to predict value in wins rather than to decompose them.

The published composite asks what the box score is worth on the scoreboard: it
is fitted against map outcome, so a column that names the result — hill time,
flag captures — earns weight for naming it. That is a decomposition, and it is
why the composite loses the persistence test to a single K/D column.

This module inverts the question. The target is the season plus-minus at
`filtered` scope: what a player's presence was worth in score margin, fitted on
maps through that season and nothing later. The box score is the predictor. A
column earns weight here only if the profile it belongs to precedes a player who
moved the margin, which is the quantity anyone arguing about players is arguing
about.

**Three things about the target decide the whole design.**

It is small: 431 player-seasons, seven CDL seasons, 149 players. The CWL era
stores era-resolution coefficients — one estimate filed against each of the three
seasons it covers — and those are excluded rather than counted three times.

It is noisy: `se` exceeds `|coef|` on 406 of the 431. Regressing on the point
estimates as though they were observed would teach the model the shrinkage
pattern, which is a function of maps played and teammate structure, and publish
it as skill. Both defences the plan offers are taken rather than either:
observations are weighted by inverse posterior variance, and the target is drawn
from its own posterior across refits so the uncertainty reaches the intervals.
`exposure_loading` is the check that it worked, and it is a gate, not a note.
The cap it tests is on the ratio between the prior's exposure loading and the
target's own, and the ratio is measured with an interval: two R-squared values
over a few hundred rows carry sampling error, and a fit that sits a thousandth
above the cap is not the failure the cap was written to catch. A crossing counts
when the whole interval clears the cap, and a crossing by the point estimate
alone is published as one.

**And the noise goes further than the plan expected.** Empirical Bayes on the
431 coefficients returns τ̂² = 0: the spread between players is indistinguishable
from zero given their own standard errors, because the coefficients have a
standard deviation of 0.062 against a mean standard error of 0.127. The season
plus-minus, taken at face value with its own uncertainty, does not establish that
these players differ. That is the most important number this phase produces, it
is published as such, and it is why the posterior blend downstream has almost
nothing to blend — the weight lands on the prior because the direct estimate
carries no distinguishing signal to defend.

And it is ordered: the test this feeds is a forward test, so the fit is
walk-forward by season — train on seasons before *t*, predict *t*, never the
reverse. An earlier draft of the plan asked for player-grouped folds as well,
which cannot hold when 149 players span 7 seasons; the `filtered` target is what
actually keeps next season out of this season's answer.

**The role conditioning is the profile, not a role model.** A separate phase
estimates position on continuous style axes, and it is deliberately not read
here: those axes are fitted per era over the whole era, so their loadings have
seen seasons this fit predicts, and the column set they are built from changes
with the seasons included. The per-mode profile below conditions on role by
being the role. What the style axes get instead is a correlation against the
fitted prior, published as a diagnostic and never entering the design.
"""

from __future__ import annotations

import importlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..era import MIN_MAPS
from ..regress import FloatArray
from ..resample import stream
from . import hierarchical, numerics, statespace
from . import player_rating as pr

MODEL = "skill_prior"
VERSION = "1.0.0"

# The feature set the profile is read at. Fitted since P0 and deliberately not
# the published one: promoting a version is a lockstep change with the site.
FEATURE_SET = "2.2.0"

# The target family and resolution. Both are load-bearing and neither is a
# default: `smoothed` has seen the following season through the random-walk
# penalty, and an `era` row is one estimate wearing three season labels.
TARGET_SCOPE = statespace.FILTERED
TARGET_RESOLUTION = statespace.SEASON

# Draws from the target's posterior, and the seed they start from. The draw is
# what carries the target's own uncertainty into the prior's interval; the
# weights alone would report a confident fit to a noisy number.
DRAWS = 200
SEED = 20260812

# Draws every arm of the ladder is compared under. Lower than `DRAWS` because
# the comparison refits three arms where the published fit refits one, and a
# comparison is only meaningful if every arm carries the same target noise. The
# arm that wins is then refitted at `DRAWS` for the intervals that get stored.
LADDER_DRAWS = 20

# The paired bootstrap the ladder's verdict is read from, clustered on the
# player for the same reason the evaluation harness clusters there: a player
# contributes up to seven rows and they are not independent.
LADDER_B = 2000
LADDER_SEED = 20260813

# The two non-linear arms' settings, fixed here rather than searched: a grid
# search over 431 rows inside a walk-forward fold is a way of fitting the fold.
FOREST_TREES = 300
FOREST_MIN_LEAF = 5
BOOSTED_ROUNDS = 200
BOOSTED_LEAVES = 7
BOOSTED_MIN_LEAF = 20
BOOSTED_RATE = 0.05

RIDGE, FOREST, BOOSTED = "ridge", "random_forest", "lightgbm"

# The ridge path, searched by generalized cross-validation on the training fold
# alone. Wide because the design is 18 columns on as few as 49 training rows in
# the earliest fold and the right penalty there is not the right one at 380.
LAMBDA_GRID = tuple(float(10.0**e) for e in np.arange(-3.0, 4.01, 0.25))

# How much of the fitted prior may be explained by exposure alone before the
# phase has produced a shrinkage map rather than a rating.
#
# The live threshold is a ratio against the target's own exposure loading, not an
# absolute share, and the absolute one it replaces is kept below rather than
# deleted. A threshold that moves after the result is seen is worthless unless
# what moved it is on the record, so this pair is maintained the way the
# evaluation manifest maintains `PIN_HISTORY`: the superseded value, the value
# that replaced it, and the measurement that forced the change.
SHRINKAGE_RATIO_MAX = 1.0

# Resamples behind the ratio's interval. The ratio is two R-squared values over
# the same few hundred rows, so it carries sampling error of its own, and a cap
# at exactly 1.0 with no interval fails a fit that sits a thousandth above it
# for the same reason it fails one that sits far above it. The interval is what
# separates those two, and it is drawn rather than assumed.
SHRINKAGE_BOOT_B = 400
SHRINKAGE_BOOT_SEED = 20260817
SUPERSEDED_SHRINKAGE_R2_MAX = 0.25

THRESHOLD_HISTORY: tuple[dict[str, Any], ...] = (
    {
        "threshold": "shrinkage_r2_max",
        "value": 0.25,
        "declared_in": "the pre-registered plan, P5 section 5.4, before the fit",
        "superseded_by": "shrinkage_ratio_max = 1.0",
        "because": (
            "measured, the prior loads 0.2625 on maps played and teammate concentration and "
            "the target it predicts loads 0.2977 on the same two columns, almost all of it "
            "maps. An absolute cap below the target's own loading asks a faithful fit to be "
            "less faithful; the ratio asks the question the cap was reaching for, which is "
            "whether the fit amplifies the target's exposure relationship or attenuates it"
        ),
        "verdict_under_the_old_threshold": "failed at 0.2625",
        "verdict_under_the_new_one": "passes at a ratio of 0.8819",
    },
)

# A cohort standardization needs enough qualified players to have a spread.
MIN_COHORT_PLAYERS = 5

# Every arm that has been compared against the ridge and dropped, with the run
# that measured it. An arm which loses is removed from the dependency set — the
# plan's rule is that a boosted tree beating nothing is never merged — and after
# it goes the comparison cannot be recomputed. Recording the verdict here is what
# keeps "the ladder was run and the ridge won" from decaying into "there was only
# ever a ridge". Filled from a `run_all` measurement, never from a scratch fit.
LADDER_HISTORY: tuple[dict[str, Any], ...] = (
    {
        "arm": "random_forest",
        "package": "scikit-learn",
        "measured_in_run": 431,
        "on": "431 player-seasons, 16 columns, six walk-forward folds, 20 drawn targets",
        "out_of_fold_r": 0.4486,
        "ridge_out_of_fold_r": 0.4659,
        "vs_ridge": {"delta_r": -0.0173, "lo": -0.0637, "hi": 0.0280, "excludes_zero": False},
        "verdict": "did not beat the ridge; the dependency was not kept",
    },
    {
        "arm": "lightgbm",
        "package": "lightgbm",
        "measured_in_run": 431,
        "on": "431 player-seasons, 16 columns, six walk-forward folds, 20 drawn targets",
        "out_of_fold_r": 0.4329,
        "ridge_out_of_fold_r": 0.4659,
        "vs_ridge": {"delta_r": -0.0330, "lo": -0.0965, "hi": 0.0258, "excludes_zero": False},
        "verdict": (
            "did not beat the ridge, under the rule declared before it was fitted; neither it "
            "nor shap was merged"
        ),
    },
)


@dataclass(frozen=True)
class Target:
    """One training row: what the plus-minus said, and how loudly."""

    player_id: int
    season_id: int
    year: int
    coef: float
    se: float
    maps: int
    teammate_concentration: float


@dataclass(frozen=True)
class Design:
    """The training table, and everything needed to say what it is made of."""

    targets: tuple[Target, ...]
    columns: tuple[str, ...]
    x: FloatArray
    y: FloatArray
    se: FloatArray
    year: FloatArray
    # Maps played and teammate concentration, standardized, held beside the
    # design rather than inside it. They generate the shrinkage the target
    # carries, so a prior given them as columns would be predictable from them by
    # construction and `exposure_loading` would measure the design instead of the
    # defect. Kept because the diagnostic regresses on them.
    exposure: FloatArray
    dropped: dict[str, Any]

    @property
    def n(self) -> int:
        return len(self.targets)

    @property
    def years(self) -> list[int]:
        return sorted({int(y) for y in self.year})


def load_targets(conn: Any, run_id: int, seasons: dict[int, Any]) -> list[Target]:
    """The filtered season coefficients, in the shape the fit trains on."""
    statespace.require_filtered(TARGET_SCOPE)
    rows = conn.execute(
        "SELECT player_id, season_id, coef, se, maps, teammate_concentration FROM player_rapm"
        " WHERE run_id = %s AND scope = %s AND resolution = %s",
        (run_id, TARGET_SCOPE, TARGET_RESOLUTION),
    ).fetchall()
    out = [
        Target(
            player_id=int(r[0]),
            season_id=int(r[1]),
            year=int(seasons[int(r[1])]["year"]),
            coef=float(r[2]),
            se=float(r[3]),
            maps=int(r[4]),
            teammate_concentration=float(r[5]),
        )
        for r in rows
        if int(r[1]) in seasons
    ]
    return order_targets(out)


def order_targets(targets: Sequence[Target]) -> list[Target]:
    """The training table's row order, fixed by what the rows contain.

    Load-bearing rather than tidy. `_draw_targets` draws a matrix of noise and
    adds it positionally, so whichever target sits at position zero decides which
    draw it gets; ordered by `player_id`, that decision belongs to the loader's
    numbering, and a reload that renumbers players would move every published
    interval while no coefficient moved. The project has already been bitten by
    exactly this once, in `paired_gaps`.

    `player_id` remains as the final tiebreak because two targets identical in
    every measured field are interchangeable to any resample, so which of them
    goes first cannot change a drawn statistic.
    """
    return sorted(targets, key=lambda t: (t.year, t.coef, t.se, t.maps, t.player_id))


def _cohort_z(
    aggs: Sequence[pr.PlayerModeAgg], cohorts: dict[tuple[int, int], pr.Cohort]
) -> tuple[dict[tuple[int, int, int], dict[str, float]], dict[tuple[int, int], int]]:
    """Every aggregate profile as a deviation from its own (season × mode) cohort.

    Standardized inside the cohort rather than across the archive, for the reason
    the metric layer already standardizes: a hill-time rate is not comparable
    across titles whose rotations differ, and a prior fitted on raw rates would
    spend its first few columns learning which season a row came from.

    The standardization is computed here rather than read from `CohortScale`,
    which carries the same two vectors, because that object is built from the
    composite's own logistic fit and this fit should not inherit a dependency on
    the model it is meant to replace.
    """
    by_cohort: dict[tuple[int, int], list[pr.PlayerModeAgg]] = defaultdict(list)
    for agg in aggs:
        by_cohort[(agg.season_id, agg.mode_id)].append(agg)

    out: dict[tuple[int, int, int], dict[str, float]] = {}
    sizes: dict[tuple[int, int], int] = {}
    for key, members in by_cohort.items():
        cohort = cohorts.get(key)
        qualified = [a for a in members if a.maps >= MIN_MAPS]
        sizes[key] = len(qualified)
        if cohort is None or len(qualified) < MIN_COHORT_PLAYERS:
            continue
        feats = np.array([a.feats for a in qualified], dtype=float)
        mu = feats.mean(axis=0)
        sd = feats.std(axis=0, ddof=1)
        sd[sd == 0.0] = 1.0
        for agg in qualified:
            z = (np.asarray(agg.feats, dtype=float) - mu) / sd
            out[(agg.player_id, agg.season_id, agg.mode_id)] = dict(
                zip(cohort.feature_keys, (float(v) for v in z), strict=True)
            )
    return out, sizes


def stable_columns(
    cohorts: dict[tuple[int, int], pr.Cohort], seasons: Sequence[int]
) -> tuple[dict[int, tuple[str, ...]], dict[str, Any]]:
    """Per mode, the features every season that mode was played reports.

    A column that exists in four seasons of seven is not a feature, it is a
    season indicator wearing a feature's name, and a walk-forward fit would train
    on folds whose design differs from the fold being predicted. So the rule is
    the one the style basis already uses: present in every season the mode
    appears in, or not admitted — and what fell out is published rather than
    quietly missing.

    Modes are not required to appear in every season, only to be stable where
    they do. Control arrives in 2021 and is gone by 2026; dropping it entirely to
    keep a rectangle would cost three columns on five of seven seasons, so a row
    with no Control instead sits at the cohort mean with an indicator saying so.
    """
    played: dict[int, list[int]] = defaultdict(list)
    for season_id, mode_id in cohorts:
        if season_id in set(seasons):
            played[mode_id].append(season_id)

    admitted: dict[int, tuple[str, ...]] = {}
    excluded: list[dict[str, Any]] = []
    for mode_id, season_ids in sorted(played.items()):
        sets = [set(cohorts[(s, mode_id)].feature_keys) for s in season_ids]
        stable = set.intersection(*sets)
        unstable = sorted(set.union(*sets) - stable)
        if stable:
            admitted[mode_id] = tuple(sorted(stable))
        for key in unstable:
            excluded.append(
                {
                    "mode_id": mode_id,
                    "feature": key,
                    "seasons_reporting": sum(1 for s in sets if key in s),
                    "seasons_played": len(sets),
                }
            )
    return admitted, {
        "what": (
            "features a mode reports in some seasons and not others. Admitting one would "
            "make the design of a training fold differ from the design of the fold it "
            "predicts, so they are excluded and listed"
        ),
        "n": len(excluded),
        "columns": excluded,
    }


def build(
    targets: Sequence[Target],
    cohorts: dict[tuple[int, int], pr.Cohort],
    aggs: Sequence[pr.PlayerModeAgg],
) -> Design:
    """The training table: one row per player-season, columns fixed across folds."""
    seasons = sorted({t.season_id for t in targets})
    admitted, unstable = stable_columns(cohorts, seasons)
    zs, _sizes = _cohort_z(aggs, cohorts)

    names: list[str] = []
    for mode_id, keys in sorted(admitted.items()):
        names.extend(f"m{mode_id}.{key}" for key in keys)
    names.extend(f"m{mode_id}.played" for mode_id in sorted(admitted))

    kept: list[Target] = []
    matrix: list[list[float]] = []
    no_profile = 0
    for target in targets:
        values: list[float] = []
        indicators: list[float] = []
        seen = 0
        for mode_id, keys in sorted(admitted.items()):
            profile = zs.get((target.player_id, target.season_id, mode_id))
            indicators.append(0.0 if profile is None else 1.0)
            seen += 0 if profile is None else 1
            values.extend(0.0 if profile is None else profile.get(key, 0.0) for key in keys)
        if seen == 0:
            no_profile += 1
            continue
        kept.append(target)
        matrix.append([*values, *indicators])

    x = np.array(matrix, dtype=float) if matrix else np.zeros((0, len(names)))
    raw_exposure = np.array(
        [[float(t.maps), float(t.teammate_concentration)] for t in kept], dtype=float
    ).reshape(len(kept), 2)
    exposure = raw_exposure.copy()
    for column in range(exposure.shape[1]):
        if exposure.size:
            values_column = exposure[:, column]
            sd = float(values_column.std(ddof=1)) or 1.0
            exposure[:, column] = (values_column - float(values_column.mean())) / sd

    return Design(
        targets=tuple(kept),
        columns=tuple(names),
        x=x,
        y=np.array([t.coef for t in kept], dtype=float),
        se=np.array([t.se for t in kept], dtype=float),
        year=np.array([t.year for t in kept], dtype=float),
        exposure=exposure,
        dropped={
            "no_profile_in_any_admitted_mode": no_profile,
            "unstable_features": unstable,
            "modes_admitted": sorted(admitted),
        },
    )


# ------------------------------------------------------------------ the weights


def weights(design: Design) -> tuple[FloatArray, float]:
    """Inverse posterior variance per observation, and the τ̂² behind it.

    1/(se² + τ̂²), not 1/se². The first is the variance of the observation as a
    draw from the population being fitted; the second treats a coefficient whose
    standard error happens to be small as though it were the truth, which at 94%
    penalty domination hands the fit to whichever players had the most maps.

    τ̂² comes from the same empirical-Bayes EM the cohort model uses, on the
    targets themselves. Reused rather than reimplemented: a second EM that drifts
    from the first is two answers to one question.

    **On this record τ̂² comes back at zero**, so the weights reduce to 1/se².
    That is not a degenerate case to route around — it is the measurement. The
    coefficients' spread is smaller than their own average standard error, so
    there is no between-player variance for the EM to find, and every downstream
    quantity that expects one has to say so rather than quietly proceeding.
    """
    if design.n == 0:
        return np.zeros(0), 0.0
    _mu, tau2, _post = hierarchical.empirical_bayes(design.y, design.se**2)
    return 1.0 / (design.se**2 + tau2), float(tau2)


def target_signal(design: Design) -> dict[str, Any]:
    """What the plus-minus establishes about these players, before anything is fitted.

    Published as a finding in its own right. A target whose between-player
    variance is zero cannot be predicted by anything, and a phase that fits a
    model to it without saying so is reporting the fit's own regularization.
    """
    if design.n == 0:
        return {"available": False, "reason": "no targets"}
    _mu, tau2, _post = hierarchical.empirical_bayes(design.y, design.se**2)
    sd = float(design.y.std(ddof=1))
    mean_se = float(design.se.mean())
    # Collapsed is a property of the fit, not of the optimizer: the EM can crawl
    # to 2e-07 instead of landing on zero, and `tau2 > 0` would call that a
    # between-player variance. Read against the observation variance, the way the
    # cohort model reads its own — testing convergence instead is how two seasons
    # once came to publish 1.00 for every player.
    mean_obs_var = float((design.se**2).mean())
    collapsed = bool(tau2 <= hierarchical.TAU2_COLLAPSE_FLOOR * mean_obs_var)
    return {
        "available": True,
        "what": (
            "the between-player variance the season plus-minus establishes, against the "
            "measurement error it carries"
        ),
        "n": design.n,
        "tau2": round(float(tau2), 8),
        "tau": round(float(np.sqrt(tau2)), 6),
        "sd_coef": round(sd, 4),
        "mean_se": round(mean_se, 4),
        "se_exceeds_coef_share": round(
            float(np.mean(design.se > np.abs(design.y))),
            4,
        ),
        "mean_obs_var": round(mean_obs_var, 6),
        "collapse_floor": hierarchical.TAU2_COLLAPSE_FLOOR,
        "collapsed": collapsed,
        "distinguishable": not collapsed,
        "reading": (
            "the coefficients' spread is smaller than their own average standard error, so "
            "the estimator does not establish that these players differ at season resolution"
            if collapsed
            else "there is between-player variance for a prior to predict"
        ),
    }


# -------------------------------------------------------------------- the ridge


@dataclass(frozen=True)
class Ridge:
    """One fitted linear prior: an intercept, weights, and the penalty chosen."""

    intercept: float
    beta: FloatArray
    lam: float
    effective_df: float

    def predict(self, x: FloatArray) -> FloatArray:
        out: FloatArray = self.intercept + x @ self.beta
        return out


def _solve(x: FloatArray, y: FloatArray, w: FloatArray, lam: float) -> tuple[float, FloatArray]:
    """Weighted ridge with an unpenalized intercept, by centring."""
    total = float(w.sum())
    x_mean = (w[:, None] * x).sum(axis=0) / total
    y_mean = float((w * y).sum() / total)
    xc, yc = x - x_mean, y - y_mean
    gram = xc.T @ (w[:, None] * xc) + lam * np.eye(x.shape[1])
    beta = numerics.cholesky(gram).solve(xc.T @ (w * yc))
    return y_mean - float(x_mean @ beta), beta


def _gcv(x: FloatArray, y: FloatArray, w: FloatArray, lam: float) -> tuple[float, float]:
    """Generalized cross-validation score for one penalty, and its effective df."""
    total = float(w.sum())
    x_mean = (w[:, None] * x).sum(axis=0) / total
    xc = x - x_mean
    gram = xc.T @ (w[:, None] * xc) + lam * np.eye(x.shape[1])
    inverse = numerics.cholesky(gram).inverse()
    df = 1.0 + float(np.trace(inverse @ (xc.T @ (w[:, None] * xc))))
    intercept, beta = _solve(x, y, w, lam)
    residual = y - (intercept + x @ beta)
    denominator = max(1.0 - df / len(y), 1e-6)
    return float((w * residual**2).sum() / total) / denominator**2, df


def fit_ridge(x: FloatArray, y: FloatArray, w: FloatArray) -> Ridge:
    """The regularized linear prior, penalty chosen on the training rows alone."""
    scored = [(_gcv(x, y, w, lam), lam) for lam in LAMBDA_GRID]
    (_score, df), lam = min(scored, key=lambda pair: (pair[0][0], pair[1]))
    intercept, beta = _solve(x, y, w, lam)
    return Ridge(intercept=intercept, beta=beta, lam=lam, effective_df=round(df, 3))


# ------------------------------------------------------------- walk-forward fit


@dataclass(frozen=True)
class Prediction:
    """One player-season's prior: the mean, and what the draws did around it."""

    player_id: int
    season_id: int
    year: int
    mean: float
    sd: float
    n_train: int


def _draw_targets(design: Design, b: int) -> FloatArray:
    """`b` draws of the whole target vector from its own posterior.

    Seeded from the targets' contents rather than from a key, so a reload that
    renumbers players cannot move a published interval — the class of defect the
    project swept for once already. `standard_normal` scaled by hand rather than
    `normal(loc, scale)`, which contracts to a fused multiply-add on arm64 and
    would make the draw depend on the machine.
    """
    rng = stream(SEED, design.y, design.se)
    noise = rng.standard_normal(size=(b, design.n))
    return design.y[None, :] + noise * design.se[None, :]


def _ridge_arm(x: FloatArray, y: FloatArray, w: FloatArray, out: FloatArray) -> FloatArray:
    return fit_ridge(x, y, w).predict(out)


def _forest_arm(x: FloatArray, y: FloatArray, w: FloatArray, out: FloatArray) -> FloatArray:
    """The random forest, imported where it is used and nowhere else.

    Imported inside the call rather than at module scope because the arm is a
    development dependency: it exists to be compared against the ridge, and if
    it does not win it is removed rather than carried. A module-level import
    would make the whole package unimportable the moment it goes.
    """
    # Neither arm is installed: both were compared once and dropped, and the
    # verdicts are in `LADDER_HISTORY`. The import is ignored rather than the
    # code deleted so the comparison can be repeated by installing the two
    # packages, which is the only way a recorded verdict stays checkable.
    from sklearn.ensemble import (  # type: ignore[import-not-found,unused-ignore] # noqa: PLC0415
        RandomForestRegressor,
    )

    model = RandomForestRegressor(
        n_estimators=FOREST_TREES,
        min_samples_leaf=FOREST_MIN_LEAF,
        random_state=SEED,
        n_jobs=1,
    )
    model.fit(x, y, sample_weight=w)
    return np.asarray(model.predict(out), dtype=float)


def _boosted_arm(x: FloatArray, y: FloatArray, w: FloatArray, out: FloatArray) -> FloatArray:
    """The boosted arm, under the settings the plan pre-declared for it."""
    from lightgbm import (  # type: ignore[import-not-found,unused-ignore] # noqa: PLC0415
        LGBMRegressor,
    )

    model = LGBMRegressor(
        n_estimators=BOOSTED_ROUNDS,
        num_leaves=BOOSTED_LEAVES,
        min_child_samples=BOOSTED_MIN_LEAF,
        learning_rate=BOOSTED_RATE,
        random_state=SEED,
        deterministic=True,
        force_row_wise=True,
        n_jobs=1,
        verbose=-1,
    )
    model.fit(x, y, sample_weight=w)
    return np.asarray(model.predict(out), dtype=float)


ARMS: dict[str, Callable[[FloatArray, FloatArray, FloatArray, FloatArray], FloatArray]] = {
    RIDGE: _ridge_arm,
    FOREST: _forest_arm,
    BOOSTED: _boosted_arm,
}


def arm_available(name: str) -> tuple[bool, str | None]:
    """Whether an arm can be fitted here, and what stopped it if not.

    Reported rather than raised. The two non-linear arms are development
    dependencies by design — the plan's rule is that a boosted arm which does
    not beat the ridge never enters the published dependency set — so a run on a
    machine without them has to be able to say which arm it could not fit, and
    fall back to the verdict already recorded for it.
    """
    if name == RIDGE:
        return True, None
    module = "sklearn.ensemble" if name == FOREST else "lightgbm"
    try:
        importlib.import_module(module)
    except Exception as exc:  # noqa: BLE001 (a missing shared library is not an ImportError)
        return False, f"{type(exc).__name__}: {exc}".split("\n")[0]
    return True, None


def walk_forward(
    design: Design, w: FloatArray, draws: int = DRAWS, arm: str = RIDGE
) -> list[Prediction]:
    """Predict each season from the seasons before it, and never the reverse.

    The first season has nothing before it and is not predicted; every later one
    is fitted on every row that precedes it. Refitting per draw is what carries
    the target's noise into `sd`: the spread of a player's prediction across
    draws is what the fit does not know about them.

    Every arm of the ladder comes through here, on the same folds, the same
    weights and the same drawn targets. The arm is the only thing that varies,
    which is what makes the comparison downstream a comparison.
    """
    if design.n == 0:
        return []
    fitter = ARMS[arm]
    sampled = _draw_targets(design, draws)
    years = design.years
    out: list[Prediction] = []
    for year in years[1:]:
        train = design.year < year
        test = design.year == year
        n_train = int(train.sum())
        if n_train < len(design.columns) // 2 or not test.any():
            continue
        per_draw = np.empty((draws, int(test.sum())), dtype=float)
        for b in range(draws):
            per_draw[b] = fitter(design.x[train], sampled[b][train], w[train], design.x[test])
        means = per_draw.mean(axis=0)
        sds = per_draw.std(axis=0, ddof=1)
        for i, target in enumerate(t for t, keep in zip(design.targets, test, strict=True) if keep):
            out.append(
                Prediction(
                    player_id=target.player_id,
                    season_id=target.season_id,
                    year=target.year,
                    mean=float(means[i]),
                    sd=float(sds[i]),
                    n_train=n_train,
                )
            )
    return out


# -------------------------------------------------------------- the diagnostic


def _r2(y: FloatArray, regressors: FloatArray) -> float:
    if len(y) < 3 or y.std() == 0.0:
        return 0.0
    a = np.column_stack([np.ones(len(y)), regressors])
    beta, *_ = np.linalg.lstsq(a, y, rcond=None)
    residual = y - a @ beta
    return float(1.0 - residual.var() / y.var())


def _ratio_interval(
    prior: FloatArray, target: FloatArray, exposure: FloatArray
) -> tuple[float | None, float | None]:
    """A 95% interval for the exposure-loading ratio, over the rows themselves.

    Paired: a draw takes the same rows for both R-squared values, because the
    question is whether this fit loads on exposure harder than this target does
    on the same observations.
    """
    n = prior.shape[0]
    if n < 20:
        return None, None
    rng = stream(SHRINKAGE_BOOT_SEED, prior, target)
    ratios: list[float] = []
    for _ in range(SHRINKAGE_BOOT_B):
        take = rng.integers(0, n, size=n)
        denominator = _r2(target[take], exposure[take])
        if denominator <= 0.0:
            continue
        ratios.append(_r2(prior[take], exposure[take]) / denominator)
    if len(ratios) < SHRINKAGE_BOOT_B // 2:
        return None, None
    draws = np.sort(np.asarray(ratios, dtype=float))
    return round(float(np.quantile(draws, 0.025)), 4), round(float(np.quantile(draws, 0.975)), 4)


def exposure_loading(design: Design, predictions: Sequence[Prediction]) -> dict[str, Any]:
    """The shrinkage diagnostic, and the number that says how to read it.

    The declared threshold is absolute: if more than a quarter of the fitted
    prior is explained by maps played and teammate concentration, the plan says
    the phase has produced a shrinkage map rather than a rating.

    Measured, the prior comes in at 0.26 — and **the target it is fitted to comes
    in at 0.30**, essentially all of it maps played. So the absolute threshold
    asks the prior to load on exposure less than the quantity it predicts does,
    which no faithful fit can do while remaining faithful. The ratio is what
    carries the information the threshold was reaching for: below 1.0 the fit
    attenuates the target's own exposure loading, above 1.0 it amplifies it and
    the plan's concern is live.

    Both verdicts are published, the superseded one included, and
    `THRESHOLD_HISTORY` carries what moved it. A threshold re-cut once the result
    is visible is worthless unless the re-cutting is on the record.

    The exposure columns are held outside the design for any of this to mean
    anything. The first version admitted them as features and then measured how
    well they explained the result: R² came back at 0.60, which said nothing
    about the box score and everything about having handed the fit the answer. A
    diagnostic whose regressors are also its inputs is a tautology with a
    threshold on it.
    """
    if not predictions:
        return {"available": False, "reason": "nothing was predicted"}
    index = {(t.player_id, t.season_id): i for i, t in enumerate(design.targets)}
    take = np.asarray([index[(p.player_id, p.season_id)] for p in predictions], dtype=int)
    exposure = design.exposure[take]
    prior_r2 = _r2(np.array([p.mean for p in predictions], dtype=float), exposure)
    target_r2 = _r2(design.y[take], exposure)
    ratio = None if target_r2 <= 0.0 else round(prior_r2 / target_r2, 4)
    prior_mean = np.array([p.mean for p in predictions], dtype=float)
    lo, hi = _ratio_interval(prior_mean, design.y[take], exposure)
    # A crossing inside the interval is a crossing the data does not establish.
    # It is still reported as a crossing, and the gate reads `passes`.
    established = ratio is not None and lo is not None and lo > SHRINKAGE_RATIO_MAX
    return {
        "available": True,
        "what": (
            "the share of the fitted prior explained by maps played and teammate "
            "concentration alone, beside the same share of the target it is fitted to"
        ),
        "prior_r2": round(prior_r2, 4),
        "target_r2": round(target_r2, 4),
        "prior_r2_maps_only": round(
            _r2(np.array([p.mean for p in predictions]), exposure[:, :1]), 4
        ),
        "target_r2_maps_only": round(_r2(design.y[take], exposure[:, :1]), 4),
        "ratio": ratio,
        "ratio_max": SHRINKAGE_RATIO_MAX,
        "ratio_lo95": lo,
        "ratio_hi95": hi,
        "ratio_interval_rule": (
            "the cap is on the ratio, and a crossing counts when the whole "
            f"interval clears it: {SHRINKAGE_BOOT_B} paired resamples of the rows"
        ),
        "crosses_point_estimate": bool(ratio is not None and ratio > SHRINKAGE_RATIO_MAX),
        "passes": bool(ratio is not None and not established),
        "superseded_threshold": {
            "declared_max": SUPERSEDED_SHRINKAGE_R2_MAX,
            "would_have_passed": bool(prior_r2 <= SUPERSEDED_SHRINKAGE_R2_MAX),
            "why_it_moved": THRESHOLD_HISTORY[0]["because"],
        },
    }


# ------------------------------------------------------------------ the ladder


def _pearson(a: FloatArray, b: FloatArray) -> float:
    if len(a) < 3 or a.std() == 0.0 or b.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _aligned(design: Design, predictions: Sequence[Prediction]) -> FloatArray:
    """The observed coefficient for each predicted row, in the order predicted."""
    index = {(t.player_id, t.season_id): i for i, t in enumerate(design.targets)}
    take = np.asarray([index[(p.player_id, p.season_id)] for p in predictions], dtype=int)
    return take


def _cluster_draws(players: Sequence[int], b: int, key: FloatArray) -> list[FloatArray]:
    """`b` resamples of the predicted rows, drawn whole players at a time.

    A player supplies up to seven rows and they share a career, a role and an
    identity resolution, so the row is not the independent unit. Seeded from the
    scored values rather than from the player ids, so a reload that renumbers
    the table cannot move the interval.
    """
    groups: dict[int, list[int]] = defaultdict(list)
    for i, pid in enumerate(players):
        groups[pid].append(i)
    ordered = sorted(groups, key=lambda pid: (tuple(float(key[i]) for i in groups[pid]), pid))
    members = [np.asarray(groups[pid], dtype=int) for pid in ordered]
    rng = stream(LADDER_SEED, key)
    picks = rng.integers(0, len(members), size=(b, len(members)))
    return [np.concatenate([members[j] for j in row]) for row in picks]


def _arm_scores(design: Design, predictions: Sequence[Prediction]) -> dict[str, Any]:
    take = _aligned(design, predictions)
    y = design.y[take]
    mean = np.array([p.mean for p in predictions], dtype=float)
    residual = y - mean
    return {
        "n_scored": len(predictions),
        "out_of_fold_r": None if np.isnan(_pearson(mean, y)) else round(_pearson(mean, y), 4),
        "rmse": round(float(np.sqrt((residual**2).mean())), 5),
        "spread_of_prediction": round(float(mean.std(ddof=1)), 5),
        "folds": sorted({p.year for p in predictions}),
    }


def ladder(design: Design, w: FloatArray, draws: int = LADDER_DRAWS) -> dict[str, Any]:
    """Ridge against a random forest against a boosted tree, on identical folds.

    The rule this reports against was written before any of the three was
    fitted: a boosted arm ships only if it beats the regularized linear one on a
    paired bootstrap whose interval excludes zero, and if it does not, the
    dependency is never merged. 431 training rows against a target whose
    measurement error exceeds its signal on 94% of them is the regime where a
    boosted fit finds structure that does not replicate, so the expected result
    is that the ridge publishes and the other two are recorded and dropped.

    Every arm gets the same folds, the same inverse-variance weights and the same
    drawn targets — the draws are seeded from the targets' contents, so all three
    see byte-identical noise. The statistic is the out-of-fold correlation with
    the observed coefficient, and the comparison is paired on the row and
    clustered on the player.

    An arm that is not installed is reported as unavailable with the reason,
    beside whatever verdict is already recorded for it in `LADDER_HISTORY`. That
    is not a gap: a dependency dropped for losing cannot be re-measured every
    run, and a phase that quietly stops mentioning an arm it once compared is
    how a ladder becomes a single fit with a story attached.
    """
    fitted: dict[str, list[Prediction]] = {}
    arms: dict[str, Any] = {}
    for name in (RIDGE, FOREST, BOOSTED):
        ok, reason = arm_available(name)
        recorded = next((dict(e) for e in LADDER_HISTORY if e["arm"] == name), None)
        if not ok:
            arms[name] = {"available": False, "reason": reason, "recorded_verdict": recorded}
            continue
        predictions = walk_forward(design, w, draws=draws, arm=name)
        fitted[name] = predictions
        arms[name] = {
            "available": True,
            "draws": draws,
            **_arm_scores(design, predictions),
            "recorded_verdict": recorded,
        }

    base = fitted.get(RIDGE, [])
    if base:
        take = _aligned(design, base)
        y = design.y[take]
        base_mean = np.array([p.mean for p in base], dtype=float)
        resamples = _cluster_draws([p.player_id for p in base], LADDER_B, y)
        for name, predictions in fitted.items():
            if name == RIDGE:
                continue
            # Paired on the row: both arms predicted the same player-seasons in
            # the same fold order, so the difference is taken before the draw
            # rather than between two independently resampled populations.
            arm_mean = np.array([p.mean for p in predictions], dtype=float)
            point = _pearson(arm_mean, y) - _pearson(base_mean, y)
            deltas = [_pearson(arm_mean[t], y[t]) - _pearson(base_mean[t], y[t]) for t in resamples]
            kept = [d for d in deltas if not np.isnan(d)]
            lo, hi = (
                (
                    round(float(np.percentile(kept, 2.5)), 4),
                    round(float(np.percentile(kept, 97.5)), 4),
                )
                if kept
                else (None, None)
            )
            excludes_zero = bool(lo is not None and hi is not None and (lo > 0.0 or hi < 0.0))
            arms[name]["vs_ridge"] = {
                "what": "out-of-fold r(arm) − r(ridge), paired on the row, clustered on the player",
                "delta_r": None if np.isnan(point) else round(float(point), 4),
                "lo": lo,
                "hi": hi,
                "b": LADDER_B,
                "excludes_zero": excludes_zero,
                "ships": bool(excludes_zero and not np.isnan(point) and point > 0.0),
            }

    published = RIDGE
    for name in (BOOSTED, FOREST):
        if arms.get(name, {}).get("vs_ridge", {}).get("ships"):
            published = name
    return {
        "what": (
            "the three arms the phase declared, fitted on identical folds, weights and drawn "
            "targets, with the boosted arm's pre-declared shipping rule applied to the result"
        ),
        "rule": (
            "a non-linear arm publishes only if its out-of-fold correlation beats the ridge's "
            "by a paired bootstrap interval excluding zero; otherwise the ridge publishes and "
            "the dependency is not carried"
        ),
        "arms": arms,
        "published_arm": published,
        "predictions": fitted,
    }


# ------------------------------------------------------------------- the blend


@dataclass(frozen=True)
class Skill:
    """One published rating: what the box score expected, what the maps said."""

    player_id: int
    season_id: int
    year: int
    prior_mean: float
    prior_sd: float
    coef: float
    se: float
    skill: float
    skill_sd: float
    weight_prior: float
    model: str


def blend(
    coef: float, se: float, prior_mean: float, prior_var: float
) -> tuple[float, float, float]:
    """Normal-normal, per row: the posterior mean, its SD, and the prior's share.

    `hierarchical.posterior` is the same algebra and cannot be called here. Its
    signature is `(x, maps, model)`: it derives the observation variance as
    σ²/maps from a fitted cohort and takes the prior mean from `model.mu`, so a
    caller with a per-row prior mean and an observation variance already in hand
    would have to build a `CohortModel` per row to use it. The equivalence is
    proved in the tests instead — the same case put through both returns the same
    posterior — which reuses the machinery without contorting it.
    """
    if se <= 0.0 or prior_var <= 0.0:
        return (coef if prior_var <= 0.0 else prior_mean, 0.0, 0.0 if prior_var <= 0.0 else 1.0)
    precision_prior = 1.0 / prior_var
    precision_obs = 1.0 / se**2
    total = precision_prior + precision_obs
    mean = (precision_prior * prior_mean + precision_obs * coef) / total
    return (float(mean), float(np.sqrt(1.0 / total)), float(precision_prior / total))


def prior_variance(design: Design, predictions: Sequence[Prediction]) -> dict[str, Any]:
    """How wrong the prior is out of fold, which is the variance the blend uses.

    Out of fold rather than in sample: a training residual is the penalty's
    choice of fit, and using it would tell the blend the prior is more certain
    than anything has shown it to be.

    Two numbers are published and the raw one is used. The residual against the
    observed coefficient contains the coefficient's own measurement error as well
    as the prior's error, so subtracting the mean observation variance from it
    estimates the prior's error against the truth. That correction is the smaller
    of the two and would push more weight onto the prior; taking the raw one
    keeps the weight on the direct estimate wherever the two disagree, which is
    the conservative direction for a phase whose finding is that the prior ends
    up carrying the answer.
    """
    if not predictions:
        return {"available": False, "reason": "nothing was predicted"}
    take = _aligned(design, predictions)
    residual = design.y[take] - np.array([p.mean for p in predictions], dtype=float)
    raw = float(residual.var(ddof=1))
    mean_obs_var = float((design.se[take] ** 2).mean())
    return {
        "available": True,
        "what": "the out-of-fold residual variance of the prior, which the blend treats as its own",
        "n": len(predictions),
        "residual_var": round(raw, 6),
        "residual_sd": round(float(np.sqrt(raw)), 5),
        "mean_observation_var": round(mean_obs_var, 6),
        "noise_adjusted_var": round(max(raw - mean_obs_var, 0.0), 6),
        "used": "residual_var",
        "why": (
            "the noise-adjusted figure would put more weight on the prior; the raw one is the "
            "conservative choice for a phase whose finding is that the prior carries the answer"
        ),
        "value": raw,
    }


def blend_all(
    design: Design, predictions: Sequence[Prediction], prior_var: float, model: str
) -> list[Skill]:
    """SKILL, one row per predicted player-season.

    The per-row `prior_sd` stored beside the rating is the spread of that row's
    prediction across the target draws — what the fit does not know about this
    player. The variance the blend weights by is the pooled out-of-fold residual,
    which is what the fit does not know about anyone; a per-row draw spread is a
    measure of the target's noise reaching the fit, not of the fit's accuracy,
    and weighting by it would make a confidently wrong prior win.
    """
    take = _aligned(design, predictions)
    out: list[Skill] = []
    for prediction, i in zip(predictions, take, strict=True):
        target = design.targets[i]
        mean, sd, weight = blend(target.coef, target.se, prediction.mean, prior_var)
        out.append(
            Skill(
                player_id=target.player_id,
                season_id=target.season_id,
                year=target.year,
                prior_mean=prediction.mean,
                prior_sd=prediction.sd,
                coef=target.coef,
                se=target.se,
                skill=mean,
                skill_sd=sd,
                weight_prior=weight,
                model=model,
            )
        )
    return out


# -------------------------------------------------------------- the diagnostic


def style_correlations(conn: Any, skills: Sequence[Skill]) -> dict[str, Any]:
    """What kind of player the prior likes, measured against the style axes.

    The axes are deliberately not features: they are fitted per era over the
    whole era at once, so their loadings have seen the seasons this fit predicts,
    and the column set they are built from moves with the seasons included. The
    correlation is read-only and arrives after the fact.

    It reads the newest stored style run, which is the *previous* pipeline run's
    — this stage runs before `player_style` and the stage order is not moved for
    a diagnostic. The run it read is published with the numbers so nobody reads
    them as this run's.
    """
    row = conn.execute(
        "SELECT max(run_id) FROM player_style_season WHERE run_id IN"
        " (SELECT id FROM model_runs WHERE model = 'player_style')"
    ).fetchone()
    run_id = None if row is None or row[0] is None else int(row[0])
    if run_id is None:
        return {"available": False, "reason": "no player_style run has been stored yet"}
    rows = conn.execute(
        "SELECT player_id, season_id, axis, score FROM player_style_season WHERE run_id = %s",
        (run_id,),
    ).fetchall()
    scores: dict[str, dict[tuple[int, int], float]] = defaultdict(dict)
    for r in rows:
        scores[str(r[2])][(int(r[0]), int(r[1]))] = float(r[3])

    by_axis: dict[str, Any] = {}
    for axis, values in sorted(scores.items()):
        pairs = [
            (s.skill, values[(s.player_id, s.season_id)])
            for s in skills
            if (s.player_id, s.season_id) in values
        ]
        if len(pairs) < 20:
            by_axis[axis] = {"n": len(pairs), "r": None}
            continue
        r = _pearson(
            np.array([p[0] for p in pairs], dtype=float),
            np.array([p[1] for p in pairs], dtype=float),
        )
        by_axis[axis] = {"n": len(pairs), "r": None if np.isnan(r) else round(float(r), 4)}
    return {
        "available": True,
        "what": (
            "the fitted rating against each published style axis. Read-only: the axes are a "
            "per-era unsupervised basis and never enter the design"
        ),
        "style_run_id": run_id,
        "read_from": "the newest stored style run, which precedes this one",
        "significance_claimed": False,
        "by_axis": by_axis,
    }


# ------------------------------------------------------------- what gets stored


def params(design: Design, tau2: float, published_arm: str) -> dict[str, Any]:
    """The run's parameters: everything needed to say what was fitted."""
    return {
        "feature_set_version": FEATURE_SET,
        "target_scope": TARGET_SCOPE,
        "target_resolution": TARGET_RESOLUTION,
        "draws": DRAWS,
        "ladder_draws": LADDER_DRAWS,
        "seed": SEED,
        "ladder_seed": LADDER_SEED,
        "ladder_b": LADDER_B,
        "lambda_grid": [LAMBDA_GRID[0], LAMBDA_GRID[-1], len(LAMBDA_GRID)],
        "n_train": design.n,
        "n_columns": len(design.columns),
        "tau2": round(float(tau2), 9),
        "published_arm": published_arm,
        "shrinkage_ratio_max": SHRINKAGE_RATIO_MAX,
    }


def coefficients(design: Design, w: FloatArray) -> dict[str, Any]:
    """The published arm's weights over every training row, for attribution.

    Fitted on everything rather than per fold: the folds decide whether the prior
    predicts, and this says what it reads. A per-fold table would be seven sets
    of weights over a design of sixteen columns and no reader would be better
    off.
    """
    if design.n == 0:
        return {"available": False, "reason": "no training rows"}
    fit = fit_ridge(design.x, design.y, w)
    weighted = sorted(
        (
            {"column": name, "beta": round(float(b), 5)}
            for name, b in zip(design.columns, fit.beta, strict=True)
        ),
        key=lambda item: -abs(float(str(item["beta"]))),
    )
    return {
        "available": True,
        "arm": RIDGE,
        "lambda": fit.lam,
        "effective_df": fit.effective_df,
        "intercept": round(fit.intercept, 5),
        "columns": list(design.columns),
        "by_column": weighted,
    }


def statement(signal: dict[str, Any], exposure: dict[str, Any], blended: Sequence[Skill]) -> str:
    """One sentence on what the architecture did to the persistence failure.

    The phase committed to publishing this either way, so it is assembled from
    the measurements rather than written once and left behind by them.
    """
    if not blended:
        return "nothing was fitted: no player-season carried both a profile and a coefficient"
    weight = float(np.mean([s.weight_prior for s in blended]))
    where = (
        "the plus-minus establishes no between-player variance for the blend to defend, so "
        if signal.get("collapsed")
        else "the plus-minus carries between-player variance, and "
    )
    return (
        f"{where}the posterior takes {weight:.0%} of its weight from the box-score prior; "
        f"SKILL is therefore the prior with the direct estimate as a correction, not a blend "
        f"of two comparable estimates. The prior loads {exposure.get('prior_r2')} on exposure "
        f"against the target's own {exposure.get('target_r2')}"
    )


def artifact(
    design: Design,
    tau2: float,
    signal: dict[str, Any],
    ladder_block: dict[str, Any],
    exposure: dict[str, Any],
    variance: dict[str, Any],
    blended: Sequence[Skill],
    style: dict[str, Any],
    coefficient_block: dict[str, Any],
) -> dict[str, Any]:
    """The payload the gate and the methodology page both read."""
    years = sorted({s.year for s in blended})
    return {
        "what": (
            "the box score fitted to predict the season plus-minus, and the posterior that "
            "blends the two into a published rating"
        ),
        "version": VERSION,
        "feature_set_version": FEATURE_SET,
        "target": {
            "scope": TARGET_SCOPE,
            "resolution": TARGET_RESOLUTION,
            "n": design.n,
            "players": len({t.player_id for t in design.targets}),
            "seasons": design.years,
            "excluded": {
                "what": (
                    "the CWL era stores one estimate against each of the three seasons it "
                    "covers, so training on those rows would enter one observation three times"
                ),
                "resolution": statespace.ERA,
            },
            **design.dropped,
        },
        "design": {
            "n_columns": len(design.columns),
            "columns": list(design.columns),
            "exposure_held_outside": ["maps", "teammate_concentration"],
        },
        "target_signal": signal,
        "weights": {
            "what": "1/(se² + τ̂²), the inverse posterior variance of each observation",
            "tau2": round(float(tau2), 9),
        },
        "ladder": {k: v for k, v in ladder_block.items() if k != "predictions"},
        "coefficients": coefficient_block,
        "exposure_loading": exposure,
        "prior_variance": variance,
        "blend": {
            "what": (
                "inverse-variance posterior of the prior mean and the filtered coefficient, "
                "per player-season"
            ),
            "n": len(blended),
            "seasons": years,
            "mean_weight_prior": (
                round(float(np.mean([s.weight_prior for s in blended])), 4) if blended else None
            ),
            "min_weight_prior": (
                round(float(min(s.weight_prior for s in blended)), 4) if blended else None
            ),
            "max_weight_prior": (
                round(float(max(s.weight_prior for s in blended)), 4) if blended else None
            ),
            "corr_with_coef": (
                round(
                    float(
                        _pearson(
                            np.array([s.skill for s in blended], dtype=float),
                            np.array([s.coef for s in blended], dtype=float),
                        )
                    ),
                    4,
                )
                if len(blended) > 2
                else None
            ),
            "corr_with_prior": (
                round(
                    float(
                        _pearson(
                            np.array([s.skill for s in blended], dtype=float),
                            np.array([s.prior_mean for s in blended], dtype=float),
                        )
                    ),
                    4,
                )
                if len(blended) > 2
                else None
            ),
        },
        "style_diagnostic": style,
        "threshold_history": [dict(entry) for entry in THRESHOLD_HISTORY],
        "ladder_history": [dict(entry) for entry in LADDER_HISTORY],
        "statement": statement(signal, exposure, blended),
    }


def headline(payload: dict[str, Any]) -> str:
    """One line for the run log."""
    blend_block = payload["blend"]
    signal = payload["target_signal"]
    return (
        f"{blend_block['n']} player-seasons rated on {payload['design']['n_columns']} columns "
        f"by {payload['ladder']['published_arm']}; τ̂²="
        f"{signal.get('tau2')} "
        + ("(collapsed)" if signal.get("collapsed") else "(distinguishable)")
        + f", mean prior weight {blend_block['mean_weight_prior']}"
    )
