"""Load pulled LPDB snapshots into Postgres.

P2 quick wins (placements, team extras) plus the P4 structure/careers tables:
/tournament enriches existing events (prize pool, tier, format, location),
/squadplayer fills roster_stints, /transfer fills transfers, /player fills
bios on modeled players. Rows attach only to events that already exist
locally — an LPDB tournament with no local counterpart is reported or
explicitly skipped, never guessed at. Each run wipes and reloads all
lpdb-tagged rows (placements, stints, transfers), so alias edits converge
like the other loaders.

Event matching: alias map first (aliases.json lpdb_events, pagename -> local
event name, null = deliberately out of scope), then normalized-name equality
within the season the game code implies. Team matching: alias map
(lpdb_teams), then the dated cito_teams brand history — Liquipedia renames
team pages on rebrand, so old placements can carry a slot's CURRENT brand
("FaZe Vegas" winning a 2021 event) exactly like Cito does — then
case-insensitive name match. A still-unmatched team inside a matched event is
a real participant we lack, so it is created and reported.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime
from typing import Any, cast

import psycopg

from .. import venue
from ..identity import Aliases
from .pull import (
    GAME_SEASONS,
    MATCHES_PATH,
    PLACEMENTS_PATH,
    PLAYERS_PATH,
    SQUADPLAYERS_PATH,
    TEAMS_PATH,
    TOURNAMENTS_PATH,
    TRANSFERS_PATH,
)

SOURCE = "lpdb"

# LPDB nationalities are full names; players.country is ISO 3166-1 alpha-2.
# UK home nations fold onto GB. Unmapped values are reported, never guessed.
COUNTRY_CODES = {
    "united states": "US",
    "united kingdom": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "northern ireland": "GB",
    "canada": "CA",
    "france": "FR",
    "spain": "ES",
    "germany": "DE",
    "netherlands": "NL",
    "belgium": "BE",
    "ireland": "IE",
    "australia": "AU",
    "new zealand": "NZ",
    "denmark": "DK",
    "sweden": "SE",
    "norway": "NO",
    "finland": "FI",
    "italy": "IT",
    "portugal": "PT",
    "austria": "AT",
    "switzerland": "CH",
    "poland": "PL",
    "czechia": "CZ",
    "czech republic": "CZ",
    "saudi arabia": "SA",
    "kuwait": "KW",
    "united arab emirates": "AE",
    "qatar": "QA",
    "israel": "IL",
    "mexico": "MX",
    "brazil": "BR",
    "argentina": "AR",
    "chile": "CL",
    "colombia": "CO",
    "japan": "JP",
    "south korea": "KR",
    "china": "CN",
    "singapore": "SG",
    "malaysia": "MY",
    "india": "IN",
    "russia": "RU",
    "ukraine": "UA",
    "turkey": "TR",
    "egypt": "EG",
    "morocco": "MA",
    "south africa": "ZA",
    "iceland": "IS",
    "hungary": "HU",
    "romania": "RO",
    "greece": "GR",
    "slovakia": "SK",
    "croatia": "HR",
    "puerto rico": "PR",
    "bangladesh": "BD",
    "bulgaria": "BG",
    "ecuador": "EC",
    "indonesia": "ID",
    "myanmar": "MM",
    "pakistan": "PK",
    "philippines": "PH",
    "slovenia": "SI",
    "thailand": "TH",
}

# LPDB player statuses that mean "no longer competing".
INACTIVE_STATUSES = {"retired", "deceased", "banned"}

# Squad roles that are business/content positions, not competitive staff.
# Stints with these roles are skipped: they would otherwise create player
# rows for streamers and executives. Coaches/analysts/managers stay — the
# player model carries competitive staff.
NON_COMPETITIVE_ROLES = re.compile(
    r"content creator|streamer|owner|founder|ceo|coo|chairman|chief|"
    r"community manager|social media|host|caster|creator"
)


def move_season(d: date) -> int:
    """The season a roster move belongs to: Aug–Dec is the next season's
    offseason (rebrands and rostermania land before the Dec/Jan start)."""
    return d.year + 1 if d.month >= 8 else d.year


# Whole circuits we deliberately don't model (amateur/invitational/other):
# mirrors the Cito scope denylist. Row counts still show up in the report.
SKIP_PREFIXES = ("Call_of_Duty_Challengers/", "Call_of_Duty_King/", "Esports_World_Cup/")


# Pages that carry placements, model an event this project is in scope for, and
# have no local counterpart because no box-score source ever covered them.
# `events` is otherwise built by the sources that hold matches, so a tournament
# nobody recorded a map for is invisible - which in 2017 meant the whole Global
# Pro League and three of the four opens, while every 2018 and 2019 open is
# present. The list is written out rather than derived: a rule over tier and
# publisher tier also admits Challengers finals, regular-season standings pages
# and third-party invitationals, none of which the rest of the model carries.
# Names come from the /tournament row, so nothing here invents one.
# The value overrides the name: `None` takes the /tournament row's own name,
# which is what keeps this from inventing one. The CDL playoffs are the single
# exception, because six earlier seasons of it are called `CDL Championship`
# locally and the Cito loader finds an event by that name.
CATALOG_BACKFILL: dict[str, str | None] = {
    "Call_of_Duty_World_League/2017/Pro_League/Stage_1": None,
    "Call_of_Duty_World_League/2017/Pro_League/Stage_2": None,
    "Call_of_Duty_World_League/2017/Atlanta": None,
    "Call_of_Duty_World_League/2017/Dallas": None,
    "Call_of_Duty_World_League/2017/Anaheim": None,
    "Call_of_Duty_World_League/2017/Birmingham": None,
    "Call_of_Duty_World_League/2017/London": None,
    "Call_of_Duty_World_League/2017/Paris": None,
    "Call_of_Duty_World_League/2017/Sheffield": None,
    "Call_of_Duty_World_League/2017/Sydney": None,
    "Call_of_Duty_World_League/2017/Sydney/2": None,
    "Call_of_Duty_World_League/2019/Las_Vegas/Open": None,
    "Call_of_Duty_League/Season_7/Playoffs": "CDL Championship",
}

# `opponentplayers` keys: p1..pN name the finishing roster, p1dn..pNdn its
# display spellings.
_SLOT_KEY = re.compile(r"p\d+$")


# The award string carries the season in some years and not others -- 2022 says
# `CDL First All-Star Team`, 2023 says `CDL 2023 Team of The Year`, and the two
# name the same selection. Matching on the part that does not move is what folds
# them together. Anything unrecognised is stored as `unmapped` and reported, so
# a new award kind shows up as a name in the report and not as a silent bucket.
def award_kind(raw: str) -> str:
    """Fold an LPDB award string onto a normalized kind."""
    name = raw.strip()
    if "Second All-Star" in name:
        return "second_team"
    if "First All-Star" in name or "Team of The Year" in name:
        return "first_team"
    if name == "Rookie of the Year":
        return "roty"
    if name == "Regular Season MVP":
        return "rs_mvp"
    if name in ("MVP", "Tournament MVP"):
        return "event_mvp"
    if name == "FMVP":
        return "fmvp"
    if name == "Captain's MVP":
        return "captains_mvp"
    if name.startswith("Best ") and name.endswith("Player"):
        return "mode_best"
    return "unmapped"


_ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def normalize_event_name(name: str) -> str:
    """Fold LPDB and local event-name conventions onto one key."""
    s = name.lower()
    s = s.replace("call of duty world league", "cwl")
    s = s.replace("call of duty league", "cdl")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    tokens = []
    for tok in s.split():
        if re.fullmatch(r"(19|20)\d\d", tok):
            continue  # season/edition years never disambiguate within a season
        tokens.append(_ROMAN.get(tok, tok))
    return " ".join(tokens)


def parse_placement(raw: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d+)(?:-(\d+))?", raw.strip())
    if not m:
        return None
    lo = int(m.group(1))
    return lo, int(m.group(2)) if m.group(2) else lo


def _parse_date(raw: str | None) -> date | None:
    if not raw or raw.startswith(("0000", "1970-01-01")):
        return None
    return datetime.fromisoformat(raw).date()


class LpdbLoader:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]], aliases: Aliases):
        self.conn = conn
        self.aliases = aliases
        self.counts: dict[str, int] = defaultdict(int)
        self.report: dict[str, Any] = {
            "matched_events": {},
            "skipped_tournaments": [],
            "unmatched_tournaments": [],
            "teams_created": [],
            "unparseable_placements": [],
            "placement_non_team_rows": 0,
            "teams_updated": [],
            "teams_region_from_lineage": [],
            "teams_without_lpdb_row": [],
            "events_enriched": {},
            "tournaments_unmatched": [],
            "venue": [],
            "stints_loaded": 0,
            "stints_skipped": [],
            "stint_players_created": [],
            "transfers_loaded": 0,
            "transfers_unresolved": 0,
            "transfers_noncompetitive": 0,
            "orphan_players_removed": [],
            "bios_updated": [],
            "players_without_bio": [],
            "unmapped_nationalities": [],
            "awards_unresolved": [],
            "awards_unmapped": [],
            "awards_without_season": [],
            "catalog_events_created": [],
            "catalog_backfill_unavailable": [],
            "roster_unresolved": [],
            "roster_ambiguous_handles": [],
            "roster_slots_held_elsewhere": [],
        }
        self._seasons: dict[int, int] = {
            cast(int, year): cast(int, sid)
            for sid, year in self.conn.execute("SELECT id, year FROM seasons").fetchall()
        }
        # (season year, event name) -> id; season year -> [(name, start, end, id)]
        self._events: dict[tuple[int, str], int] = {}
        self._season_events: dict[int, list[tuple[str, date, date, int]]] = defaultdict(list)
        for eid, name, year, start, end in self.conn.execute(
            "SELECT e.id, e.name, se.year, e.start_date, e.end_date "
            "FROM events e JOIN seasons se ON se.id = e.season_id"
        ).fetchall():
            self._events[(cast(int, year), cast(str, name))] = cast(int, eid)
            self._season_events[cast(int, year)].append(
                (cast(str, name), cast(date, start), cast(date, end), cast(int, eid))
            )
        self._teams: dict[str, int] = {
            cast(str, name).lower(): cast(int, tid)
            for tid, name in self.conn.execute("SELECT id, name FROM teams").fetchall()
        }
        # brand name (any era) -> cito slug, for the dated-history correction
        self._brand_slugs: dict[str, str] = {}
        for slug, history in aliases.cito_teams.items():
            for entry in history:
                self._brand_slugs.setdefault(str(entry["name"]).lower(), slug)
        # lowercased handle (canonical or alias) -> player id
        self._players: dict[str, int] = {}
        for pid, alias in self.conn.execute(
            "SELECT player_id, alias FROM player_aliases"
        ).fetchall():
            self._players[cast(str, alias).lower()] = cast(int, pid)
        for pid, handle in self.conn.execute("SELECT id, handle FROM players").fetchall():
            self._players[cast(str, handle).lower()] = cast(int, pid)
        # Liquipedia separates two people by letter case alone, so a lowercased
        # index can hand one person another's identity. Roster slots resolve
        # through `player_slot`, which refuses these rather than picking one.
        claims: dict[str, set[int]] = defaultdict(set)
        for pid, spelling in self.conn.execute(
            "SELECT player_id, alias FROM player_aliases UNION ALL SELECT id, handle FROM players"
        ).fetchall():
            claims[cast(str, spelling).lower()].add(cast(int, pid))
        self._ambiguous_handles = {name for name, ids in claims.items() if len(ids) > 1}
        # A roster slot names a Liquipedia page, and a page belongs to one
        # person. It survives a rename the handle index cannot: `Scrappy` is
        # the page of the player this database calls `Scrap`.
        self._player_pages: dict[str, int] = {
            cast(str, page).lower(): cast(int, pid)
            for pid, page in self.conn.execute(
                "SELECT id, liquipedia_page FROM players WHERE liquipedia_page IS NOT NULL"
            ).fetchall()
        }

    def canonical_team(self, lpdb_name: str, season: int) -> str:
        """Resolve an LPDB opponent name to the brand it wore that season."""
        name = self.aliases.lpdb_teams.get(lpdb_name, lpdb_name)
        if season >= 2020:  # brand histories only cover the CDL franchise era
            slug = self._brand_slugs.get(name.lower())
            if slug:
                name = self.aliases.cito_team(slug, season) or name
        return self.aliases.team(name)

    def team_id(self, lpdb_name: str, season: int, create: bool = False) -> int | None:
        name = self.canonical_team(lpdb_name, season)
        tid = self._teams.get(name.lower())
        if tid is None and create:
            org = self.aliases.org_of(name)
            org_id = None
            if org:
                self.conn.execute(
                    "INSERT INTO orgs (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (org,)
                )
                row = self.conn.execute("SELECT id FROM orgs WHERE name = %s", (org,)).fetchone()
                assert row is not None
                org_id = cast(int, row[0])
            row = self.conn.execute(
                "INSERT INTO teams (name, org_id) VALUES (%s, %s) RETURNING id", (name, org_id)
            ).fetchone()
            assert row is not None
            tid = cast(int, row[0])
            self._teams[name.lower()] = tid
            self.report["teams_created"].append(name)
        return tid

    def match_event(self, pagename: str, tournament: str, season: int) -> int | None:
        if pagename in self.aliases.lpdb_events:
            alias = self.aliases.lpdb_events[pagename]
            return self._events.get((season, alias)) if alias else None
        wanted = normalize_event_name(tournament or pagename.replace("_", " "))
        hits = [
            eid
            for name, _, _, eid in self._season_events.get(season, [])
            if normalize_event_name(name) == wanted
        ]
        return hits[0] if len(hits) == 1 else None

    def _candidates(self, season: int, start: date | None, end: date | None) -> list[str]:
        """Local events overlapping the tournament's date window, for the report."""
        if end is None:
            return []
        lo = start or end
        return [
            name
            for name, e_start, e_end, _ in sorted(self._season_events.get(season, []))
            if e_start <= end and e_end >= lo
        ]

    def create_catalog_events(self, rows: list[dict[str, Any]]) -> None:
        """Create the events `CATALOG_BACKFILL` names, from their /tournament row.

        Runs before the placement load, and adds to the loader's own event
        index, so the placements find the event in the same pass. An event that
        already exists under its page or its name is left alone, which makes a
        rerun a no-op.
        """
        by_page = {row["pagename"]: row for row in rows}
        for pagename, override in CATALOG_BACKFILL.items():
            row = by_page.get(pagename)
            if row is None:
                self.report["catalog_backfill_unavailable"].append(
                    {"pagename": pagename, "reason": "no tournament row"}
                )
                continue
            season = GAME_SEASONS[row["game"]]
            season_id = self._seasons.get(season)
            name = override or str(row.get("name") or "").strip()
            start, end = _parse_date(row.get("startdate")), _parse_date(row.get("enddate"))
            if season_id is None or not name or start is None or end is None:
                self.report["catalog_backfill_unavailable"].append(
                    {"pagename": pagename, "reason": "incomplete tournament row"}
                )
                continue
            held = self.conn.execute(
                "SELECT id FROM events WHERE liquipedia_page = %s", (pagename,)
            ).fetchone()
            if held is not None or (season, name) in self._events:
                continue
            created = self.conn.execute(
                "INSERT INTO events (season_id, name, start_date, end_date, liquipedia_page) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                (season_id, name, start, end, pagename),
            ).fetchone()
            assert created is not None
            event_id = cast(int, created[0])
            self._events[(season, name)] = event_id
            self._season_events[season].append((name, start, end, event_id))
            self.report["catalog_events_created"].append(
                {"pagename": pagename, "name": name, "season": season, "event_id": event_id}
            )
            self.counts["catalog_events_created"] += 1

    def player_slot(self, slot: str) -> int | None:
        """A roster slot's player, or None. Never creates a player row.

        The page is asked first, because it identifies a person where a handle
        identifies only a spelling. The handle index answers for the players
        whose bio never attached, and the alias map for the spellings only this
        source uses.
        """
        page = self._player_pages.get(slot.lower())
        if page is not None:
            return page
        key = self.aliases.player(slot, SOURCE).lower()
        if key in self._ambiguous_handles:
            self.report["roster_ambiguous_handles"].append(slot)
            return None
        return self._players.get(key)

    def load_placements(self, rows: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM event_placements WHERE data_source = %s", (SOURCE,))
        self.conn.execute("DELETE FROM event_rosters WHERE data_source = %s", (SOURCE,))

        by_page: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_page[row["pagename"]].append(row)

        event_pages: dict[int, set[str]] = defaultdict(set)
        for pagename, page_rows in sorted(by_page.items()):
            first = page_rows[0]
            season = GAME_SEASONS[first["game"]]
            if pagename.startswith(SKIP_PREFIXES) or (
                pagename in self.aliases.lpdb_events and self.aliases.lpdb_events[pagename] is None
            ):
                self.report["skipped_tournaments"].append(
                    {"pagename": pagename, "rows": len(page_rows)}
                )
                continue
            event_id = self.match_event(pagename, first.get("tournament") or "", season)
            if event_id is None:
                self.report["unmatched_tournaments"].append(
                    {
                        "pagename": pagename,
                        "tournament": first.get("tournament"),
                        "season": season,
                        "date": first.get("date"),
                        "rows": len(page_rows),
                        "local_candidates": self._candidates(
                            season,
                            _parse_date(first.get("startdate")),
                            _parse_date(first.get("date")),
                        ),
                    }
                )
                continue
            event_pages[event_id].add(pagename)
            self.report["matched_events"][pagename] = event_id
            for row in page_rows:
                self._load_placement(event_id, season, row)

        for event_id, pages in event_pages.items():
            page = next(iter(pages))
            first = by_page[page][0]
            self.conn.execute(
                "UPDATE events SET tier = COALESCE(tier, %s), "
                "publisher_tier = COALESCE(publisher_tier, %s) WHERE id = %s",
                (first.get("liquipediatier") or None, first.get("publishertier") or None, event_id),
            )
            if len(pages) == 1:
                # only an unambiguous 1:1 mapping may claim the unique page column
                self.conn.execute(
                    "UPDATE events SET liquipedia_page = %s WHERE id = %s "
                    "AND liquipedia_page IS NULL "
                    "AND NOT EXISTS (SELECT 1 FROM events WHERE liquipedia_page = %s)",
                    (page, event_id, page),
                )

    def _load_placement(self, event_id: int, season: int, row: dict[str, Any]) -> None:
        if row["opponentname"] in ("", "TBD"):  # unresolved bracket slots
            return
        # The pull no longer filters `opponenttype`, so individual awards and
        # solo finishes arrive alongside team placements. `event_placements` is
        # keyed on a team, and `team_id(create=True)` would mint a team named
        # after a player, so the filter moves here rather than disappearing.
        if str(row.get("opponenttype") or "team") != "team":
            self.report["placement_non_team_rows"] += 1
            return
        placement = parse_placement(row.get("placement") or "")
        if placement is None:
            if row.get("placement"):  # empty = participant-only row, not worth noise
                self.report["unparseable_placements"].append(
                    {
                        "pagename": row["pagename"],
                        "team": row["opponentname"],
                        "placement": row["placement"],
                    }
                )
            return
        team_id = self.team_id(row["opponentname"], season, create=True)
        assert team_id is not None
        self.conn.execute(
            """
            INSERT INTO event_placements
              (event_id, team_id, placement_min, placement_max, prize,
               individual_prize, lpdb_weight, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, team_id) DO UPDATE SET
              placement_min = EXCLUDED.placement_min,
              placement_max = EXCLUDED.placement_max,
              prize = EXCLUDED.prize,
              individual_prize = EXCLUDED.individual_prize,
              lpdb_weight = EXCLUDED.lpdb_weight,
              data_source = EXCLUDED.data_source
            """,
            (
                event_id,
                team_id,
                placement[0],
                placement[1],
                row.get("prizemoney") or None,
                row.get("individualprizemoney") or None,
                row.get("weight") or None,
                SOURCE,
            ),
        )
        self.counts["event_placements"] += 1
        self._load_roster(event_id, team_id, row)

    def _load_roster(self, event_id: int, team_id: int, row: dict[str, Any]) -> None:
        """The finishing roster behind one placement, as `opponentplayers` gives it.

        A handle with no player row is reported and skipped. Creating one here
        would mint a player out of a name on a bracket, and the resume work
        needs the opposite: a slot nobody can answer for has to stay visible.
        """
        for key, value in sorted((row.get("opponentplayers") or {}).items()):
            if not _SLOT_KEY.fullmatch(key):
                continue
            handle = str(value or "").strip()
            if not handle:
                continue
            self.counts["roster_slots"] += 1
            player_id = self.player_slot(handle)
            if player_id is None:
                self.report["roster_unresolved"].append(
                    {
                        "pagename": row["pagename"],
                        "team": row.get("opponentname"),
                        "handle": handle,
                    }
                )
                continue
            written = self.conn.execute(
                "INSERT INTO event_rosters (event_id, team_id, player_id, data_source) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING player_id",
                (event_id, team_id, player_id, SOURCE),
            ).fetchone()
            if written is None:
                self.report["roster_slots_held_elsewhere"].append(
                    {"event_id": event_id, "handle": handle}
                )
                continue
            self.counts["event_rosters"] += 1

    def load_awards(self, rows: list[dict[str, Any]]) -> None:
        """Individual awards, keyed on the season the game code implies.

        These arrive in the placements payload and never reach
        `_load_placement`, which drops them at its non-team guard. They are
        loaded here instead, on the season rather than the event: six of the
        seven CDL regular-season pages have no local counterpart, so an
        event-keyed load would keep one season of selections out of seven.
        """
        self.conn.execute("DELETE FROM player_awards WHERE data_source = %s", (SOURCE,))
        for row in rows:
            if str(row.get("mode")) != "award_individual":
                continue
            handle = str(row.get("opponentname") or "").strip()
            if handle in ("", "TBD", "Tbd"):
                continue
            season = GAME_SEASONS[row["game"]]
            season_id = self._seasons.get(season)
            if season_id is None:
                self.report["awards_without_season"].append({"handle": handle, "season": season})
                continue
            raw = str((row.get("extradata") or {}).get("award") or "")
            kind = award_kind(raw)
            if kind == "unmapped":
                self.report["awards_unmapped"].append(raw)
            pagename = str(row["pagename"])
            player_id = self.player_id(handle)
            if player_id is None:
                self.report["awards_unresolved"].append({"handle": handle, "award": raw})
            self.conn.execute(
                """
                INSERT INTO player_awards
                  (pagename, raw_award, handle, award, season_id, player_id, event_id,
                   awarded_on, data_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (pagename, raw_award, handle) DO UPDATE SET
                  award = EXCLUDED.award,
                  season_id = EXCLUDED.season_id,
                  player_id = EXCLUDED.player_id,
                  event_id = EXCLUDED.event_id,
                  awarded_on = EXCLUDED.awarded_on,
                  data_source = EXCLUDED.data_source
                """,
                (
                    pagename,
                    raw,
                    handle,
                    kind,
                    season_id,
                    player_id,
                    self.match_event(pagename, str(row.get("tournament") or ""), season),
                    _parse_date(row.get("date")),
                    SOURCE,
                ),
            )
            self.counts["player_awards"] += 1

    def load_teams(self, rows: list[dict[str, Any]]) -> None:
        by_name: dict[str, dict[str, Any]] = {}
        for row in rows:
            if "/" in cast(str, row["pagename"]):
                continue  # Warzone/Mobile sub-rosters share the org's name
            by_name.setdefault(cast(str, row["name"]).lower(), row)
        reverse_alias = {
            canonical.lower(): lpdb.lower() for lpdb, canonical in self.aliases.lpdb_teams.items()
        }
        for name_lower, team_id in sorted(self._teams.items()):
            match = by_name.get(reverse_alias.get(name_lower, name_lower))
            if match is not None:
                self.conn.execute(
                    """
                    UPDATE teams SET
                      region = COALESCE(NULLIF(%s, ''), region),
                      earnings = %s,
                      create_date = %s,
                      disband_date = %s,
                      liquipedia_page = COALESCE(liquipedia_page, %s)
                    WHERE id = %s
                    """,
                    (
                        match.get("region"),
                        match.get("earnings"),
                        _parse_date(match.get("createdate")),
                        _parse_date(match.get("disbanddate")),
                        match.get("pagename"),
                        team_id,
                    ),
                )
                self.report["teams_updated"].append(match["name"])
                self.counts["teams_updated"] += 1
                continue
            # Liquipedia renamed the page on rebrand: the lineage row (under the
            # slot's current brand) still carries the region, which is
            # brand-invariant. Earnings/dates are lineage-scoped, so only
            # region transfers.
            slug = self._brand_slugs.get(name_lower)
            lineage = self.aliases.cito_team(slug, 2026) if slug else None
            lineage_row = by_name.get(lineage.lower()) if lineage else None
            if lineage_row is not None and lineage_row.get("region"):
                self.conn.execute(
                    "UPDATE teams SET region = COALESCE(region, %s) WHERE id = %s",
                    (lineage_row["region"], team_id),
                )
                self.report["teams_region_from_lineage"].append(name_lower)
                self.counts["teams_region_updated"] += 1
            else:
                self.report["teams_without_lpdb_row"].append(name_lower)

    def player_id(self, handle: str, create: bool = False) -> int | None:
        canonical = self.aliases.player(handle)
        if not canonical:
            return None
        pid = self._players.get(canonical.lower())
        if pid is None and create:
            row = self.conn.execute(
                "INSERT INTO players (handle) VALUES (%s) RETURNING id", (canonical,)
            ).fetchone()
            assert row is not None
            pid = cast(int, row[0])
            self._players[canonical.lower()] = pid
            self.report["stint_players_created"].append(canonical)
        return pid

    def load_events_extras(self, rows: list[dict[str, Any]]) -> None:
        pages: dict[str, int] = {
            cast(str, page): cast(int, eid)
            for eid, page in self.conn.execute(
                "SELECT id, liquipedia_page FROM events WHERE liquipedia_page IS NOT NULL"
            ).fetchall()
        }
        by_event: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            pagename = row["pagename"]
            season = GAME_SEASONS[row["game"]]
            if pagename.startswith(SKIP_PREFIXES) or (
                pagename in self.aliases.lpdb_events and self.aliases.lpdb_events[pagename] is None
            ):
                continue
            event_id = pages.get(pagename) or self.match_event(
                pagename, row.get("name") or "", season
            )
            if event_id is None:
                self.report["tournaments_unmatched"].append(
                    {"pagename": pagename, "name": row.get("name"), "season": season}
                )
                continue
            by_event[event_id].append(row)
        lpdb_types: dict[int, str | None] = {}
        for event_id, group in sorted(by_event.items()):
            # one page speaks for the event; sub-pages (stages, qualifiers)
            # carry partial prize pools, so the largest-pool page wins
            rep = max(group, key=lambda r: r.get("prizepool") or 0)
            lpdb_types[event_id] = rep.get("type")
            locations = rep.get("locations") or {}
            parts = [locations.get("city1") or "", locations.get("country1") or ""]
            location = ", ".join(p for p in parts if p) or None
            if location and location.lower() == "online":
                location = None
            self.conn.execute(
                """
                UPDATE events SET
                  tier = COALESCE(NULLIF(%s, ''), tier),
                  tier_type = COALESCE(NULLIF(%s, ''), tier_type),
                  publisher_tier = COALESCE(NULLIF(%s, ''), publisher_tier),
                  format = COALESCE(NULLIF(%s, ''), format),
                  prize_pool = COALESCE(%s, prize_pool),
                  location = COALESCE(%s, location)
                WHERE id = %s
                """,
                (
                    rep.get("liquipediatier"),
                    rep.get("liquipediatiertype"),
                    rep.get("publishertier"),
                    rep.get("format"),
                    rep.get("prizepool") or None,
                    location,
                    event_id,
                ),
            )
            self.report["events_enriched"][str(event_id)] = rep["pagename"]
            self.counts["events_enriched"] += 1

        self.apply_venue(lpdb_types)

    def apply_venue(self, lpdb_types: dict[int, str | None]) -> None:
        """Set `is_lan` on every event from the stated derivation (venue.py).

        Runs over all events, not only the ones LPDB matched, because the events
        that most need a verdict are the ones with no tournament page — the nine
        CWL opens whose flag was the archive importer's default. The verdict is
        written whole, undecided included: an event that stops being decidable
        loses its flag rather than keeping what a loader once put there.
        """
        rules = venue.VenueRules.load()
        events = self.conn.execute(
            "SELECT e.id, se.year, e.name FROM events e "
            "LEFT JOIN seasons se ON se.id = e.season_id ORDER BY se.year, e.name"
        ).fetchall()
        for row in events:
            event_id, season_year, event_name = (
                cast(int, row[0]),
                cast("int | None", row[1]),
                cast(str, row[2]),
            )
            verdict = venue.derive(rules, season_year, event_name, lpdb_types.get(event_id))
            self.conn.execute(
                "UPDATE events SET is_lan = %s WHERE id = %s", (verdict.is_lan, event_id)
            )
            self.report["venue"].append(
                {
                    "season": season_year,
                    "event": event_name,
                    "is_lan": verdict.is_lan,
                    "source": verdict.source,
                    "reviewed": verdict.reviewed,
                    "reason": verdict.reason,
                }
            )
            self.counts["events_venue_set"] += 1

    def load_roster_stints(self, rows: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM roster_stints WHERE source = %s", (SOURCE,))
        # Squad pages live under the slot's CURRENT brand; each stint is
        # re-branded to the name the team wore when the player joined.
        team_pages: dict[str, str] = {
            cast(str, page): cast(str, name)
            for name, page in self.conn.execute(
                "SELECT name, liquipedia_page FROM teams WHERE liquipedia_page IS NOT NULL"
            ).fetchall()
        }
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            page_team = team_pages.get(row["pagename"])
            if page_team is None or not row.get("id"):
                continue
            join = _parse_date(row.get("joindate"))
            if join is None:
                self.report["stints_skipped"].append(
                    {"page": row["pagename"], "id": row["id"], "reason": "no joindate"}
                )
                continue
            leave = _parse_date(row.get("leavedate"))
            role = (
                row.get("role")
                or row.get("position")
                or ("Staff" if row.get("type") == "staff" else None)
            )
            if role and NON_COMPETITIVE_ROLES.search(role.lower()):
                self.report["stints_skipped"].append(
                    {"page": row["pagename"], "id": row["id"], "reason": f"role: {role}"}
                )
                continue
            team_id = self.team_id(page_team, move_season(join), create=True)
            assert team_id is not None
            pid = self.player_id(row["id"], create=True)
            key = (pid, team_id, join, leave, role)
            if key in seen:
                continue
            seen.add(key)
            self.conn.execute(
                "INSERT INTO roster_stints "
                "(player_id, team_id, role, start_date, end_date, source) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (pid, team_id, role, join, leave, SOURCE),
            )
            self.counts["roster_stints"] += 1
            self.report["stints_loaded"] += 1

    def load_transfers(self, rows: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM transfers WHERE data_source = %s", (SOURCE,))
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            pid = self.player_id(row.get("player") or "")
            if pid is None:
                continue
            when = _parse_date((row.get("date") or "").split(" ")[0] or None)
            if when is None:
                continue
            season = move_season(when)
            # a named side that cannot be resolved to a modeled team would be
            # indistinguishable from free agency, so the row is dropped instead
            resolved: dict[str, int | None] = {}
            unresolved = False
            for side in ("fromteam", "toteam"):
                name = row.get(side) or ""
                resolved[side] = self.team_id(name, season) if name else None
                if name and resolved[side] is None:
                    unresolved = True
            if unresolved or (resolved["fromteam"] is None and resolved["toteam"] is None):
                self.report["transfers_unresolved"] += 1
                continue
            role = row.get("role2") or row.get("role1") or None
            if role and NON_COMPETITIVE_ROLES.search(role.lower()):
                self.report["transfers_noncompetitive"] += 1
                continue
            key = (pid, when, resolved["fromteam"], resolved["toteam"], role)
            if key in seen:
                continue
            seen.add(key)
            extradata = row.get("extradata") or {}
            reference = row.get("reference") or {}
            self.conn.execute(
                "INSERT INTO transfers (player_id, transfer_date, from_team_id, "
                "to_team_id, role, platform, reference, data_source) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    pid,
                    when,
                    resolved["fromteam"],
                    resolved["toteam"],
                    role,
                    extradata.get("platform") or None,
                    reference.get("reference1") or None,
                    SOURCE,
                ),
            )
            self.counts["transfers"] += 1
            self.report["transfers_loaded"] += 1

    def load_player_bios(self, rows: list[dict[str, Any]]) -> None:
        """Bind each player to at most one Liquipedia biography.

        Liquipedia tells two people apart by the case of one letter — `Methodz`
        is Anthony Zinni and `MethodZ` is Jorge Bancells — so a folded index
        collapses them and hands one player the other's name, country and
        birthdate. 47 handles in this pull fold together. The match is therefore
        by exact spelling first; a folded match binds only where it is
        unambiguous, and an ambiguous handle binds nothing until `player_pages`
        pins it. A pinned `null` binds nothing on purpose.
        """
        exact, folded = self._bio_index(rows)
        handles_by_pid = self._db_handles()
        unmapped: set[str] = set()
        for pid, handles in sorted(handles_by_pid.items()):
            bio, why = self._bio_for(handles, exact, folded)
            if bio is None:
                self.report["players_without_bio"].append(handles[0])
                if why:
                    self.report.setdefault("bios_not_bound", []).append(f"{handles[0]}: {why}")
                continue
            nationality = (bio.get("nationality") or "").lower()
            country = COUNTRY_CODES.get(nationality)
            if nationality and country is None:
                unmapped.add(cast(str, bio["nationality"]))
            eby = bio.get("earningsbyyear") or None
            self.conn.execute(
                """
                UPDATE players SET
                  real_name = COALESCE(NULLIF(%s, ''), real_name),
                  country = COALESCE(%s, country),
                  birthdate = COALESCE(%s, birthdate),
                  earnings = %s,
                  earnings_by_year = %s,
                  is_active = %s
                WHERE id = %s
                """,
                (
                    bio.get("name"),
                    country,
                    _parse_date(bio.get("birthdate")),
                    bio.get("earnings") or None,
                    json.dumps(eby) if eby else None,
                    (bio.get("status") or "").lower() not in INACTIVE_STATUSES,
                    pid,
                ),
            )
            override = self.aliases.real_name(handles[0])
            if override:
                self.conn.execute(
                    "UPDATE players SET real_name = %s WHERE id = %s", (override, pid)
                )
            self.conn.execute(
                "UPDATE players SET liquipedia_page = %s WHERE id = %s "
                "AND liquipedia_page IS DISTINCT FROM %s "
                "AND NOT EXISTS (SELECT 1 FROM players WHERE liquipedia_page = %s)",
                (bio["pagename"], pid, bio["pagename"], bio["pagename"]),
            )
            self.report["bios_updated"].append(handles[0])
            self.counts["player_bios"] += 1
        self.report["unmapped_nationalities"] = sorted(unmapped)

    def _bio_index(
        self, rows: list[dict[str, Any]]
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
        """Biographies by exact spelling, and by folded spelling for fallback."""
        exact: dict[str, dict[str, Any]] = {}
        folded: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            for h in [row.get("id") or "", *(row.get("alternateid") or "").split(",")]:
                h = h.strip()
                if not h:
                    continue
                exact.setdefault(h, row)
                folded[h.lower()].append(row)
        return exact, folded

    def _db_handles(self) -> dict[int, list[str]]:
        """Every spelling a player is known by here, their own handle first."""
        out: dict[int, list[str]] = defaultdict(list)
        for pid, handle in self.conn.execute("SELECT id, handle FROM players").fetchall():
            out[cast(int, pid)].append(cast(str, handle))
        for pid, alias in self.conn.execute(
            "SELECT player_id, alias FROM player_aliases ORDER BY alias"
        ).fetchall():
            if cast(str, alias) not in out[cast(int, pid)]:
                out[cast(int, pid)].append(cast(str, alias))
        return out

    def _bio_for(
        self,
        handles: list[str],
        exact: dict[str, dict[str, Any]],
        folded: dict[str, list[dict[str, Any]]],
    ) -> tuple[dict[str, Any] | None, str]:
        """The one biography this player may take, and why it took none."""
        pinned, page = self.aliases.page_pin(handles[0])
        if pinned:
            if page is None:
                return None, "quarantined: no page may supply this handle"
            found = next(
                (bio for bio in folded.get(page.lower(), []) if bio.get("pagename") == page), None
            )
            return (found, "") if found else (None, f"pinned to '{page}', which this pull has no")
        for handle in handles:
            if handle in exact:
                return exact[handle], ""
        for handle in handles:
            candidates = folded.get(handle.lower(), [])
            pages = {str(bio.get("pagename")) for bio in candidates}
            if len(pages) == 1:
                return candidates[0], ""
            if len(pages) > 1:
                return None, f"'{handle}' matches {len(pages)} pages: {', '.join(sorted(pages))}"
        return None, ""

    def cleanup_orphan_players(self) -> None:
        """Remove player rows nothing references: a stint-created player whose
        stints vanished after an alias/role-filter change would otherwise
        linger forever.

        The tables to check are read from the foreign keys rather than listed.
        A hand-written list drifts: `event_rosters` arrived with the wiki load
        and was not in it, so this deleted a player the new table still
        referenced and the whole load failed on the constraint.
        """
        references = self.conn.execute(_REFERENCES_PLAYERS_SQL).fetchall()
        clauses = " ".join(
            f"AND NOT EXISTS (SELECT 1 FROM {cast(str, table)} x "
            f"WHERE x.{cast(str, column)} = p.id)"
            for table, column in references
        )
        rows = self.conn.execute(
            f"DELETE FROM players p WHERE true {clauses} RETURNING p.handle"
        ).fetchall()
        self.report["orphan_players_removed"] = sorted(cast(str, r[0]) for r in rows)
        self.counts["orphan_players_removed"] = len(rows)
        for handle in self.report["orphan_players_removed"]:
            self._players.pop(handle.lower(), None)


# Every column that points at `players`, so a new table cannot be forgotten.
_REFERENCES_PLAYERS_SQL = """
SELECT c.conrelid::regclass::text AS table_name,
       a.attname                  AS column_name
FROM pg_constraint c
JOIN unnest(c.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
WHERE c.contype = 'f' AND c.confrelid = 'players'::regclass
ORDER BY 1, 2
"""


def load(conn: psycopg.Connection[tuple[object, ...]]) -> tuple[dict[str, int], dict[str, Any]]:
    aliases = Aliases.load()
    loader = LpdbLoader(conn, aliases)
    placements = json.loads(PLACEMENTS_PATH.read_text())
    if TOURNAMENTS_PATH.exists():
        loader.create_catalog_events(json.loads(TOURNAMENTS_PATH.read_text()))
    loader.load_placements(placements)
    loader.load_awards(placements)
    loader.load_teams(json.loads(TEAMS_PATH.read_text()))
    if TOURNAMENTS_PATH.exists():
        loader.load_events_extras(json.loads(TOURNAMENTS_PATH.read_text()))
    if SQUADPLAYERS_PATH.exists():
        loader.load_roster_stints(json.loads(SQUADPLAYERS_PATH.read_text()))
    if TRANSFERS_PATH.exists():
        loader.load_transfers(json.loads(TRANSFERS_PATH.read_text()))
    if PLAYERS_PATH.exists():
        loader.load_player_bios(json.loads(PLAYERS_PATH.read_text()))
    loader.cleanup_orphan_players()
    if MATCHES_PATH.exists():
        from .series_fix import SeriesFixer  # deferred: series_fix imports this module

        fixer = SeriesFixer(conn, loader)
        fixer.run(json.loads(MATCHES_PATH.read_text()))
        for key, value in sorted(fixer.counts.items()):
            loader.counts[f"fix_{key}"] = value
        loader.report["series_fix"] = fixer.report
    return dict(sorted(loader.counts.items())), loader.report
