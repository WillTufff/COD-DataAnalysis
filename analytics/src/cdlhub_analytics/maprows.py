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
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, cast

import psycopg

TITLE_IW = "IW"
TITLE_WWII = "WWII"
TITLE_BO4 = "BO4"
# Chronological, and it is a gate as much as an order: `titles_tracking` reads
# only titles named here, so a title the archive gains and this tuple does not
# publishes no metric at all and does so silently. The four pre-2017 titles sat
# in the database for a whole run before this line caught up with them.
TITLE_ORDER = (
    "BO2",
    "GHO",
    "AW",
    "BO3",
    TITLE_IW,
    TITLE_WWII,
    TITLE_BO4,
    "MW19",
    "BOCW",
    "VG",
    "MWII",
    "MWIII",
    "BO6",
    "BO7",
)

MIN_NONZERO_ROWS = 20

# The first season whose rating is published. Earlier seasons are loaded, fitted
# and shown as maps on a player's page; what waits is the score that would rank
# one of them against a modern season, because the rule that makes an
# open-bracket field comparable to a closed league is not built yet.
#
# It lives here, next to the title vocabulary, because two very different places
# need the same answer: the career-rank engine, which decides what to publish,
# and the evaluation harness, which decides what to score. A forward test run on
# seasons the site withholds is measuring a number nobody can see, and when the
# 2013-2016 seasons first entered that panel they doubled it and halved the
# published forward correlation.
PUBLISHED_FROM_YEAR = 2017

MODE_HARDPOINT = "hardpoint"
MODE_SND = "search-and-destroy"
MODE_CONTROL = "control"
MODE_CTF = "capture-the-flag"
MODE_UPLINK = "uplink"
MODE_DOMINATION = "domination"
MODE_BLITZ = "blitz"
MODE_OVERLOAD = "overload"

# Every mode the archive holds maps for. A rating feature set is a dictionary
# keyed by these, and a lookup that misses returns no features rather than an
# error, so a mode absent from that dictionary is a mode nobody rates and
# nothing says so. Ghosts Domination and Blitz sat unrated for four years and
# Black Ops 7 Overload for a season, each a third of its year's maps. This
# tuple is what `gates.mode_vocabulary_failures` checks the archive against.
MODE_ORDER = (
    MODE_HARDPOINT,
    MODE_SND,
    MODE_CONTROL,
    MODE_CTF,
    MODE_UPLINK,
    MODE_DOMINATION,
    MODE_BLITZ,
    MODE_OVERLOAD,
)

# Typed columns on game_player_stats, read straight off the row. Keys shared
# with NUMERIC_EXTRAS (snd_rounds, ctrl_captures) merge typed-first: the CWL
# archive reports them in extras, Cito in columns, and either way it is one
# quantity under one key.
TYPED_COLUMNS: tuple[str, ...] = (
    "kills",
    "deaths",
    "assists",
    "damage",
    "hill_time",
    "first_bloods",
    "plants",
    "defuses",
    "contested_hill_time",
    "first_deaths",
    "captures",
    "non_traded_kills",
    "snd_rounds",
    "clutch_1v1",
    "clutch_1v2",
    "clutch_1v3",
    "clutch_1v4",
    "ctl_attack_rounds",
    "ctl_defense_rounds",
    "ctrl_captures",
    "headshots",
    "suicides",
    "team_kills",
    "hits",
    "shots",
    "hill_captures",
    "hill_defends",
    "bomb_pickups",
    "multikill_2",
    "multikill_3",
    "multikill_4",
)

# Columns whose name differs from the key everything downstream reads them by.
# Migration 0015 promoted the archive's `2-piece`/`3-piece`/`4-piece` counts out
# of extras, and a column cannot lead with a digit; the metric layer's keys are
# published and do not move for a storage change.
TYPED_ALIASES: dict[str, str] = {
    "multikill_2": "2_piece",
    "multikill_3": "3_piece",
    "multikill_4": "4_piece",
}

# The player's primary weapon on the map: a label, not a count, so it travels
# beside `values` rather than in it. Observed on 2017-2019 only, and the only
# role ground truth in the record.
WEAPON_KEY = "fave_weapon"

