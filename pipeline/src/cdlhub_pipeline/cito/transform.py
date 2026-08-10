"""Normalize Cito snapshots into series/games/stat-line structures for loading.

The scoped manifest mixes three upstream catalogs. Only bp-match-* ids are
trustworthy series records; codwiki-* and numeric ids alias real matches under
garbage metadata often enough that a series is deduplicated by (team-slug pair,
match day ±1) across catalogs, preferring bp, then the numeric/cdl catalog
(which carries stats), then codwiki. Whatever a lower-priority record loses to
is logged, never dropped silently.

Seasons come from matchDate only (Cito's season/game tags are wrong for whole
seasons); Nov/Dec matches belong to the following season year. Team identity
resolves (slug, season) through the dated cito_teams alias map, undoing Cito's
retroactive current-branding of franchise slots.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ..identity import Aliases

SNAPSHOT_ROOT = Path(__file__).resolve().parents[4] / "pipeline" / "snapshots" / "cito"

LEAGUE = "CDL"

# season year -> title short_name (matches db/seeds reference data)
SEASON_TITLES = {
    2020: "MW19",
    2021: "BOCW",
    2022: "VG",
    2023: "MWII",
    2024: "MWIII",
    2025: "BO6",
    2026: "BO7",
}

MODE_SLUGS = {
    "Hardpoint": "hardpoint",
    "Search and Destroy": "search-and-destroy",
    "Search & Destroy": "search-and-destroy",
    "Control": "control",
    "Domination": "domination",
    "Overload": "overload",
}


def season_year(match_date: str) -> int:
    """CDL season a match belongs to; Nov/Dec starts the next year's season."""
    year, month = int(match_date[:4]), int(match_date[5:7])
    return year + 1 if month >= 11 else year


def catalog(match_id: str) -> str:
    if match_id.startswith("bp-match-"):
        return "bp"
    if match_id.startswith("codwiki-"):
        return "codwiki"
    return "cdl"


_EVENT_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # bp catalog
    (re.compile(r"^CDL (League )?Championship$"), "CDL Championship"),
    (re.compile(r"^CDL Major (\d) Qualifier$"), r"CDL Major \1 Qualifiers"),
    (re.compile(r"^CDL Major (\d) Tournament$"), r"CDL Major \1"),
    (re.compile(r"^CDL Minor (\d) Tournament$"), r"CDL Minor \1"),
    # codwiki catalog ("Call of Duty League/YYYY Season/..." paths)
    (re.compile(r"^Call of Duty League Championship \d{4}$"), "CDL Championship"),
    (re.compile(r".*Season/Major (\d)/Qualifiers$"), r"CDL Major \1 Qualifiers"),
    (re.compile(r".*Season/Major (\d)/Minor$"), r"CDL Minor \1"),
    (re.compile(r".*Season/Major (\d)$"), r"CDL Major \1"),
    (re.compile(r".*Season/Stage (\d)/Major$"), r"CDL Major \1"),
    (re.compile(r".*Season/Stage (\d)$"), r"CDL Major \1 Qualifiers"),
    (re.compile(r".*Season/Kickoff Classic$"), "CDL Kickoff Classic"),
    (re.compile(r".*Season/Pro-Am Classic$"), "CDL Pro-Am Classic"),
    (re.compile(r".*Season/Launch Invitational$"), "CDL Launch Invitational"),
    # cdl catalog
    (re.compile(r"^CDL \d{4} Regular Season$"), "CDL Regular Season"),
    (
        re.compile(r"^CDL \d{4} .* MAJOR (\w+) QUALIFIERS$", re.IGNORECASE),
        r"CDL Major \1 Qualifiers",
    ),
)

_ROMAN = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5"}


def event_name(raw: str) -> str:
    """Canonical per-season event name, merging the catalogs' naming schemes."""
    name = " ".join((raw or "").split())
    for pattern, repl in _EVENT_RULES:
        m = pattern.match(name)
        if m:
            out = m.expand(repl)
            tail = out.rsplit(" ", 1)
            if len(tail) == 2 and tail[1].upper() in _ROMAN:
                out = f"{tail[0]} {_ROMAN[tail[1].upper()]}"
            return out
    return name


@dataclass
class StatLine:
    player: str
    team_slug: str
    stats: dict[str, Any]  # raw per-map stats payload


@dataclass
class Game:
    ordinal: int
    map_name: str
    mode: str
    team1_score: int | None
    team2_score: int | None
    winner_slug: str | None
    lines: list[StatLine] = field(default_factory=list)


@dataclass
class Series:
    match_id: str
    season: int
    event: str
    played_at: datetime
    team1_slug: str
    team2_slug: str
    team1_name: str  # dated canonical name via cito_teams
    team2_name: str
    team1_score: int
    team2_score: int
    best_of: int | None
    round_label: str | None
    games: list[Game] = field(default_factory=list)


