"""Normalize wiki box scores into series/game/stat-line structures for loading.

One `PlayerStats` row is one player on one map. Rows group into games by
`SeriesId` and `GameNumber`, and games group into series by `SeriesId`.

The season comes from the title, not from the calendar year. Each pre-2017
title had exactly one competitive season, and the December 2016 Infinite
Warfare events belong to the 2017 season, the same rule the rest of the
pipeline follows.

Infinite Warfare is the one title loaded from the overlap window as well. The
Activision archive holds only its championship, so the wiki is the only box
score for the rest of the 2017 season. `IW_OVERLAP_EVENTS` names the pages that
load and the event each lands on.

The series score comes from the wiki's `MatchSchedule` table, not from the box
scores, the same rule the Cito load follows for its list records. A box score
can lack a map or repeat one, and a map's winner is read from every row of it,
whether or not its players resolved. Where the box score's map wins contradict
the schedule, the map winners are nulled and the stat lines kept. A series the
schedule scores and the box scores do not cover loads score-only.

A row is dropped, with its reason counted, when it carries no kill count, no
resolved player, no team, no opponent, no win flag, or a mode the database does
not model. Nothing is guessed.

A game whose surviving lines cover only one team keeps its result and loses its
lines: half a box score is not a map's box score. The wiki does this where it
left one team's kills blank, and where it copied one team's rows over the
other's.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
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

# Overlap-window pages that load, against the event each one lands on. Most of
# these events already exist from the LPDB tournament catalog with no series
# under them, so the wiki's series join those rows instead of creating a second
# event under the wiki's page name. The championship is absent: cwl_archive
# holds it.
IW_OVERLAP_EVENTS: dict[str, str] = {
    "CWL/2017 Season/London Invitational": "CWL London Invitational 2017",
    "CWL/2017 Season/Atlanta Open": "CWL Atlanta Open 2017",
    "CWL/2017 Season/Paris Open": "CWL Paris Open 2017",
    "CWL/2017 Season/Sydney Open 1": "CWL Sydney Open 2017",
    "CWL/2017 Season/Dallas Open": "CWL Dallas Open 2017",
    "CWL/2017 Season/Birmingham Open": "CWL Birmingham Open 2017",
    "CWL/2017 Season/Anaheim Open": "CWL Anaheim Open 2017",
    "CWL/2017 Season/Global Pro League/Stage 1": "CWL 2017 Global Pro League Stage 1",
    "CWL/2017 Season/Global Pro League/Stage 1/Playoffs": "CWL 2017 Global Pro League Stage 1",
    "CWL/2017 Season/Global Pro League/Stage 2": "CWL 2017 Global Pro League Stage 2",
    "CWL/2017 Season/Global Pro League/Stage 2/Playoffs": "CWL 2017 Global Pro League Stage 2",
    "CWL/2017 Season/Global Pro League/Relegation": "CWL/2017 Season/Global Pro League/Relegation",
}

# Schedule pages with no box score at all, against the event and season each
# lands on.
SCHEDULE_ONLY_EVENTS: dict[str, tuple[str, int]] = {
    "CWL/2017 Season/Sydney Open 2": ("CWL Sydney Open #2 2017", 2017),
    "CWL/2017 Season/North America/Last Chance Qualifier": (
        "CWL/2017 Season/North America/Last Chance Qualifier",
        2017,
    ),
}

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
    # (team1, team2) from the schedule; map wins stand in where it is absent.
    score: tuple[int, int] | None = None
    # The schedule's `Round`, else its `Tab`; None where no row matched.
    round_label: str | None = None

    @property
    def team1_score(self) -> int:
        if self.score is not None:
            return self.score[0]
        return sum(1 for g in self.games if g.winner_team == self.team1)

    @property
    def team2_score(self) -> int:
        if self.score is not None:
            return self.score[1]
        return sum(1 for g in self.games if g.winner_team == self.team2)


@dataclass
class TransformResult:
    series: list[Series]
    dropped: dict[str, int]
    quarantine: list[dict[str, str]]
    schedule: dict[str, int] = field(default_factory=dict)
    conflicts: list[dict[str, str]] = field(default_factory=list)


def rows_path(window: str = "playerstats") -> Any:
    return SNAPSHOT_ROOT / f"playerstats-{window}.json"


def schedule_path() -> Any:
    return SNAPSHOT_ROOT / "matchschedule.json"


def load_schedule() -> list[dict[str, Any]]:
    """Scored, played schedule rows whose score names the recorded winner.

    Empty before the first pull.
    """
    path = schedule_path()
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for row in json.loads(path.read_text()):
        s1, s2 = _int(row.get("Team1Score")), _int(row.get("Team2Score"))
        # A tied score under a named winner is a bracket-reset final written as
        # one row; the map wins decide it instead.
        if (
            s1 is not None
            and s2 is not None
            and s1 != s2
            and row.get("Winner") == ("1" if s1 > s2 else "2")
            and not (row.get("FF") or "").strip()
            and (row.get("Team1") or "").strip()
            and (row.get("Team2") or "").strip()
            and _schedule_key(row)
        ):
            out.append(row)
    return out


def _schedule_key(row: dict[str, Any]) -> str:
    """SeriesId where the wiki gives one, the match id where it does not."""
    series_id = str(row.get("SeriesId") or "").strip()
    if series_id:
        return series_id
    match_id = str(row.get("MatchId") or "").strip()
    return f"match:{match_id}" if match_id else ""


_ORG_FILLER = {"team", "the", "gaming", "esports", "esport", "club", "clan"} | {
    "na",
    "eu",
    "uk",
    "apac",
    "anz",
}


def _same_org(a: str, b: str) -> bool:
    """Two spellings of one org: equal, or one a prefix of the other, once a
    parenthetical and region and filler words are dropped."""

    def core(name: str) -> str:
        name = re.sub(r"\(.*?\)", " ", name.lower())
        return " ".join(w for w in name.split() if w not in _ORG_FILLER)

    x, y = core(a), core(b)
    return bool(x and y) and (x.startswith(y) or y.startswith(x))


def _schedule_date(row: dict[str, Any]) -> date | None:
    # Cargo returns the field as `DateTime UTC`; the underscore form is its name.
    raw = (row.get("DateTime UTC") or row.get("DateTime_UTC") or "")[:10]
    return date.fromisoformat(raw) if raw else None


class _Schedule:
    """Schedule rows by SeriesId and by (page, team pair), each used once."""

    def __init__(self, rows: list[dict[str, Any]], team: Callable[[str], str]) -> None:
        self.rows = rows
        self.team = team
        self.by_id = {str(r["SeriesId"]): r for r in rows if (r.get("SeriesId") or "").strip()}
        self.by_pair: dict[tuple[str, frozenset[str]], list[dict[str, Any]]] = defaultdict(list)
        for r in rows:
            self.by_pair[(r.get("OverviewPage") or "", self._pair(r))].append(r)
        self.used: set[int] = set()

    def _pair(self, r: dict[str, Any]) -> frozenset[str]:
        return frozenset((self.team(r["Team1"]), self.team(r["Team2"])))

    def match(
        self, series_id: str, page: str, team1: str, team2: str, on: date
    ) -> tuple[dict[str, Any], tuple[int, int], str] | None:
        """The row scoring this series, its score in (team1, team2) order, and how it matched.

        The SeriesId comes first. Where its teams differ, the page and the
        pair within a day decide, since the wiki swaps SeriesIds between two
        series on a page. A SeriesId sharing one team with the box score is
        taken last, and only when the other two names are one org spelled two
        ways (`Curse NA` and `Team Curse`).
        """
        t1, t2 = self.team(team1), self.team(team2)
        by_id = self.by_id.get(series_id)
        if by_id is not None and id(by_id) not in self.used:
            score = _oriented(by_id, t1, t2, self.team)
            if score is not None:
                return self._take(by_id, score, "series id")
        near = [
            r
            for r in self.by_pair.get((page, frozenset((t1, t2))), [])
            if id(r) not in self.used
            and ((d := _schedule_date(r)) is None or abs((d - on).days) <= 1)
        ]
        if len(near) == 1:
            score = _oriented(near[0], t1, t2, self.team)
            assert score is not None
            return self._take(near[0], score, "page and pair")
        if by_id is not None and id(by_id) not in self.used:
            p1, p2 = self.team(by_id["Team1"]), self.team(by_id["Team2"])
            s1, s2 = int(by_id["Team1Score"]), int(by_id["Team2Score"])
            straight = (p1 == t1 and _same_org(p2, t2)) or (p2 == t2 and _same_org(p1, t1))
            crossed = (p1 == t2 and _same_org(p2, t1)) or (p2 == t1 and _same_org(p1, t2))
            if straight != crossed:
                return self._take(by_id, (s1, s2) if straight else (s2, s1), "series id, one side")
        return None

    def _take(
        self, row: dict[str, Any], score: tuple[int, int], how: str
    ) -> tuple[dict[str, Any], tuple[int, int], str]:
        self.used.add(id(row))
        return row, score, how


def load_rows() -> list[dict[str, Any]]:
    """The load window's rows plus the overlap rows on `IW_OVERLAP_EVENTS` pages."""
    rows: list[dict[str, Any]] = json.loads(rows_path().read_text())
    overlap: list[dict[str, Any]] = json.loads(rows_path("overlap").read_text())
    return rows + [
        row
        for row in overlap
        if row.get("GameTitle") == "Infinite Warfare"
        and row.get("TournamentPage") in IW_OVERLAP_EVENTS
    ]


