"""Parse + normalize CWL archive CSV rows.

One CSV row = one player's box score on one map. Column sets differ by year
(2017 IW / 2018 WWII / 2019 BO4); mapped basics become typed fields, every
other *measured* stat lands in `extras` (derived stats like k/d and per-10min
rates are recomputable and dropped). Empty cells stay absent — never zero.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from pydantic import BaseModel, model_validator

from .manifest import EVENTS, MODE_SLUGS, ArchiveEvent

# Archive columns mapped to typed game_player_stats columns.
_MAPPED = {
    "kills": "kills",
    "deaths": "deaths",
    "assists": "assists",
    "damage dealt": "damage",
    "hill time (s)": "hill_time",
    "snd firstbloods": "first_bloods",
    "bomb plants": "plants",
    "bomb defuses": "defuses",
}

# Derived columns: recomputable from basics, not stored.
_DERIVED = {
    "+/-",
    "k/d",
    "kills per 10min",
    "deaths per 10min",
    "accuracy (%)",
    "player spm",
    "avg time per life (s)",
    "4+-streak",
    "multikills",
}

# Row-context columns consumed by game/series assembly, not stats.
_CONTEXT = {
    "match id",
    "series id",
    "end time",
    "duration (s)",
    "mode",
    "map",
    "team",
    "player",
    "win?",
    "score",
}


class ArchiveStatLine(BaseModel):
    """One player-map box score from the archive."""

    match_id: str
    series_id: str
    ended_at: datetime
    duration_s: int
    mode: str
    map_name: str
    team: str
    player: str
    won: bool
    team_score: int
    kills: int
    deaths: int
    assists: int
    damage: int | None = None
    hill_time: int | None = None
    first_bloods: int | None = None
    plants: int | None = None
    defuses: int | None = None
    extras: dict[str, int | float | str] = {}

    @model_validator(mode="after")
    def non_negative(self) -> ArchiveStatLine:
        for f in ("kills", "deaths", "assists", "damage", "hill_time"):
            v = getattr(self, f)
            if v is not None and v < 0:
                raise ValueError(f"{f} must be >= 0, got {v}")
        if self.mode not in MODE_SLUGS:
            raise ValueError(f"unknown mode {self.mode!r}")
        return self


def _num(raw: str) -> int | float | str:
    """Coerce a cell to int, then float; strip unit suffixes ('%', 'm')."""
    s = raw.strip().removesuffix("%").removesuffix("m").strip()
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return raw.strip()


def _extras_key(col: str) -> str:
    return (
        col.replace(" (s)", "_s")
        .replace(" (%)", "_pct")
        .replace(" (m)", "_m")
        .replace(" (stayed alive)", "_stayed_alive")
        .replace("+", "plus")
        .replace("-", "_")
        .replace("/", "_")
        .replace(" ", "_")
        .lower()
    )


def _opt_int(row: dict[str, str], col: str) -> int | None:
    v = row.get(col, "").strip()
    if v == "":
        return None
    return int(float(v))


def parse_row(row: dict[str, str]) -> ArchiveStatLine:
    extras: dict[str, int | float | str] = {}
    for col, raw in row.items():
        if col in _CONTEXT or col in _MAPPED or col in _DERIVED:
            continue
        v = raw.strip()
        if v == "" or v == "0.0%":
            continue
        extras[_extras_key(col)] = _num(v)

    ended = datetime.strptime(row["end time"], "%Y-%m-%d %H:%M:%S %Z").replace(tzinfo=UTC)
    return ArchiveStatLine(
        match_id=row["match id"],
        series_id=row["series id"],
        ended_at=ended,
        duration_s=int(float(row["duration (s)"])),
        mode=row["mode"],
        map_name=row["map"],
        team=row["team"],
        player=row["player"],
        won=row["win?"].strip().upper() == "W",
        team_score=int(row["score"]),
        kills=int(row["kills"]),
        deaths=int(row["deaths"]),
        assists=int(row["assists"]),
        damage=_opt_int(row, "damage dealt"),
        hill_time=_opt_int(row, "hill time (s)"),
        first_bloods=_opt_int(row, "snd firstbloods"),
        plants=_opt_int(row, "bomb plants"),
        defuses=_opt_int(row, "bomb defuses"),
        extras=extras,
    )


@dataclass
class Aliases:
    players: dict[str, str]  # archive spelling -> canonical handle
    teams: dict[str, str]  # archive name -> canonical team name

    @classmethod
    def load(cls) -> Aliases:
        raw = json.loads(
            resources.files("cdlhub_pipeline.cwl_archive").joinpath("aliases.json").read_text()
        )
        return cls(players=dict(raw["players"]), teams=dict(raw["teams"]))

    def team(self, name: str) -> str:
        return self.teams.get(name, name)

    def player(self, handle: str) -> str:
        return self.players.get(handle, handle)


@dataclass
class ParsedEvent:
    event: ArchiveEvent
    lines: list[ArchiveStatLine] = field(default_factory=list)


def parse_archive(archive_dir: Path, aliases: Aliases) -> list[ParsedEvent]:
    """Parse every manifest CSV, applying identity normalization."""
    out: list[ParsedEvent] = []
    for ev in EVENTS:
        path = archive_dir / ev.filename
        parsed = ParsedEvent(event=ev)
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                line = parse_row(row)
                line.team = aliases.team(line.team)
                line.player = aliases.player(line.player)
                parsed.lines.append(line)
        out.append(parsed)
    return out


def canonical_handles(events: list[ParsedEvent]) -> dict[str, str]:
    """Case-insensitive canonicalization: for each lowercased handle, the most
    frequent spelling wins (ties broken lexicographically for determinism)."""
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for pe in events:
        for line in pe.lines:
            spellings[line.player.lower()][line.player] += 1
    return {
        low: max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        for low, counts in spellings.items()
    }
