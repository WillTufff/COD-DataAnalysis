"""Derived metric layer.

Aggregates every player's box scores into per (season, mode) totals, then
computes each catalog metric from those totals. Numerators and denominators are
summed separately and divided once, so a metric over many maps is never the
mean of per-map ratios.

Which titles a metric covers is derived from the data, not declared. Each metric
names the source columns it reads; a title publishes the metric only when every
source has real values in that title's rows. Several columns exist for a title
but were never populated (BO4 kills_stayed_alive, WWII hill_captures), and a
declared matrix would publish those as zeros.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

import psycopg

from .cohort import z_and_pctl
from .era import MIN_MAPS

MODEL = "metric_layer"
# 2.0.0 adds the Phase B kill-feed layer (trades, clutch, man-advantage) and the
# weapon/engagement-distance artifacts on top of the 1.x box-score catalog.
VERSION = "2.0.0"

ALL_MODES = "__all__"

MIN_SND_ROUNDS = 50.0
MIN_CTRL_ROUNDS = 50.0
MIN_SHOTS = 1000.0
MIN_KILLS = 100.0

# --- kill-feed (Phase B) qualification and constants ---
# 5,000 ms matches the archive's own trade convention (and the box-score
# kills_stayed_alive column, which flags a kill answered within 5 s).
TRADE_WINDOW_MS = 5000
MIN_FEED_DEATHS = 100.0
MIN_FEED_KILLS = 100.0
MIN_FEED_FIRST_DEATHS = 20.0

# Per-(player, map) kill-feed quantities, summed into Aggregate.sums under these
# keys. Recorded only for reconciled IW/WWII player-maps, so their coverage is
# zero for BO4 and the sources= mechanism excludes it with no titles list.
KF_DEATHS = "kf_deaths"
KF_TRADED = "kf_traded_deaths"
KF_UNTRADED = "kf_untraded_deaths"
KF_KILLS = "kf_kills"
KF_TRADE_KILLS = "kf_trade_kills"
KF_ANSWERED = "kf_answered_kills"
KF_FIRST_DEATHS = "kf_first_deaths"
KF_FIRST_UNTRADED = "kf_first_deaths_untraded"
KF_KEYS: tuple[str, ...] = (
    KF_DEATHS,
    KF_TRADED,
    KF_UNTRADED,
    KF_KILLS,
    KF_TRADE_KILLS,
    KF_ANSWERED,
    KF_FIRST_DEATHS,
    KF_FIRST_UNTRADED,
)

# --- clutch and man-advantage (SnD only) ---
CLUTCH_NS = (1, 2, 3, 4)
MIN_CLUTCH_ATTEMPTS = 15.0
MIN_CLUTCH_N_ATTEMPTS = 5.0
MIN_ADV_ROUNDS = 25.0


def _clutch_att(n: int) -> str:
    return f"kf_clutch_att_{n}"


def _clutch_win(n: int) -> str:
    return f"kf_clutch_win_{n}"


KF_CLUTCH_ATT = "kf_clutch_att"  # totals across N, for the combined win rate
KF_CLUTCH_WIN = "kf_clutch_win"
KF_ADV_ROUNDS = "kf_adv_rounds"  # rounds the team opened up a man (took first blood)
KF_ADV_WINS = "kf_adv_wins"
KF_DISADV_ROUNDS = "kf_disadv_rounds"  # rounds the team opened down a man (conceded first blood)
KF_DISADV_WINS = "kf_disadv_wins"
KF_THROWN = "kf_thrown_deaths"  # deaths taken while the team was up a man
KF_SND_ROUNDS = "kf_snd_rounds_played"

CLUTCH_KEYS: tuple[str, ...] = (
    *(_clutch_att(n) for n in CLUTCH_NS),
    *(_clutch_win(n) for n in CLUTCH_NS),
    KF_CLUTCH_ATT,
    KF_CLUTCH_WIN,
    KF_ADV_ROUNDS,
    KF_ADV_WINS,
    KF_DISADV_ROUNDS,
    KF_DISADV_WINS,
    KF_THROWN,
    KF_SND_ROUNDS,
)

TITLE_IW = "IW"
TITLE_WWII = "WWII"
TITLE_BO4 = "BO4"
TITLE_ORDER = (TITLE_IW, TITLE_WWII, TITLE_BO4)

# A source column counts as tracked for a title once this many of its rows are
# non-zero. An absolute floor rather than a share: genuinely rare events (aces,
# 4-pieces) sit near 1% of rows, while untracked columns sit at 0.
MIN_NONZERO_ROWS = 20

MODE_HARDPOINT = "hardpoint"
MODE_SND = "search-and-destroy"
MODE_CONTROL = "control"
MODE_CTF = "capture-the-flag"
MODE_UPLINK = "uplink"

# extras keys summed across maps, by the titles that carry them.
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

_MAP_SQL = """
SELECT gps.player_id, gps.team_id, se.id AS season_id, g.mode_id, gm.slug AS mode_slug,
       t.short_name AS title, g.duration_s,
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
"""


@dataclass
class Aggregate:
    """Summed totals for one player across one (season, mode) slice."""

    player_id: int
    season_id: int
    mode_id: int | None
    mode_slug: str
    title: str
    maps: int = 0
    duration_s: float = 0.0
    damage_duration_s: float = 0.0
    team_kills: float = 0.0
    team_hill_time: float = 0.0
    kill_dist_weighted: float = 0.0
    kill_dist_kills: float = 0.0
    # kill feed: reconciled feed maps only, so the p10 denom covers just those.
    feed_maps: int = 0
    feed_duration_s: float = 0.0
    sums: dict[str, float] = field(default_factory=dict)
    present_maps: dict[str, int] = field(default_factory=dict)

    def total(self, key: str) -> float:
        return self.sums.get(key, 0.0)

    def has(self, key: str) -> bool:
        return self.present_maps.get(key, 0) > 0

    def add(self, key: str, value: float) -> None:
        self.sums[key] = self.sums.get(key, 0.0) + value

    @property
    def minutes(self) -> float:
        return self.duration_s / 60.0

    @property
    def per10_denom(self) -> float:
        return self.duration_s / 600.0

    @property
    def feed_per10_denom(self) -> float:
        return self.feed_duration_s / 600.0


Computed = tuple[float, float] | None


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


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    category: str
    tier: str
    unit: str
    higher_is_better: bool
    formula: str
    denom_kind: str
    min_denom: float
    sources: tuple[str, ...]
    modes: tuple[str, ...]
    compute: Callable[[Aggregate], Computed]
    note: str | None = None

    def titles(self, coverage: Coverage) -> tuple[str, ...]:
        """Titles whose rows actually carry every source column this metric reads."""
        found = [
            title
            for title in TITLE_ORDER
            if title in coverage
            and all(coverage[title].get(src, KeyCoverage()).tracked for src in self.sources)
        ]
        return tuple(found)

    def covers_mode(self, mode_slug: str) -> bool:
        return ALL_MODES in self.modes or mode_slug in self.modes

    def applies_to(self, agg: Aggregate, coverage: Coverage) -> bool:
        return agg.title in self.titles(coverage) and self.covers_mode(agg.mode_slug)

    def catalog_entry(self, coverage: Coverage) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "tier": self.tier,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "formula": self.formula,
            "denom_kind": self.denom_kind,
            "min_denom": self.min_denom,
            "sources": list(self.sources),
            "titles": list(self.titles(coverage)),
            "modes": list(self.modes),
            "note": self.note,
        }


def _ratio(numerator: float, denominator: float) -> Computed:
    """Value plus its denominator, or None when there is nothing to divide by."""
    if denominator <= 0:
        return None
    value = numerator / denominator
    if not math.isfinite(value):
        return None
    return value, denominator


def _per10(agg: Aggregate, numerator: float) -> Computed:
    if agg.per10_denom <= 0:
        return None
    value = numerator / agg.per10_denom
    if not math.isfinite(value):
        return None
    return value, float(agg.maps)


# ---------- metric builders ----------

Terms = tuple[tuple[str, float], ...]


def _terms(*keys: str) -> Terms:
    return tuple((k, 1.0) for k in keys)


def _weighted(agg: Aggregate, terms: Terms) -> float:
    return sum(agg.total(key) * weight for key, weight in terms)


def _p10(*keys: str) -> Callable[[Aggregate], Computed]:
    """Per 10 minutes of map time, qualified by maps played."""
    terms = _terms(*keys)

    def compute(agg: Aggregate) -> Computed:
        return _per10(agg, _weighted(agg, terms))

    return compute


def _weighted_p10(terms: Terms) -> Callable[[Aggregate], Computed]:
    def compute(agg: Aggregate) -> Computed:
        return _per10(agg, _weighted(agg, terms))

    return compute


def _pm(*keys: str) -> Callable[[Aggregate], Computed]:
    """Per map, qualified by maps played."""
    terms = _terms(*keys)

    def compute(agg: Aggregate) -> Computed:
        return _ratio(_weighted(agg, terms), float(agg.maps))

    return compute


def _total(*keys: str) -> Callable[[Aggregate], Computed]:
    """Raw count over the slice, qualified by maps played."""
    terms = _terms(*keys)

    def compute(agg: Aggregate) -> Computed:
        if agg.maps <= 0:
            return None
        return _weighted(agg, terms), float(agg.maps)

    return compute


def _rate(numerator: Terms, denominator: str) -> Callable[[Aggregate], Computed]:
    """Ratio whose qualification denominator is the divisor itself."""

    def compute(agg: Aggregate) -> Computed:
        return _ratio(_weighted(agg, numerator), agg.total(denominator))

    return compute


def _rate_over_maps(numerator: Terms, denominator: Terms) -> Callable[[Aggregate], Computed]:
    """Ratio of two totals, but qualified by maps played."""

    def compute(agg: Aggregate) -> Computed:
        result = _ratio(_weighted(agg, numerator), _weighted(agg, denominator))
        if result is None:
            return None
        return result[0], float(agg.maps)

    return compute


def _count_over(key: str, denominator: str) -> Callable[[Aggregate], Computed]:
    """A raw count, qualified by a different total (e.g. rounds played)."""

    def compute(agg: Aggregate) -> Computed:
        denom = agg.total(denominator)
        if denom <= 0:
            return None
        return agg.total(key), denom

    return compute


def _complement(inner: Callable[[Aggregate], Computed]) -> Callable[[Aggregate], Computed]:
    def compute(agg: Aggregate) -> Computed:
        result = inner(agg)
        if result is None:
            return None
        return 1.0 - result[0], result[1]

    return compute


SND_ROUND_KILL_KEYS = (
    "snd_1_kill_round",
    "snd_2_kill_round",
    "snd_3_kill_round",
    "snd_4_kill_round",
)


def _kd(agg: Aggregate) -> Computed:
    if agg.maps <= 0:
        return None
    value = agg.total("kills") / max(agg.total("deaths"), 1.0)
    return value, float(agg.maps)


def _damage_p10(agg: Aggregate) -> Computed:
    """Rate over only the maps that reported damage."""
    maps_with_damage = float(agg.present_maps.get("damage", 0))
    if agg.damage_duration_s <= 0 or maps_with_damage <= 0:
        return None
    value = agg.total("damage") / (agg.damage_duration_s / 600.0)
    if not math.isfinite(value):
        return None
    return value, maps_with_damage


def _kill_share(agg: Aggregate) -> Computed:
    result = _ratio(agg.total("kills"), agg.team_kills)
    if result is None:
        return None
    return result[0], float(agg.maps)


def _hill_time_share(agg: Aggregate) -> Computed:
    result = _ratio(agg.total("hill_time"), agg.team_hill_time)
    if result is None:
        return None
    return result[0], float(agg.maps)


def _avg_kill_dist(agg: Aggregate) -> Computed:
    return _ratio(agg.kill_dist_weighted, agg.kill_dist_kills)


def _snd_round_share(kills_in_round: int) -> Callable[[Aggregate], Computed]:
    """Share of SnD rounds in which the player got exactly N kills."""
    if kills_in_round == 0:

        def zero(agg: Aggregate) -> Computed:
            rounds = agg.total("snd_rounds")
            scored = _weighted(agg, _terms(*SND_ROUND_KILL_KEYS))
            return _ratio(rounds - scored, rounds)

        return zero

    key = f"snd_{kills_in_round}_kill_round"

    def compute(agg: Aggregate) -> Computed:
        return _ratio(agg.total(key), agg.total("snd_rounds"))

    return compute


# ---------- kill-feed compute helpers ----------


def _feed_rate(numerator: str, denominator: str) -> Callable[[Aggregate], Computed]:
    """Ratio of two kill-feed totals, qualified by the divisor itself."""

    def compute(agg: Aggregate) -> Computed:
        return _ratio(agg.total(numerator), agg.total(denominator))

    return compute


def _feed_p10(key: str) -> Callable[[Aggregate], Computed]:
    """Per 10 minutes of reconciled feed-map time, qualified by feed maps."""

    def compute(agg: Aggregate) -> Computed:
        denom = agg.feed_per10_denom
        if denom <= 0:
            return None
        value = agg.total(key) / denom
        if not math.isfinite(value):
            return None
        return value, float(agg.feed_maps)

    return compute


# ---------- catalog ----------

_SLAYING: tuple[Metric, ...] = (
    Metric(
        key="kd",
        label="K/D",
        category="slaying",
        tier="standard",
        unit="ratio",
        higher_is_better=True,
        formula="sum(kills) / max(sum(deaths), 1)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("kills", "deaths"),
        modes=(ALL_MODES,),
        compute=_kd,
    ),
    Metric(
        key="kills_p10",
        label="Kills per 10 min",
        category="slaying",
        tier="gold",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(kills) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("kills",),
        modes=(ALL_MODES,),
        compute=_p10("kills"),
    ),
    Metric(
        key="deaths_p10",
        label="Deaths per 10 min",
        category="slaying",
        tier="gold",
        unit="per 10 min",
        higher_is_better=False,
        formula="sum(deaths) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("deaths",),
        modes=(ALL_MODES,),
        compute=_p10("deaths"),
    ),
    Metric(
        key="plus_minus_p10",
        label="Plus/minus per 10 min",
        category="slaying",
        tier="gold",
        unit="per 10 min",
        higher_is_better=True,
        formula="(sum(kills) - sum(deaths)) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("kills", "deaths"),
        modes=(ALL_MODES,),
        compute=_weighted_p10((("kills", 1.0), ("deaths", -1.0))),
    ),
    Metric(
        key="engagement_p10",
        label="Engagements per 10 min",
        category="slaying",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="(sum(kills) + sum(deaths)) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("kills", "deaths"),
        modes=(ALL_MODES,),
        compute=_p10("kills", "deaths"),
        note="A pace and aggression axis rather than a quality one.",
    ),
    Metric(
        key="assists_p10",
        label="Assists per 10 min",
        category="slaying",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(assists) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("assists",),
        modes=(ALL_MODES,),
        compute=_p10("assists"),
    ),
    Metric(
        key="ekia_p10",
        label="EKIA per 10 min",
        category="slaying",
        tier="gold",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(ekia) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ekia",),
        modes=(ALL_MODES,),
        compute=_p10("ekia"),
        note="Kills plus assists that counted as eliminations.",
    ),
    Metric(
        key="damage_p10",
        label="Damage per 10 min",
        category="slaying",
        tier="gold",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(damage) / sum(duration_s of maps reporting damage) * 600",
        denom_kind="maps with damage",
        min_denom=float(MIN_MAPS),
        sources=("damage",),
        modes=(ALL_MODES,),
        compute=_damage_p10,
        note="Damage is missing on a share of maps; the rate uses only maps that reported it.",
    ),
    Metric(
        key="kill_share",
        label="Kill share",
        category="slaying",
        tier="gold",
        unit="share of team",
        higher_is_better=True,
        formula="sum(kills) / sum(team kills on the same maps)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("kills",),
        modes=(ALL_MODES,),
        compute=_kill_share,
        note="An even split across a four-player roster is 25%.",
    ),
    Metric(
        key="headshot_rate",
        label="Headshot rate",
        category="slaying",
        tier="standard",
        unit="share of kills",
        higher_is_better=True,
        formula="sum(headshots) / sum(kills)",
        denom_kind="kills",
        min_denom=MIN_KILLS,
        sources=("headshots", "kills"),
        modes=(ALL_MODES,),
        compute=_rate(_terms("headshots"), "kills"),
    ),
    Metric(
        key="accuracy",
        label="Accuracy",
        category="slaying",
        tier="standard",
        unit="share of shots",
        higher_is_better=True,
        formula="sum(hits) / sum(shots)",
        denom_kind="shots",
        min_denom=MIN_SHOTS,
        sources=("hits", "shots"),
        modes=(ALL_MODES,),
        compute=_rate(_terms("hits"), "shots"),
    ),
    Metric(
        key="avg_kill_dist_m",
        label="Average kill distance",
        category="slaying",
        tier="fun",
        unit="metres",
        higher_is_better=True,
        formula="kills-weighted mean of each map's average kill distance",
        denom_kind="kills",
        min_denom=MIN_KILLS,
        sources=("avg_kill_dist_m",),
        modes=(ALL_MODES,),
        compute=_avg_kill_dist,
    ),
)

_DISCIPLINE: tuple[Metric, ...] = (
    Metric(
        key="time_per_life_s",
        label="Time per life",
        category="discipline",
        tier="gold",
        unit="seconds",
        higher_is_better=True,
        formula="sum(time_alive_s) / sum(num_lives)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("time_alive_s", "num_lives"),
        modes=(ALL_MODES,),
        compute=_rate_over_maps(_terms("time_alive_s"), _terms("num_lives")),
    ),
    Metric(
        key="lives_p10",
        label="Lives per 10 min",
        category="discipline",
        tier="standard",
        unit="per 10 min",
        higher_is_better=False,
        formula="sum(num_lives) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("num_lives",),
        modes=(ALL_MODES,),
        compute=_p10("num_lives"),
    ),
    Metric(
        key="clean_kill_rate",
        label="Clean kill rate",
        category="discipline",
        tier="gold",
        unit="share of kills",
        higher_is_better=True,
        formula="sum(kills_stayed_alive) / sum(kills)",
        denom_kind="kills",
        min_denom=MIN_KILLS,
        sources=("kills_stayed_alive", "kills"),
        modes=(ALL_MODES,),
        compute=_rate(_terms("kills_stayed_alive"), "kills"),
        note="Share of a player's kills after which they were not killed within 5 seconds.",
    ),
    Metric(
        key="traded_back_rate",
        label="Kills answered rate",
        category="discipline",
        tier="gold",
        unit="share of kills",
        higher_is_better=False,
        formula="1 - sum(kills_stayed_alive) / sum(kills)",
        denom_kind="kills",
        min_denom=MIN_KILLS,
        sources=("kills_stayed_alive", "kills"),
        modes=(ALL_MODES,),
        compute=_complement(_rate(_terms("kills_stayed_alive"), "kills")),
        note=(
            "Share of kills answered within 5 seconds, seen from the attacker's side. "
            "A box-score proxy: true trade accounting needs the kill feed."
        ),
    ),
    Metric(
        key="suicides_pm",
        label="Suicides per map",
        category="discipline",
        tier="fun",
        unit="per map",
        higher_is_better=False,
        formula="sum(suicides) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("suicides",),
        modes=(ALL_MODES,),
        compute=_pm("suicides"),
        note="Deaths to the environment rather than to an opponent.",
    ),
    Metric(
        key="teamkills_total",
        label="Teamkills",
        category="discipline",
        tier="fun",
        unit="count",
        higher_is_better=False,
        formula="sum(team_kills)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("team_kills",),
        modes=(ALL_MODES,),
        compute=_total("team_kills"),
    ),
)

_BURST: tuple[Metric, ...] = (
    Metric(
        key="two_piece_p10",
        label="2-pieces per 10 min",
        category="streaks",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(2_piece) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("2_piece",),
        modes=(ALL_MODES,),
        compute=_p10("2_piece"),
    ),
    Metric(
        key="three_piece_p10",
        label="3-pieces per 10 min",
        category="streaks",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(3_piece) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("3_piece",),
        modes=(ALL_MODES,),
        compute=_p10("3_piece"),
    ),
    Metric(
        key="four_piece_p10",
        label="4-pieces per 10 min",
        category="streaks",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(4_piece) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("4_piece",),
        modes=(ALL_MODES,),
        compute=_p10("4_piece"),
    ),
    Metric(
        key="blitz_index_p10",
        label="Blitz index",
        category="streaks",
        tier="gold",
        unit="per 10 min",
        higher_is_better=True,
        formula="(sum(2_piece) + 2*sum(3_piece) + 4*sum(4_piece)) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("2_piece", "3_piece", "4_piece"),
        modes=(ALL_MODES,),
        compute=_weighted_p10((("2_piece", 1.0), ("3_piece", 2.0), ("4_piece", 4.0))),
        note="Burst slaying weighted so each multikill tier counts double the last.",
    ),
    Metric(
        key="streak4_pm",
        label="4-streaks per map",
        category="streaks",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(4_streak) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("4_streak",),
        modes=(ALL_MODES,),
        compute=_pm("4_streak"),
    ),
    Metric(
        key="streak5_pm",
        label="5-streaks per map",
        category="streaks",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(5_streak) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("5_streak",),
        modes=(ALL_MODES,),
        compute=_pm("5_streak"),
    ),
    Metric(
        key="streak6_pm",
        label="6-streaks per map",
        category="streaks",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(6_streak) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("6_streak",),
        modes=(ALL_MODES,),
        compute=_pm("6_streak"),
    ),
    Metric(
        key="streak7_pm",
        label="7-streaks per map",
        category="streaks",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(7_streak) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("7_streak",),
        modes=(ALL_MODES,),
        compute=_pm("7_streak"),
    ),
    Metric(
        key="streak8plus_pm",
        label="8+ streaks per map",
        category="streaks",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(8plus_streak) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("8plus_streak",),
        modes=(ALL_MODES,),
        compute=_pm("8plus_streak"),
    ),
    Metric(
        key="deep_streak_rate",
        label="Deep streaks per map",
        category="streaks",
        tier="gold",
        unit="per map",
        higher_is_better=True,
        formula="(sum(6_streak) + sum(7_streak) + sum(8plus_streak)) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("6_streak", "7_streak", "8plus_streak"),
        modes=(ALL_MODES,),
        compute=_pm("6_streak", "7_streak", "8plus_streak"),
    ),
    Metric(
        key="eight_plus_streaks_total",
        label="8+ streaks",
        category="streaks",
        tier="fun",
        unit="count",
        higher_is_better=True,
        formula="sum(8plus_streak)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("8plus_streak",),
        modes=(ALL_MODES,),
        compute=_total("8plus_streak"),
    ),
)

_HARDPOINT: tuple[Metric, ...] = (
    Metric(
        key="hill_time_p10",
        label="Hill time per 10 min",
        category="hardpoint",
        tier="gold",
        unit="seconds per 10 min",
        higher_is_better=True,
        formula="sum(hill_time) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("hill_time",),
        modes=(MODE_HARDPOINT,),
        compute=_p10("hill_time"),
    ),
    Metric(
        key="hill_time_share",
        label="Hill time share",
        category="hardpoint",
        tier="gold",
        unit="share of team",
        higher_is_better=True,
        formula="sum(hill_time) / sum(team hill_time on the same maps)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("hill_time",),
        modes=(MODE_HARDPOINT,),
        compute=_hill_time_share,
        note="An even split across a four-player roster is 25%.",
    ),
    Metric(
        key="hill_caps_p10",
        label="Hill captures per 10 min",
        category="hardpoint",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(hill_captures) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("hill_captures",),
        modes=(MODE_HARDPOINT,),
        compute=_p10("hill_captures"),
    ),
    Metric(
        key="hill_defends_p10",
        label="Hill defends per 10 min",
        category="hardpoint",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(hill_defends) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("hill_defends",),
        modes=(MODE_HARDPOINT,),
        compute=_p10("hill_defends"),
    ),
)

_SND: tuple[Metric, ...] = (
    Metric(
        key="snd_kpr",
        label="Kills per round",
        category="snd",
        tier="gold",
        unit="per round",
        higher_is_better=True,
        formula="sum(kills) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("kills", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("kills"), "snd_rounds"),
    ),
    Metric(
        key="snd_dpr",
        label="Deaths per round",
        category="snd",
        tier="gold",
        unit="per round",
        higher_is_better=False,
        formula="sum(deaths) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("deaths", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("deaths"), "snd_rounds"),
    ),
    Metric(
        key="snd_fb_rate",
        label="First-blood rate",
        category="snd",
        tier="gold",
        unit="per round",
        higher_is_better=True,
        formula="sum(first_bloods) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("first_bloods", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("first_bloods"), "snd_rounds"),
        note="First bloods exclude teamkills.",
    ),
    Metric(
        key="snd_fd_rate",
        label="First-death rate",
        category="snd",
        tier="gold",
        unit="per round",
        higher_is_better=False,
        formula="sum(snd_firstdeaths) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("snd_firstdeaths", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("snd_firstdeaths"), "snd_rounds"),
        note="First deaths include deaths to teamkill.",
    ),
    Metric(
        key="snd_fb_net_pr",
        label="First-blood net per round",
        category="snd",
        tier="gold",
        unit="per round",
        higher_is_better=True,
        formula="(sum(first_bloods) - sum(snd_firstdeaths)) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("first_bloods", "snd_firstdeaths", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate((("first_bloods", 1.0), ("snd_firstdeaths", -1.0)), "snd_rounds"),
        note="First deaths include deaths to teamkill; first bloods exclude them.",
    ),
    Metric(
        key="snd_opening_involvement",
        label="Opening-duel involvement",
        category="snd",
        tier="gold",
        unit="per round",
        higher_is_better=True,
        formula="(sum(first_bloods) + sum(snd_firstdeaths)) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("first_bloods", "snd_firstdeaths", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("first_bloods", "snd_firstdeaths"), "snd_rounds"),
        note="How often the player is one half of the round's opening duel.",
    ),
    Metric(
        key="snd_opening_duel_win",
        label="Opening-duel win rate",
        category="snd",
        tier="gold",
        unit="share of openings",
        higher_is_better=True,
        formula="sum(first_bloods) / (sum(first_bloods) + sum(snd_firstdeaths))",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("first_bloods", "snd_firstdeaths", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate_over_maps(_terms("first_bloods"), _terms("first_bloods", "snd_firstdeaths")),
    ),
    Metric(
        key="snd_survival_rate",
        label="Round survival rate",
        category="snd",
        tier="gold",
        unit="share of rounds",
        higher_is_better=True,
        formula="sum(snd_survives) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("snd_survives", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("snd_survives"), "snd_rounds"),
    ),
    Metric(
        key="snd_plants_pr",
        label="Plants per round",
        category="snd",
        tier="standard",
        unit="per round",
        higher_is_better=True,
        formula="sum(plants) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("plants", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("plants"), "snd_rounds"),
    ),
    Metric(
        key="snd_defuses_pr",
        label="Defuses per round",
        category="snd",
        tier="standard",
        unit="per round",
        higher_is_better=True,
        formula="sum(defuses) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("defuses", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("defuses"), "snd_rounds"),
    ),
    Metric(
        key="snd_pickups_pr",
        label="Bomb pickups per round",
        category="snd",
        tier="standard",
        unit="per round",
        higher_is_better=True,
        formula="sum(bomb_pickups) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("bomb_pickups", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("bomb_pickups"), "snd_rounds"),
    ),
    Metric(
        key="sneak_defuses_total",
        label="Sneak defuses",
        category="snd",
        tier="gold-fun",
        unit="count",
        higher_is_better=True,
        formula="sum(bomb_sneak_defuses)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("bomb_sneak_defuses", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_count_over("bomb_sneak_defuses", "snd_rounds"),
        note="Defuses completed with at least one opponent still alive.",
    ),
    Metric(
        key="snd_ace_total",
        label="Aces",
        category="snd",
        tier="gold-fun",
        unit="count",
        higher_is_better=True,
        formula="sum(snd_4_kill_round)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("snd_4_kill_round", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_count_over("snd_4_kill_round", "snd_rounds"),
        note="Four-kill rounds.",
    ),
    Metric(
        key="snd_ace_rate",
        label="Ace rate",
        category="snd",
        tier="standard",
        unit="share of rounds",
        higher_is_better=True,
        formula="sum(snd_4_kill_round) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("snd_4_kill_round", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("snd_4_kill_round"), "snd_rounds"),
    ),
    Metric(
        key="snd_3k_round_rate",
        label="3-kill round rate",
        category="snd",
        tier="standard",
        unit="share of rounds",
        higher_is_better=True,
        formula="sum(snd_3_kill_round) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("snd_3_kill_round", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(_terms("snd_3_kill_round"), "snd_rounds"),
    ),
    Metric(
        key="snd_multi_kill_round_rate",
        label="Multikill round rate",
        category="snd",
        tier="gold",
        unit="share of rounds",
        higher_is_better=True,
        formula=(
            "(sum(snd_2_kill_round) + sum(snd_3_kill_round) + sum(snd_4_kill_round))"
            " / sum(snd_rounds)"
        ),
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=("snd_2_kill_round", "snd_3_kill_round", "snd_4_kill_round", "snd_rounds"),
        modes=(MODE_SND,),
        compute=_rate(
            _terms("snd_2_kill_round", "snd_3_kill_round", "snd_4_kill_round"), "snd_rounds"
        ),
    ),
    Metric(
        key="snd_zero_kill_round_rate",
        label="Zero-kill round rate",
        category="snd",
        tier="gold",
        unit="share of rounds",
        higher_is_better=False,
        formula="1 - sum(snd_1..4_kill_round) / sum(snd_rounds)",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=(*SND_ROUND_KILL_KEYS, "snd_rounds"),
        modes=(MODE_SND,),
        compute=_snd_round_share(0),
        note="Rounds the player finished without a kill.",
    ),
    *(
        Metric(
            key=f"snd_rounds_{n}k_share",
            label=f"{n}-kill round share",
            category="snd",
            tier="standard",
            unit="share of rounds",
            higher_is_better=n > 0,
            formula=(
                f"sum(snd_{n}_kill_round) / sum(snd_rounds)"
                if n > 0
                else "1 - sum(snd_1..4_kill_round) / sum(snd_rounds)"
            ),
            denom_kind="rounds",
            min_denom=MIN_SND_ROUNDS,
            sources=(
                (f"snd_{n}_kill_round", "snd_rounds")
                if n > 0
                else (*SND_ROUND_KILL_KEYS, "snd_rounds")
            ),
            modes=(MODE_SND,),
            compute=_snd_round_share(n),
            note="Part of the round kill distribution shown on player pages.",
        )
        for n in (0, 1, 2, 3, 4)
    ),
)

_CTF: tuple[Metric, ...] = (
    Metric(
        key="ctf_caps_pm",
        label="Flag captures per map",
        category="ctf",
        tier="gold",
        unit="per map",
        higher_is_better=True,
        formula="sum(ctf_captures) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_captures",),
        modes=(MODE_CTF,),
        compute=_pm("ctf_captures"),
    ),
    Metric(
        key="ctf_returns_pm",
        label="Flag returns per map",
        category="ctf",
        tier="gold",
        unit="per map",
        higher_is_better=True,
        formula="sum(ctf_returns) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_returns",),
        modes=(MODE_CTF,),
        compute=_pm("ctf_returns"),
    ),
    Metric(
        key="ctf_defends_pm",
        label="Flag defends per map",
        category="ctf",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(ctf_defends) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_defends",),
        modes=(MODE_CTF,),
        compute=_pm("ctf_defends"),
    ),
    Metric(
        key="ctf_carrier_kills_pm",
        label="Carrier kills per map",
        category="ctf",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(ctf_kill_carriers) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_kill_carriers",),
        modes=(MODE_CTF,),
        compute=_pm("ctf_kill_carriers"),
    ),
    Metric(
        key="ctf_pickups_pm",
        label="Flag pickups per map",
        category="ctf",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(ctf_pickups) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_pickups",),
        modes=(MODE_CTF,),
        compute=_pm("ctf_pickups"),
    ),
    Metric(
        key="ctf_carry_time_pm_s",
        label="Flag carry time per map",
        category="ctf",
        tier="gold",
        unit="seconds per map",
        higher_is_better=True,
        formula="sum(ctf_flag_carry_time_s) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_flag_carry_time_s",),
        modes=(MODE_CTF,),
        compute=_pm("ctf_flag_carry_time_s"),
    ),
    Metric(
        key="ctf_carry_efficiency",
        label="Carry efficiency",
        category="ctf",
        tier="gold",
        unit="caps per pickup",
        higher_is_better=True,
        formula="sum(ctf_captures) / sum(ctf_pickups)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_captures", "ctf_pickups"),
        modes=(MODE_CTF,),
        compute=_rate_over_maps(_terms("ctf_captures"), _terms("ctf_pickups")),
        note="How often picking the flag up ends in a capture.",
    ),
    Metric(
        key="ctf_flag_involvement_pm",
        label="Flag involvement per map",
        category="ctf",
        tier="gold",
        unit="per map",
        higher_is_better=True,
        formula="(sum(ctf_captures) + sum(ctf_returns) + sum(ctf_kill_carriers)) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctf_captures", "ctf_returns", "ctf_kill_carriers"),
        modes=(MODE_CTF,),
        compute=_pm("ctf_captures", "ctf_returns", "ctf_kill_carriers"),
    ),
)

_UPLINK: tuple[Metric, ...] = (
    Metric(
        key="uplink_points_pm",
        label="Uplink points per map",
        category="uplink",
        tier="gold",
        unit="per map",
        higher_is_better=True,
        formula="sum(uplink_points) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("uplink_points",),
        modes=(MODE_UPLINK,),
        compute=_pm("uplink_points"),
    ),
    Metric(
        key="uplink_points_p10",
        label="Uplink points per 10 min",
        category="uplink",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(uplink_points) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("uplink_points",),
        modes=(MODE_UPLINK,),
        compute=_p10("uplink_points"),
    ),
    Metric(
        key="uplink_dunk_rate",
        label="Dunk rate",
        category="uplink",
        tier="gold",
        unit="share of scores",
        higher_is_better=True,
        formula="sum(uplink_dunks) / (sum(uplink_dunks) + sum(uplink_throws))",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("uplink_dunks", "uplink_throws"),
        modes=(MODE_UPLINK,),
        compute=_rate_over_maps(_terms("uplink_dunks"), _terms("uplink_dunks", "uplink_throws")),
        note="Dunks score two points but require carrying into the goal.",
    ),
    Metric(
        key="uplink_dunks_pm",
        label="Dunks per map",
        category="uplink",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(uplink_dunks) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("uplink_dunks",),
        modes=(MODE_UPLINK,),
        compute=_pm("uplink_dunks"),
    ),
    Metric(
        key="uplink_throws_pm",
        label="Throws per map",
        category="uplink",
        tier="standard",
        unit="per map",
        higher_is_better=True,
        formula="sum(uplink_throws) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("uplink_throws",),
        modes=(MODE_UPLINK,),
        compute=_pm("uplink_throws"),
    ),
)

_CONTROL: tuple[Metric, ...] = (
    Metric(
        key="ctrl_caps_pm",
        label="Zone captures per map",
        category="control",
        tier="gold",
        unit="per map",
        higher_is_better=True,
        formula="sum(ctrl_captures) / maps",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("ctrl_captures",),
        modes=(MODE_CONTROL,),
        compute=_pm("ctrl_captures"),
    ),
    Metric(
        key="ctrl_fb_rate",
        label="First-blood rate",
        category="control",
        tier="gold",
        unit="per round",
        higher_is_better=True,
        formula="sum(ctrl_firstbloods) / sum(ctrl_rounds)",
        denom_kind="rounds",
        min_denom=MIN_CTRL_ROUNDS,
        sources=("ctrl_firstbloods", "ctrl_rounds"),
        modes=(MODE_CONTROL,),
        compute=_rate(_terms("ctrl_firstbloods"), "ctrl_rounds"),
    ),
    Metric(
        key="ctrl_fd_rate",
        label="First-death rate",
        category="control",
        tier="gold",
        unit="per round",
        higher_is_better=False,
        formula="sum(ctrl_firstdeaths) / sum(ctrl_rounds)",
        denom_kind="rounds",
        min_denom=MIN_CTRL_ROUNDS,
        sources=("ctrl_firstdeaths", "ctrl_rounds"),
        modes=(MODE_CONTROL,),
        compute=_rate(_terms("ctrl_firstdeaths"), "ctrl_rounds"),
    ),
    Metric(
        key="ctrl_fb_net_pr",
        label="First-blood net per round",
        category="control",
        tier="gold",
        unit="per round",
        higher_is_better=True,
        formula="(sum(ctrl_firstbloods) - sum(ctrl_firstdeaths)) / sum(ctrl_rounds)",
        denom_kind="rounds",
        min_denom=MIN_CTRL_ROUNDS,
        sources=("ctrl_firstbloods", "ctrl_firstdeaths", "ctrl_rounds"),
        modes=(MODE_CONTROL,),
        compute=_rate((("ctrl_firstbloods", 1.0), ("ctrl_firstdeaths", -1.0)), "ctrl_rounds"),
    ),
    Metric(
        key="ctrl_opening_duel_win",
        label="Opening-duel win rate",
        category="control",
        tier="gold",
        unit="share of openings",
        higher_is_better=True,
        formula="sum(ctrl_firstbloods) / (sum(ctrl_firstbloods) + sum(ctrl_firstdeaths))",
        denom_kind="rounds",
        min_denom=MIN_CTRL_ROUNDS,
        sources=("ctrl_firstbloods", "ctrl_firstdeaths", "ctrl_rounds"),
        modes=(MODE_CONTROL,),
        compute=_rate_over_maps(
            _terms("ctrl_firstbloods"), _terms("ctrl_firstbloods", "ctrl_firstdeaths")
        ),
    ),
)

_SCORESTREAKS: tuple[Metric, ...] = (
    Metric(
        key="streaks_earned_p10",
        label="Scorestreaks earned per 10 min",
        category="scorestreaks",
        tier="standard",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(scorestreaks_earned) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("scorestreaks_earned",),
        modes=(ALL_MODES,),
        compute=_p10("scorestreaks_earned"),
    ),
    Metric(
        key="streak_conversion",
        label="Scorestreak conversion",
        category="scorestreaks",
        tier="standard",
        unit="share of earned",
        higher_is_better=True,
        formula="sum(scorestreaks_used) / sum(scorestreaks_earned)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("scorestreaks_used", "scorestreaks_earned"),
        modes=(ALL_MODES,),
        compute=_rate_over_maps(_terms("scorestreaks_used"), _terms("scorestreaks_earned")),
    ),
    Metric(
        key="streak_kills_p10",
        label="Scorestreak kills per 10 min",
        category="scorestreaks",
        tier="gold",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(scorestreaks_kills) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("scorestreaks_kills",),
        modes=(ALL_MODES,),
        compute=_p10("scorestreaks_kills"),
    ),
    Metric(
        key="streak_assists_p10",
        label="Scorestreak assists per 10 min",
        category="scorestreaks",
        tier="fun",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(scorestreaks_assists) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("scorestreaks_assists",),
        modes=(ALL_MODES,),
        compute=_p10("scorestreaks_assists"),
    ),
    Metric(
        key="payloads_earned_p10",
        label="Payloads earned per 10 min",
        category="scorestreaks",
        tier="fun",
        unit="per 10 min",
        higher_is_better=True,
        formula="sum(payloads_earned) / sum(duration_s) * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("payloads_earned",),
        modes=(ALL_MODES,),
        compute=_p10("payloads_earned"),
    ),
    Metric(
        key="score_pm",
        label="Score per minute",
        category="scorestreaks",
        tier="standard",
        unit="per minute",
        higher_is_better=True,
        formula="sum(player_score) / (sum(duration_s) / 60)",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=("player_score",),
        modes=(ALL_MODES,),
        compute=lambda agg: (
            None if agg.minutes <= 0 else (agg.total("player_score") / agg.minutes, float(agg.maps))
        ),
    ),
)

_TRADES: tuple[Metric, ...] = (
    Metric(
        key="untraded_death_rate",
        label="Untraded-death rate",
        category="trades",
        tier="gold",
        unit="share of deaths",
        higher_is_better=False,
        formula="deaths not avenged within 5s / normal deaths",
        denom_kind="deaths",
        min_denom=MIN_FEED_DEATHS,
        sources=(KF_UNTRADED, KF_DEATHS),
        modes=(ALL_MODES,),
        compute=_feed_rate(KF_UNTRADED, KF_DEATHS),
        note=(
            "Share of a player's deaths that their team did not trade back within "
            "the 5,000 ms window. Kill-feed only, so IW and WWII."
        ),
    ),
    Metric(
        key="traded_death_rate",
        label="Traded-death rate",
        category="trades",
        tier="standard",
        unit="share of deaths",
        higher_is_better=True,
        formula="deaths avenged within 5s / normal deaths",
        denom_kind="deaths",
        min_denom=MIN_FEED_DEATHS,
        sources=(KF_TRADED, KF_DEATHS),
        modes=(ALL_MODES,),
        compute=_feed_rate(KF_TRADED, KF_DEATHS),
        note="Complement of the untraded-death rate; a death answered by a teammate costs less.",
    ),
    Metric(
        key="trade_kills_p10",
        label="Trade kills per 10 min",
        category="trades",
        tier="gold",
        unit="per 10 min",
        higher_is_better=True,
        formula="kills that avenge a teammate within 5s / feed-map time * 600",
        denom_kind="maps",
        min_denom=float(MIN_MAPS),
        sources=(KF_TRADE_KILLS,),
        modes=(ALL_MODES,),
        compute=_feed_p10(KF_TRADE_KILLS),
        note="A kill of an enemy who killed one of the player's teammates within the last 5s.",
    ),
    Metric(
        key="kill_answered_rate",
        label="Kills answered rate",
        category="trades",
        tier="gold",
        unit="share of kills",
        higher_is_better=False,
        formula="kills after which the player died within 5s / normal kills",
        denom_kind="kills",
        min_denom=MIN_FEED_KILLS,
        sources=(KF_ANSWERED, KF_KILLS),
        modes=(ALL_MODES,),
        compute=_feed_rate(KF_ANSWERED, KF_KILLS),
        note=(
            "Kill-feed measure of a kill being answered within 5s, from the attacker's "
            "side. Cross-checked against WWII's kills_stayed_alive box column."
        ),
    ),
    Metric(
        key="first_death_untraded_rate",
        label="First-death untraded rate",
        category="trades",
        tier="gold",
        unit="share of first deaths",
        higher_is_better=False,
        formula="round-opening deaths not avenged within 5s / round-opening deaths taken",
        denom_kind="first deaths",
        min_denom=MIN_FEED_FIRST_DEATHS,
        sources=(KF_FIRST_UNTRADED, KF_FIRST_DEATHS),
        modes=(ALL_MODES,),
        compute=_feed_rate(KF_FIRST_UNTRADED, KF_FIRST_DEATHS),
        note="How often the player opens a round by dying with no trade back.",
    ),
)

_CLUTCH: tuple[Metric, ...] = (
    Metric(
        key="clutch_win_rate",
        label="Clutch win rate",
        category="clutch",
        tier="gold",
        unit="share of clutches",
        higher_is_better=True,
        formula="last-man rounds won / last-man rounds reached (1vN, N=1..4)",
        denom_kind="clutch attempts",
        min_denom=MIN_CLUTCH_ATTEMPTS,
        sources=(KF_CLUTCH_WIN, KF_CLUTCH_ATT),
        modes=(MODE_SND,),
        compute=_feed_rate(KF_CLUTCH_WIN, KF_CLUTCH_ATT),
        note="A clutch is being the last player alive on your team; win means taking the round.",
    ),
    *(
        Metric(
            key=f"clutch_1v{n}_win_rate",
            label=f"1v{n} clutch win rate",
            category="clutch",
            tier="standard",
            unit=f"share of 1v{n}",
            higher_is_better=True,
            formula=f"1v{n} clutches won / 1v{n} clutches reached",
            denom_kind="clutch attempts",
            min_denom=MIN_CLUTCH_N_ATTEMPTS,
            sources=(_clutch_win(n), _clutch_att(n)),
            modes=(MODE_SND,),
            compute=_feed_rate(_clutch_win(n), _clutch_att(n)),
            note="Value times denominator is wins; denominator is attempts — the W-L on the card.",
        )
        for n in CLUTCH_NS
    ),
)

_ADVANTAGE: tuple[Metric, ...] = (
    Metric(
        key="snd_adv_conversion",
        label="Man-advantage conversion",
        category="advantage",
        tier="gold",
        unit="share of rounds",
        higher_is_better=True,
        formula="rounds won after taking first blood / rounds opened up a man",
        denom_kind="advantage rounds",
        min_denom=MIN_ADV_ROUNDS,
        sources=(KF_ADV_WINS, KF_ADV_ROUNDS),
        modes=(MODE_SND,),
        compute=_feed_rate(KF_ADV_WINS, KF_ADV_ROUNDS),
        note="Opening up a man means the player's team drew first blood in the round.",
    ),
    Metric(
        key="snd_adv_rounds_lost",
        label="Man-advantage rounds lost",
        category="advantage",
        tier="standard",
        unit="share of rounds",
        higher_is_better=False,
        formula="1 - man-advantage conversion",
        denom_kind="advantage rounds",
        min_denom=MIN_ADV_ROUNDS,
        sources=(KF_ADV_WINS, KF_ADV_ROUNDS),
        modes=(MODE_SND,),
        compute=_complement(_feed_rate(KF_ADV_WINS, KF_ADV_ROUNDS)),
        note="How often the player's team gave away a round it opened up a man.",
    ),
    Metric(
        key="snd_disadv_steal_rate",
        label="Disadvantage steal rate",
        category="advantage",
        tier="gold",
        unit="share of rounds",
        higher_is_better=True,
        formula="rounds won after conceding first blood / rounds opened down a man",
        denom_kind="disadvantage rounds",
        min_denom=MIN_ADV_ROUNDS,
        sources=(KF_DISADV_WINS, KF_DISADV_ROUNDS),
        modes=(MODE_SND,),
        compute=_feed_rate(KF_DISADV_WINS, KF_DISADV_ROUNDS),
        note="Winning a round the team opened a man down — a steal.",
    ),
    Metric(
        key="snd_adv_thrown_deaths_pr",
        label="Thrown deaths per round",
        category="advantage",
        tier="standard",
        unit="per round",
        higher_is_better=False,
        formula="deaths taken while the team was up a man / SnD rounds played",
        denom_kind="rounds",
        min_denom=MIN_SND_ROUNDS,
        sources=(KF_THROWN, KF_SND_ROUNDS),
        modes=(MODE_SND,),
        compute=_feed_rate(KF_THROWN, KF_SND_ROUNDS),
        note="Dying while your team is up a man hands back the advantage.",
    ),
)

CATALOG: tuple[Metric, ...] = (
    *_SLAYING,
    *_DISCIPLINE,
    *_BURST,
    *_HARDPOINT,
    *_SND,
    *_CTF,
    *_UPLINK,
    *_CONTROL,
    *_SCORESTREAKS,
    *_TRADES,
    *_CLUTCH,
    *_ADVANTAGE,
)


def coverage_report(coverage: Coverage) -> list[dict[str, Any]]:
    """Source columns a title carries but never populated, so nothing publishes them."""
    out: list[dict[str, Any]] = []
    for title in TITLE_ORDER:
        for key, cov in sorted(coverage.get(title, {}).items()):
            if cov.present > 0 and not cov.tracked:
                out.append(
                    {"title": title, "column": key, "rows": cov.present, "nonzero": cov.nonzero}
                )
    return out


# Definitions for the Phase B (kill-feed) metrics, rendered into the methodology
# glossary alongside the catalog so the constants live in one place.
KILL_FEED_CONSTANTS: dict[str, Any] = {
    "trade_window_ms": TRADE_WINDOW_MS,
    "trade": (
        "A death is traded when the killer is themselves killed within the "
        f"{TRADE_WINDOW_MS} ms window, in the same round, by a teammate of the "
        "victim; that avenging kill is a trade kill. Matches the archive's own "
        "5,000 ms trade convention and the box kills_stayed_alive column."
    ),
    "advantage_state": (
        "In SnD, a team opens 'up a man' by taking the round's first blood (the "
        "first normal death). Conversion is winning a round opened up a man; a "
        "steal is winning one opened down a man; a thrown death is dying while "
        "still up a man."
    ),
    "clutch": (
        "A clutch is a last-man-standing round: the player is their team's sole "
        "survivor, classed 1vN by the number of opponents alive at that moment."
    ),
    "reconciliation": (
        "Kill-feed metrics use only reconciled player-maps (box deaths equal "
        "normal feed deaths); see the kill_feed_reconciliation artifact. Feed "
        "coverage is IW and WWII only — BO4 carries box scores but no events, so "
        "the sources mechanism publishes no kill-feed metric for it."
    ),
}


def catalog_payload(coverage: Coverage, catalog: Iterable[Metric] = CATALOG) -> dict[str, Any]:
    return {
        "version": VERSION,
        "min_nonzero_rows": MIN_NONZERO_ROWS,
        "metrics": [m.catalog_entry(coverage) for m in catalog],
        "untracked_columns": coverage_report(coverage),
        "kill_feed_constants": KILL_FEED_CONSTANTS,
    }


# ---------- aggregation ----------


def _extras_number(extras: dict[str, Any], key: str) -> float | None:
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
    aggregates: list[Aggregate]
    coverage: Coverage


def _record_coverage(coverage: Coverage, title: str, key: str, value: float | None) -> None:
    by_key = coverage.setdefault(title, {})
    cov = by_key.setdefault(key, KeyCoverage())
    cov.rows += 1
    if value is not None:
        cov.present += 1
        if value != 0.0:
            cov.nonzero += 1


def load(conn: psycopg.Connection[tuple[object, ...]]) -> Loaded:
    """One aggregate per (player, season, mode) plus a (player, season, all-modes) row,
    alongside per-title column coverage measured once per player-map."""
    buckets: dict[tuple[int, int, int | None], Aggregate] = {}
    coverage: Coverage = {}

    for row in conn.execute(_MAP_SQL):
        (
            player_id,
            _team_id,
            season_id,
            mode_id,
            mode_slug,
            title,
            duration_s,
            kills,
            deaths,
            assists,
            damage,
            hill_time,
            first_bloods,
            plants,
            defuses,
            extras,
            team_kills_map,
            team_hill_time_map,
        ) = row

        extras_dict = cast(dict[str, Any], extras or {})
        typed = {
            "kills": kills,
            "deaths": deaths,
            "assists": assists,
            "hill_time": hill_time,
            "first_bloods": first_bloods,
            "plants": plants,
            "defuses": defuses,
            "damage": damage,
        }
        row_title = cast(str, title)

        # Coverage is counted once per player-map, before the row is folded into
        # both its mode slice and the all-modes slice.
        for name, raw in typed.items():
            _record_coverage(
                coverage, row_title, name, None if raw is None else float(cast(int, raw))
            )
        for name in (*NUMERIC_EXTRAS, "avg_kill_dist_m"):
            _record_coverage(coverage, row_title, name, _extras_number(extras_dict, name))

        for slice_mode_id, slice_slug in (
            (cast(int, mode_id), cast(str, mode_slug)),
            (None, ALL_MODES),
        ):
            key = (cast(int, player_id), cast(int, season_id), slice_mode_id)
            agg = buckets.get(key)
            if agg is None:
                agg = Aggregate(
                    player_id=cast(int, player_id),
                    season_id=cast(int, season_id),
                    mode_id=slice_mode_id,
                    mode_slug=slice_slug,
                    title=cast(str, title),
                )
                buckets[key] = agg

            agg.maps += 1
            agg.duration_s += float(cast(int, duration_s))
            agg.team_kills += float(cast(int, team_kills_map) or 0)
            agg.team_hill_time += float(cast(int, team_hill_time_map) or 0)

            # Damage is missing on most non-BO4 maps, so its rate needs a
            # duration total covering only the maps that reported it.
            if damage is not None:
                agg.damage_duration_s += float(cast(int, duration_s))

            for name, raw in typed.items():
                if raw is None:
                    continue
                agg.sums[name] = agg.sums.get(name, 0.0) + float(cast(int, raw))
                agg.present_maps[name] = agg.present_maps.get(name, 0) + 1

            for name in NUMERIC_EXTRAS:
                value = _extras_number(extras_dict, name)
                if value is None:
                    continue
                agg.sums[name] = agg.sums.get(name, 0.0) + value
                agg.present_maps[name] = agg.present_maps.get(name, 0) + 1

            # Per-map average, so it is re-weighted by that map's kills.
            dist = _extras_number(extras_dict, "avg_kill_dist_m")
            map_kills = float(cast(int, kills)) if kills is not None else 0.0
            if dist is not None and map_kills > 0:
                agg.kill_dist_weighted += dist * map_kills
                agg.kill_dist_kills += map_kills
                agg.present_maps["avg_kill_dist_m"] = agg.present_maps.get("avg_kill_dist_m", 0) + 1

    return Loaded(aggregates=list(buckets.values()), coverage=coverage)


# ---------- kill feed (Phase B): trades ----------

# A normal death, minimally: (round, game-clock ms, seq, victim, killer). Only
# normal deaths enter trade accounting — suicides and team kills are excluded,
# matching the reconciliation rule.
FeedDeath = tuple[int, int | None, int, int, int | None]


def compute_map_trades(
    deaths: Sequence[FeedDeath],
    team_of: dict[int, int],
    window_ms: int = TRADE_WINDOW_MS,
) -> dict[int, dict[str, float]]:
    """Per-player trade quantities for one map's ordered normal-death feed.

    A death (victim V, killer K at time t) is *traded* when K is themselves
    killed within the window, in the same round, by someone on V's team; that
    avenging kill is a *trade kill* for its author. The same avenging death also
    means K's kill of V was *answered* (K died within the window of getting the
    kill). Round-opening deaths are tracked separately so their trade state can
    be reported on its own.

    Pure and deterministic: the importer's DB output is one caller, hand-built
    synthetic timelines are another (tests).
    """
    counts: dict[int, dict[str, float]] = defaultdict(lambda: dict.fromkeys(KF_KEYS, 0.0))
    seen_rounds: set[int] = set()
    n = len(deaths)
    for i, (rnd, t, _seq, victim, killer) in enumerate(deaths):
        counts[victim][KF_DEATHS] += 1
        if killer is not None:
            counts[killer][KF_KILLS] += 1

        traded = False
        if killer is not None and t is not None:
            for j in range(i + 1, n):
                rnd2, t2, _s2, victim2, killer2 = deaths[j]
                if rnd2 != rnd or t2 is None or t2 - t > window_ms:
                    break
                if victim2 == killer:
                    # K died within the window: the kill of V was answered.
                    counts[killer][KF_ANSWERED] += 1
                    if killer2 is not None and team_of.get(killer2) == team_of.get(victim):
                        traded = True
                        counts[killer2][KF_TRADE_KILLS] += 1
                    break

        counts[victim][KF_TRADED if traded else KF_UNTRADED] += 1
        if rnd not in seen_rounds:
            seen_rounds.add(rnd)
            counts[victim][KF_FIRST_DEATHS] += 1
            if not traded:
                counts[victim][KF_FIRST_UNTRADED] += 1
    return counts


def resolve_round_winners(
    rounds: Sequence[tuple[int, int | None, int | None, int | None]],
    team1_id: int,
    team2_id: int,
    team1_score: int | None,
    team2_score: int | None,
    game_winner: int | None,
) -> dict[int, int]:
    """Map each round to the team_id that won it.

    The feed carries cumulative round-win scores, but the deciding round resets
    to (0, 0), so its winner_side is unreliable. The score1/score2 <-> team
    orientation is recovered by trying both and keeping the one whose totals
    reproduce the box-score map result; reset rounds are credited to the game
    winner. Returns {} if neither orientation reconstructs the box score (the
    round outcomes cannot be trusted, so the game is skipped upstream).
    """
    if team1_score is None or team2_score is None:
        return {}
    for s1_team, s2_team in ((team1_id, team2_id), (team2_id, team1_id)):
        side_team = {1: s1_team, 2: s2_team}
        winners: dict[int, int] = {}
        tally = {team1_id: 0, team2_id: 0}
        ok = True
        for rnd, score1, score2, winner_side in rounds:
            reset = not score1 and not score2  # (0,0) or NULL — the decider
            if reset and game_winner is not None:
                winners[rnd] = game_winner
                tally[game_winner] += 1
            elif not reset and winner_side in side_team:
                w = side_team[winner_side]
                winners[rnd] = w
                tally[w] += 1
            else:
                ok = False
                break
        if ok and tally.get(team1_id) == team1_score and tally.get(team2_id) == team2_score:
            return winners
    return {}


def compute_map_clutch_adv(
    deaths_by_round: Mapping[int, Sequence[tuple[int, int | None]]],
    roster_by_team: dict[int, set[int]],
    team_of: dict[int, int],
    winner_by_round: dict[int, int],
) -> dict[int, dict[str, float]]:
    """Per-player clutch and man-advantage counts for one SnD map.

    SnD has one life per round, so alive counts are the roster minus the round's
    deaths so far — exact, and the life index on each death is the cross-check.
    A clutch is a last-man-standing 1vN (N = opponents alive when the player
    becomes their team's sole survivor). Man advantage is read from the opening:
    the team that draws first blood opens up a man; conversion is whether they
    win, a steal is winning from the opposite side, and a thrown death is dying
    while still up a man. Advantage and clutch outcomes need a known round
    winner; thrown deaths and participation do not.
    """
    counts: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    teams = list(roster_by_team)
    if len(teams) != 2:
        return counts
    other = {teams[0]: teams[1], teams[1]: teams[0]}

    for rnd, deaths in deaths_by_round.items():
        winner = winner_by_round.get(rnd)
        for roster in roster_by_team.values():
            for pid in roster:
                counts[pid][KF_SND_ROUNDS] += 1

        alive = {t: set(roster_by_team[t]) for t in teams}
        opening: dict[int, int | None] = {t: None for t in teams}
        clutched: set[int] = set()

        for victim, _killer in deaths:
            vt = team_of.get(victim)
            if vt not in alive:
                continue
            ot = other[vt]
            if len(alive[vt]) - len(alive[ot]) > 0:  # up a man, then died: thrown
                counts[victim][KF_THROWN] += 1
            alive[vt].discard(victim)

            for t in teams:
                if opening[t] is None:
                    diff = len(alive[t]) - len(alive[other[t]])
                    if diff != 0:
                        opening[t] = 1 if diff > 0 else -1

            if winner is not None:
                for t in teams:
                    if len(alive[t]) == 1 and t not in clutched:
                        n = len(alive[other[t]])
                        if 1 <= n <= 4:
                            clutched.add(t)
                            survivor = next(iter(alive[t]))
                            counts[survivor][_clutch_att(n)] += 1
                            counts[survivor][KF_CLUTCH_ATT] += 1
                            if winner == t:
                                counts[survivor][_clutch_win(n)] += 1
                                counts[survivor][KF_CLUTCH_WIN] += 1

        if winner is not None:
            for t in teams:
                won = winner == t
                if opening[t] == 1:
                    for pid in roster_by_team[t]:
                        counts[pid][KF_ADV_ROUNDS] += 1
                        if won:
                            counts[pid][KF_ADV_WINS] += 1
                elif opening[t] == -1:
                    for pid in roster_by_team[t]:
                        counts[pid][KF_DISADV_ROUNDS] += 1
                        if won:
                            counts[pid][KF_DISADV_WINS] += 1
    return counts


_RECON_MAPS_SQL = """
SELECT r.game_id, r.player_id, se.id AS season_id, g.mode_id, gm.slug, r.title, g.duration_s
FROM kill_feed_recon r
JOIN games g       ON g.id = r.game_id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s      ON s.id = g.series_id
JOIN events e      ON e.id = s.event_id
JOIN seasons se    ON se.id = e.season_id
WHERE r.reconciled AND g.duration_s IS NOT NULL
"""

_SND_GAME_META_SQL = """
SELECT g.id, s.team1_id, s.team2_id, g.team1_score, g.team2_score, g.winner_team_id
FROM games g
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s      ON s.id = g.series_id
WHERE gm.slug = 'search-and-destroy'
  AND EXISTS (SELECT 1 FROM kill_events k WHERE k.game_id = g.id)
"""

_SND_ROUNDS_SQL = """
SELECT gr.game_id, gr.round, gr.score1, gr.score2, gr.winner_side
FROM game_rounds gr
JOIN games g       ON g.id = gr.game_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE gm.slug = 'search-and-destroy'
ORDER BY gr.game_id, gr.round
"""

_FEED_DEATHS_SQL = """
SELECT game_id, round, time_ms, seq, victim_id, killer_id
FROM kill_events
WHERE death_kind = 'normal'
ORDER BY game_id, round, time_ms, seq
"""

_FEED_TEAMS_SQL = """
SELECT gps.game_id, gps.player_id, gps.team_id
FROM game_player_stats gps
WHERE EXISTS (SELECT 1 FROM kill_events k WHERE k.game_id = gps.game_id)
"""


def augment_with_kill_feed(conn: psycopg.Connection[tuple[object, ...]], loaded: Loaded) -> None:
    """Fold reconciled kill-feed quantities into the box-score aggregates.

    Trades are computed over each game's full normal-death timeline, but only
    reconciled player-maps contribute their numbers to the metric layer — the
    exclusion set is applied here, not buried in a downstream query. Coverage is
    recorded for the feed keys so the sources= mechanism gates titles: BO4 has
    no feed rows, so it publishes none of these metrics.
    """
    index = {(a.player_id, a.season_id, a.mode_id): a for a in loaded.aggregates}

    team_of: dict[int, dict[int, int]] = defaultdict(dict)
    for row in conn.execute(_FEED_TEAMS_SQL):
        game_id, player_id, team_id = cast(int, row[0]), cast(int, row[1]), cast(int, row[2])
        team_of[game_id][player_id] = team_id

    deaths_by_game: dict[int, list[FeedDeath]] = defaultdict(list)
    for row in conn.execute(_FEED_DEATHS_SQL):
        game_id = cast(int, row[0])
        deaths_by_game[game_id].append(
            (
                cast(int, row[1]),
                cast("int | None", row[2]),
                cast(int, row[3]),
                cast(int, row[4]),
                cast("int | None", row[5]),
            )
        )

    # Reconciled player-maps, grouped by game so each game is scored once.
    recon: dict[int, list[tuple[int, int, int, str, str, float]]] = defaultdict(list)
    for row in conn.execute(_RECON_MAPS_SQL):
        game_id, player_id, season_id = cast(int, row[0]), cast(int, row[1]), cast(int, row[2])
        mode_id, mode_slug, title = cast(int, row[3]), cast(str, row[4]), cast(str, row[5])
        duration_s = float(cast(int, row[6]))
        recon[game_id].append((player_id, season_id, mode_id, mode_slug, title, duration_s))

    clutch_by_game = _load_snd_clutch(conn, team_of, deaths_by_game)

    for game_id, players in recon.items():
        per_player = compute_map_trades(deaths_by_game.get(game_id, []), team_of[game_id])
        clutch = clutch_by_game.get(game_id, {})
        zero = dict.fromkeys(KF_KEYS, 0.0)
        for player_id, season_id, mode_id, mode_slug, title, duration_s in players:
            counts = per_player.get(player_id, zero)
            # Coverage once per player-map, so a title without a feed stays untracked.
            for key in KF_KEYS:
                _record_coverage(loaded.coverage, title, key, counts[key])
            is_snd = mode_slug == MODE_SND and game_id in clutch_by_game
            cc = clutch.get(player_id, {}) if is_snd else {}
            if is_snd:
                for key in CLUTCH_KEYS:
                    _record_coverage(loaded.coverage, title, key, cc.get(key, 0.0))
            for slice_mode in (mode_id, None):
                agg = index.get((player_id, season_id, slice_mode))
                if agg is None:
                    continue
                agg.feed_maps += 1
                agg.feed_duration_s += duration_s
                for key in KF_KEYS:
                    agg.add(key, counts[key])
                if is_snd:
                    for key in CLUTCH_KEYS:
                        agg.add(key, cc.get(key, 0.0))


def _load_snd_clutch(
    conn: psycopg.Connection[tuple[object, ...]],
    team_of: dict[int, dict[int, int]],
    deaths_by_game: dict[int, list[FeedDeath]],
) -> dict[int, dict[int, dict[str, float]]]:
    """Per-game, per-player clutch/advantage counts for SnD games with a resolvable
    round-winner mapping. Games whose winners cannot be reconciled are omitted."""
    meta: dict[int, tuple[int, int, int | None, int | None, int | None]] = {}
    for row in conn.execute(_SND_GAME_META_SQL):
        meta[cast(int, row[0])] = (
            cast(int, row[1]),
            cast(int, row[2]),
            cast("int | None", row[3]),
            cast("int | None", row[4]),
            cast("int | None", row[5]),
        )
    rounds_by_game: dict[int, list[tuple[int, int | None, int | None, int | None]]] = defaultdict(
        list
    )
    for row in conn.execute(_SND_ROUNDS_SQL):
        rounds_by_game[cast(int, row[0])].append(
            (
                cast(int, row[1]),
                cast("int | None", row[2]),
                cast("int | None", row[3]),
                cast("int | None", row[4]),
            )
        )

    out: dict[int, dict[int, dict[str, float]]] = {}
    for game_id, (t1, t2, t1s, t2s, gw) in meta.items():
        winners = resolve_round_winners(rounds_by_game.get(game_id, []), t1, t2, t1s, t2s, gw)
        if not winners:
            continue
        tof = team_of.get(game_id, {})
        roster_by_team: dict[int, set[int]] = defaultdict(set)
        for pid, tid in tof.items():
            roster_by_team[tid].add(pid)
        deaths_by_round: dict[int, list[tuple[int, int | None]]] = defaultdict(list)
        for rnd, _t, _seq, victim, killer in deaths_by_game.get(game_id, []):
            deaths_by_round[rnd].append((victim, killer))
        out[game_id] = compute_map_clutch_adv(deaths_by_round, roster_by_team, tof, winners)
    return out


# ---------- writeback ----------

PlayerRow = tuple[int, int, int, int | None, str, float, float, float | None, float | None, bool]


def build_rows(run_id: int, loaded: Loaded, catalog: Iterable[Metric] = CATALOG) -> list[PlayerRow]:
    """Compute every applicable metric, then score each within its cohort."""
    rows: list[PlayerRow] = []
    for metric in catalog:
        # Cohort is (season, mode) for this metric; ALL_MODES metrics form one
        # cohort per mode plus a separate all-modes cohort.
        by_cohort: dict[tuple[int, int | None], list[tuple[Aggregate, float, float]]] = {}
        for agg in loaded.aggregates:
            if not metric.applies_to(agg, loaded.coverage):
                continue
            result = metric.compute(agg)
            if result is None:
                continue
            value, denom = result
            if not math.isfinite(value) or denom < 0:
                continue
            by_cohort.setdefault((agg.season_id, agg.mode_id), []).append((agg, value, denom))

        for members in by_cohort.values():
            qualified_ids = [a.player_id for a, _, d in members if d >= metric.min_denom]
            values = {a.player_id: v for a, v, _ in members}
            stats = z_and_pctl(values, qualified_ids)
            qualified = set(qualified_ids)
            for agg, value, denom in members:
                scored = stats.get(agg.player_id)
                rows.append(
                    (
                        run_id,
                        agg.player_id,
                        agg.season_id,
                        agg.mode_id,
                        metric.key,
                        value,
                        denom,
                        scored[0] if scored else None,
                        scored[1] if scored else None,
                        agg.player_id in qualified,
                    )
                )
    rows.extend(_split_rows(run_id, loaded, rows))
    return rows


# One metric is a contrast between two others' cohort z-scores rather than a
# quantity of its own, so it is built from finished rows.
SPLIT_METRIC = Metric(
    key="hp_obj_slay_split",
    label="Objective vs slaying lean",
    category="hardpoint",
    tier="standard",
    unit="z difference",
    higher_is_better=True,
    formula="z(hill_time_share) - z(kills_p10), within the same season and mode",
    denom_kind="maps",
    min_denom=float(MIN_MAPS),
    sources=("hill_time",),
    modes=(MODE_HARDPOINT,),
    compute=lambda _agg: None,
    note=(
        "Positive means the player takes more hill time than their slaying would predict; "
        "negative means the reverse. Not a quality ranking."
    ),
)


def _split_rows(run_id: int, loaded: Loaded, rows: list[PlayerRow]) -> list[PlayerRow]:
    hardpoint_ids = {agg.mode_id for agg in loaded.aggregates if agg.mode_slug == MODE_HARDPOINT}
    z_by: dict[tuple[str, int, int, int | None], float] = {}
    denom_by: dict[tuple[int, int, int | None], float] = {}
    for _, player_id, season_id, mode_id, metric, _, denom, z, _, _ in rows:
        if mode_id not in hardpoint_ids or z is None:
            continue
        if metric in ("hill_time_share", "kills_p10"):
            z_by[(metric, player_id, season_id, mode_id)] = z
            if metric == "hill_time_share":
                denom_by[(player_id, season_id, mode_id)] = denom

    by_cohort: dict[tuple[int, int | None], dict[int, tuple[float, float]]] = {}
    for (metric, player_id, season_id, mode_id), z in z_by.items():
        if metric != "hill_time_share":
            continue
        slay = z_by.get(("kills_p10", player_id, season_id, mode_id))
        if slay is None:
            continue
        denom = denom_by[(player_id, season_id, mode_id)]
        by_cohort.setdefault((season_id, mode_id), {})[player_id] = (z - slay, denom)

    out: list[PlayerRow] = []
    for (season_id, mode_id), members in by_cohort.items():
        qualified_ids = [p for p, (_, d) in members.items() if d >= SPLIT_METRIC.min_denom]
        stats = z_and_pctl({p: v for p, (v, _) in members.items()}, qualified_ids)
        qualified = set(qualified_ids)
        for player_id, (value, denom) in members.items():
            scored = stats.get(player_id)
            out.append(
                (
                    run_id,
                    player_id,
                    season_id,
                    mode_id,
                    SPLIT_METRIC.key,
                    value,
                    denom,
                    scored[0] if scored else None,
                    scored[1] if scored else None,
                    player_id in qualified,
                )
            )
    return out


def compute_and_write(conn: psycopg.Connection[tuple[object, ...]], run_id: int) -> int:
    loaded = load(conn)
    augment_with_kill_feed(conn, loaded)
    rows = build_rows(run_id, loaded)
    conn.cursor().executemany(
        "INSERT INTO player_metric_season (run_id, player_id, season_id, mode_id, metric,"
        " value, denom, z, pctl, qualified)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        rows,
    )

    team_loaded = load_teams(conn)
    team_rows = build_team_rows(run_id, team_loaded)
    conn.cursor().executemany(
        "INSERT INTO team_metric_season (run_id, team_id, season_id, mode_id, metric,"
        " value, denom, z, pctl, qualified)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        team_rows,
    )

    catalog = catalog_payload(loaded.coverage)
    catalog["metrics"].append(SPLIT_METRIC.catalog_entry(loaded.coverage))
    catalog["team_metrics"] = [m.catalog_entry(loaded.coverage) for m in TEAM_CATALOG]
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, "metric_catalog", json.dumps(catalog)),
    )
    for name, payload in build_meta_artifacts(conn).items():
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (run_id, name, json.dumps(payload)),
        )
    for name, payload in build_feed_artifacts(conn).items():
        conn.execute(
            "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
            (run_id, name, json.dumps(payload)),
        )
    return len(rows) + len(team_rows)


# ---------- team metrics ----------

MIN_TEAM_MAPS = 8.0

_TEAM_SQL = """
SELECT g.id AS game_id, se.id AS season_id, g.mode_id, gm.slug AS mode_slug,
       gps.team_id, gps.player_id,
       COALESCE(gps.kills, 0) AS kills,
       COALESCE(gps.hill_time, 0) AS hill_time,
       COALESCE(gps.first_bloods, 0) AS first_bloods,
       g.winner_team_id, g.team1_score, g.team2_score,
       s.team1_id, s.team2_id
FROM game_player_stats gps
JOIN games g       ON g.id = gps.game_id
JOIN series s      ON s.id = g.series_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE g.duration_s IS NOT NULL
"""


@dataclass
class TeamMap:
    """One team's showing on one map."""

    team_id: int
    season_id: int
    mode_id: int
    mode_slug: str
    won: bool | None
    score: int | None
    opp_score: int | None
    kills_by_player: dict[int, float] = field(default_factory=dict)
    hill_by_player: dict[int, float] = field(default_factory=dict)
    fb_by_player: dict[int, float] = field(default_factory=dict)


def load_teams(conn: psycopg.Connection[tuple[object, ...]]) -> list[TeamMap]:
    maps: dict[tuple[int, int], TeamMap] = {}
    for row in conn.execute(_TEAM_SQL):
        (
            game_id,
            season_id,
            mode_id,
            mode_slug,
            team_id,
            player_id,
            kills,
            hill_time,
            first_bloods,
            winner_team_id,
            team1_score,
            team2_score,
            team1_id,
            team2_id,
        ) = row
        key = (cast(int, game_id), cast(int, team_id))
        tm = maps.get(key)
        if tm is None:
            score: int | None = None
            opp: int | None = None
            if team_id == team1_id:
                score, opp = cast("int | None", team1_score), cast("int | None", team2_score)
            elif team_id == team2_id:
                score, opp = cast("int | None", team2_score), cast("int | None", team1_score)
            tm = TeamMap(
                team_id=cast(int, team_id),
                season_id=cast(int, season_id),
                mode_id=cast(int, mode_id),
                mode_slug=cast(str, mode_slug),
                won=None if winner_team_id is None else winner_team_id == team_id,
                score=score,
                opp_score=opp,
            )
            maps[key] = tm
        pid = cast(int, player_id)
        tm.kills_by_player[pid] = float(cast(int, kills))
        tm.hill_by_player[pid] = float(cast(int, hill_time))
        tm.fb_by_player[pid] = float(cast(int, first_bloods))
    return list(maps.values())


def _gini(values: list[float]) -> float | None:
    """0 when every player carries an equal load, approaching 1 when one does it all."""
    if len(values) < 2:
        return None
    total = sum(values)
    if total <= 0:
        return None
    ordered = sorted(values)
    n = len(ordered)
    weighted = sum((i + 1) * v for i, v in enumerate(ordered))
    return (2.0 * weighted) / (n * total) - (n + 1.0) / n


def _herfindahl(values: list[float]) -> float | None:
    total = sum(values)
    if total <= 0 or len(values) < 2:
        return None
    return sum((v / total) ** 2 for v in values)


def _stddev(values: list[float]) -> float | None:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))