def transform(
    player_ids: dict[str, int], team: Callable[[str], str] = lambda name: name
) -> TransformResult:
    """Group wiki rows into series; `player_ids` maps PlayerLink to player id.

    `team` folds a wiki team name onto the name the load keys teams by, so the
    schedule's spelling of a team can be matched to the box score's.
    """
    raw = load_rows()
    schedule = _Schedule(load_schedule(), team)
    dropped: dict[str, int] = defaultdict(int)
    counts: dict[str, int] = defaultdict(int)
    quarantine: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    by_game: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    meta: dict[str, dict[str, Any]] = {}
    maps: dict[tuple[str, int], dict[str, Any]] = {}
    map_winner: dict[tuple[str, int], str] = {}
    names: dict[str, set[str]] = defaultdict(set)

    for row in raw:
        series_id = str(row.get("SeriesId") or "").strip()
        if series_id and row.get("GameTitle") in TITLE_SEASONS:
            meta.setdefault(series_id, row)
            for side in ("Team", "TeamVs"):
                if (row.get(side) or "").strip():
                    names[series_id].add(team(row[side]))
            key = (series_id, int(row.get("GameNumber") or 1))
            if row.get("Gamemode") in MODE_SLUGS:
                maps.setdefault(key, row)
            if row.get("Win") == "1" and (row.get("Team") or "").strip():
                map_winner.setdefault(key, row["Team"])
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
        by_game[(series_id, int(row["GameNumber"] or 1))].append(row)

    games_by_series: dict[str, list[Game]] = defaultdict(list)
    for key, first in sorted(maps.items()):
        lines = [_stat_line(row, player_ids) for row in by_game.get(key, [])]
        winner = map_winner.get(key)
        if not lines and winner is None:
            continue
        game = Game(
            ordinal=key[1],
            map_name=first["Map"] or "",
            mode_slug=MODE_SLUGS[first["Gamemode"]],
            winner_team=winner,
            lines=lines,
        )
        games_by_series[key[0]].append(game)

    series: list[Series] = []
    held: set[tuple[str, frozenset[str], date]] = set()
    for series_id, first in meta.items():
        title, season = TITLE_SEASONS[first["GameTitle"]]
        team1, team2 = (first.get("Team") or "").strip(), (first.get("TeamVs") or "").strip()
        if not team1 or not team2 or team(team1) == team(team2):
            dropped["series without two sides"] += 1
            continue
        if len(names[series_id]) > 2:
            # The wiki reuses a SeriesId across matches on some pages.
            dropped["series naming more than two teams"] += 1
            continue
        games = sorted(games_by_series.get(series_id, []), key=lambda g: g.ordinal)
        stripped = _strip_one_sided(games)
        if stripped:
            dropped["game with one side's lines"] += stripped
        s = Series(
            series_id=series_id,
            event=IW_OVERLAP_EVENTS.get(first["TournamentPage"], first["TournamentPage"]),
            season=season,
            title=title,
            played_on=date.fromisoformat(first["Date"][:10]),
            team1=team1,
            team2=team2,
            games=games,
        )
        found = schedule.match(series_id, first["TournamentPage"], team1, team2, s.played_on)
        if found is not None:
            planned, s.score, how = found
            s.round_label = _round(planned)
            counts[f"scored by the schedule, matched on {how}"] += 1
            if _contradicts(s):
                conflicts.append(_conflict(s, planned))
                for game in s.games:
                    game.winner_team = None
                counts["map winners nulled"] += 1
        elif not games:
            dropped["series with no result"] += 1
            continue
        else:
            counts["scored by map wins"] += 1
        held.add((first["TournamentPage"], frozenset((team(team1), team(team2))), s.played_on))
        series.append(s)

    series += _schedule_only(schedule, meta, held, team, counts)
    return TransformResult(
        series=series,
        dropped=dict(dropped),
        quarantine=quarantine,
        schedule=dict(counts),
        conflicts=conflicts,
    )


