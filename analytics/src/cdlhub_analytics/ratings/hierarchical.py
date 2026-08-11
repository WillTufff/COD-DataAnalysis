"""The composite rating as a posterior. Spec: /methodology#player-rating.

The rating used to be built from two separate devices bolted together. A
player's score was z-scored against the *observed* spread of qualified players'
scores, and then multiplied by m / (m + k) to pull short seasons toward the
mean. Both steps have the right instinct and neither is a model: the observed
spread is inflated by the very per-map noise the second step exists to discount,
so the scale and the shrinkage were estimated against each other, and the
published uncertainty had to come from a bootstrap bolted on afterwards.

One two-level normal-normal model states all of it at once. Within a cohort
c = (season × mode), a player's per-map score is a noisy read of a true score,
and true scores are spread across the league:

    y_ij = θ_i + ε_ij,   ε_ij ~ N(0, σ²_c)      what a map measures
    θ_i  ~ N(μ_c, τ²_c)                          how good players actually are

The player's season is summarized by x_i, the score of the season profile
(sum-then-divide, as everywhere else), whose sampling variance is v_i = σ²_c/m_i
over m_i maps. Then the posterior for θ_i is available in closed form, with no
sampler and no PPL dependency:

    B_i = τ² / (τ² + v_i)          how much of this season is signal
    θ̂_i = μ + B_i (x_i − μ)        posterior mean
    V_i = B_i v_i                  posterior variance

Three things fall out of that which the old pipeline could not state:

1. **The shrinkage is the model, not a correction to it.** B_i = m_i/(m_i + k)
   with k = σ²/τ² exactly, so the estimated-k work this replaces is recovered
   rather than discarded — it was the posterior mean of this model all along.
2. **The scale is τ, the real spread between players**, not the observed spread
   of season scores. The observed spread is τ² + mean(v) wide; dividing by it
   published a league SD that was partly noise, and made "one rating SD" mean
   something slightly different in every cohort depending on how many maps its
   players happened to play.
3. **The interval is the posterior's**, √(B_i v_i), and needs no bootstrap. It
   is also *not* the bootstrap: resampling a shrunk point estimate measures how
   much that estimate wobbles, B_i√v_i, which is √B_i times smaller. The old
   ±rating_sd was answering "how much would this number move on other maps",
   not "how sure are we about this player", and for a short season those differ
   by a factor of two or more. `observation_check` keeps the bootstrap around as
   an audit of the one assumption the closed form makes — that the season
   profile's sampling variance really is σ²/m.

(μ, τ²) are fitted by EM, which for this model is a handful of closed-form lines
per step and monotone in the marginal likelihood; σ² comes from within-player
replication, i.e. how much a player's own maps disagree with each other. Every
player in the cohort is fitted, not only the qualified ones, for the same reason
the shrinkage always used all of them: the prior is applied to short seasons, so
estimating it from long ones alone would describe a different population.

A cohort with no replication at all cannot support the fit. It falls back to the
constant the old pipeline asserted (k = 15) and says so in the artifact rather
than silently flattening.

A cohort whose between-player variance collapses to zero is a different case and
publishes nothing. τ² = 0 says the players in it are not distinguishable by these
features, and the rating that follows — everybody at exactly 1.00 — is a claim
that they tied rather than an abstention from the claim. So the rating is stored
NULL and the cohort is flagged, which is the rule the metric layer already
applies below MIN_Z_COHORT.

Where 1.00 sits is a presentation choice and stays the one the site already
publishes: the origin is the qualified cohort's mean posterior score, so the
qualified average is still 1.00, and RATING_SCALE still puts a rating point at
0.15. What changed underneath is that the unit is now τ.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

import numpy as np
import psycopg

from ..era import MIN_MAPS
from ..maprows import Coverage, MapRow
from ..regress import FloatArray
from ..resample import stream as resample_stream
from . import player_rating as pr

EM_TOL = 1e-12  # relative change in τ² that ends the iteration
# A cohort with real spread converges in tens of steps. One whose τ² is heading
# for zero crawls, because the fixed point sits on the boundary — the cap is set
# high enough that those finish too, since "did not converge" and "converged at
# almost no signal" would otherwise be reported as the same thing.
EM_MAX_ITERS = 100_000

# τ² = 0 is a fixed point of the EM map: b = τ²/(τ²+v) is zero, so θ̂ = μ, so the
# M-step returns zero again and the loop exits "converged" on its first step
# having looked at nothing. The moment estimate Var(x) − mean(v) lands exactly
# there whenever it goes negative, which is a property of σ² being large rather
# than of the players being alike. So the start is floored at a strictly positive
# share of the observed spread. EM is monotone in the marginal likelihood, so a
# cohort whose optimum really is on the boundary still descends to it — it just
# has to get there by iterating rather than by starting on it.
TAU2_START_FLOOR = 0.01

# What counts as collapsed: τ² this small a share of the mean observation
# variance is no signal at all. Tested on the fit rather than on the optimizer,
# because a run that never iterated is not a run that failed to converge. The
# cohorts that fit report 0.15 and up on this ratio, so the floor sits an order
# of magnitude below anything the archive has produced.
TAU2_COLLAPSE_FLOOR = 0.01

# The audit of v_i = σ²/m: maps resampled within a player-season, same scheme and
# seed as every other bootstrap here.
CHECK_B = 200
CHECK_SEED = pr.BOOTSTRAP_SEED

# Cohorts where the fit cannot be supported keep the constant the old pipeline
# used, so the fallback is the previous published behaviour rather than a new
# invention. See `_fallback`.
FALLBACK_K = pr.SHRINK_FALLBACK


# --------------------------------------------------------------- the model


@dataclass(frozen=True)
class CohortModel:
    """The fitted variance components for one (season × mode) cohort."""

    mu: float  # population mean of true scores, raw score units
    tau2: float  # τ², spread of true scores between players
    sigma2: float  # σ², per-map noise around a player's own true score
    origin: float  # posterior score that publishes as 1.00 (qualified mean)
    n_players: int
    n_maps: int
    n_replicated: int  # players with two or more maps, i.e. those σ² is read from
    iterations: int
    converged: bool
    estimated: bool  # False when the cohort fell back to the constant
    fallback: str | None
    loglik: float | None
    calibration: dict[str, Any] | None = None  # see `calibrate`

    @property
    def tau(self) -> float:
        return math.sqrt(self.tau2)

    @property
    def sigma(self) -> float:
        return math.sqrt(self.sigma2)

    @property
    def implied_k(self) -> float:
        """σ²/τ², the maps-to-keep-half-the-signal constant this model implies."""
        return self.sigma2 / self.tau2 if self.tau2 > 0.0 else float("inf")

    @property
    def mean_obs_var(self) -> float:
        """mean(v_i) at the cohort's average map count: what one season measures."""
        return self.sigma2 / max(self.n_maps / self.n_players, 1.0)

    @property
    def collapsed(self) -> bool:
        """τ² is at zero: the cohort's players are not distinguishable.

        A property of the fit, not of the optimizer. The condition being tested
        is that between-player variance is negligible beside what a single
        season's worth of maps measures, which is true whether EM crawled to the
        boundary over a hundred thousand steps or started on it. Testing the
        symptom instead — "did not converge" — misses the second case entirely,
        which is how two seasons came to publish 1.00 for every player.
        """
        return self.estimated and self.tau2 <= TAU2_COLLAPSE_FLOOR * self.mean_obs_var

    @property
    def stalled(self) -> bool:
        """EM ran out of iterations. A separate condition from collapsing: it
        says the fit is untrustworthy, not that the answer is zero."""
        return self.estimated and not self.converged