@dataclass(frozen=True)
class TeamMetric:
    key: str
    label: str
    tier: str
    unit: str
    higher_is_better: bool
    formula: str
    modes: tuple[str, ...]
    note: str | None = None

    def catalog_entry(self, _coverage: Coverage) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "category": "team",
            "tier": self.tier,
            "unit": self.unit,
            "higher_is_better": self.higher_is_better,
            "formula": self.formula,
            "denom_kind": "maps",
            "min_denom": MIN_TEAM_MAPS,
            "modes": list(self.modes),
            "note": self.note,
        }


TEAM_CATALOG: tuple[TeamMetric, ...] = (
    TeamMetric(
        key="map_win_rate",
        label="Map win rate",
        tier="standard",
        unit="share of maps",
        higher_is_better=True,
        formula="maps won / maps played",
        modes=(ALL_MODES,),
    ),
    TeamMetric(
        key="hp_avg_margin",
        label="Average hill margin",
        tier="gold",
        unit="points",
        higher_is_better=True,
        formula="mean(team score - opponent score) on Hardpoint maps",
        modes=(MODE_HARDPOINT,),
    ),
    TeamMetric(
        key="snd_round_win_rate",
        label="Round win rate",
        tier="gold",
        unit="share of rounds",
        higher_is_better=True,
        formula="sum(team rounds won) / sum(all rounds) on SnD maps",
        modes=(MODE_SND,),
    ),
    TeamMetric(
        key="hill_time_gini",
        label="Hill duty concentration",
        tier="gold",
        unit="Gini",
        higher_is_better=False,
        formula="mean over Hardpoint maps of the Gini coefficient of the roster's hill time",
        modes=(MODE_HARDPOINT,),
        note="0 is hill time split evenly across the roster; higher means one player carries it.",
    ),
    TeamMetric(
        key="snd_fb_concentration",
        label="Opening concentration",
        tier="gold",
        unit="Herfindahl",
        higher_is_better=False,
        formula="Herfindahl index of first bloods across the roster on SnD maps",
        modes=(MODE_SND,),
        note="0.25 is first bloods spread evenly across four players; 1.0 is a single opener.",
    ),
    TeamMetric(
        key="slay_balance",
        label="Slaying spread",
        tier="standard",
        unit="std dev of share",
        higher_is_better=False,
        formula="standard deviation of the roster's kill shares, averaged over maps",
        modes=(ALL_MODES,),
    ),
)


