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
from dataclasses import dataclass
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


_METRIC_SQL = """
SELECT player_id, season_id, mode_id, metric, pctl
FROM player_metric_season
WHERE run_id = (SELECT max(run_id) FROM player_metric_season)
  AND pctl IS NOT NULL
  AND qualified
"""

_MAPS_SQL = """
SELECT gps.player_id, ev.season_id, g.mode_id, count(*) AS maps
FROM game_player_stats gps
JOIN games g   ON g.id = gps.game_id
JOIN series s  ON s.id = g.series_id
JOIN events ev ON ev.id = s.event_id
GROUP BY gps.player_id, ev.season_id, g.mode_id
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
    slice_scores: dict[tuple[int, int, int | None], tuple[float, int, float]] = {}
    for key, pts in by_slice.items():
        keys = [p.metric for p in pts]
        drop = _redundant_per_map(keys)
        kept = [p for p in pts if p.metric not in drop]
        n = len(kept)
        if n < MIN_SLICE_STATS:
            continue
        pctls = [p.pctl for p in kept]
        mean_pctl = sum(pctls) / n
        variance = sum((p - mean_pctl) ** 2 for p in pctls) / n  # population variance
        se = math.sqrt(variance / n) * 100.0
        slice_scores[key] = (mean_pctl * 100.0, n, se)

    by_player_season: dict[tuple[int, int], list[tuple[int | None, float, int, float]]] = (
        defaultdict(list)
    )
    for (player_id, season_id, mode_id), (score, n_stats, se) in slice_scores.items():
        by_player_season[(player_id, season_id)].append((mode_id, score, n_stats, se))

    out: list[SeasonBreadth] = []
    for (player_id, season_id), slices in sorted(by_player_season.items()):
        weights = [
            max(1, slice_maps.get((player_id, season_id, mode_id), 0))
            for mode_id, _, _, _ in slices
        ]
        total_weight = sum(weights)
        weighted = sum(w * score for w, (_, score, _, _) in zip(weights, slices, strict=True))
        # Weighted-mean standard error: treats each mode slice as an
        # independent noisy estimate of the season's true breadth, combined
        # the same way a weighted mean's own SE is combined.
        sd: float | None
        if total_weight:
            se_sq_sum = sum((w * se) ** 2 for w, (_, _, _, se) in zip(weights, slices, strict=True))
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
                n_stats=sum(n for _, _, n, _ in slices),
                maps=sum(
                    slice_maps.get((player_id, season_id, mode_id), 0)
                    for mode_id, _, _, _ in slices
                ),
            )
        )
    return out
