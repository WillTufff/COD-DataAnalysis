"""Insight kinds that read the metric layer and the loadout data.

The older kinds in insights.py interpret K/D and the rating systems. These six
read the metric layer, so they can make claims a box score cannot support:

  intangible_outlier  elite at an intangible while ordinary at K/D (or reverse)
  profile_extreme     the league-best qualified season value of a gold metric
  clutch_milestone    1vN records from the kill-feed tier
  trade_asymmetry     slaying and trade economy pointing opposite ways
  meta_shift          a weapon's usage share swinging between consecutive events
  team_style          teams at the extremes of hill duty and opening duty

Every kind is deliberately conservative about volume: these run over 43 gold
metrics across three seasons, so without caps they would drown the older,
harder-won findings. Each generator ranks its candidates by surprisingness and
keeps only the top few.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, cast

import psycopg

from .insights import Atom

# Qualification and volume caps.
TOP_DECILE = 0.9
BOTTOM_HALF = 0.5
MAX_INTANGIBLE_OUTLIERS = 24
MAX_PROFILE_EXTREMES = 30
MAX_CLUTCH = 10
MAX_TRADE_ASYMMETRY = 12
MAX_META_SHIFTS = 12
MAX_TEAM_STYLE = 12

MIN_CLUTCH_ATTEMPTS = 20  # a 1vN record needs a real sample before it is a record
EXTREME_DENOM_MULTIPLE = 2.0  # a league-best claim needs twice the qualifying sample
META_SHIFT_POINTS = 0.20  # usage-share swing that counts as a meta shift
MIN_META_EVENT_MAPS = 40  # player-maps an event needs before its meta is read

# The intangibles worth contrasting against K/D: things a box score never showed.
INTANGIBLES: tuple[str, ...] = (
    "snd_fb_net_pr",
    "snd_fb_rate",
    "snd_survival_rate",
    "snd_opening_duel_win",
    "snd_zero_kill_round_rate",
    "hill_time_share",
    "time_per_life_s",
    "untraded_death_rate",
    "trade_kills_p10",
    "clutch_win_rate",
    "ctrl_fb_net_pr",
    "ctf_flag_involvement_pm",
)


def _catalog(
    conn: psycopg.Connection[tuple[object, ...]], metric_run: int
) -> dict[str, dict[str, Any]]:
    row = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = 'metric_catalog'",
        (metric_run,),
    ).fetchone()
    if row is None:
        return {}
    payload = cast("dict[str, Any]", row[0])
    entries = [*payload.get("metrics", []), *payload.get("team_metrics", [])]
    return {cast(str, m["key"]): m for m in entries}


def _label(catalog: dict[str, dict[str, Any]], metric: str) -> str:
    entry = catalog.get(metric)
    return cast(str, entry["label"]) if entry else metric


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _ordinal(value: float) -> str:
    """A percentile as a rank, e.g. 0.93 -> '93rd percentile'."""
    n = max(1, min(100, round(value * 100)))
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} percentile"


def _quality_pctl(catalog: dict[str, dict[str, Any]], metric: str, pctl: float) -> float:
    """Percentile re-read so that higher always means better.

    Half the intangibles are lower-is-better (first deaths, untraded deaths,
    zero-kill rounds). Comparing their raw percentile against K/D's would call a
    player who is elite at both an outlier, in the wrong direction.
    """
    higher_better = bool(catalog.get(metric, {}).get("higher_is_better", True))
    return pctl if higher_better else 1.0 - pctl


def _fmt(catalog: dict[str, dict[str, Any]], metric: str, value: float) -> str:
    """Format a value the way the catalog says it should read."""
    unit = cast(str, catalog.get(metric, {}).get("unit", ""))
    if unit == "rate" or metric.endswith("_rate") or metric.endswith("_share"):
        return _pct(value)
    if abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.2f}"


# ------------------------------------------------------- intangible_outlier

_OUTLIER_SQL = """
WITH kd AS (
  SELECT player_id, season_id, pctl
  FROM player_metric_season
  WHERE run_id = %(run)s AND metric = 'kd' AND mode_id IS NULL AND qualified
)
SELECT m.player_id, p.handle, se.year, t.short_name, gm.name,
       m.metric, m.value, m.pctl, m.denom, kd.pctl
FROM player_metric_season m
JOIN kd ON kd.player_id = m.player_id AND kd.season_id = m.season_id
JOIN players p  ON p.id = m.player_id
JOIN seasons se ON se.id = m.season_id
JOIN titles t   ON t.id = se.title_id
LEFT JOIN game_modes gm ON gm.id = m.mode_id
WHERE m.run_id = %(run)s AND m.qualified AND m.pctl IS NOT NULL
  AND m.metric = ANY(%(metrics)s)