@dataclass(frozen=True)
class Posterior:
    """One player-season-mode, on the cohort's published scale."""

    z: float  # posterior mean in units of τ, centred on the cohort origin
    sd: float  # posterior SD in the same units
    shrinkage: float  # B_i, the share of the observed score that survived


def posterior(x: float, maps: int, model: CohortModel) -> Posterior:
    """The closed-form posterior for one season score."""
    v = model.sigma2 / max(maps, 1)
    b = model.tau2 / (model.tau2 + v) if model.tau2 + v > 0.0 else 0.0
    mean = model.mu + b * (x - model.mu)
    tau = model.tau or 1.0
    return Posterior(z=(mean - model.origin) / tau, sd=math.sqrt(b * v) / tau, shrinkage=b)


def _loglik(x: FloatArray, v: FloatArray, mu: float, tau2: float) -> float:
    """Marginal log-likelihood: x_i ~ N(μ, τ² + v_i), independent across players."""
    s2 = tau2 + v
    return float(-0.5 * np.sum(np.log(2.0 * math.pi * s2) + (x - mu) ** 2 / s2))


def _em(x: FloatArray, v: FloatArray, tau2_init: float) -> tuple[float, float, int, bool]:
    """ML for (μ, τ²) with the per-player observation variances v_i known.

    The E-step is the posterior above; the M-step averages its mean and variance.
    Both are one line, both are monotone in the marginal likelihood, and neither
    can take τ² negative — which is the practical reason to prefer this over the
    moment estimator it replaces, whose whole failure mode was a negative
    variance that had to be caught and explained.
    """
    tau2 = max(tau2_init, 1e-12)
    mu = float(x.mean())
    converged = False
    iterations = 0
    while iterations < EM_MAX_ITERS and not converged:
        iterations += 1
        w = 1.0 / (tau2 + v)
        mu = float((w * x).sum() / w.sum())
        b = tau2 / (tau2 + v)
        theta = mu + b * (x - mu)
        updated = float((b * v + (theta - mu) ** 2).mean())
        converged = abs(updated - tau2) <= EM_TOL * max(1.0, tau2)
        tau2 = updated
    return (mu, tau2, iterations, converged)