# The map's score margin, tracked like a column so a title that records no map
# score is visible as one rather than as a run of draws.
MARGIN_KEY = "margin"

# extras keys that are counts, summable across maps. The dozen the fits read
# every run moved to columns in migration 0015 and are listed above instead —
# one home per quantity, so the two cannot drift.
NUMERIC_EXTRAS: tuple[str, ...] = (
    "4_streak",
    "5_streak",
    "6_streak",
    "7_streak",
    "8plus_streak",
    "bomb_sneak_defuses",
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
    "blitz_caps",
    "ctrl_captures",
    "ctrl_firstbloods",
    "ctrl_firstdeaths",
    "ctrl_rounds",
)

# A per-map mean, not a count: it re-weights by that map's kills rather than summing.
KILL_DIST = "avg_kill_dist_m"

# The keys a metric may name as a source — logical names, so an aliased column
# is known by the name the catalog uses rather than by the name it is stored as.
MEASURED_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        (
            *(TYPED_ALIASES.get(name, name) for name in TYPED_COLUMNS),
            *NUMERIC_EXTRAS,
            KILL_DIST,
        )
    )
)

MAP_SQL = """
SELECT gps.player_id, gps.team_id, g.id AS game_id, se.id AS season_id,
       g.mode_id, gm.slug AS mode_slug, t.short_name AS title,
       ev.id AS event_id, s.played_at, g.duration_s, g.winner_team_id,
       gps.kills, gps.deaths, gps.assists, gps.damage, gps.hill_time,
       gps.first_bloods, gps.plants, gps.defuses,
       gps.contested_hill_time, gps.first_deaths, gps.captures,
       gps.non_traded_kills, gps.snd_rounds,
       gps.clutch_1v1, gps.clutch_1v2, gps.clutch_1v3, gps.clutch_1v4,
       gps.ctl_attack_rounds, gps.ctl_defense_rounds, gps.ctl_zone_captures,
       gps.headshots, gps.suicides, gps.team_kills, gps.hits, gps.shots,
       gps.hill_captures, gps.hill_defends, gps.bomb_pickups,
       gps.multikill_2, gps.multikill_3, gps.multikill_4,
       gps.extras,
       sum(COALESCE(gps.kills, 0))
         OVER (PARTITION BY gps.game_id, gps.team_id) AS team_kills_map,
       sum(COALESCE(gps.hill_time, 0))
         OVER (PARTITION BY gps.game_id, gps.team_id) AS team_hill_time_map,
       gps.fave_weapon,
       s.id AS series_id,
       -- The map's score margin from this player's side. Maps within a series
       -- share a lineup, a day and an opponent, so the series is also the unit
       -- every interval in the rating stack resamples.
       CASE WHEN gps.team_id = s.team1_id THEN g.team1_score - g.team2_score
            WHEN gps.team_id = s.team2_id THEN g.team2_score - g.team1_score
       END AS margin,
       -- The map's natural key. `games.id` and `series.id` are assigned by the
       -- loader and renumber on any reload that deletes and recreates rows, so
       -- anything a resample orders or seeds on reads this instead.
       s.source_uid || '#' || g.ordinal::text AS map_key
FROM game_player_stats gps
JOIN games g       ON g.id = gps.game_id
JOIN series s      ON s.id = g.series_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN titles t      ON t.id = se.title_id
JOIN game_modes gm ON gm.id = g.mode_id
ORDER BY s.played_at, g.id, gps.player_id
"""

# Map time itself, tracked like a column: Cito games carry no duration, so
# per-10-minute metrics gate on this key to stay off titles that cannot pace.
DURATION_KEY = "duration_s"


@dataclass(frozen=True)
class MapRow:
    """One player's line on one map, with its framing."""

    player_id: int
    team_id: int
    game_id: int
    series_id: int
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
    weapon: str | None = None
    # The map's score margin from this side, where the record carries both
    # scores. None on the 27 maps that carry neither; a plus-minus fitted on
    # margin says so rather than reading a missing score as a draw.
    margin: float | None = None
    # <source_uid>#<ordinal>, the map's natural key. None where the series
    # carries no source_uid. Anything ordered or seeded by a map reads this
    # rather than `game_id`, which the loader renumbers.
    map_key: str | None = None

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


