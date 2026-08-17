"""Load pre-2017 placements, event rosters and awards from the wiki snapshots.

Three tables, one scope, one key. `TournamentResults` gives a team a place at an
event, `TournamentRosters` gives that same team its players, and both carry
`PageAndTeam`, so the two join without a name match. `Awards` names a player at
an event.

**Scope.** The wiki holds 633 result-bearing events between 2013 and 2016, and
most of them are daily online cups: one page, `MLG GameBattles/500 Series/North
America/2016-07-31`, is 42 of those rows. Loading all of them would fill
`events` with several hundred rows that carry no box score and no meaning for a
resume. The rule is therefore: keep an event when the wiki calls it Premier,
Major or Minor, or when the box-score load already created it. That is 208
events. Every event we hold has result rows, so the second half of the rule
never loses one.

**The window ends at 2017, with one named exception.** Awards for 2017 onward
are already held from LPDB under different page names, and loading the wiki's
copy would count one MVP twice. They are reported, not loaded, which is the
same rule the box scores follow. The exception is `CWL All-Star`: LPDB holds no
all-league team before 2020 at all, so excluding these would give the CWL years
no first-team credit while the CDL years have it, which is an era difference in
the record rather than in the sport. Nothing can be counted twice, because
there is no LPDB row of that kind to collide with.

A team, a player or an event that cannot be resolved is counted and named. None
is guessed.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any, cast

import psycopg

from ..identity import Aliases
from .client import SNAPSHOT_ROOT
from .transform import SEASON_LEAGUES, SOURCE, TITLE_SEASONS

WINDOW_START = date(2013, 1, 1)
WINDOW_END = date(2017, 1, 1)
# The wiki's own tier vocabulary. Premier and Major are the events a ring or a
# major is counted from. Minor is 131 more events, mostly regional ladders in
# markets the rest of the database does not carry, and taking it would more than
# double the team table for placements no career page would show. Widening the
# scope later is this one constant.
KEPT_TIERS = {"Premier", "Major"}
ROSTER_SEPARATOR = ";;"
# `#4 BO6 Top 20` and its siblings are a published end-of-year ranking list, not
# an award. They belong to the face-validity anchor set.
RANKING_TYPE = re.compile(r"^#\d+ ")


def _snapshot(name: str) -> Any:
    return json.loads((SNAPSHOT_ROOT / name).read_text())


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _in_window(value: Any) -> bool:
    day = _day(value)
    return day is not None and WINDOW_START <= day < WINDOW_END


def _places(raw: str) -> tuple[int, int] | None:
    """`1` is one place; `3-4` is a shared place; `DQ` and `-` are neither."""
    text = (raw or "").strip().lstrip("T")
    if not text:
        return None
    parts = text.split("-")
    try:
        numbers = [int(part) for part in parts[:2]]
    except ValueError:
        return None
    return (numbers[0], numbers[-1])


def _prize(row: dict[str, Any]) -> float | None:
    raw = (row.get("Prize USD") or "").strip()
    if not raw:
        return None
    try:
        return float(raw.replace(",", ""))
    except ValueError:
        return None


# The one award kind loaded outside the window: see the module docstring.
ALL_LEAGUE_OUTSIDE_WINDOW = {"CWL All-Star"}

# The wiki title name for each season these selections cover, against the name
# our `titles` table uses. Kept here rather than in `TITLE_SEASONS`, which
# decides which box scores load and must not gain a year.
ALL_LEAGUE_TITLES = {
    "Infinite Warfare": "Infinite Warfare",
    "World War II": "WWII",
    "Black Ops 4": "Black Ops 4",
}

# Our event for each wiki page these selections sit on. A page with no local
# event takes the season alone: an all-league team is a season honour, and
# inventing an event to hang it on would put a tournament in the record that
# this project holds no maps for.
ALL_LEAGUE_EVENTS = {
    "CWL/2017 Season/Global Pro League/Stage 1": None,
    "CWL/2018 Season/Pro League/Stage 2": "CWL Pro League 2018 Stage 2",
    "CWL/2019 Season/Pro League": "CWL Pro League 2019",
}


def award_kind(raw: str) -> str:
    """Fold a wiki award string onto the kinds `player_awards` already uses."""
    name = (raw or "").strip()
    if name in ("MVP", "Event MVP", "Tournament MVP"):
        return "event_mvp"
    if name in ("Grand Finals MVP", "Finals MVP", "FMVP"):
        return "fmvp"
    if name in ("Season MVP", "Regular Season MVP"):
        return "rs_mvp"
    if name in ("Stage MVP", "Player of the Stage"):
        return "stage_mvp"
    if name in ("Rookie of the Year", "Breakout Player of the Year"):
        return "roty"
    if name in ("CWL All-Star", "CDL First Team", "Scuf TOTY"):
        return "first_team"
    if name == "CDL Second Team":
        return "second_team"
    if name.endswith("Player of the Year") or name.startswith("Best "):
        return "mode_best"
    return "unmapped"


@dataclass
class Report:
    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    unresolved_players: list[str] = field(default_factory=list)
    collisions: list[dict[str, str]] = field(default_factory=list)
    events_created: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": dict(self.counts),
            "skipped": dict(self.skipped),
            "unresolved_players": sorted(set(self.unresolved_players)),
            "collisions": self.collisions,
            "events_created": sorted(self.events_created),
        }


class ResultsLoader:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]], aliases: Aliases) -> None:
        self.conn = conn
        self.aliases = aliases
        self.report = Report()
        self._team_ids: dict[str, int] = {}
        self._event_ids: dict[str, int] = {}
        self._season_ids: dict[int, int] = {}
        self._players: dict[str, int] = {}

    # --- identity -------------------------------------------------------

    def load_players(self) -> None:
        """Wiki page to player id, through the handle and the alias rows.

        The box-score load already created every player it could justify, so
        nothing new is created here: a roster name with no player row is a
        player who never appeared on a scoreboard we hold, and it is reported.
        """
        for row in self.conn.execute("SELECT id, handle FROM players").fetchall():
            self._players[cast(str, row[1]).lower()] = cast(int, row[0])
        for row in self.conn.execute("SELECT player_id, alias FROM player_aliases").fetchall():
            self._players.setdefault(cast(str, row[1]).lower(), cast(int, row[0]))
        for redirect in _snapshot("playerredirects.json"):
            target = self._players.get(str(redirect["OverviewPage"]).lower())
            if target is not None:
                self._players.setdefault(str(redirect["AllName"]).lower(), target)

    def player_id(self, name: str) -> int | None:
        found = self._players.get((name or "").strip().lower())
        if found is None and name.strip():
            self.report.unresolved_players.append(name.strip())
        return found

    def team_id(self, name: str) -> int | None:
        canonical = self.aliases.team((name or "").strip())
        if not canonical:
            return None
        if canonical not in self._team_ids:
            row = self.conn.execute(
                "SELECT id FROM teams WHERE lower(name) = lower(%s)", (canonical,)
            ).fetchone()
            if row is None:
                self.report.counts["teams"] += 1
                row = self.conn.execute(
                    "INSERT INTO teams (name) VALUES (%s) RETURNING id", (canonical,)
                ).fetchone()
            assert row is not None
            self._team_ids[canonical] = cast(int, row[0])
        return self._team_ids[canonical]

    def season_id(self, title: str) -> int | None:
        mapped = TITLE_SEASONS.get(title)
        if mapped is None:
            return None
        name, year = mapped
        if year not in self._season_ids:
            row = self.conn.execute(
                "SELECT s.id FROM seasons s JOIN titles t ON t.id = s.title_id "
                "WHERE s.year = %s AND t.name = %s AND s.league = %s",
                (year, name, SEASON_LEAGUES[year]),
            ).fetchone()
            if row is None:
                return None
            self._season_ids[year] = cast(int, row[0])
        return self._season_ids[year]

    def event_id(self, page: str, meta: dict[str, Any]) -> int | None:
        """Our event for a wiki page, created only when the season is known."""
        if page in self._event_ids:
            return self._event_ids[page]
        season = self.season_id(str(meta.get("Game") or ""))
        if season is None:
            self.report.skipped["event title out of scope"] += 1
            return None
        row = self.conn.execute(
            "SELECT id FROM events WHERE season_id = %s AND name = %s", (season, page)
        ).fetchone()
        if row is None:
            start = _day(meta.get("DateStart")) or _day(meta.get("Date"))
            end = _day(meta.get("Date")) or start
            row = self.conn.execute(
                "INSERT INTO events (season_id, name, start_date, end_date) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (season, page, start, end),
            ).fetchone()
            self.report.counts["events"] += 1
            self.report.events_created.append(page)
        assert row is not None
        self._event_ids[page] = cast(int, row[0])
        return self._event_ids[page]

    # --- loads ----------------------------------------------------------

    def load_placements(self, rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        for row in rows:
            event = self.event_id(str(row["OverviewPage"]), meta.get(row["OverviewPage"], {}))
            if event is None:
                continue
            team = self.team_id(str(row.get("Team") or ""))
            if team is None:
                self.report.skipped["no team"] += 1
                continue
            places = _places(str(row.get("Place") or row.get("Place Number") or ""))
            if places is None:
                self.report.skipped["no place"] += 1
                continue
            held = self.conn.execute(
                "SELECT data_source FROM event_placements WHERE event_id = %s AND team_id = %s",
                (event, team),
            ).fetchone()
            if held is not None and held[0] != SOURCE:
                self.report.collisions.append(
                    {"event": str(row["OverviewPage"]), "held_by": str(held[0])}
                )
                continue
            self.conn.execute(
                """
                INSERT INTO event_placements
                  (event_id, team_id, placement_min, placement_max, prize, data_source)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, team_id) DO UPDATE SET
                  placement_min = EXCLUDED.placement_min,
                  placement_max = EXCLUDED.placement_max,
                  prize = EXCLUDED.prize
                """,
                (event, team, places[0], places[1], _prize(row), SOURCE),
            )
            self.report.counts["event_placements"] += 1

    def load_rosters(self, rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        for row in rows:
            page = str(row["OverviewPage"])
            event = self.event_id(page, meta.get(page, {}))
            if event is None:
                continue
            team = self.team_id(str(row.get("Team") or ""))
            if team is None:
                self.report.skipped["roster without a team"] += 1
                continue
            links = [n for n in str(row.get("RosterLinks") or "").split(ROSTER_SEPARATOR) if n]
            roles = str(row.get("Roles") or "").split(ROSTER_SEPARATOR)
            for index, link in enumerate(links):
                player = self.player_id(link)
                if player is None:
                    self.report.skipped["roster player unresolved"] += 1
                    continue
                role = roles[index].strip() if index < len(roles) else ""
                cur = self.conn.execute(
                    """
                    INSERT INTO event_rosters (event_id, team_id, player_id, role, data_source)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (event_id, team_id, player_id) DO NOTHING
                    """,
                    (event, team, player, role or None, SOURCE),
                )
                # A team fields the same player on several roster rows for one
                # event, so the count is rows written and not rows offered.
                self.report.counts["event_rosters"] += cur.rowcount

    def all_league_target(self, page: str, title: str) -> tuple[int | None, int | None]:
        """The season and event an out-of-window all-league selection attaches to."""
        our_title = ALL_LEAGUE_TITLES.get(title)
        if our_title is None or page not in ALL_LEAGUE_EVENTS:
            return None, None
        row = self.conn.execute(
            "SELECT s.id FROM seasons s JOIN titles t ON t.id = s.title_id WHERE t.name = %s",
            (our_title,),
        ).fetchone()
        if row is None:
            return None, None
        season = cast(int, row[0])
        name = ALL_LEAGUE_EVENTS[page]
        if name is None:
            return season, None
        found = self.conn.execute(
            "SELECT id FROM events WHERE season_id = %s AND name = %s", (season, name)
        ).fetchone()
        return season, (cast(int, found[0]) if found else None)

    def load_awards(self, rows: list[dict[str, Any]], meta: dict[str, Any]) -> None:
        for row in rows:
            page = str(row.get("TournamentPage") or "")
            entry = meta.get(page)
            if entry is None:
                self.report.skipped["award without an event"] += 1
                continue
            title = str(entry.get("Game") or "")
            if str(row.get("Type")) in ALL_LEAGUE_OUTSIDE_WINDOW and not _in_window(
                entry.get("Date")
            ):
                season, event = self.all_league_target(page, title)
                if season is None:
                    self.report.skipped["all-league selection with no season"] += 1
                    continue
            else:
                event = self.event_id(page, entry)
                season = self.season_id(title)
                if event is None or season is None:
                    self.report.skipped["award with no event or season"] += 1
                    continue
            handle = str(row.get("PlayerName") or "").strip()
            raw = str(row.get("Type") or "").strip()
            player = self.player_id(str(row.get("PlayerLink") or handle))
            kind = award_kind(raw)
            if kind == "unmapped":
                self.report.skipped["award kind unmapped"] += 1
            self.conn.execute(
                """
                INSERT INTO player_awards
                  (pagename, raw_award, handle, award, season_id, player_id, event_id,
                   awarded_on, data_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (pagename, raw_award, handle) DO NOTHING
                """,
                (
                    page,
                    raw,
                    handle,
                    kind,
                    season,
                    player,
                    event,
                    _day(entry.get("Date")),
                    SOURCE,
                ),
            )
            self.report.counts["player_awards"] += 1


def scope(conn: psycopg.Connection[tuple[object, ...]]) -> tuple[set[str], dict[str, Any]]:
    """The event pages this load covers, and the Tournaments row for each."""
    meta = {str(row["OverviewPage"]): row for row in _snapshot("tournaments.json")}
    held = {
        cast(str, row[0])
        for row in conn.execute(
            "SELECT e.name FROM events e JOIN seasons s ON s.id = e.season_id "
            "WHERE s.year BETWEEN 2013 AND 2016"
        ).fetchall()
    }
    pages = set()
    for row in _snapshot("tournamentresults.json"):
        page = str(row.get("OverviewPage") or "")
        if not _in_window(row.get("Date")) or page not in meta:
            continue
        if meta[page].get("Tier") in KEPT_TIERS or page in held:
            pages.add(page)
    return pages, meta


def load(conn: psycopg.Connection[tuple[object, ...]], aliases: Aliases) -> dict[str, Any]:
    pages, meta = scope(conn)
    loader = ResultsLoader(conn, aliases)
    loader.load_players()

    results = [r for r in _snapshot("tournamentresults.json") if r.get("OverviewPage") in pages]
    rosters = [r for r in _snapshot("tournamentrosters.json") if r.get("OverviewPage") in pages]
    awards_all = _snapshot("awards.json")
    ranking_lists = [a for a in awards_all if RANKING_TYPE.match(str(a.get("Type") or ""))]
    real = [a for a in awards_all if not RANKING_TYPE.match(str(a.get("Type") or ""))]
    awards = [
        a
        for a in real
        if _in_window((meta.get(str(a.get("TournamentPage"))) or {}).get("Date"))
        or str(a.get("Type")) in ALL_LEAGUE_OUTSIDE_WINDOW
    ]

    loader.load_placements(results, meta)
    loader.load_rosters(rosters, meta)
    loader.load_awards(awards, meta)

    report = loader.report.as_dict()
    report["scope"] = {
        "events": len(pages),
        "result_rows": len(results),
        "roster_rows": len(rosters),
        "award_rows": len(awards),
        "awards_out_of_window": len(real) - len(awards),
        "ranking_list_rows": len(ranking_lists),
    }
    return report