def _team_metric_value(key: str, maps: list[TeamMap]) -> float | None:
    if key == "map_win_rate":
        decided = [m for m in maps if m.won is not None]
        if not decided:
            return None
        return sum(1.0 for m in decided if m.won) / len(decided)
    if key == "hp_avg_margin":
        margins = [
            float(m.score - m.opp_score)
            for m in maps
            if m.score is not None and m.opp_score is not None
        ]
        return sum(margins) / len(margins) if margins else None
    if key == "snd_round_win_rate":
        won = sum(m.score for m in maps if m.score is not None)
        total = sum(
            m.score + m.opp_score for m in maps if m.score is not None and m.opp_score is not None
        )
        return won / total if total > 0 else None
    if key == "hill_time_gini":
        ginis = [g for g in (_gini(list(m.hill_by_player.values())) for m in maps) if g is not None]
        return sum(ginis) / len(ginis) if ginis else None
    if key == "snd_fb_concentration":
        totals: dict[int, float] = {}
        for m in maps:
            for pid, fb in m.fb_by_player.items():
                totals[pid] = totals.get(pid, 0.0) + fb
        return _herfindahl(list(totals.values()))
    if key == "slay_balance":
        spreads: list[float] = []
        for m in maps:
            kills = list(m.kills_by_player.values())
            map_kills = sum(kills)
            if map_kills <= 0:
                continue
            spread = _stddev([k / map_kills for k in kills])
            if spread is not None:
                spreads.append(spread)
        return sum(spreads) / len(spreads) if spreads else None
    return None


