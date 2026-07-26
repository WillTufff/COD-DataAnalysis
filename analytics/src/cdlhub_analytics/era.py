"""Era adjustment. Spec: /methodology#era.

Cohort = every qualified player (>= MIN_MAPS maps) in the same (season, mode).
Each player-season-mode aggregate gets a z-score and percentile *within its
cohort*, making a 2017 IW K/D and a 2019 BO4 K/D comparable objects. Rows are
written for all players; z/pctl are relative to the qualified cohort, and
maps_played lets consumers filter.

Objective metrics per mode (per-map unless noted):
  hardpoint          hill seconds per 10 min of map time
  search-and-destroy first bloods + plants + defuses per map
  control            zone captures per map        (extras: ctrl_captures)
  capture-the-flag   flag captures + returns per map (extras: ctf_*)
  uplink             uplink points per map        (extras: uplink_points)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import psycopg

from .cohort import cohort_spread, z_and_pctl

MIN_MAPS = 8

_BY_MODE_SQL = """
SELECT gps.player_id, e.season_id, g.mode_id, gm.slug,
       count(*) AS maps,
       sum(gps.kills) AS kills,
       sum(gps.deaths) AS deaths,
       sum(g.duration_s) AS duration_s,
       sum(COALESCE(gps.hill_time, 0)) AS hill_time,
       sum(COALESCE(gps.first_bloods, 0) + COALESCE(gps.plants, 0)
           + COALESCE(gps.defuses, 0)) AS snd_obj,
       sum(COALESCE((gps.extras->>'ctrl_captures')::float, 0)) AS ctrl_obj,
       sum(COALESCE((gps.extras->>'ctf_captures')::float, 0)
           + COALESCE((gps.extras->>'ctf_returns')::float, 0)) AS ctf_obj,
       sum(COALESCE((gps.extras->>'uplink_points')::float, 0)) AS uplink_obj,
       avg((gps.kills IS NOT NULL)::int)::float AS completeness,
       -- Second moments over the player's own maps. Appended rather than
       -- inserted: _obj_value reads this row positionally.
       sum(gps.kills::float * gps.kills) AS kk,
       sum(gps.deaths::float * gps.deaths) AS dd,
       sum(gps.kills::float * gps.deaths) AS kd_cross
FROM game_player_stats gps
JOIN games g  ON g.id = gps.game_id
JOIN series s ON s.id = g.series_id
JOIN events e ON e.id = s.event_id
JOIN game_modes gm ON gm.id = g.mode_id
GROUP BY gps.player_id, e.season_id, g.mode_id, gm.slug
"""

_ALL_MODES_SQL = """
SELECT gps.player_id, e.season_id,
       count(*) AS maps,
       sum(gps.kills) AS kills,
       sum(gps.deaths) AS deaths,
       sum(g.duration_s) AS duration_s,
       avg((gps.kills IS NOT NULL)::int)::float AS completeness,
       sum(gps.kills::float * gps.kills) AS kk,
       sum(gps.deaths::float * gps.deaths) AS dd,
       sum(gps.kills::float * gps.deaths) AS kd_cross
