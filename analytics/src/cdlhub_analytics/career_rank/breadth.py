"""Breadth score: the gold-tier metric grid rolled into one number per season.

Pre-registered before the fit ran. The basket is
exactly the metrics `buildMetricCards` renders on the player page — gold tier,
minus the kill-feed categories (IW/WWII only, see the kill-feed reconciliation
note in the metrics module) and the round-card keys, with the same per-map/
per-10 de-duplication the page applies. A season's score is the coverage-
weighted mean of its qualifying (mode, metric) percentiles, weighted by each
mode's share of the player's maps that season.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import cast

import psycopg

from .. import metrics as metrics_module

Conn = psycopg.Connection[tuple[object, ...]]

FEED_CATEGORIES = {"trades", "clutch", "advantage"}
ROUND_CARD_KEYS = {
    "snd_rounds_0k_share",
    "snd_rounds_1k_share",
    "snd_rounds_2k_share",
    "snd_rounds_3k_share",
    "snd_rounds_4k_share",
    "snd_fb_net_pr",
    "snd_opening_involvement",
    "snd_survival_rate",
    "snd_zero_kill_round_rate",
}

# Minimum surviving stats for a (season, mode) slice to count at all, matching
# the page's own floor on `buildMetricCards`.
MIN_SLICE_STATS = 2

# Which family each basket metric belongs to. The catalog's own `category` is
# not this: it holds twelve mode-shaped labels (slaying, snd, ctf, control,
# hardpoint, uplink, domination, blitz, objective, discipline, streaks,
# scorestreaks), which say which mode a metric comes from and not what it
# measures. Averaging a slice over every surviving metric at equal weight makes
# a season worth however many metrics happen to point at the same thing: seven
# of the 43 read slaying volume and six read slaying efficiency, against three
# for discipline. Four of the CDL-era slaying metrics read one underlying
# event.
#
# Authored metric by metric and fixed before the first run that used it, so a
# name cannot move once a career has been seen to depend on it. `test_breadth`
# fails if any basket metric is missing from this table.
VOLUME = "volume"
EFFICIENCY = "efficiency"
OBJECTIVE = "objective"
DISCIPLINE = "discipline"
OPENING = "opening"
STREAKS = "streaks"

FAMILIES: tuple[str, ...] = (VOLUME, EFFICIENCY, OBJECTIVE, DISCIPLINE, OPENING, STREAKS)

FAMILY: dict[str, str] = {
    "kills_pm": VOLUME,
    "kills_p10": VOLUME,
    "ekia_p10": VOLUME,
    "damage_pm": VOLUME,
    "damage_p10": VOLUME,
    "kill_share": VOLUME,
    "snd_kpr": VOLUME,
    "deaths_pm": EFFICIENCY,
    "deaths_p10": EFFICIENCY,
    "plus_minus_pm": EFFICIENCY,
    "plus_minus_p10": EFFICIENCY,
    "non_traded_kill_rate": EFFICIENCY,
    "snd_dpr": EFFICIENCY,
    "hill_time_pm": OBJECTIVE,
    "hill_time_p10": OBJECTIVE,
    "hill_time_share": OBJECTIVE,
    "contested_hill_share": OBJECTIVE,
    "ctrl_caps_pm": OBJECTIVE,
    "ctf_caps_pm": OBJECTIVE,
    "ctf_returns_pm": OBJECTIVE,
    "ctf_carry_efficiency": OBJECTIVE,
    "ctf_carry_time_pm_s": OBJECTIVE,
    "ctf_flag_involvement_pm": OBJECTIVE,
    "dom_caps_pm": OBJECTIVE,
    "blitz_caps_pm": OBJECTIVE,
    "uplink_points_pm": OBJECTIVE,
    "uplink_dunk_rate": OBJECTIVE,
    "sneak_defuses_total": OBJECTIVE,
    "time_per_life_s": DISCIPLINE,
    "traded_back_rate": DISCIPLINE,
    "clean_kill_rate": DISCIPLINE,
    "ctrl_fb_rate": OPENING,
    "ctrl_fd_rate": OPENING,
    "ctrl_fb_net_pr": OPENING,
    "ctrl_opening_duel_win": OPENING,
    "snd_fb_rate": OPENING,
    "snd_fd_rate": OPENING,
    "snd_opening_duel_win": OPENING,
    "deep_streak_rate": STREAKS,
    "blitz_index_p10": STREAKS,
    "streak_kills_p10": STREAKS,
    "snd_multi_kill_round_rate": STREAKS,
    "snd_ace_total": STREAKS,
}

FAMILY_RULE = (
    "a slice scores the unweighted mean over its live families, each family "
    "contributing the mean of its own surviving percentiles; a family with no "
    "surviving metric is absent from the mean and never a zero inside it"
)

# The map count at which a season's score is worth half its distance from the
# season mean. It comes from a measurement. Season scores centred inside their
# own season have a variance that falls as 1/maps, so binning by map count and
# regressing the bin variance on the reciprocal of its mean map count splits
# the spread into a true part and a sampling part, and their ratio is this
# number. `estimate_shrink_k` below is that fit; on the 1,458 season deviations
# the archive holds it reads a true variance of 131.48 and a sampling term of
# 2,875.73, so K = 21.87, at a weighted R^2 of 0.986. The basis is maps rather
# than the surviving stat count, which the same estimator fitted far worse.
#
# Re-derived 2026-08-22, from 15.4838. Nothing about the estimator changed; its
# input was repaired. A season scored only on the pooled all-modes row carried
# no map count, and a season with no maps cannot be binned by map count, so the
# regression had been running on 1,196 of 1,458 seasons and dropping exactly
# the thin ones it exists to characterise. A sampling-noise-per-map term fitted
# with the low-map end cut off is biased by construction, and the R^2 moving
# from 0.837 to 0.989 is that bias leaving.
#
# Re-derived again 2026-08-22, from 19.9228, when the score became the mean
# over its live metric families instead of the mean over every surviving
# metric. K is the ratio of the true season-to-season variance to the sampling
# variance per map *of the score it shrinks*, so a constant fitted against the
# flat-basket score and applied to the family-weighted one is fitted against a
# distribution that no longer exists. Same estimator, same 1,458 seasons, new
# score: true variance 131.48, sampling term 2,875.73, weighted R^2 0.986. The
# refit was declared in the pre-registration before the run that applies it,
# with its acceptance written first — it may move an anchor, and if a test
# degrades the constant stands and the degradation is published.
#
# The value here is whatever that estimator returned, never a number arrived at
# some other way: a constant fitted by one implementation and published beside
# another is the drift the run report exists to catch.
#
# It is frozen rather than refitted each run, for the reason the
# pre-registration gives: a parameter that moves with the data is a target.
# `estimate_shrink_k` is published beside the run so the drift is visible.
SHRINK_K = 21.8718

# A season needs a field before it has a mean to shrink toward.
MIN_SHRINK_COHORT = 5

SHRINK_RULE = (
    f"score = season mean + (score - season mean) * maps / (maps + {SHRINK_K}); "
    f"a season in a cohort smaller than {MIN_SHRINK_COHORT} is left alone"
)

_NORMALIZED = re.compile(r"^(.+)_(p10|pm)$")


def gold_basket() -> dict[str, metrics_module.Metric]:
    """Gold-tier metrics eligible for the breadth score, keyed by metric key."""
    return {
        m.key: m
        for m in metrics_module.CATALOG
        if m.tier.startswith("gold")
        and m.category not in FEED_CATEGORIES
        and m.key not in ROUND_CARD_KEYS
    }


def _redundant_per_map(keys: Sequence[str]) -> set[str]:
    """The per-map keys to drop because their per-10 twin is also present.

    Mirrors `redundantPerMap` in `web/app/players/[slug]/page.tsx` exactly —
    same regex, same rule — so the breadth basket matches what a reader sees.
    """
    timed: set[str] = set()
    per_map: dict[str, str] = {}
    for key in keys:
        m = _NORMALIZED.match(key)
        if not m:
            continue
        stem, form = m.group(1), m.group(2)
        if form == "p10":
            timed.add(stem)
        else:
            per_map[stem] = key
    return {key for stem, key in per_map.items() if stem in timed}


@dataclass(frozen=True)
class MetricPoint:
    player_id: int
    season_id: int
    mode_id: int | None
    metric: str
    pctl: float


@dataclass(frozen=True)
class SliceMaps:
    player_id: int
    season_id: int
    mode_id: int | None
    maps: int


# Every slice the metric layer holds, pooled row included. `player_metric_season`
# carries one row per mode plus a pooled row on `mode_id IS NULL`, aggregated
# over the same maps (`metrics._fold`), and which of the two a season may use is
# decided in `build` rather than here.
_METRIC_SQL = """
SELECT player_id, season_id, mode_id, metric, pctl
FROM player_metric_season
WHERE run_id = (SELECT max(run_id) FROM player_metric_season)
  AND pctl IS NOT NULL
  AND qualified
