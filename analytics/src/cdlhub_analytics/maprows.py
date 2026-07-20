"""One definition of what a player-map's numbers are.

Both tiers that read the box score — the metric layer and the open player
rating — need the same thing: every typed column and every `extras` key for one
player on one map, plus enough framing (season, mode, event, date, outcome) to
group and to walk forward in time. They also need the same answer to "does this
title actually track this column", which is measured from the data rather than
declared, so the two can never disagree about what exists.

Coverage is counted once per player-map, before the row is folded into any
slice. A column counts as tracked for a title once MIN_NONZERO_ROWS of its rows
are non-zero — an absolute floor rather than a share, so genuinely rare events
(aces, 4-pieces) stay tracked while never-populated columns drop out.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast

import psycopg

TITLE_IW = "IW"
TITLE_WWII = "WWII"
TITLE_BO4 = "BO4"
TITLE_ORDER = (TITLE_IW, TITLE_WWII, TITLE_BO4)

MIN_NONZERO_ROWS = 20

MODE_HARDPOINT = "hardpoint"
MODE_SND = "search-and-destroy"
MODE_CONTROL = "control"
MODE_CTF = "capture-the-flag"
MODE_UPLINK = "uplink"

# Typed columns on game_player_stats, read straight off the row.
TYPED_COLUMNS: tuple[str, ...] = (
    "kills",
    "deaths",
    "assists",
    "damage",
    "hill_time",
    "first_bloods",
    "plants",
    "defuses",
)

# extras keys that are counts, summable across maps.
NUMERIC_EXTRAS: tuple[str, ...] = (
    "2_piece",
    "3_piece",
    "4_piece",
    "4_streak",
    "5_streak",
    "6_streak",
    "7_streak",
    "8plus_streak",
    "bomb_pickups",
    "bomb_sneak_defuses",
    "headshots",
    "hill_captures",
    "hill_defends",
    "hits",
    "shots",
    "snd_rounds",
    "suicides",
    "team_kills",
    "time_alive_s",
    "num_lives",
    "kills_stayed_alive",
    "team_deaths",
    "snd_firstdeaths",
    "snd_survives",
    "snd_1_kill_round",
    "snd_2_kill_round",
    "snd_3_kill_round",
    "snd_4_kill_round",
    "uplink_dunks",
    "uplink_throws",
    "uplink_points",
    "payloads_earned",
    "payloads_used",
    "ctf_captures",
    "ctf_returns",
    "ctf_pickups",
    "ctf_defends",
    "ctf_kill_carriers",
    "ctf_flag_carry_time_s",
    "scorestreaks_deployed",
    "scorestreaks_kills",
    "scorestreaks_assists",
    "scorestreaks_earned",
    "scorestreaks_used",
    "ekia",
    "player_score",
    "ctrl_captures",
    "ctrl_firstbloods",
    "ctrl_firstdeaths",
    "ctrl_rounds",
)

# A per-map mean, not a count: it re-weights by that map's kills rather than summing.
KILL_DIST = "avg_kill_dist_m"

MEASURED_KEYS: tuple[str, ...] = (*TYPED_COLUMNS, *NUMERIC_EXTRAS, KILL_DIST)

MAP_SQL = """
SELECT gps.player_id, gps.team_id, g.id AS game_id, se.id AS season_id,
       g.mode_id, gm.slug AS mode_slug, t.short_name AS title,
       ev.id AS event_id, s.played_at, g.duration_s, g.winner_team_id,
       gps.kills, gps.deaths, gps.assists, gps.damage, gps.hill_time,
       gps.first_bloods, gps.plants, gps.defuses, gps.extras,
       sum(COALESCE(gps.kills, 0))
         OVER (PARTITION BY gps.game_id, gps.team_id) AS team_kills_map,
       sum(COALESCE(gps.hill_time, 0))
         OVER (PARTITION BY gps.game_id, gps.team_id) AS team_hill_time_map