def _fallback(x: FloatArray, maps: FloatArray, reason: str, n_replicated: int) -> CohortModel:
    """The old constant, restated as variance components.

    k = σ²/τ² is asserted at 15; the marginal spread of the observed scores then
    pins the pair, since Var(x_i) = τ² + kτ²/m_i. So the cohort keeps exactly the
    shrinkage the pipeline used before this model existed, and the artifact
    records that it did.
    """
    spread = float(((x - x.mean()) ** 2).mean())
    tau2 = spread / (1.0 + FALLBACK_K * float(np.mean(1.0 / maps)))
    tau2 = max(tau2, 1e-12)
    return CohortModel(
        mu=float(x.mean()),
        tau2=tau2,
        sigma2=FALLBACK_K * tau2,
        origin=float(x.mean()),
        n_players=len(x),
        n_maps=int(maps.sum()),
        n_replicated=n_replicated,
        iterations=0,
        converged=False,
        estimated=False,
        fallback=reason,
        loglik=None,
    )


def pooled_map_variance(
    members: Sequence[pr.PlayerModeAgg],
    feat_mu: FloatArray,
    feat_sd: FloatArray,
    weights: FloatArray,
) -> tuple[float, int, int]:
    """σ̂², the per-map score variance around each player's own mean.

    Pooled across players with the usual (m_i − 1) weighting, so a player with a
    full season contributes more to it than one with three maps. Returns
    (σ̂², players with replication, maps read).
    """
    ss = 0.0
    df = 0
    n_maps = 0
    replicated = 0
    for a in members:
        y = pr.map_scores(a, feat_mu, feat_sd, weights)
        n_maps += len(y)
        if len(y) < 2:
            continue
        replicated += 1
        ss += float(((y - y.mean()) ** 2).sum())
        df += len(y) - 1
    return (ss / df if df > 0 else 0.0, replicated, n_maps)