@dataclass
class TransformResult:
    series: list[Series]
    dropped: list[dict[str, str]]  # dedup log: what lost to what, and why
    quarantined: list[dict[str, str]]  # records too corrupt to load; owner review
    inconsistent: list[str]  # match ids whose map results contradict the series score
    warnings: list[str]
    unmapped_slugs: dict[str, int]


def load_scoped_records() -> dict[str, dict[str, Any]]:
    """Every scoped match's list record, keyed by matchId."""
    manifest = set(json.loads((SNAPSHOT_ROOT / "cdl-pro-manifest.json").read_text())["matchIds"])
    records: dict[str, dict[str, Any]] = {}
    for path in sorted((SNAPSHOT_ROOT / "match-lists").glob("*.json")):
        for m in json.loads(path.read_text()).get("data", []):
            if m["matchId"] in manifest and m["matchId"] not in records:
                records[m["matchId"]] = m
    missing = manifest - set(records)
    if missing:
        raise ValueError(f"{len(missing)} manifest matchIds absent from match-list snapshots")
    return records


def _pair(record: dict[str, Any]) -> frozenset[str]:
    return frozenset((record["teams"]["team1"]["slug"], record["teams"]["team2"]["slug"]))


def dedup(records: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Cross-catalog series dedup: (slug pair, day ±1), bp > cdl > codwiki.

    Same-catalog collisions are kept (bp double-headers are real); only a
    lower-priority catalog's record loses to a higher-priority one.
    """
    priority = {"bp": 0, "cdl": 1, "codwiki": 2}
    ordered = sorted(
        records.values(),
        key=lambda m: (priority[catalog(m["matchId"])], m.get("matchDate") or "", m["matchId"]),
    )
    kept: list[dict[str, Any]] = []
    seen: dict[tuple[date, frozenset[str]], dict[str, Any]] = {}
    dropped: list[dict[str, str]] = []
    for m in ordered:
        day = date.fromisoformat((m["matchDate"] or "")[:10])
        pair = _pair(m)
        rank = priority[catalog(m["matchId"])]
        winner = None
        for delta in (0, -1, 1):
            hit = seen.get((day + timedelta(days=delta), pair))
            if hit is not None and priority[catalog(hit["matchId"])] < rank:
                winner = hit
                break
        if winner is not None:
            dropped.append(
                {
                    "dropped": m["matchId"],
                    "kept": winner["matchId"],
                    "date": str(day),
                    "score": m["score"]["formatted"],
                    "kept_score": winner["score"]["formatted"],
                }
            )
            continue
        kept.append(m)
        seen.setdefault((day, pair), m)
    return kept, dropped


def _stats_payload(match_id: str) -> dict[str, Any] | None:
    path = SNAPSHOT_ROOT / "matches" / f"{match_id}.json"
    if not path.exists():
        return None
    payload: dict[str, Any] = json.loads(path.read_text())
    inner = payload.get("data", payload)
    return inner if isinstance(inner, dict) else None


def _build_games(
    data: dict[str, Any],
    team1_slug: str,
    team2_slug: str,
    warnings: list[str],
    quarantined: list[dict[str, str]],
    match_id: str,
) -> list[Game]:
    """Assemble per-map games from the player-stats payload's maps[] entries.

    The cdl catalog sometimes attaches a different match's player stats to a
    fixture (map wins contradict the series score and the stamped team slugs
    aren't the fixture's teams). Stats are only trusted when both series teams
    appear among the stamped slugs; otherwise the whole payload is quarantined
    and the series stays score-only. A stray third slug (a mis-stamped player)
    drops only that player's lines.
    """
    pair = {team1_slug, team2_slug}
    stamped = {
        p.get("teamSlug")
        for p in data.get("players", [])
        if p.get("teamSlug") and (p.get("maps") or [])
    }
    if not pair <= stamped:
        quarantined.append(
            {
                "match_id": match_id,
                "reason": "stats payload team slugs irreconcilable with series teams",
                "series_teams": f"{team1_slug} vs {team2_slug}",
                "stats_teams": ", ".join(sorted(stamped)),
            }
        )
        return []

    by_map: dict[int, dict[str, Any]] = {}
    lines: dict[int, list[StatLine]] = defaultdict(list)
    for player in data.get("players", []):
        name = (player.get("playerName") or "").strip()
        slug = player.get("teamSlug")
        if not name or not slug:
            warnings.append(f"{match_id}: player entry missing name or teamSlug, skipped")
            continue
        if slug not in pair:
            warnings.append(f"{match_id}: stray team slug {slug} on {name}, lines skipped")
            continue
        for mp in player.get("maps") or []:
            num = mp["mapNumber"]
            meta = by_map.setdefault(
                num, {"map": mp["mapName"], "mode": mp["gameMode"], "scores": {}, "opp": {}}
            )
            if mp.get("teamScore") is not None:
                prev = meta["scores"].get(slug)
                if prev is not None and prev != mp["teamScore"]:
                    warnings.append(f"{match_id} map {num}: conflicting teamScore for {slug}")
                meta["scores"][slug] = mp["teamScore"]
            if mp.get("opponentScore") is not None:
                meta["opp"][slug] = mp["opponentScore"]
            lines[num].append(StatLine(player=name, team_slug=slug, stats=mp.get("stats") or {}))

    games: list[Game] = []
    for num in sorted(by_map):
        meta = by_map[num]
        # A side missing from the payload still gets its score from the other
        # side's opponentScore; only a map with neither view is skipped.
        s1 = meta["scores"].get(team1_slug, meta["opp"].get(team2_slug))
        s2 = meta["scores"].get(team2_slug, meta["opp"].get(team1_slug))
        if s1 is None or s2 is None:
            warnings.append(f"{match_id} map {num}: no score for one side, skipped")
            continue
        winner = None
        if s1 != s2:
            winner = team1_slug if s1 > s2 else team2_slug
        games.append(
            Game(
                ordinal=num,
                map_name=meta["map"],
                mode=meta["mode"],
                team1_score=s1,
                team2_score=s2,
                winner_slug=winner,
                lines=lines[num],
            )
        )
    return games


def transform(aliases: Aliases) -> TransformResult:
    records = load_scoped_records()
    kept, dropped = dedup(records)
    warnings: list[str] = []
    quarantined: list[dict[str, str]] = []
    inconsistent: list[str] = []
    unmapped: dict[str, int] = defaultdict(int)
    series_out: list[Series] = []

    for m in kept:
        season = season_year(m["matchDate"])
        if season not in SEASON_TITLES:
            warnings.append(f"{m['matchId']}: season {season} outside CDL era, skipped")
            continue
        t1, t2 = m["teams"]["team1"], m["teams"]["team2"]
        data = _stats_payload(m["matchId"])
        if t1["slug"] == t2["slug"]:
            # A handful of 2020 bp records stamp the same slug on both sides;
            # the true opponent is unrecoverable from Cito. LPDB fills these.
            quarantined.append(
                {
                    "match_id": m["matchId"],
                    "reason": "same team slug on both sides of the fixture",
                    "series_teams": f"{t1['slug']} vs {t2['slug']}",
                    "stats_teams": "",
                }
            )
            continue

        def resolve(slug: str, fallback: str) -> str:
            # A slug that hides two teams this season (Cito files 2020 OpTic
            # Gaming LA under g2-minnesota) is split by which team's roster the
            # stamped lineup matches.
            split = aliases.cito_split(slug, season)  # noqa: B023 (loop-stable)
            if split and data is not None:  # noqa: B023
                lineup = {
                    aliases.player((p.get("playerName") or "").strip()).lower()
                    for p in data.get("players", [])  # noqa: B023
                    if p.get("teamSlug") == slug
                }
                votes = {team: len(lineup & roster) for team, roster in split.items()}
                best = max(votes, key=lambda t: votes[t])
                others = sum(v for t, v in votes.items() if t != best)
                if votes[best] > 0:
                    if others:
                        warnings.append(
                            f"{m['matchId']}: mixed lineup for split slug {slug} "  # noqa: B023
                            f"({votes}), assigned {best}"
                        )
                    return best
                warnings.append(
                    f"{m['matchId']}: lineup for split slug {slug} matches no roster"  # noqa: B023
                )
            name = aliases.cito_team(slug, season)  # noqa: B023 (season is loop-stable here)
            if name is None:
                unmapped[slug] += 1
                return fallback
            return name

        s = Series(
            match_id=m["matchId"],
            season=season,
            event=event_name(m["tournament"]["name"]),
            played_at=datetime.fromisoformat(m["matchDate"].replace("Z", "+00:00")),
            team1_slug=t1["slug"],
            team2_slug=t2["slug"],
            team1_name=resolve(t1["slug"], t1["name"]),
            team2_name=resolve(t2["slug"], t2["name"]),
            team1_score=m["score"]["team1"],
            team2_score=m["score"]["team2"],
            best_of=m.get("bestOf"),
            round_label=m.get("round"),
        )
        if data is not None:
            s.games = _build_games(
                data, s.team1_slug, s.team2_slug, warnings, quarantined, m["matchId"]
            )
            # The list-record series score is authoritative. When a full set of
            # map results fails to reproduce it (2020 payloads often stamp both
            # sides with the winner's scores; a few others are flipped), the
            # per-map scores/winners are untrustworthy and unattributable to a
            # specific map — null them all, keep the stat lines.
            if s.games and len(s.games) == s.team1_score + s.team2_score:
                w1 = sum(1 for g in s.games if g.winner_slug == s.team1_slug)
                w2 = sum(1 for g in s.games if g.winner_slug == s.team2_slug)
                if (w1, w2) != (s.team1_score, s.team2_score):
                    for g in s.games:
                        g.team1_score = g.team2_score = None
                        g.winner_slug = None
                    inconsistent.append(m["matchId"])
        series_out.append(s)

    return TransformResult(
        series=series_out,
        dropped=dropped,
        quarantined=quarantined,
        inconsistent=inconsistent,
        warnings=warnings,
        unmapped_slugs=dict(unmapped),
    )