"""


def intangible_outliers(
    conn: psycopg.Connection[tuple[object, ...]], metric_run: int
) -> list[Atom]:
    """Seasons where K/D and an intangible disagree.

    The disagreement test runs in Python rather than SQL because it needs each
    metric's direction: elite at a lower-is-better metric means a *low*
    percentile, and a query comparing raw percentiles would report players who
    are excellent at both as contradictions.
    """
    catalog = _catalog(conn, metric_run)
    out: list[Atom] = []
    for r in conn.execute(_OUTLIER_SQL, {"run": metric_run, "metrics": list(INTANGIBLES)}):
        pid, handle = cast(int, r[0]), cast(str, r[1])
        year, title = cast(int, r[2]), cast(str, r[3])
        mode, metric = cast("str | None", r[4]), cast(str, r[5])
        value, pctl = float(cast(float, r[6])), float(cast(float, r[7]))
        denom, kd_pctl = float(cast(float, r[8])), float(cast(float, r[9]))

        quality = _quality_pctl(catalog, metric, pctl)
        undersold = quality >= TOP_DECILE and kd_pctl <= BOTTOM_HALF
        oversold = quality <= 1.0 - TOP_DECILE and kd_pctl >= TOP_DECILE
        if not undersold and not oversold:
            continue

        gap = abs(quality - kd_pctl)
        scope = f" {mode}" if mode else ""
        label = _label(catalog, metric)
        if undersold:
            headline = (
                f"{handle}'s {year}{scope} K/D sat at the {_ordinal(kd_pctl)} of the "
                f"{title} cohort, while {label.lower()} put them at the "
                f"{_ordinal(quality)}."
            )
        else:
            headline = (
                f"{handle} posted a {_ordinal(kd_pctl)} {year}{scope} K/D in {title}, "
                f"while {label.lower()} ranked at the {_ordinal(quality)}."
            )
        out.append(
            Atom(
                "player",
                pid,
                "intangible_outlier",
                headline,
                {
                    "year": year,
                    "title": title,
                    "mode": mode,
                    "metric": metric,
                    "metric_label": label,
                    "value": round(value, 4),
                    "pctl": round(pctl, 3),
                    "quality_pctl": round(quality, 3),
                    "kd_pctl": round(kd_pctl, 3),
                    "n": round(denom, 1),
                    "undersold": undersold,
                    "metric_run_id": metric_run,
                },
                min(0.5 + gap * 0.45, 0.98),
            )
        )
    out.sort(key=lambda a: -a.score)
    return out[:MAX_INTANGIBLE_OUTLIERS]


# ---------------------------------------------------------- profile_extreme

_EXTREME_SQL = """
SELECT DISTINCT ON (m.metric, m.season_id, m.mode_id)
       m.player_id, p.handle, se.year, t.short_name, gm.name,
       m.metric, m.value, m.denom, m.z
FROM player_metric_season m
JOIN players p  ON p.id = m.player_id
JOIN seasons se ON se.id = m.season_id
JOIN titles t   ON t.id = se.title_id
LEFT JOIN game_modes gm ON gm.id = m.mode_id
WHERE m.run_id = %(run)s AND m.qualified AND m.z IS NOT NULL
  AND m.metric = ANY(%(metrics)s)