def season_score(agg: pr.PlayerModeAgg, scale: pr.CohortScale, fit: pr.ModeFit) -> float:
    """x_i: the season profile on the cohort's feature scale, dotted with the
    weights that cohort learned. Left in raw units — dividing this by an observed
    spread is one of the two things the model exists to stop doing."""
    return float(((agg.feats - scale.feat_mu) / scale.feat_sd) @ fit.weights)


def resample_score_var(
    agg: pr.PlayerModeAgg,
    scale: pr.CohortScale,
    fit: pr.ModeFit,
    b: int = CHECK_B,
) -> float | None:
    """The sampling variance of x_i, by resampling this player's own maps.

    x_i is a ratio of sums, not a mean of per-map scores, so its sampling
    variance is only approximately σ²/m. Rather than assume the approximation and
    caveat it, the cohort measures how far off it is (see `calibrate`) — one
    vectorized resample per player-season, settling by measurement a question the
    old shrinkage answered by assumption.

    The m/(m−1) factor is the nonparametric bootstrap's own small-sample bias and
    not a correction to the model: resampling m maps with replacement estimates
    the variance of a mean as (m−1)/m of the truth, which on the six-map seasons
    this archive is full of is a 17% understatement. Leaving it in would let the
    calibration read a property of the bootstrap as a property of the season
    profile and pass it into σ². Variances, not SDs, for the same reason: E[s²] is
    σ² exactly, while E[s] is not, and on four-map seasons that gap is 8%.
    """
    if agg.maps < 2:
        return None
    # This player's own maps, ordered by their own contents, drawn from a stream
    # this player's own contents seed. The generator used to be threaded through
    # a loop over players in `player_id` order, so every player's variance
    # estimate depended on their position in the loader's numbering — and the
    # cohort factor built from those estimates moved when the numbering did.
    numerators, denominators = pr.stat_order(agg)
    rng = resample_stream(CHECK_SEED, numerators, denominators)
    idx = rng.integers(0, agg.maps, size=(b, agg.maps))
    totals = denominators[idx].sum(axis=1)  # (b, F)
    ok = np.all(totals > 0, axis=1)
    if ok.sum() < 2:
        return None
    feats = numerators[idx].sum(axis=1)[ok] / totals[ok]
    draws = ((feats - scale.feat_mu) / scale.feat_sd) @ fit.weights
    m = float(agg.maps)
    return float(np.var(draws, ddof=1)) * m / (m - 1.0)


def calibrate(
    members: Sequence[pr.PlayerModeAgg],
    scale: pr.CohortScale,
    fit: pr.ModeFit,
    sigma2: float,
    b: int = CHECK_B,
) -> tuple[float, dict[str, Any]]:
    """How wrong σ²/m is for this cohort, as a factor to multiply it by.

    Every player-season's score is resampled from its own maps, and the ratio of
    that variance to the σ²/m the closed form assumes is one player's answer. The
    cohort applies the mean of those answers: each is unbiased and extremely
    noisy — a six-map season's variance estimate is nearly meaningless on its own
    — so averaging over a hundred and fifty of them is what makes the correction
    a measurement rather than a guess. One factor per cohort, never per player.

    This matters beyond tidiness. v_i enters τ² = Var(x) − mean(v) with a minus
    sign, so an observation variance that is too large does not merely widen the
    intervals — it eats the between-player variance and reports a cohort as
    flatter than it really is.
    """
    ratios: list[float] = []
    for a in sorted(members, key=lambda a: a.player_id):
        assumed = sigma2 / a.maps
        drawn = resample_score_var(a, scale, fit, b) if assumed > 0.0 else None
        if drawn is not None:
            ratios.append(drawn / assumed)
    # Averaged, so the order this loop ran in no longer reaches the factor at
    # all: each member draws from its own stream.
    if len(ratios) < 10:
        return (1.0, {"n": len(ratios), "applied": 1.0, "ratio": None})
    factor = float(np.mean(ratios))
    q = np.percentile(ratios, [25, 50, 75])
    return (
        factor,
        {
            "n": len(ratios),
            # Reported as SD ratios, which is the scale the intervals live on.
            "ratio": round(math.sqrt(factor), 4),
            "p25": round(math.sqrt(float(q[0])), 4),
            "median": round(math.sqrt(float(q[1])), 4),
            "p75": round(math.sqrt(float(q[2])), 4),
            "applied": round(factor, 4),
        },
    )