TeamRow = tuple[int, int, int, int | None, str, float, float, float | None, float | None, bool]


def build_team_rows(
    run_id: int, team_maps: list[TeamMap], catalog: Iterable[TeamMetric] = TEAM_CATALOG
) -> list[TeamRow]:
    by_slice: dict[tuple[int, int, int | None], list[TeamMap]] = {}
    for tm in team_maps:
        by_slice.setdefault((tm.team_id, tm.season_id, tm.mode_id), []).append(tm)
        by_slice.setdefault((tm.team_id, tm.season_id, None), []).append(tm)

    rows: list[TeamRow] = []
    for metric in catalog:
        by_cohort: dict[tuple[int, int | None], list[tuple[int, float, float]]] = {}
        for (team_id, season_id, mode_id), maps in by_slice.items():
            if ALL_MODES not in metric.modes and (
                mode_id is None or maps[0].mode_slug not in metric.modes
            ):
                continue
            value = _team_metric_value(metric.key, maps)
            if value is None or not math.isfinite(value):
                continue
            by_cohort.setdefault((season_id, mode_id), []).append(
                (team_id, value, float(len(maps)))
            )

        for (season_id, mode_id), members in by_cohort.items():
            qualified_ids = [t for t, _, d in members if d >= MIN_TEAM_MAPS]
            stats = z_and_pctl({t: v for t, v, _ in members}, qualified_ids)
            qualified = set(qualified_ids)
            for team_id, value, denom in members:
                scored = stats.get(team_id)
                rows.append(
                    (
                        run_id,
                        team_id,
                        season_id,
                        mode_id,
                        metric.key,
                        value,
                        denom,
                        scored[0] if scored else None,
                        scored[1] if scored else None,
                        team_id in qualified,
                    )
                )
    return rows