ORDER BY m.metric, m.season_id, m.mode_id, (m.z * %(direction)s) DESC
"""


def profile_extremes(conn: psycopg.Connection[tuple[object, ...]], metric_run: int) -> list[Atom]:
    """The league-best qualified season on each gold metric, in each season."""
    catalog = _catalog(conn, metric_run)
    gold = [k for k, m in catalog.items() if m.get("tier") == "gold"]
    higher = [k for k in gold if catalog[k].get("higher_is_better", True)]
    lower = [k for k in gold if not catalog[k].get("higher_is_better", True)]

    out: list[Atom] = []
    for metrics, direction in ((higher, 1), (lower, -1)):
        if not metrics:
            continue
        for r in conn.execute(
            _EXTREME_SQL, {"run": metric_run, "metrics": metrics, "direction": direction}
        ):
            pid, handle = cast(int, r[0]), cast(str, r[1])
            year, title, mode = cast(int, r[2]), cast(str, r[3]), cast("str | None", r[4])
            metric, value = cast(str, r[5]), float(cast(float, r[6]))
            denom, z = float(cast(float, r[7])), float(cast(float, r[8]))
            if abs(z) < 1.5:  # "league-best" in a flat cohort is not a finding
                continue
            # Qualifying is the floor for appearing on a leaderboard; claiming
            # nobody in the league matched you deserves more than the minimum.
            min_denom = float(catalog.get(metric, {}).get("min_denom") or 0.0)
            if denom < min_denom * EXTREME_DENOM_MULTIPLE:
                continue
            label = _label(catalog, metric)
            scope = f" {mode}" if mode else ""
            unit = cast(str, catalog.get(metric, {}).get("unit", ""))
            out.append(
                Atom(
                    "player",
                    pid,
                    "profile_extreme",
                    f"No one in {year} {title}{scope} matched {handle}'s "
                    f"{label.lower()}: {_fmt(catalog, metric, value)}"
                    f"{' ' + unit if unit and unit != 'rate' else ''} "
                    f"({abs(z):.1f} SD from the cohort mean, n={denom:.0f}).",
                    {
                        "year": year,
                        "title": title,
                        "mode": mode,
                        "metric": metric,
                        "metric_label": label,
                        "value": round(value, 4),
                        "z": round(z, 2),
                        "n": round(denom, 1),
                        "metric_run_id": metric_run,
                    },
                    min(0.55 + abs(z) * 0.09, 0.97),
                )
            )
    out.sort(key=lambda a: -a.score)
    return out[:MAX_PROFILE_EXTREMES]


# --------------------------------------------------------- clutch_milestone

_CLUTCH_SQL = """
SELECT m.player_id, p.handle, se.year, t.short_name, m.metric, m.value, m.denom, m.pctl
FROM player_metric_season m
JOIN players p  ON p.id = m.player_id
JOIN seasons se ON se.id = m.season_id
JOIN titles t   ON t.id = se.title_id
WHERE m.run_id = %(run)s AND m.metric LIKE 'clutch%%' AND m.qualified
  AND m.denom >= %(min_attempts)s AND m.pctl IS NOT NULL
ORDER BY m.value DESC
"""


def clutch_milestones(conn: psycopg.Connection[tuple[object, ...]], metric_run: int) -> list[Atom]:
    """Last-man-standing records, reconstructed from the kill feed."""
    catalog = _catalog(conn, metric_run)
    out: list[Atom] = []
    for r in conn.execute(_CLUTCH_SQL, {"run": metric_run, "min_attempts": MIN_CLUTCH_ATTEMPTS}):
        pid, handle, year, title = (
            cast(int, r[0]),
            cast(str, r[1]),
            cast(int, r[2]),
            cast(str, r[3]),
        )
        metric, value = cast(str, r[4]), float(cast(float, r[5]))
        denom, pctl = float(cast(float, r[6])), float(cast(float, r[7]))
        if pctl < TOP_DECILE:
            continue
        situation = "1vN rounds" if metric == "clutch_win_rate" else metric.split("_")[1]
        out.append(
            Atom(
                "player",
                pid,
                "clutch_milestone",
                f"{handle} won {_pct(value)} of their {year} {title} {situation} "
                f"({denom:.0f} attempts), top decile of the league.",
                {
                    "year": year,
                    "title": title,
                    "metric": metric,
                    "metric_label": _label(catalog, metric),
                    "win_rate": round(value, 4),
                    "attempts": round(denom, 0),
                    "pctl": round(pctl, 3),
                    "metric_run_id": metric_run,
                },
                min(0.55 + value * 0.35, 0.95),
            )
        )
    return out[:MAX_CLUTCH]


# ---------------------------------------------------------- trade_asymmetry

_TRADE_SQL = """
WITH slay AS (
  SELECT player_id, season_id, pctl, value
  FROM player_metric_season
  WHERE run_id = %(run)s AND metric = 'kills_p10' AND mode_id IS NULL AND qualified
)
SELECT m.player_id, p.handle, se.year, t.short_name,
       m.value, m.pctl, m.denom, slay.pctl, slay.value
FROM player_metric_season m
JOIN slay ON slay.player_id = m.player_id AND slay.season_id = m.season_id
JOIN players p  ON p.id = m.player_id
JOIN seasons se ON se.id = m.season_id
JOIN titles t   ON t.id = se.title_id
WHERE m.run_id = %(run)s AND m.metric = 'untraded_death_rate'
  AND m.mode_id IS NULL AND m.qualified AND m.pctl IS NOT NULL
