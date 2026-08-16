"""Normalize wiki box scores into series/game/stat-line structures for loading.

One `PlayerStats` row is one player on one map. Rows group into games by
`SeriesId` and `GameNumber`, and games group into series by `SeriesId`.

The season comes from the title, not from the calendar year. Each pre-2017
title had exactly one competitive season, and the December 2016 Infinite
Warfare events belong to the 2017 season, the same rule the rest of the
pipeline follows.

A row is dropped, with its reason counted, when it carries no kill count, no
resolved player, no team, no opponent, no win flag, or a mode the database does
not model. Nothing is guessed.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .client import SNAPSHOT_ROOT

SOURCE = "codwiki"

# wiki GameTitle -> (title name in `titles`, season year)
TITLE_SEASONS: dict[str, tuple[str, int]] = {
    "Black Ops 2": ("Black Ops 2", 2013),
    "Ghosts": ("Ghosts", 2014),
    "Advanced Warfare": ("Advanced Warfare", 2015),
    "Black Ops 3": ("Black Ops 3", 2016),
    "Infinite Warfare": ("Infinite Warfare", 2017),
}

SEASON_LEAGUES = {2013: "MLG", 2014: "MLG", 2015: "MLG", 2016: "CWL", 2017: "CWL"}

MODE_SLUGS = {
    "Hardpoint": "hardpoint",
    "Search and Destroy": "search-and-destroy",
    "Search & Destroy": "search-and-destroy",
    "Capture the Flag": "capture-the-flag",
    "Uplink": "uplink",
    "Domination": "domination",
    "Blitz": "blitz",
}

# Mode-split columns that have no typed column, keyed as the CWL archive keys
# them so the metric layer reads one name per idea across sources.
EXTRA_INTS = {
    "HPKills": "hp_kills",
    "HPDeaths": "hp_deaths",
    "SDKills": "snd_kills",
    "SDDeaths": "snd_deaths",
    "CTFKills": "ctf_kills",
    "CTFDeaths": "ctf_deaths",
    "CTFReturns": "ctf_returns",
    "CTFCaptures": "ctf_captures",
    "UPKills": "uplink_kills",
    "UPDeaths": "uplink_deaths",
    "UPThrows": "uplink_throws",
    "UPCarries": "uplink_carries",
    "UPPoints": "uplink_points",
    "BLIKills": "blitz_kills",
    "BLIDeaths": "blitz_deaths",
    "BLICaps": "blitz_caps",
    "TotalScore": "player_score",
}


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


@dataclass
class StatLine:
    player_link: str
    player_id: int
    team_name: str
    kills: int
    deaths: int
    hill_time: int | None
    plants: int | None
    defuses: int | None
    first_bloods: int | None
    first_deaths: int | None
    captures: int | None
    extras: dict[str, int]


@dataclass
class Game:
    ordinal: int
    map_name: str
    mode_slug: str
    winner_team: str | None
    lines: list[StatLine] = field(default_factory=list)


@dataclass
class Series:
    series_id: str
    event: str
    season: int
    title: str
    played_on: date
    team1: str
    team2: str
    games: list[Game] = field(default_factory=list)

    @property
    def team1_score(self) -> int:
        return sum(1 for g in self.games if g.winner_team == self.team1)

    @property
    def team2_score(self) -> int:
        return sum(1 for g in self.games if g.winner_team == self.team2)


@dataclass
class TransformResult:
    series: list[Series]
    dropped: dict[str, int]
    quarantine: list[dict[str, str]]


def rows_path(window: str = "playerstats") -> Any:
    return SNAPSHOT_ROOT / f"playerstats-{window}.json"


def transform(player_ids: dict[str, int], window: str = "playerstats") -> TransformResult:
    """Group wiki rows into series; `player_ids` maps PlayerLink to player id."""
    raw: list[dict[str, Any]] = json.loads(rows_path(window).read_text())
    dropped: dict[str, int] = defaultdict(int)
    quarantine: list[dict[str, str]] = []
    by_game: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    meta: dict[str, dict[str, Any]] = {}

    for row in raw:
        reason = _reject(row, player_ids)
        if reason:
            dropped[reason] += 1
            if reason in ("unresolved player", "unmodelled mode"):
                quarantine.append(
                    {
                        "reason": reason,
                        "player": row.get("PlayerLink") or row.get("PlayerName") or "",
                        "event": row.get("TournamentPage") or "",
                        "mode": row.get("Gamemode") or "",
                        "date": row.get("Date") or "",
                    }
                )
            continue
        series_id = str(row["SeriesId"])
        by_game[(series_id, int(row["GameNumber"] or 1))].append(row)
        meta.setdefault(series_id, row)

    games_by_series: dict[str, list[Game]] = defaultdict(list)
    for (series_id, number), rows in sorted(by_game.items()):
        first = rows[0]
        mode = MODE_SLUGS[first["Gamemode"]]
        winner = None
        for row in rows:
            if row["Win"] == "1":
                winner = row["Team"]
                break
        game = Game(ordinal=number, map_name=first["Map"] or "", mode_slug=mode, winner_team=winner)
        for row in rows:
            game.lines.append(_stat_line(row, player_ids))
        games_by_series[series_id].append(game)

    series: list[Series] = []
    for series_id, games in games_by_series.items():
        first = meta[series_id]
        title, season = TITLE_SEASONS[first["GameTitle"]]
        sides = _sides(games)
        if len(sides) != 2:
            dropped["series without two sides"] += len(games)
            continue
        team1, team2 = sides
        series.append(
            Series(
                series_id=series_id,
                event=first["TournamentPage"],
                season=season,
                title=title,
                played_on=date.fromisoformat(first["Date"][:10]),
                team1=team1,
                team2=team2,
                games=sorted(games, key=lambda g: g.ordinal),
            )
        )
    return TransformResult(series=series, dropped=dict(dropped), quarantine=quarantine)


def _sides(games: list[Game]) -> list[str]:
    names: list[str] = []
    for game in games:
        for line in game.lines:
            if line.team_name not in names:
                names.append(line.team_name)
    return names


def _reject(row: dict[str, Any], player_ids: dict[str, int]) -> str | None:
    if not (row.get("PlayerName") or "").strip() or _int(row.get("Kills")) is None:
        return "no box score"
    if _int(row.get("Deaths")) is None:
        return "no death count"
    if not (row.get("Team") or "").strip() or not (row.get("TeamVs") or "").strip():
        return "no team"
    if row.get("Win") not in ("0", "1"):
        return "no win flag"
    if not (row.get("SeriesId") or "").strip():
        return "no series"
    if row.get("GameTitle") not in TITLE_SEASONS:
        return "title out of scope"
    if row.get("Gamemode") not in MODE_SLUGS:
        return "unmodelled mode"
    if (row.get("PlayerLink") or "") not in player_ids:
        return "unresolved player"
    return None


def _stat_line(row: dict[str, Any], player_ids: dict[str, int]) -> StatLine:
    extras = {}
    for wiki_key, our_key in EXTRA_INTS.items():
        value = _int(row.get(wiki_key))
        if value is not None:
            extras[our_key] = value
    captures = _int(row.get("DomCaptures"))
    if captures is None:
        captures = _int(row.get("ConCaptures"))
    kills = _int(row["Kills"])
    deaths = _int(row["Deaths"])
    assert kills is not None and deaths is not None
    return StatLine(
        player_link=row["PlayerLink"],
        player_id=player_ids[row["PlayerLink"]],
        team_name=row["Team"],
        kills=kills,
        deaths=deaths,
        hill_time=_int(row.get("HPTime")),
        plants=_int(row.get("SDPlants")),
        defuses=_int(row.get("SDDefuses")),
        first_bloods=_int(row.get("SDFirstKill")),
        first_deaths=_int(row.get("SDFirstDeath")),
        captures=captures,
        extras=extras,
    )