# ---------- loadout meta ----------

MIN_META_PLAYER_MAPS = 30

# extras key -> artifact name
META_KEYS: dict[str, str] = {
    "fave_weapon": "meta_weapons",
    "fave_specialist": "meta_specialists",
    "fave_division": "meta_divisions",
    "fave_training": "meta_training",
    "fave_scorestreaks": "meta_scorestreaks",
    "fave_rig": "meta_rigs",
    "fave_payload": "meta_payloads",
    "fave_trait": "meta_traits",
}

_META_SQL = """
SELECT se.id AS season_id, gm.slug AS mode_slug, t.short_name AS title,
       gps.extras->>%(key)s AS choice,
       count(*) AS n_player_maps,
       count(*) FILTER (WHERE gps.team_id = g.winner_team_id) AS wins,
       count(*) FILTER (WHERE g.winner_team_id IS NOT NULL) AS decided
FROM game_player_stats gps
JOIN games g       ON g.id = gps.game_id
JOIN series s      ON s.id = g.series_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN titles t      ON t.id = se.title_id
JOIN game_modes gm ON gm.id = g.mode_id
WHERE g.duration_s IS NOT NULL AND gps.extras->>%(key)s IS NOT NULL
GROUP BY 1, 2, 3, 4
"""


def build_meta_artifacts(
    conn: psycopg.Connection[tuple[object, ...]],
) -> dict[str, dict[str, Any]]:
    """Usage share and map win rate per loadout choice, by season and mode."""
    artifacts: dict[str, dict[str, Any]] = {}
    for key, name in META_KEYS.items():
        grouped: dict[tuple[int, str, str], list[tuple[str, int, int, int]]] = {}
        for row in conn.execute(_META_SQL, {"key": key}):
            season_id, mode_slug, title, choice, n, wins, decided = row
            if not choice:
                continue
            grouped.setdefault(
                (cast(int, season_id), cast(str, mode_slug), cast(str, title)), []
            ).append((cast(str, choice), cast(int, n), cast(int, wins), cast(int, decided)))
        if not grouped:
            continue

        groups: list[dict[str, Any]] = []
        for (season_id, mode_slug, title), entries in sorted(grouped.items()):
            total = sum(n for _, n, _, _ in entries)
            kept = [e for e in entries if e[1] >= MIN_META_PLAYER_MAPS]
            if not kept or total <= 0:
                continue
            groups.append(
                {
                    "season_id": season_id,
                    "title": title,
                    "mode": mode_slug,
                    "n_player_maps": total,
                    "entries": sorted(
                        (
                            {
                                "name": choice,
                                "share": n / total,
                                "map_win_rate": (wins / decided) if decided > 0 else None,
                                "n_player_maps": n,
                            }
                            for choice, n, wins, decided in kept
                        ),
                        key=lambda e: cast(float, e["share"]),
                        reverse=True,
                    ),
                }
            )
        if groups:
            artifacts[name] = {
                "key": key,
                "min_player_maps": MIN_META_PLAYER_MAPS,
                "groups": groups,
            }
    return artifacts