def fit_cohort(
    members: Sequence[pr.PlayerModeAgg],
    scale: pr.CohortScale,
    fit: pr.ModeFit,
    b: int = CHECK_B,
) -> CohortModel | None:
    """Fit (μ, τ², σ²) for one cohort from its own maps."""
    usable = [a for a in members if a.maps > 0]
    if len(usable) < 2:
        return None
    x = np.array([season_score(a, scale, fit) for a in usable])
    maps = np.array([float(a.maps) for a in usable])
    raw_sigma2, replicated, _n_map_scores = pooled_map_variance(
        usable, scale.feat_mu, scale.feat_sd, fit.weights
    )
    if raw_sigma2 <= 0.0:
        model = _fallback(x, maps, "no within-player replication to estimate σ² from", replicated)
    else:
        factor, calibration = calibrate(usable, scale, fit, raw_sigma2, b)
        sigma2 = raw_sigma2 * factor
        v = sigma2 / maps
        spread = float(x.var(ddof=1))
        start = max(spread - float(v.mean()), TAU2_START_FLOOR * spread)
        mu, tau2, iterations, converged = _em(x, v, start)
        model = CohortModel(
            mu=mu,
            tau2=max(tau2, 1e-12),
            sigma2=sigma2,
            origin=mu,
            n_players=len(usable),
            n_maps=int(maps.sum()),
            n_replicated=replicated,
            iterations=iterations,
            converged=converged,
            estimated=True,
            fallback=None,
            loglik=round(_loglik(x, v, mu, tau2), 4),
            calibration=calibration,
        )
    return _recentre(model, usable, x)


def _recentre(
    model: CohortModel, members: Sequence[pr.PlayerModeAgg], x: FloatArray
) -> CohortModel:
    """Move the origin to the qualified cohort's mean posterior score.

    The fit reads every player, including the ones with four maps; the published
    1.00 has always meant "average among players the site is willing to rank".
    Keeping those two populations apart is the whole reason this is a separate
    step rather than a choice of μ.
    """
    qualified = [
        posterior(float(xi), a.maps, model).z
        for xi, a in zip(x, members, strict=True)
        if a.maps >= MIN_MAPS
    ]
    if len(qualified) < 2:
        return model
    shift = float(np.mean(qualified)) * model.tau
    return replace(model, origin=model.origin + shift)


def build_models(
    aggs: Sequence[pr.PlayerModeAgg],
    fits: dict[tuple[int, int], pr.ModeFit],
    scales: dict[tuple[int, int], pr.CohortScale],
) -> dict[tuple[int, int], CohortModel]:
    by_cohort: dict[tuple[int, int], list[pr.PlayerModeAgg]] = defaultdict(list)
    for a in aggs:
        by_cohort[(a.season_id, a.mode_id)].append(a)
    out: dict[tuple[int, int], CohortModel] = {}
    for key, members in by_cohort.items():
        scale, fit = scales.get(key), fits.get(key)
        if scale is None or fit is None:
            continue
        model = fit_cohort(members, scale, fit)
        if model is not None:
            out[key] = model
    return out


# ------------------------------------------------------------------ ratings