FROM game_player_stats gps
JOIN games g       ON g.id = gps.game_id
JOIN series s      ON s.id = g.series_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN titles t      ON t.id = se.title_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE g.duration_s IS NOT NULL
ORDER BY s.played_at, g.id, gps.player_id
"""


@dataclass(frozen=True)
class MapRow:
    """One player's line on one map, with its framing."""

    player_id: int
    team_id: int
    game_id: int
    season_id: int
    mode_id: int
    mode_slug: str
    title: str
    event_id: int
    played_at: date
    duration_s: float
    winner_team_id: int | None
    values: dict[str, float]  # measured keys only — absent means not reported
    team_kills: float
    team_hill_time: float

    @property
    def won(self) -> bool | None:
        if self.winner_team_id is None:
            return None
        return self.team_id == self.winner_team_id

    def get(self, key: str, default: float = 0.0) -> float:
        return self.values.get(key, default)


@dataclass
class KeyCoverage:
    """How many of a title's player-map rows carry a real value for one column."""

    rows: int = 0
    present: int = 0
    nonzero: int = 0

    @property
    def tracked(self) -> bool:
        return self.nonzero >= MIN_NONZERO_ROWS


Coverage = dict[str, dict[str, KeyCoverage]]


def record_coverage(coverage: Coverage, title: str, key: str, value: float | None) -> None:
    cov = coverage.setdefault(title, {}).setdefault(key, KeyCoverage())
    cov.rows += 1
    if value is not None:
        cov.present += 1
        if value != 0.0:
            cov.nonzero += 1


def tracked(coverage: Coverage, title: str, key: str) -> bool:
    return coverage.get(title, {}).get(key, KeyCoverage()).tracked


def titles_tracking(coverage: Coverage, keys: tuple[str, ...]) -> tuple[str, ...]:
    """Titles whose rows carry every one of these columns."""
    return tuple(
        title
        for title in TITLE_ORDER
        if title in coverage and all(tracked(coverage, title, key) for key in keys)
    )


def extras_number(extras: dict[str, Any], key: str) -> float | None:
    raw = extras.get(key)
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        value = float(raw)
    elif isinstance(raw, str):
        try:
            value = float(raw)
        except ValueError:
            return None
    else:
        return None
    return value if math.isfinite(value) else None


@dataclass
class Loaded:
    rows: list[MapRow] = field(default_factory=list)
    coverage: Coverage = field(default_factory=dict)


def load_map_rows(conn: psycopg.Connection[tuple[object, ...]]) -> Loaded:
    """Every player-map in the archive, in played order, with coverage measured."""
    out = Loaded()
    for r in conn.execute(MAP_SQL):
        title = cast(str, r[6])
        extras = cast(dict[str, Any], r[19] or {})

        values: dict[str, float] = {}
        for i, name in enumerate(TYPED_COLUMNS):
            raw = cast("int | None", r[11 + i])
            value = None if raw is None else float(raw)
            record_coverage(out.coverage, title, name, value)
            if value is not None:
                values[name] = value
        for name in (*NUMERIC_EXTRAS, KILL_DIST):
            value = extras_number(extras, name)
            record_coverage(out.coverage, title, name, value)
            if value is not None:
                values[name] = value

        out.rows.append(
            MapRow(
                player_id=cast(int, r[0]),
                team_id=cast(int, r[1]),
                game_id=cast(int, r[2]),
                season_id=cast(int, r[3]),
                mode_id=cast(int, r[4]),
                mode_slug=cast(str, r[5]),
                title=title,
                event_id=cast(int, r[7]),
                played_at=cast(datetime, r[8]).date(),
                duration_s=float(cast(int, r[9])),
                winner_team_id=cast("int | None", r[10]),
                values=values,
                team_kills=float(cast(int, r[20]) or 0),
                team_hill_time=float(cast(int, r[21]) or 0),
            )
        )
    return out