# ---------- kill-feed artifacts (Phase B: fields the plan did not anticipate) ----------

MIN_WEAPON_KILLS = 50

# Distance histogram edges, in the feed's game units (≈ metres; see the box
# validation below). IW only — WWII's nested attacker carries no distance.
_DIST_EDGES = (0, 15, 30, 50, 75, 100, 150, 250, 10_000)

_WEAPON_KILLS_SQL = """
SELECT se.id AS season_id, t.short_name AS title, gm.slug AS mode,
       ke.weapon AS choice, count(*) AS n_kills
FROM kill_events ke
JOIN games g       ON g.id = ke.game_id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s      ON s.id = g.series_id
JOIN events ev     ON ev.id = s.event_id
JOIN seasons se    ON se.id = ev.season_id
JOIN titles t      ON t.id = se.title_id
WHERE ke.death_kind = 'normal' AND ke.weapon IS NOT NULL
GROUP BY 1, 2, 3, 4
"""


def _weapon_kill_artifact(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any] | None:
    """Every-kill weapon usage, the counterpart to the fave-weapon (one-per-map) meta."""
    grouped: dict[tuple[int, str, str], list[tuple[str, int]]] = defaultdict(list)
    for row in conn.execute(_WEAPON_KILLS_SQL):
        season_id, title, mode = cast(int, row[0]), cast(str, row[1]), cast(str, row[2])
        choice, n = cast(str, row[3]), cast(int, row[4])
        grouped[(season_id, title, mode)].append((choice, n))

    groups: list[dict[str, Any]] = []
    for (season_id, title, mode), entries in sorted(grouped.items()):
        total = sum(n for _, n in entries)
        kept = [(w, n) for w, n in entries if n >= MIN_WEAPON_KILLS]
        if not kept or total <= 0:
            continue
        groups.append(
            {
                "season_id": season_id,
                "title": title,
                "mode": mode,
                "n_kills": total,
                "entries": sorted(
                    ({"name": w, "share": n / total, "n_kills": n} for w, n in kept),
                    key=lambda e: cast(int, e["n_kills"]),
                    reverse=True,
                ),
            }
        )
    if not groups:
        return None
    return {
        "key": "weapon",
        "min_kills": MIN_WEAPON_KILLS,
        "note": (
            "Share of every kill by weapon, from the kill feed. The fave-weapon meta "
            "counts one weapon per player-map; this counts each kill."
        ),
        "groups": groups,
    }