FROM game_player_stats gps
JOIN games g  ON g.id = gps.game_id
JOIN series s ON s.id = g.series_id
JOIN events e ON e.id = s.event_id
GROUP BY gps.player_id, e.season_id
"""


@dataclass
class Aggregate:
    player_id: int
    season_id: int
    mode_id: int | None  # None = all modes
    maps: int
    kd: float
    kd_se: float | None  # sampling SE of the season K/D, in K/D units
    engagement: float  # (kills+deaths) per 10 min of map time
    obj: float | None
    completeness: float


def kd_standard_error(
    maps: int, kills: float, deaths: float, kk: float, dd: float, kd_cross: float
) -> float | None:
    """Sampling SE of a season K/D, by the delta method on a ratio of means.

    A season K/D is sum(kills) / sum(deaths), i.e. the ratio of two means over
    the same m maps — which are correlated, since a map where a player was
    involved in a lot of fighting inflates both. For R = k̄ / d̄,

        Var(R) ≈ (1/d̄²) · [ Var(k̄) + R²·Var(d̄) − 2R·Cov(k̄,d̄) ]

    with Var(k̄) = s_k²/m and so on. The covariance term is what makes this
    worth doing properly: kills and deaths correlate strongly across maps, so
    treating them as independent overstates the error, while the naive
    1.96/√maps band understated it. Both are avoidable with second moments the
    aggregate query can return for free.

    Returns None where the quantity is undefined: fewer than two maps (no
    within-player variance to estimate), or no deaths (no ratio).
    """
    if maps < 2 or deaths <= 0:
        return None
    m = float(maps)
    k_bar, d_bar = kills / m, deaths / m
    # Sample (co)variances over the player's maps, ddof=1.
    var_k = (kk - m * k_bar * k_bar) / (m - 1.0)
    var_d = (dd - m * d_bar * d_bar) / (m - 1.0)
    cov = (kd_cross - m * k_bar * d_bar) / (m - 1.0)
    r = k_bar / d_bar
    var_r = (var_k + r * r * var_d - 2.0 * r * cov) / (m * d_bar * d_bar)
    # Rounding in the moment sums can push a near-zero variance below zero.
    return math.sqrt(var_r) if var_r > 0.0 else None


def _obj_value(slug: str, maps: int, duration: int, r: tuple[object, ...]) -> float | None:
    hill = float(cast(float, r[8]))
    snd = float(cast(float, r[9]))
    ctrl = float(cast(float, r[10]))
    ctf = float(cast(float, r[11]))
    uplink = float(cast(float, r[12]))
    match slug:
        case "hardpoint":
            return hill / (duration / 600.0) if duration else None
        case "search-and-destroy":
            return snd / maps
        case "control":
            return ctrl / maps
        case "capture-the-flag":
            return ctf / maps
        case "uplink":
            return uplink / maps
    return None


def load_aggregates(conn: psycopg.Connection[tuple[object, ...]]) -> list[Aggregate]:
    out: list[Aggregate] = []
    for r in conn.execute(_BY_MODE_SQL).fetchall():
        maps = cast(int, r[4])
        kills, deaths = cast(int, r[5]), cast(int, r[6])
        duration = cast(int, r[7])
        out.append(
            Aggregate(
                player_id=cast(int, r[0]),
                season_id=cast(int, r[1]),
                mode_id=cast(int, r[2]),
                maps=maps,
                kd=kills / max(deaths, 1),
                kd_se=kd_standard_error(
                    maps,
                    kills,
                    deaths,
                    float(cast(float, r[14])),
                    float(cast(float, r[15])),
                    float(cast(float, r[16])),
                ),
                engagement=(kills + deaths) / (duration / 600.0) if duration else 0.0,
                obj=_obj_value(cast(str, r[3]), maps, duration, r),
                completeness=cast(float, r[13]),
            )
        )
    for r in conn.execute(_ALL_MODES_SQL).fetchall():
        maps = cast(int, r[2])
        kills, deaths = cast(int, r[3]), cast(int, r[4])
        duration = cast(int, r[5])
        out.append(
            Aggregate(
                player_id=cast(int, r[0]),
                season_id=cast(int, r[1]),
                mode_id=None,
                maps=maps,
                kd=kills / max(deaths, 1),
                kd_se=kd_standard_error(
                    maps,
                    kills,
                    deaths,
                    float(cast(float, r[7])),
                    float(cast(float, r[8])),
                    float(cast(float, r[9])),
                ),
                engagement=(kills + deaths) / (duration / 600.0) if duration else 0.0,
                obj=None,
                completeness=cast(float, r[6]),
            )
        )
    return out


def compute_and_write(conn: psycopg.Connection[tuple[object, ...]], run_id: int) -> int:
    """Compute cohort z-scores/percentiles and write player_season_adjusted."""
    by_cohort: dict[tuple[int, int | None], list[Aggregate]] = {}
    for a in load_aggregates(conn):
        by_cohort.setdefault((a.season_id, a.mode_id), []).append(a)

    Row = tuple[
        int,
        int,
        int,
        int | None,
        int,
        float,
        float | None,
        float | None,
        float | None,
        float | None,
        float,
        float | None,
    ]
    rows: list[Row] = []
    for _, members in sorted(by_cohort.items(), key=lambda kv: (kv[0][0], kv[0][1] or 0)):
        qualified = [a.player_id for a in members if a.maps >= MIN_MAPS]
        kd_values = {a.player_id: a.kd for a in members}
        kd_stats = z_and_pctl(kd_values, qualified)
        eng_stats = z_and_pctl({a.player_id: a.engagement for a in members}, qualified)
        obj_values = {a.player_id: a.obj for a in members if a.obj is not None}
        obj_stats = z_and_pctl(obj_values, [p for p in qualified if p in obj_values])
        # The same denominator the z-score used, so a value and its error stay
        # on one scale. None where the cohort has no measurable spread.
        spread = cohort_spread(kd_values, qualified)
        kd_cohort_sd = spread[1] if spread else None
        for a in members:
            kd = kd_stats.get(a.player_id)
            eng = eng_stats.get(a.player_id)
            obj = obj_stats.get(a.player_id)
            # An error bar on a z-score nobody published would be a claim about
            # a number that isn't there, so it is gated on the z-score itself.
            kd_z_se = (
                a.kd_se / kd_cohort_sd
                if a.kd_se is not None and kd_cohort_sd and kd and kd[0] is not None
                else None
            )
            rows.append(
                (
                    run_id,
                    a.player_id,
                    a.season_id,
                    a.mode_id,
                    a.maps,
                    a.kd,
                    kd[0] if kd else None,
                    kd[1] if kd else None,
                    eng[0] if eng else None,
                    obj[0] if obj else None,
                    a.completeness,
                    kd_z_se,
                )
            )
    conn.cursor().executemany(
        "INSERT INTO player_season_adjusted (run_id, player_id, season_id, mode_id,"
        " maps_played, kd_raw, kd_z, kd_pctl, engagement_z, obj_z, completeness, kd_z_se)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        rows,
    )
    return len(rows)