"""

# Maps per slice, and the season's own total beside them. The grouping set on
# (player, season) alone is what answers for the pooled slice: `games.mode_id`
# is never null, so grouping by it alone left the pooled key with no count at
# all, and the pooled slice fell to the `max(1, ...)` floor and weighed one map
# against modes weighing hundreds. The shrinkage reads this same count, so a
# missing one is not only a weight but a season pulled almost entirely onto its
# cohort mean.
_MAPS_SQL = """
SELECT gps.player_id, ev.season_id, g.mode_id, count(*) AS maps
FROM game_player_stats gps
JOIN games g   ON g.id = gps.game_id
JOIN series s  ON s.id = g.series_id
JOIN events ev ON ev.id = s.event_id
GROUP BY GROUPING SETS (
    (gps.player_id, ev.season_id, g.mode_id),
    (gps.player_id, ev.season_id)
)
"""


def load_metric_points(conn: Conn, basket: dict[str, metrics_module.Metric]) -> list[MetricPoint]:
    rows = conn.execute(_METRIC_SQL).fetchall()
    out: list[MetricPoint] = []
    for r in rows:
        metric = cast(str, r[3])
        entry = basket.get(metric)
        if entry is None:
            continue
        pctl = cast(float, r[4])
        if not entry.higher_is_better:
            pctl = 1.0 - pctl
        out.append(
            MetricPoint(
                player_id=cast(int, r[0]),
                season_id=cast(int, r[1]),
                mode_id=cast(int | None, r[2]),
                metric=metric,
                pctl=pctl,
            )
        )
    return out


def load_slice_maps(conn: Conn) -> dict[tuple[int, int, int | None], int]:
    rows = conn.execute(_MAPS_SQL).fetchall()
    return {
        (cast(int, r[0]), cast(int, r[1]), cast(int | None, r[2])): cast(int, r[3]) for r in rows
    }


@dataclass(frozen=True)
class SeasonBreadth:
    player_id: int
    season_id: int
    score: float  # 0..100
    sd: float | None  # weighted-mean SE across mode slices, basket-disagreement only
    n_slices: int
    n_stats: int
    maps: int
    families: tuple[str, ...] = ()


def shrink(rows: Sequence[SeasonBreadth], k: float = SHRINK_K) -> list[SeasonBreadth]:
    """Pull each season toward its own season's mean by how many maps it is.

    A 40-map season is a noisier estimate of a player than a 124-map season, so
    left alone it reaches further from the middle in both directions. That is
    the whole of the difference between a 2013-2016 season and a CDL one on
    this scale — 18 percentiles over 40 maps against 31 over 124 — and a board
    that admits both without this reads the noise as a peak.

    The mean is the season's own field, so this never moves a season against
    another era: it moves it against the players it played.

    `sd` is the width of the same deviation and scales with it. A season alone
    in its cohort has no mean to shrink toward and is returned untouched.
    """
    by_season: dict[int, list[SeasonBreadth]] = defaultdict(list)
    for row in rows:
        by_season[row.season_id].append(row)

    out: list[SeasonBreadth] = []
    for _season_id, cohort in sorted(by_season.items()):
        if len(cohort) < MIN_SHRINK_COHORT:
            out.extend(cohort)
            continue
        mean = sum(row.score for row in cohort) / len(cohort)
        for row in cohort:
            weight = row.maps / (row.maps + k) if row.maps > 0 else 0.0
            out.append(
                replace(
                    row,
                    score=mean + (row.score - mean) * weight,
                    sd=None if row.sd is None else row.sd * weight,
                )
            )
    return sorted(out, key=lambda row: (row.player_id, row.season_id))


def estimate_shrink_k(rows: Sequence[SeasonBreadth], bins: int = 10) -> dict[str, float | int]:
    """Refit `SHRINK_K` from the rows in hand, for reporting only.

    Bins the season deviations by map count, takes each bin's variance, and
    regresses it on the reciprocal of the bin's mean map count weighted by bin
    size. The intercept is the variance a season of unlimited length would
    still have and the slope is the sampling part; K is their ratio. Nothing
    reads the number it returns — it is published beside the run so a drift
    away from the frozen constant is visible rather than silent.
    """
    by_season: dict[int, list[SeasonBreadth]] = defaultdict(list)
    for row in rows:
        by_season[row.season_id].append(row)
    points: list[tuple[int, float]] = []
    for cohort in by_season.values():
        if len(cohort) < MIN_SHRINK_COHORT:
            continue
        mean = sum(row.score for row in cohort) / len(cohort)
        points.extend((row.maps, row.score - mean) for row in cohort if row.maps > 0)
    if len(points) < bins * MIN_SHRINK_COHORT:
        return {"n": len(points), "k": SHRINK_K, "fitted": 0}

    points.sort()
    per_bin = len(points) // bins
    xs: list[float] = []
    ys: list[float] = []
    ws: list[float] = []
    for index in range(bins):
        chunk = points[index * per_bin : (index + 1) * per_bin if index < bins - 1 else len(points)]
        if len(chunk) < MIN_SHRINK_COHORT:
            continue
        mean_maps = sum(maps for maps, _ in chunk) / len(chunk)
        centre = sum(dev for _, dev in chunk) / len(chunk)
        variance = sum((dev - centre) ** 2 for _, dev in chunk) / (len(chunk) - 1)
        xs.append(1.0 / mean_maps)
        ys.append(variance)
        ws.append(float(len(chunk)))

    total = sum(ws)
    mean_x = sum(w * x for w, x in zip(ws, xs, strict=True)) / total
    mean_y = sum(w * y for w, y in zip(ws, ys, strict=True)) / total
    covariance = sum(w * (x - mean_x) * (y - mean_y) for w, x, y in zip(ws, xs, ys, strict=True))
    spread = sum(w * (x - mean_x) ** 2 for w, x in zip(ws, xs, strict=True))
    slope = covariance / spread if spread else 0.0
    intercept = mean_y - slope * mean_x
    residual = sum(
        w * (y - (intercept + slope * x)) ** 2 for w, x, y in zip(ws, xs, ys, strict=True)
    )
    about_mean = sum(w * (y - mean_y) ** 2 for w, y in zip(ws, ys, strict=True))
    return {
        "n": len(points),
        "bins": len(xs),
        "true_variance": intercept,
        "sampling_variance_per_map": slope,
        "r_squared": 1.0 - residual / about_mean if about_mean else 0.0,
        "k": slope / intercept if intercept > 0 else SHRINK_K,
        "fitted": 1,
    }


def build(
    points: Sequence[MetricPoint], slice_maps: dict[tuple[int, int, int | None], int]
) -> list[SeasonBreadth]:
    """One breadth score per (player, season): coverage-weighted mean percentile
    across qualifying (mode, metric) points, weighted by the mode's share of
    the player's maps that season.
    """
    by_slice: dict[tuple[int, int, int | None], list[MetricPoint]] = defaultdict(list)
    for p in points:
        by_slice[(p.player_id, p.season_id, p.mode_id)].append(p)

    # slice_scores: key -> (score 0..100, n_stats, standard error of the mean
    # pctl within the slice, 0..100 scale). The basket has no per-metric
    # measurement error (player_metric_season stores no sd), so the only
    # honest uncertainty available here is disagreement *among* the metrics
    # in the basket: if a season's gold-tier stats mostly agree, the slice SE
    # is small; if they scatter, it is large. This is a real signal (the
    # basket disagreeing with itself), not a model of measurement noise —
    # stated so it is not read as more than it is.
    slice_scores: dict[tuple[int, int, int | None], tuple[float, int, float, tuple[str, ...]]] = {}
    for key, pts in by_slice.items():
        keys = [p.metric for p in pts]
        drop = _redundant_per_map(keys)
        kept = [p for p in pts if p.metric not in drop]
        n = len(kept)
        if n < MIN_SLICE_STATS:
            continue
        # Each live family contributes the mean of its own surviving
        # percentiles and the slice is the unweighted mean over those, so a
        # slice is worth what it measured and not how many metrics happen to
        # measure the same thing. A family with no surviving metric here is
        # absent from the mean, never a zero inside it — the same rule
        # `blend.renormalize` keeps over components, one level down. The
        # `MIN_SLICE_STATS` floor is still read on surviving metrics.
        by_family: dict[str, list[float]] = defaultdict(list)
        for p in kept:
            by_family[FAMILY[p.metric]].append(p.pctl)
        family_means = {f: sum(v) / len(v) for f, v in by_family.items()}
        live = tuple(f for f in FAMILIES if f in family_means)
        mean_pctl = sum(family_means[f] for f in live) / len(live)
        # Disagreement inside the basket, which is the only uncertainty the
        # metric layer makes available (`player_metric_season` stores no sd).
        # It is read at the level the score is a mean of: across the families
        # where there is more than one, and across the one family's own metrics
        # where there is not, because a single family disagreeing with itself
        # is then the whole of the disagreement there is.
        spread = [family_means[f] for f in live] if len(live) >= 2 else [p.pctl for p in kept]
        centre = sum(spread) / len(spread)
        variance = sum((v - centre) ** 2 for v in spread) / len(spread)  # population variance
        se = math.sqrt(variance / len(spread)) * 100.0
        slice_scores[key] = (mean_pctl * 100.0, n, se, live)

    by_player_season: dict[
        tuple[int, int], list[tuple[int | None, float, int, float, tuple[str, ...]]]
    ] = defaultdict(list)
    for (player_id, season_id, mode_id), (score, n_stats, se, live) in slice_scores.items():
        by_player_season[(player_id, season_id)].append((mode_id, score, n_stats, se, live))

    out: list[SeasonBreadth] = []
    for (player_id, season_id), all_slices in sorted(by_player_season.items()):
        # Mode slices where there are any, the pooled slice only where there
        # are none. The pooled row aggregates the same maps as the mode rows,
        # so beside them it is the season counted twice; with no mode row
        # qualifying it is the only reading of the season there is. A thin
        # season spread across four modes can clear the coverage floor pooled
        # and clear it in none of the four, and refusing it would drop the
        # season for having been spread out rather than for being unmeasured.
        by_mode = [entry for entry in all_slices if entry[0] is not None]
        slices = by_mode or [entry for entry in all_slices if entry[0] is None]
        # `max(1, ...)` guards a slice whose maps the join cannot answer for, so
        # one unanswerable slice cannot take the whole season's weight to zero.
        # The pooled slice is answered for by its own grouping set and no longer
        # reaches this floor.
        weights = [
            max(1, slice_maps.get((player_id, season_id, mode_id), 0))
            for mode_id, _, _, _, _ in slices
        ]
        total_weight = sum(weights)
        weighted = sum(w * score for w, (_, score, _, _, _) in zip(weights, slices, strict=True))
        # Weighted-mean standard error: treats each mode slice as an
        # independent noisy estimate of the season's true breadth, combined
        # the same way a weighted mean's own SE is combined.
        sd: float | None
        if total_weight:
            se_sq_sum = sum(
                (w * se) ** 2 for w, (_, _, _, se, _) in zip(weights, slices, strict=True)
            )
            sd = math.sqrt(se_sq_sum) / total_weight
        else:
            sd = None
        out.append(
            SeasonBreadth(
                player_id=player_id,
                season_id=season_id,
                score=weighted / total_weight if total_weight else 0.0,
                sd=sd,
                n_slices=len(slices),
                n_stats=sum(n for _, _, n, _, _ in slices),
                maps=sum(
                    slice_maps.get((player_id, season_id, mode_id), 0)
                    for mode_id, _, _, _, _ in slices
                ),
                # The families this season was actually built from, unioned
                # over the slices that scored it. Published on the season row
                # so the era's thinness is visible rather than assumed: no era
                # carries all six, and a 2013-2016 season is scored on three.
                families=tuple(
                    f for f in FAMILIES if any(f in live for _, _, _, _, live in slices)
                ),
            )
        )
    return out