# The share of a slice's own rows that must report a column before anything may
# rate on it. `tracked` above answers a different question — does this title
# record this quantity at all — and answers it across every mode the title
# played, which is the only grain the metric layer needs.
#
# A rating cohort is one (season x mode), and a column reported on a handful of
# that cohort's rows fails it in a way the title-wide count cannot see. Missing
# is not zero, but a rate sums numerator and denominator across a player's maps,
# so an absent column enters the sum as a zero and the player's rate becomes a
# function of how many of their maps happened to be reported. Black Ops 7
# reports `captures` on 32 of 2,240 Overload rows and 24 of them are non-zero,
# which clears the title-wide floor of 20 and means nothing.
#
# 0.25 is not a tuned number. Measured over every cohort in the archive, the
# columns that are genuinely one mode's own sit at 0.497 and above, and the
# partly-reported ones at 0.065 and below. The floor sits inside an empty band,
# so moving it anywhere in that band changes nothing.
MIN_PRESENT_SHARE = 0.25


def present_share(rows: Sequence[MapRow], key: str) -> float:
    """The share of these rows that report a real value for one column."""
    if not rows:
        return 0.0
    if key == DURATION_KEY:
        return sum(1 for r in rows if r.duration_s > 0) / len(rows)
    return sum(1 for r in rows if key in r.values) / len(rows)


def reported(rows: Sequence[MapRow], key: str) -> bool:
    return present_share(rows, key) >= MIN_PRESENT_SHARE


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
    extras_at = 11 + len(TYPED_COLUMNS)
    for r in conn.execute(MAP_SQL):
        title = cast(str, r[6])
        extras = cast(dict[str, Any], r[extras_at] or {})
        duration = cast("int | None", r[9])

        # Typed column first, extras key as fallback, one coverage record per
        # key either way.
        merged: dict[str, float | None] = {}
        for i, name in enumerate(TYPED_COLUMNS):
            raw = cast("int | None", r[11 + i])
            merged[TYPED_ALIASES.get(name, name)] = None if raw is None else float(raw)
        for name in (*NUMERIC_EXTRAS, KILL_DIST):
            if merged.get(name) is None:
                merged[name] = extras_number(extras, name)
        if merged.get("ctrl_rounds") is None:
            attack, defense = merged.get("ctl_attack_rounds"), merged.get("ctl_defense_rounds")
            if attack is not None or defense is not None:
                merged["ctrl_rounds"] = (attack or 0.0) + (defense or 0.0)
        # The archive reports first deaths in extras and only for SnD; the CDL
        # source reports the same quantity in a column. One key, either way.
        if merged.get("snd_firstdeaths") is None:
            merged["snd_firstdeaths"] = merged.get("first_deaths")

        values: dict[str, float] = {}
        for name, value in merged.items():
            record_coverage(out.coverage, title, name, value)
            if value is not None:
                values[name] = value
        record_coverage(out.coverage, title, DURATION_KEY, float(duration) if duration else None)
        weapon = cast("str | None", r[extras_at + 3])
        record_coverage(out.coverage, title, WEAPON_KEY, 1.0 if weapon else None)
        margin = cast("int | None", r[extras_at + 5])
        record_coverage(out.coverage, title, MARGIN_KEY, None if margin is None else float(margin))

        out.rows.append(
            MapRow(
                player_id=cast(int, r[0]),
                team_id=cast(int, r[1]),
                game_id=cast(int, r[2]),
                series_id=cast(int, r[extras_at + 4]),
                season_id=cast(int, r[3]),
                mode_id=cast(int, r[4]),
                mode_slug=cast(str, r[5]),
                title=title,
                event_id=cast(int, r[7]),
                played_at=cast(datetime, r[8]).date(),
                duration_s=float(duration or 0),
                winner_team_id=cast("int | None", r[10]),
                values=values,
                team_kills=float(cast(int, r[extras_at + 1]) or 0),
                team_hill_time=float(cast(int, r[extras_at + 2]) or 0),
                weapon=weapon,
                margin=None if margin is None else float(margin),
                map_key=cast("str | None", r[extras_at + 6]),
            )
        )
    return out