"""

SLAY_HIGH = 0.75
SLAY_LOW = 0.25
TRADE_EXTREME = 0.75


def trade_asymmetries(conn: psycopg.Connection[tuple[object, ...]], metric_run: int) -> list[Atom]:
    """Slaying and trade economy pulling in opposite directions: the player who
    kills plenty but dies alone, and the one who slays little yet never wastes a
    death. Untraded-death rate is lower-is-better, so the contradiction is a
    heavy slayer high on it, or a light slayer low on it."""
    out: list[Atom] = []
    for r in conn.execute(_TRADE_SQL, {"run": metric_run}):
        pid, handle = cast(int, r[0]), cast(str, r[1])
        year, title = cast(int, r[2]), cast(str, r[3])
        untraded, untraded_pctl = float(cast(float, r[4])), float(cast(float, r[5]))
        denom = float(cast(float, r[6]))
        slay_pctl, kills = float(cast(float, r[7])), float(cast(float, r[8]))

        isolated = slay_pctl >= SLAY_HIGH and untraded_pctl >= TRADE_EXTREME
        answered = slay_pctl <= SLAY_LOW and untraded_pctl <= 1.0 - TRADE_EXTREME
        if not isolated and not answered:
            continue

        if isolated:
            headline = (
                f"{handle} ranked {_ordinal(slay_pctl)} for kills in {year} {title} "
                f"({kills:.1f} per 10 min), with {_pct(untraded)} of their deaths "
                f"untraded — {_ordinal(untraded_pctl)} of the cohort."
            )
        else:
            headline = (
                f"{handle} ranked {_ordinal(slay_pctl)} for kills in {year} {title}, "
                f"with {_pct(untraded)} of their deaths untraded — "
                f"{_ordinal(1.0 - untraded_pctl)} of the cohort for being traded back."
            )
        out.append(
            Atom(
                "player",
                pid,
                "trade_asymmetry",
                headline,
                {
                    "year": year,
                    "title": title,
                    "untraded_death_rate": round(untraded, 4),
                    "untraded_pctl": round(untraded_pctl, 3),
                    "kills_p10": round(kills, 2),
                    "kills_pctl": round(slay_pctl, 3),
                    "n_deaths": round(denom, 0),
                    "isolated": isolated,
                    "metric_run_id": metric_run,
                },
                min(0.55 + abs(untraded_pctl - (1.0 - slay_pctl)) * 0.5, 0.95),
            )
        )
    out.sort(key=lambda a: -a.score)
    return out[:MAX_TRADE_ASYMMETRY]


# ---------------------------------------------------------------- meta_shift

_META_EVENT_SQL = """
SELECT se.id, se.year, t.short_name, ev.id, ev.name, min(s.played_at) AS started,
       gps.extras->>'fave_weapon' AS weapon, count(*) AS n
FROM game_player_stats gps
JOIN games g       ON g.id = gps.game_id
JOIN series s      ON s.id = g.series_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN titles t      ON t.id = se.title_id
WHERE gps.extras->>'fave_weapon' IS NOT NULL
GROUP BY 1, 2, 3, 4, 5, 7
"""


def meta_shifts(conn: psycopg.Connection[tuple[object, ...]]) -> list[Atom]:
    """A weapon's usage share swinging between consecutive events of a season.

    Read per event rather than from the season-level meta artifacts, because a
    meta *shift* only exists on the event axis — the artifacts aggregate that
    axis away.
    """
    per_event: dict[tuple[int, int], dict[str, int]] = defaultdict(dict)
    meta: dict[tuple[int, int], tuple[int, str, str, Any]] = {}
    for r in conn.execute(_META_EVENT_SQL):
        season_id, year, title = cast(int, r[0]), cast(int, r[1]), cast(str, r[2])
        event_id, event_name, started = cast(int, r[3]), cast(str, r[4]), r[5]
        weapon, n = cast(str, r[6]), cast(int, r[7])
        per_event[(season_id, event_id)][weapon] = n
        meta[(season_id, event_id)] = (year, title, event_name, started)

    by_season: dict[int, list[tuple[Any, int]]] = defaultdict(list)
    for season_id, event_id in per_event:
        by_season[season_id].append((meta[(season_id, event_id)][3], event_id))

    out: list[Atom] = []
    for season_id, events in by_season.items():
        ordered = [e for _d, e in sorted(events)]
        for prev_id, next_id in zip(ordered, ordered[1:], strict=False):
            prev, nxt = per_event[(season_id, prev_id)], per_event[(season_id, next_id)]
            prev_total, next_total = sum(prev.values()), sum(nxt.values())
            if prev_total < MIN_META_EVENT_MAPS or next_total < MIN_META_EVENT_MAPS:
                continue
            for weapon in set(prev) | set(nxt):
                before = prev.get(weapon, 0) / prev_total
                after = nxt.get(weapon, 0) / next_total
                swing = after - before
                if abs(swing) < META_SHIFT_POINTS:
                    continue
                year, title, next_name, _d = meta[(season_id, next_id)]
                _y, _t, prev_name, _d2 = meta[(season_id, prev_id)]
                direction = "surged" if swing > 0 else "collapsed"
                out.append(
                    Atom(
                        "season",
                        season_id,
                        "meta_shift",
                        f"The {weapon} {direction} between {prev_name} and {next_name} "
                        f"in {year} {title}: {_pct(before)} to {_pct(after)} of "
                        f"player-maps as the favoured weapon.",
                        {
                            "year": year,
                            "title": title,
                            "weapon": weapon,
                            "from_event": prev_name,
                            "to_event": next_name,
                            "share_before": round(before, 4),
                            "share_after": round(after, 4),
                            "swing": round(swing, 4),
                            "n_player_maps_before": prev_total,
                            "n_player_maps_after": next_total,
                        },
                        min(0.5 + abs(swing) * 1.2, 0.95),
                    )
                )
    out.sort(key=lambda a: -a.score)
    return out[:MAX_META_SHIFTS]


# ---------------------------------------------------------------- team_style

_TEAM_STYLE_SQL = """
SELECT tm.team_id, te.name, se.year, t.short_name, gm.name,
       tm.metric, tm.value, tm.denom, tm.z, tm.pctl