def _oriented(
    planned: dict[str, Any], t1: str, t2: str, team: Callable[[str], str]
) -> tuple[int, int] | None:
    """The schedule score in (t1, t2) order, or None when its pair differs."""
    s1, s2 = int(planned["Team1Score"]), int(planned["Team2Score"])
    p1, p2 = team(planned["Team1"]), team(planned["Team2"])
    if (p1, p2) == (t1, t2):
        return s1, s2
    if (p2, p1) == (t1, t2):
        return s2, s1
    return None


def _contradicts(s: Series) -> bool:
    """Map wins that exceed a side's series score, or that give the other side the series."""
    assert s.score is not None
    w1 = sum(1 for g in s.games if g.winner_team == s.team1)
    w2 = sum(1 for g in s.games if g.winner_team == s.team2)
    if w1 > s.score[0] or w2 > s.score[1]:
        return True
    return w1 != w2 and (w1 > w2) != (s.score[0] > s.score[1])


def _conflict(s: Series, planned: dict[str, Any]) -> dict[str, str]:
    w1 = sum(1 for g in s.games if g.winner_team == s.team1)
    w2 = sum(1 for g in s.games if g.winner_team == s.team2)
    return {
        "series_id": s.series_id,
        "event": s.event,
        "date": str(s.played_on),
        "teams": f"{s.team1} vs {s.team2}",
        "schedule": f"{planned['Team1']} {planned['Team1Score']}-{planned['Team2Score']} "
        f"{planned['Team2']}",
        "map_wins": f"{w1}-{w2}",
    }