def _engagement_distance_artifact(
    conn: psycopg.Connection[tuple[object, ...]],
) -> dict[str, Any] | None:
    """IW engagement distance: distribution, weapon-class split, and the box cross-check.

    kill_distance is IW-only (41,222 of 41,265 IW kills; WWII's nested attacker
    has no distance), so this is a 2017 story. It is validated against the box's
    own 'avg kill dist (m)' column, the same quantity aggregated independently.
    """
    stats = conn.execute(
        """
        SELECT count(*),
               avg(kill_distance),
               percentile_cont(0.5) WITHIN GROUP (ORDER BY kill_distance),
               percentile_cont(0.9) WITHIN GROUP (ORDER BY kill_distance)
        FROM kill_events WHERE kill_distance IS NOT NULL AND death_kind = 'normal'
        """
    ).fetchone()
    if stats is None or not stats[0]:
        return None
    n_kills = cast(int, stats[0])

    buckets: list[dict[str, Any]] = []
    for lo, hi in zip(_DIST_EDGES, _DIST_EDGES[1:], strict=False):
        c = conn.execute(
            "SELECT count(*) FROM kill_events WHERE death_kind = 'normal' "
            "AND kill_distance >= %s AND kill_distance < %s",
            (lo, hi),
        ).fetchone()
        n = cast(int, c[0]) if c else 0
        buckets.append({"lo": lo, "hi": None if hi >= 10_000 else hi, "share": n / n_kills})

    by_class: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT weapon_class, count(*), avg(kill_distance)
        FROM kill_events WHERE kill_distance IS NOT NULL AND death_kind = 'normal'
          AND weapon_class IS NOT NULL
        GROUP BY weapon_class ORDER BY count(*) DESC
        """
    ):
        by_class.append(
            {
                "class": cast(str, row[0]),
                "n_kills": cast(int, row[1]),
                "share": cast(int, row[1]) / n_kills,
                "mean_dist": float(cast(float, row[2])),
            }
        )

    # Box cross-check: feed mean distance per IW player-season vs the box column.
    validation = conn.execute(
        """
        WITH feed AS (
          SELECT g.id AS game_id, ke.victim_id, ke.killer_id, ke.kill_distance
          FROM kill_events ke JOIN games g ON g.id = ke.game_id
          WHERE ke.kill_distance IS NOT NULL AND ke.death_kind = 'normal'
        ),
        feed_player AS (
          SELECT gps.player_id, se.id AS season_id, avg(f.kill_distance) AS feed_mean
          FROM feed f
          JOIN game_player_stats gps ON gps.game_id = f.game_id AND gps.player_id = f.killer_id
          JOIN games g ON g.id = f.game_id
          JOIN series s ON s.id = g.series_id
          JOIN events e ON e.id = s.event_id
          JOIN seasons se ON se.id = e.season_id
          GROUP BY gps.player_id, se.id
        ),
        box_player AS (
          SELECT gps.player_id, se.id AS season_id,
                 sum((gps.extras->>'avg_kill_dist_m')::real * gps.kills)
                   / NULLIF(sum(gps.kills)
                       FILTER (WHERE gps.extras->>'avg_kill_dist_m' IS NOT NULL), 0)
                   AS box_mean
          FROM game_player_stats gps
          JOIN games g ON g.id = gps.game_id
          JOIN series s ON s.id = g.series_id
          JOIN events e ON e.id = s.event_id
          JOIN seasons se ON se.id = e.season_id
          WHERE gps.extras->>'avg_kill_dist_m' IS NOT NULL
          GROUP BY gps.player_id, se.id
        )
        SELECT count(*), corr(fp.feed_mean, bp.box_mean),
               avg(fp.feed_mean), avg(bp.box_mean)
        FROM feed_player fp JOIN box_player bp
          ON bp.player_id = fp.player_id AND bp.season_id = fp.season_id
        """
    ).fetchone()

    # The feed distance and the box column measure the same thing but on
    # different scales (feed ~5.75x the box metres), so correlation and the scale
    # ratio validate the story, not an absolute difference.
    box_validation = None
    if validation is not None and validation[0]:
        feed_mean = float(cast(float, validation[2]))
        box_mean = float(cast(float, validation[3]))
        box_validation = {
            "n_players": cast(int, validation[0]),
            "correlation": float(cast(float, validation[1])),
            "feed_mean": feed_mean,
            "box_mean_m": box_mean,
            "scale_box_per_feed": box_mean / feed_mean if feed_mean else None,
            "note": (
                "Feed per-kill distance is in game units, not metres. Per IW "
                "player-season it correlates with the box avg-kill-distance (m) "
                "column at the reported r on a near-constant scale — the same "
                "quantity, differently unit-ed."
            ),
        }

    return {
        "title": TITLE_IW,
        "n_kills": n_kills,
        "unit": "game units (engine space; correlates with the box metres column, not metric)",
        "overall": {
            "mean": float(cast(float, stats[1])),
            "median": float(cast(float, stats[2])),
            "p90": float(cast(float, stats[3])),
        },
        "buckets": buckets,
        "by_class": by_class,
        "box_validation": box_validation,
    }


# Time-to-first-blood histogram edges, in seconds (round_time_ms / 1000).
_TTFB_EDGES = (0, 5, 10, 15, 20, 30, 45, 10_000)

# Kill-density grid resolution and the minimum kills a (title, map) needs to
# earn a heatmap. Positions are min-max normalized per map into this grid.
_DENSITY_BINS = 24
_MIN_DENSITY_KILLS = 200

# (round, time_ms, seq, round_time_ms, victim, killer) per normal death.
RoundDeath = tuple[int, int | None, int, int | None, int, int | None]

_ROUNDS_DEATHS_SQL = """
SELECT ke.game_id, se.year, t.short_name AS title, gm.slug AS mode,
       ke.round, ke.time_ms, ke.seq, ke.round_time_ms, ke.victim_id, ke.killer_id