def compute_ratings(
    aggs: Sequence[pr.PlayerModeAgg],
    fits: dict[tuple[int, int], pr.ModeFit],
    scales: dict[tuple[int, int], pr.CohortScale],
    models: dict[tuple[int, int], CohortModel],
) -> list[pr.SeasonRating]:
    """Posterior ratings for every player-season-mode, plus the blended row.

    Per-mode rows now carry their own interval, which the old estimator could
    not give them: its bootstrap only existed for the blend. The blended row is
    the same maps-weighted average of mode scores the site already publishes —
    deliberately unchanged, so that comparing the two estimators isolates the
    estimator — and its variance is Σ w_m² V_m, the modes' posteriors being
    independent given the cohort fits, since no map enters two of them.

    A collapsed cohort contributes a row with a NULL rating and no weight in the
    blend. Its maps are still counted in the blended row's map total, because the
    player did play them and the row says how much of the season the rating
    covers; a season all of whose cohorts collapsed publishes a NULL blend.
    """
    by_player_season: dict[tuple[int, int], list[pr.PlayerModeAgg]] = defaultdict(list)
    for a in aggs:
        key = (a.season_id, a.mode_id)
        if key in models and key in scales and key in fits:
            by_player_season[(a.player_id, a.season_id)].append(a)

    out: list[pr.SeasonRating] = []
    for (pid, season_id), modes in sorted(by_player_season.items()):
        posts: list[Posterior] = []
        weights_m: list[int] = []
        all_maps = 0
        for a in modes:
            key = (a.season_id, a.mode_id)
            scale, fit, model = scales[key], fits[key], models[key]
            x = float(((a.feats - scale.feat_mu) / scale.feat_sd) @ fit.weights)
            post = posterior(x, a.maps, model)
            all_maps += a.maps
            withheld = model.collapsed
            if not withheld:
                posts.append(post)
                weights_m.append(a.maps)
            out.append(
                pr.SeasonRating(
                    player_id=pid,
                    season_id=season_id,
                    mode_id=a.mode_id,
                    maps=a.maps,
                    rating=None if withheld else 1.0 + pr.RATING_SCALE * post.z,
                    rating_sd=None if withheld else pr.RATING_SCALE * post.sd,
                )
            )
        if not posts:
            out.append(
                pr.SeasonRating(
                    player_id=pid,
                    season_id=season_id,
                    mode_id=None,
                    maps=all_maps,
                    rating=None,
                    rating_sd=None,
                )
            )
            continue
        total = float(sum(weights_m))
        share = [m / total for m in weights_m]
        blend = sum(w * p.z for w, p in zip(share, posts, strict=True))
        var = sum((w * p.sd) ** 2 for w, p in zip(share, posts, strict=True))
        out.append(
            pr.SeasonRating(
                player_id=pid,
                season_id=season_id,
                mode_id=None,
                maps=all_maps,
                rating=1.0 + pr.RATING_SCALE * blend,
                rating_sd=pr.RATING_SCALE * math.sqrt(var),
            )
        )
    return out


def rate(
    rows: Sequence[MapRow], coverage: Coverage, version: str
) -> dict[tuple[int, int, int | None], float]:
    """Posterior ratings keyed (player, season, mode), for the holdout harness.

    Mirrors `player_rating`'s prefix fit exactly — same cohorts, same weights,
    same aggregates — so the two estimators are scored on identical maps and the
    only difference between them is the one being tested.
    """
    fitted = pr.prepare(rows, coverage, version)
    if not fitted.fits:
        return {}
    models = build_models(fitted.aggs, fitted.fits, fitted.scales)
    ratings = compute_ratings(fitted.aggs, fitted.fits, fitted.scales, models)
    return {
        (r.player_id, r.season_id, r.mode_id): r.rating for r in ratings if r.rating is not None
    }


# ---------------------------------------------------------------- artifacts


def calibration_summary(models: dict[tuple[int, int], CohortModel]) -> dict[str, Any]:
    """What the per-cohort calibration came to, over the whole run.

    Published rather than folded away, because it is the size of a correction the
    old estimator applied at 1.0 without checking: if these numbers sat at 1.0,
    the σ²/m form would need no calibration and this step could go.
    """
    measured = [m.calibration for m in models.values() if m.calibration]
    ratios = [c["ratio"] for c in measured if c.get("ratio") is not None]
    if not ratios:
        return {"available": False, "reason": "no cohort had a player-season to resample"}
    return {
        "available": True,
        "bootstrap_b": CHECK_B,
        "what": "resampled SD of the season score, over the σ/√m the closed form assumes",
        "n_cohorts": len(ratios),
        "n_player_seasons": sum(int(c["n"]) for c in measured),
        "median": round(float(np.median(ratios)), 4),
        "min": round(float(min(ratios)), 4),
        "max": round(float(max(ratios)), 4),
    }