def _schedule_only(
    schedule: _Schedule,
    meta: dict[str, dict[str, Any]],
    held: set[tuple[str, frozenset[str], date]],
    team: Callable[[str], str],
    counts: dict[str, int],
) -> list[Series]:
    """Series the schedule scores on an in-scope page and no box score covers.

    A page is in scope when the box scores reach it or `SCHEDULE_ONLY_EVENTS`
    names it. A series between the same two teams on the same page within a
    day of a box-scored one is the same series under another id, and is left.
    """
    seasons: dict[str, int] = {}
    for row in meta.values():
        seasons.setdefault(row["TournamentPage"], TITLE_SEASONS[row["GameTitle"]][1])
    titles = {season: title for title, season in TITLE_SEASONS.values()}
    out: list[Series] = []
    for planned in schedule.rows:
        if id(planned) in schedule.used:
            continue
        page = planned.get("OverviewPage") or ""
        if page in SCHEDULE_ONLY_EVENTS:
            event, season = SCHEDULE_ONLY_EVENTS[page]
        elif page in seasons:
            event, season = IW_OVERLAP_EVENTS.get(page, page), seasons[page]
        else:
            continue
        key = _schedule_key(planned)
        on = _schedule_date(planned)
        if key in meta:
            counts["schedule row for a box series it does not match"] += 1
            continue
        if on is None:
            counts["schedule row without a date"] += 1
            continue
        pair = frozenset((team(planned["Team1"]), team(planned["Team2"])))
        if len(pair) != 2:
            counts["schedule row without two sides"] += 1
            continue
        if any((page, pair, on + timedelta(days=d)) in held for d in (-1, 0, 1)):
            counts["schedule row already box-scored"] += 1
            continue
        counts["schedule only"] += 1
        out.append(
            Series(
                series_id=key,
                event=event,
                season=season,
                title=titles[season],
                played_on=on,
                team1=planned["Team1"],
                team2=planned["Team2"],
                score=(int(planned["Team1Score"]), int(planned["Team2Score"])),
                round_label=_round(planned),
            )
        )
    return out


def _round(planned: dict[str, Any]) -> str | None:
    """`Round` (WR1, GF, Group B, Week 5), else `Tab`, which names a round only on some pages."""
    for key in ("Round", "Tab"):
        value = str(planned.get(key) or "").strip()
        if value:
            return value
    return None


def _strip_one_sided(games: list[Game]) -> int:
    """Clear the lines of every game that has lines for one team only."""
    stripped = 0
    for game in games:
        if len({line.team_name for line in game.lines}) == 1:
            stripped += len(game.lines)
            game.lines.clear()
    return stripped


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