FROM kill_events ke
JOIN games g       ON g.id = ke.game_id
JOIN game_modes gm ON gm.id = g.mode_id
JOIN series s      ON s.id = g.series_id
JOIN events e      ON e.id = s.event_id
JOIN seasons se    ON se.id = e.season_id
JOIN titles t      ON t.id = se.title_id
WHERE ke.death_kind = 'normal'
ORDER BY ke.game_id, ke.round, ke.time_ms, ke.seq
"""


def _bucket(value: float, edges: tuple[int, ...]) -> int:
    for i, hi in enumerate(edges[1:]):
        if value < hi:
            return i
    return len(edges) - 2


def build_rounds_overview(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any] | None:
    """Round-level aggregates for the /rounds page: advantage-state win rates,
    clutch success by N, trade shares and time-to-first-blood — all IW/WWII.

    Advantage and clutch are round-level (counted once per round, not per
    player), read from the same first-blood opening and last-man logic the
    per-player metrics use, over SnD games with a resolvable round winner.
    """
    team_of: dict[int, dict[int, int]] = defaultdict(dict)
    for row in conn.execute(_FEED_TEAMS_SQL):
        team_of[cast(int, row[0])][cast(int, row[1])] = cast(int, row[2])

    # Per-game death timelines, and per (year, title, mode) group meta.
    deaths_by_game: dict[int, list[RoundDeath]] = defaultdict(list)
    game_meta: dict[int, tuple[int, str, str]] = {}
    for row in conn.execute(_ROUNDS_DEATHS_SQL):
        game_id = cast(int, row[0])
        game_meta[game_id] = (cast(int, row[1]), cast(str, row[2]), cast(str, row[3]))
        deaths_by_game[game_id].append(
            (
                cast(int, row[4]),
                cast("int | None", row[5]),
                cast(int, row[6]),
                cast("int | None", row[7]),
                cast(int, row[8]),
                cast("int | None", row[9]),
            )
        )

    # Aggregators keyed by (year, title, mode) for trades/ttfb, (year, title) for SnD.
    trade: dict[tuple[int, str, str], dict[str, int]] = defaultdict(
        lambda: {"traded": 0, "untraded": 0}
    )
    ttfb: dict[tuple[int, str, str], list[int]] = defaultdict(lambda: [0] * (len(_TTFB_EDGES) - 1))
    adv: dict[tuple[int, str], dict[str, int]] = defaultdict(
        lambda: {"adv_r": 0, "adv_w": 0, "dis_r": 0, "dis_w": 0}
    )
    clutch: dict[tuple[int, str], dict[str, int]] = defaultdict(
        lambda: {f"{kind}_{n}": 0 for n in CLUTCH_NS for kind in ("att", "win")}
    )

    winners_of = _snd_round_winners(conn, team_of)

    for game_id, deaths in deaths_by_game.items():
        year, title, mode = game_meta[game_id]
        gm = (year, title, mode)
        tof = team_of.get(game_id, {})

        # Trades and TTFB (all feed modes).
        trade_counts = compute_map_trades([(r, t, s, v, k) for r, t, s, _rt, v, k in deaths], tof)
        for c in trade_counts.values():
            trade[gm]["traded"] += int(c[KF_TRADED])
            trade[gm]["untraded"] += int(c[KF_UNTRADED])
        by_round: dict[int, list[tuple[int | None, int, int | None]]] = defaultdict(list)
        for rnd, _t, _s, rt, victim, killer in deaths:
            by_round[rnd].append((rt, victim, killer))
        for evs in by_round.values():
            first_rt = evs[0][0]
            if first_rt is not None:
                ttfb[gm][_bucket(first_rt / 1000.0, _TTFB_EDGES)] += 1

        # Advantage state and clutch (SnD with a resolved winner).
        if mode != MODE_SND or game_id not in winners_of:
            continue
        winners = winners_of[game_id]
        roster_by_team: dict[int, set[int]] = defaultdict(set)
        for pid, tid in tof.items():
            roster_by_team[tid].add(pid)
        if len(roster_by_team) != 2:
            continue
        st = (year, title)
        for rnd, evs in by_round.items():
            winner = winners.get(rnd)
            if winner is None:
                continue
            _tally_round_state(evs, roster_by_team, tof, winner, adv[st], clutch[st])

    groups = _rounds_groups(trade, ttfb, adv, clutch)
    if not groups:
        return None
    return {
        "trade_window_ms": TRADE_WINDOW_MS,
        "ttfb_edges_s": list(_TTFB_EDGES),
        "groups": groups,
    }


def _snd_round_winners(
    conn: psycopg.Connection[tuple[object, ...]], team_of: dict[int, dict[int, int]]
) -> dict[int, dict[int, int]]:
    meta: dict[int, tuple[int, int, int | None, int | None, int | None]] = {}
    for row in conn.execute(_SND_GAME_META_SQL):
        meta[cast(int, row[0])] = (
            cast(int, row[1]),
            cast(int, row[2]),
            cast("int | None", row[3]),
            cast("int | None", row[4]),
            cast("int | None", row[5]),
        )
    rounds_by_game: dict[int, list[tuple[int, int | None, int | None, int | None]]] = defaultdict(
        list
    )
    for row in conn.execute(_SND_ROUNDS_SQL):
        rounds_by_game[cast(int, row[0])].append(
            (
                cast(int, row[1]),
                cast("int | None", row[2]),
                cast("int | None", row[3]),
                cast("int | None", row[4]),
            )
        )
    out: dict[int, dict[int, int]] = {}
    for game_id, (t1, t2, t1s, t2s, gw) in meta.items():
        w = resolve_round_winners(rounds_by_game.get(game_id, []), t1, t2, t1s, t2s, gw)
        if w:
            out[game_id] = w
    return out


def _tally_round_state(
    evs: list[tuple[int | None, int, int | None]],
    roster_by_team: dict[int, set[int]],
    team_of: dict[int, int],
    winner: int,
    adv: dict[str, int],
    clutch: dict[str, int],
) -> None:
    teams = list(roster_by_team)
    other = {teams[0]: teams[1], teams[1]: teams[0]}
    alive = {t: len(roster_by_team[t]) for t in teams}
    opening: int | None = None
    clutched: set[int] = set()  # each team's first drop to a last man, counted once
    for _rt, victim, _killer in evs:
        vt = team_of.get(victim)
        if vt not in alive:
            continue
        alive[vt] -= 1
        if opening is None and alive[vt] != alive[other[vt]]:
            opening = vt if alive[vt] > alive[other[vt]] else other[vt]
        for t in teams:
            if alive[t] == 1 and t not in clutched:
                n = alive[other[t]]
                if 1 <= n <= 4:
                    clutched.add(t)
                    clutch[f"att_{n}"] += 1
                    if winner == t:
                        clutch[f"win_{n}"] += 1
    if opening is not None:
        adv["adv_r"] += 1
        adv["dis_r"] += 1
        if winner == opening:
            adv["adv_w"] += 1
        else:
            adv["dis_w"] += 1


def _rounds_groups(
    trade: dict[tuple[int, str, str], dict[str, int]],
    ttfb: dict[tuple[int, str, str], list[int]],
    adv: dict[tuple[int, str], dict[str, int]],
    clutch: dict[tuple[int, str], dict[str, int]],
) -> list[dict[str, Any]]:
    keys = sorted(set(trade) | {(y, t, MODE_SND) for (y, t) in adv})
    groups: list[dict[str, Any]] = []
    for year, title, mode in keys:
        gm = (year, title, mode)
        st = (year, title)
        tr = trade.get(gm, {"traded": 0, "untraded": 0})
        total_deaths = tr["traded"] + tr["untraded"]
        row: dict[str, Any] = {
            "year": year,
            "title": title,
            "mode": mode,
            "deaths": total_deaths,
            "traded_share": tr["traded"] / total_deaths if total_deaths else None,
            "ttfb": ttfb.get(gm, [0] * (len(_TTFB_EDGES) - 1)),
        }
        if mode == MODE_SND and st in adv:
            a = adv[st]
            row["advantage"] = {
                "adv_rounds": a["adv_r"],
                "adv_conversion": a["adv_w"] / a["adv_r"] if a["adv_r"] else None,
                "disadv_rounds": a["dis_r"],
                "disadv_steal": a["dis_w"] / a["dis_r"] if a["dis_r"] else None,
            }
            c = clutch[st]
            row["clutch"] = [
                {
                    "n": n,
                    "attempts": c[f"att_{n}"],
                    "wins": c[f"win_{n}"],
                    "rate": c[f"win_{n}"] / c[f"att_{n}"] if c[f"att_{n}"] else None,
                }
                for n in CLUTCH_NS
            ]
        groups.append(row)
    return groups


def build_kill_density(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any] | None:
    """Per-(title, map) 2D histogram of where kills land, min-max normalized into
    a grid. Axes and density only — no game-map imagery (copyright)."""
    maps: list[dict[str, Any]] = []
    rows = conn.execute(
        """
        SELECT t.short_name AS title, m.name AS map,
               count(*) AS n,
               min(ke.victim_x), max(ke.victim_x), min(ke.victim_y), max(ke.victim_y)
        FROM kill_events ke
        JOIN games g ON g.id = ke.game_id
        JOIN maps m  ON m.id = g.map_id
        JOIN series s ON s.id = g.series_id
        JOIN events e ON e.id = s.event_id
        JOIN seasons se ON se.id = e.season_id
        JOIN titles t ON t.id = se.title_id
        WHERE ke.death_kind = 'normal' AND ke.victim_x IS NOT NULL AND ke.victim_y IS NOT NULL
        GROUP BY t.short_name, m.name
        HAVING count(*) >= %s
        ORDER BY t.short_name, m.name
        """,
        (_MIN_DENSITY_KILLS,),
    ).fetchall()
    for row in rows:
        title, map_name = cast(str, row[0]), cast(str, row[1])
        minx, maxx, miny, maxy = (float(cast(float, row[i])) for i in (3, 4, 5, 6))
        if maxx <= minx or maxy <= miny:
            continue
        grid = [[0] * _DENSITY_BINS for _ in range(_DENSITY_BINS)]
        for pt in conn.execute(
            """
            SELECT ke.victim_x, ke.victim_y
            FROM kill_events ke
            JOIN games g ON g.id = ke.game_id
            JOIN maps m ON m.id = g.map_id
            JOIN series s ON s.id = g.series_id
            JOIN events e ON e.id = s.event_id
            JOIN seasons se ON se.id = e.season_id
            JOIN titles t ON t.id = se.title_id
            WHERE ke.death_kind = 'normal' AND t.short_name = %s AND m.name = %s
              AND ke.victim_x IS NOT NULL AND ke.victim_y IS NOT NULL
            """,
            (title, map_name),
        ):
            x = (float(cast(float, pt[0])) - minx) / (maxx - minx)
            y = (float(cast(float, pt[1])) - miny) / (maxy - miny)
            gx = min(_DENSITY_BINS - 1, int(x * _DENSITY_BINS))
            gy = min(_DENSITY_BINS - 1, int(y * _DENSITY_BINS))
            grid[gy][gx] += 1
        peak = max((max(r) for r in grid), default=0)
        maps.append(
            {
                "title": title,
                "map": map_name,
                "n_kills": cast(int, row[2]),
                "bins": _DENSITY_BINS,
                "peak": peak,
                "grid": grid,
            }
        )
    if not maps:
        return None
    return {"bins": _DENSITY_BINS, "min_kills": _MIN_DENSITY_KILLS, "maps": maps}


def build_feed_artifacts(
    conn: psycopg.Connection[tuple[object, ...]],
) -> dict[str, dict[str, Any]]:
    """Artifacts from kill-feed fields the box score never carried."""
    out: dict[str, dict[str, Any]] = {}
    weapon = _weapon_kill_artifact(conn)
    if weapon is not None:
        out["weapon_kills"] = weapon
    distance = _engagement_distance_artifact(conn)
    if distance is not None:
        out["engagement_distance"] = distance
    rounds = build_rounds_overview(conn)
    if rounds is not None:
        out["rounds_overview"] = rounds
    density = build_kill_density(conn)
    if density is not None:
        out["kill_density"] = density
    return out