def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3:
        return None
    ra = np.argsort(np.argsort(np.asarray(a, dtype=float))).astype(float)
    rb = np.argsort(np.argsort(np.asarray(b, dtype=float))).astype(float)
    if ra.std() == 0.0 or rb.std() == 0.0:
        return None
    return float(np.corrcoef(ra, rb)[0, 1])


def compare_estimators(
    posteriors: Sequence[pr.SeasonRating], legacy: Sequence[pr.SeasonRating]
) -> dict[str, Any]:
    """How far the published leaderboard moves, and how the intervals differ.

    Both arms rate the same player-seasons from the same fits, so this is a
    paired comparison of two estimators rather than of two models. The interval
    comparison is the substantive half: the bootstrap SD and the posterior SD are
    different quantities, and the ratio between them is what says how much the
    old error bars understated.
    """
    keyed = {(r.player_id, r.season_id, r.mode_id): r for r in posteriors}
    pairs = [
        (keyed[(r.player_id, r.season_id, r.mode_id)], r)
        for r in legacy
        if (r.player_id, r.season_id, r.mode_id) in keyed
    ]
    # Player-seasons the posterior withholds have no number to difference, so
    # they are counted rather than compared: "the old estimator published a
    # rating here and this one declines to" is itself part of what changed.
    withheld = sum(1 for new, _ in pairs if new.mode_id is None and new.rating is None)
    blended = [
        (new, old, float(new.rating), float(old.rating))
        for new, old in pairs
        if new.mode_id is None and new.rating is not None and old.rating is not None
    ]
    if not blended:
        return {"available": False, "reason": "no blended row in both arms"}

    delta = np.array([n - o for _, _, n, o in blended])
    sd_ratio = [
        new.rating_sd / old.rating_sd
        for new, old, _, _ in blended
        if new.rating_sd is not None and old.rating_sd
    ]
    qualified = [(new, old, n, o) for new, old, n, o in blended if new.maps >= MIN_MAPS]
    top_new = {r[0].player_id for r in sorted(qualified, key=lambda p: -p[2])[:10]}
    top_old = {r[1].player_id for r in sorted(qualified, key=lambda p: -p[3])[:10]}
    return {
        "available": True,
        "n_player_seasons": len(blended),
        "n_qualified": len(qualified),
        "n_withheld": withheld,
        "mean_abs_delta": round(float(np.abs(delta).mean()), 5),
        "max_abs_delta": round(float(np.abs(delta).max()), 5),
        "spearman": (
            None
            if (s := _spearman([n for _, _, n, _ in blended], [o for _, _, _, o in blended]))
            is None
            else round(s, 5)
        ),
        "top10_unchanged": len(top_new & top_old),
        "interval": {
            "what": "posterior SD over the bootstrap SD it replaces, same player-seasons",
            "n": len(sd_ratio),
            "median": round(float(np.median(sd_ratio)), 4) if sd_ratio else None,
            "p25": round(float(np.percentile(sd_ratio, 25)), 4) if sd_ratio else None,
            "p75": round(float(np.percentile(sd_ratio, 75)), 4) if sd_ratio else None,
        },
    }