FROM team_metric_season tm
JOIN teams te   ON te.id = tm.team_id
JOIN seasons se ON se.id = tm.season_id
JOIN titles t   ON t.id = se.title_id
LEFT JOIN game_modes gm ON gm.id = tm.mode_id
WHERE tm.run_id = %(run)s AND tm.qualified AND tm.z IS NOT NULL
  AND tm.metric = ANY(%(metrics)s) AND abs(tm.z) >= 1.5
ORDER BY abs(tm.z) DESC
"""

_STYLE_READINGS = {
    "hill_time_gini": (
        "split hill time more unevenly across the roster than any comparable team",
        "split hill time almost evenly across the roster",
    ),
    "snd_fb_concentration": (
        "concentrated its opening duels on one player",
        "spread its opening duels across the roster",
    ),
    "slay_balance": (
        "concentrated its kills on one player",
        "shared kills more evenly than any comparable team",
    ),
}


def team_styles(conn: psycopg.Connection[tuple[object, ...]], metric_run: int) -> list[Atom]:
    """Rosters at the extremes of how they divided the work."""
    catalog = _catalog(conn, metric_run)
    out: list[Atom] = []
    for r in conn.execute(_TEAM_STYLE_SQL, {"run": metric_run, "metrics": list(_STYLE_READINGS)}):
        team_id, team = cast(int, r[0]), cast(str, r[1])
        year, title, mode = cast(int, r[2]), cast(str, r[3]), cast("str | None", r[4])
        metric, value = cast(str, r[5]), float(cast(float, r[6]))
        denom, z = float(cast(float, r[7])), float(cast(float, r[8]))
        high, low = _STYLE_READINGS[metric]
        scope = f" in {mode}" if mode else ""
        out.append(
            Atom(
                "team",
                team_id,
                "team_style",
                f"{year} {team} {high if z > 0 else low}{scope} "
                f"({_label(catalog, metric).lower()} {value:.2f}, "
                f"{abs(z):.1f} SD from the {title} mean, {denom:.0f} maps).",
                {
                    "year": year,
                    "title": title,
                    "mode": mode,
                    "metric": metric,
                    "metric_label": _label(catalog, metric),
                    "value": round(value, 4),
                    "z": round(z, 2),
                    "n_maps": round(denom, 0),
                    "metric_run_id": metric_run,
                },
                min(0.5 + abs(z) * 0.12, 0.95),
            )
        )
    return out[:MAX_TEAM_STYLE]


def generate(conn: psycopg.Connection[tuple[object, ...]], metric_run: int) -> list[Atom]:
    return (
        intangible_outliers(conn, metric_run)
        + profile_extremes(conn, metric_run)
        + clutch_milestones(conn, metric_run)
        + trade_asymmetries(conn, metric_run)
        + meta_shifts(conn)
        + team_styles(conn, metric_run)
    )