def artifact(
    conn: psycopg.Connection[tuple[object, ...]],
    models: dict[tuple[int, int], CohortModel],
    scales: dict[tuple[int, int], pr.CohortScale],
    version: str,
    comparison: dict[str, Any],
    check: dict[str, Any],
) -> dict[str, Any]:
    """The fitted variance components per cohort, and what they replaced.

    `signal_share` is the number this model can state and the old one could not:
    τ over the SD of the observed season scores, i.e. how much of the spread the
    leaderboard shows is real difference between players rather than the noise of
    however many maps each of them happened to play.
    """
    seasons, modes = pr.label_context(conn)
    entries = []
    for key, model in sorted(models.items()):
        season_id, mode_id = key
        scale = scales[key]
        observed = math.sqrt(model.tau2 + model.mean_obs_var)
        entries.append(
            {
                "season_id": season_id,
                "year": seasons[season_id]["year"],
                "title": seasons[season_id]["title"],
                "mode_id": mode_id,
                "mode": modes[mode_id],
                "n_players": model.n_players,
                "n_maps": model.n_maps,
                "n_replicated": model.n_replicated,
                "tau": round(model.tau, 4),
                "sigma": round(model.sigma, 4),
                "implied_k": round(model.implied_k, 2),
                # The moment estimate this replaces, from the same cohort's maps.
                "moment_k": round(scale.shrink_maps, 2),
                "signal_share": round(model.tau / observed, 4) if observed > 0 else None,
                "iterations": model.iterations,
                "converged": model.converged,
                "collapsed": model.collapsed,
                "stalled": model.stalled,
                "estimated": model.estimated,
                "fallback": model.fallback,
                "loglik": model.loglik,
                "calibration": model.calibration,
            }
        )
    ks = [e["implied_k"] for e in entries if e["estimated"] and not e["collapsed"]]
    return {
        "version": version,
        "model": "two-level normal-normal, players within (season × mode)",
        "estimator": "EM for (μ, τ²) with v_i = σ²/m_i known; σ² from within-player replication,"
        " calibrated per cohort by resampling",
        "em_tol": EM_TOL,
        "em_max_iters": EM_MAX_ITERS,
        "tau2_start_floor": TAU2_START_FLOOR,
        "tau2_collapse_floor": TAU2_COLLAPSE_FLOOR,
        "fallback_k": FALLBACK_K,
        "n_cohorts": len(entries),
        "n_fell_back": sum(1 for e in entries if not e["estimated"]),
        "n_collapsed": sum(1 for e in entries if e["collapsed"]),
        "n_stalled": sum(1 for e in entries if e["stalled"]),
        "withheld": (
            "a collapsed cohort publishes no rating: the row carries its map count"
            " and a NULL rating rather than 1.00 for every player"
        ),
        "median_k": round(float(np.median(ks)), 2) if ks else None,
        "cohorts": entries,
        "vs_z_shrink": comparison,
        "calibration": check,
    }


def compute(
    conn: psycopg.Connection[tuple[object, ...]],
    version: str,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> tuple[list[pr.SeasonRating], list[pr.MapPrediction], dict[str, dict[str, Any]]]:
    """Fit weights, fit the hierarchy, rate players, and pack every artifact.

    The signature `player_rating.compute` has, because this is the published
    estimator now and run_all should not have to know which of the two it is
    calling. The z-and-shrink arm is still computed here, with its bootstrap, so
    the comparison in the artifact is against a live run of the old pipeline
    rather than against numbers copied out of one.
    """
    if rows is None or coverage is None:
        rows, coverage = pr.load(conn)
    fitted = pr.prepare(rows, coverage, version)
    models = build_models(fitted.aggs, fitted.fits, fitted.scales)
    ratings = compute_ratings(fitted.aggs, fitted.fits, fitted.scales, models)
    legacy = pr.compute_ratings(fitted.aggs, fitted.fits, fitted.scales)
    artifacts = pr.fit_artifacts(conn, fitted, version)
    artifacts["rating_posterior"] = artifact(
        conn,
        models,
        fitted.scales,
        version,
        compare_estimators(ratings, legacy),
        calibration_summary(models),
    )
    return ratings, fitted.preds, artifacts


def compute_and_write(
    conn: psycopg.Connection[tuple[object, ...]],
    run_id: int,
    version: str = pr.DEFAULT_VERSION,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> tuple[int, list[Any], dict[str, dict[str, Any]]]:
    ratings, preds, artifacts = compute(conn, version, rows, coverage)
    return (
        pr.write(conn, run_id, ratings, artifacts),
        [m.prediction for m in preds],
        artifacts,
    )
