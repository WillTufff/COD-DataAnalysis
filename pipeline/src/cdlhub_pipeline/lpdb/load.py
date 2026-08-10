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
            "teams_updated": [],
            "teams_region_from_lineage": [],
            "teams_without_lpdb_row": [],
            "events_enriched": {},
            "tournaments_unmatched": [],
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

    def load_placements(self, rows: list[dict[str, Any]]) -> None:
        self.conn.execute("DELETE FROM event_placements WHERE data_source = %s", (SOURCE,))

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
               individual_prize, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (event_id, team_id) DO UPDATE SET
              placement_min = EXCLUDED.placement_min,
              placement_max = EXCLUDED.placement_max,
              prize = EXCLUDED.prize,
              individual_prize = EXCLUDED.individual_prize,
              data_source = EXCLUDED.data_source
            """,
            (
                event_id,
                team_id,
                placement[0],
                placement[1],
                row.get("prizemoney") or None,
                row.get("individualprizemoney") or None,
                SOURCE,
            ),
        )
        self.counts["event_placements"] += 1

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
        for event_id, group in sorted(by_event.items()):
            # one page speaks for the event; sub-pages (stages, qualifiers)
            # carry partial prize pools, so the largest-pool page wins
            rep = max(group, key=lambda r: r.get("prizepool") or 0)
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
                  location = COALESCE(%s, location),
                  is_lan = COALESCE(%s, is_lan)
                WHERE id = %s
                """,
                (
                    rep.get("liquipediatier"),
                    rep.get("liquipediatiertype"),
                    rep.get("publishertier"),
                    rep.get("format"),
                    rep.get("prizepool") or None,
                    location,
                    {"Offline": True, "Online": False}.get(str(rep.get("type") or "")),
                    event_id,
                ),
            )
            self.report["events_enriched"][str(event_id)] = rep["pagename"]
            self.counts["events_enriched"] += 1

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
        by_handle: dict[str, dict[str, Any]] = {}
        for row in rows:
            for h in [row.get("id") or "", *(row.get("alternateid") or "").split(",")]:
                h = h.strip().lower()
                if h:
                    by_handle.setdefault(h, row)
        handles_by_pid: dict[int, list[str]] = defaultdict(list)
        for h, pid in sorted(self._players.items()):
            handles_by_pid[pid].append(h)
        unmapped: set[str] = set()
        for pid, handles in sorted(handles_by_pid.items()):
            bio = next((by_handle[h] for h in handles if h in by_handle), None)
            if bio is None:
                self.report["players_without_bio"].append(handles[0])
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
            self.conn.execute(
                "UPDATE players SET liquipedia_page = %s WHERE id = %s "
                "AND liquipedia_page IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM players WHERE liquipedia_page = %s)",
                (bio["pagename"], pid, bio["pagename"]),
            )
            self.report["bios_updated"].append(handles[0])
            self.counts["player_bios"] += 1
        self.report["unmapped_nationalities"] = sorted(unmapped)

    def cleanup_orphan_players(self) -> None:
        """Remove player rows nothing references: a stint-created player whose
        stints vanished after an alias/role-filter change would otherwise
        linger forever."""
        rows = self.conn.execute(
            """
            DELETE FROM players p
            WHERE NOT EXISTS (SELECT 1 FROM game_player_stats     x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM roster_stints         x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM transfers             x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM player_aliases        x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM kill_events           x
                              WHERE x.victim_id = p.id OR x.killer_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM career_curves         x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM player_metric_season  x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM player_rapm           x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM player_season_adjusted x WHERE x.player_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM player_style_season   x WHERE x.player_id = p.id)
            RETURNING p.handle
            """
        ).fetchall()
        self.report["orphan_players_removed"] = sorted(cast(str, r[0]) for r in rows)
        self.counts["orphan_players_removed"] = len(rows)
        for handle in self.report["orphan_players_removed"]:
            self._players.pop(handle.lower(), None)


def load(conn: psycopg.Connection[tuple[object, ...]]) -> tuple[dict[str, int], dict[str, Any]]:
    aliases = Aliases.load()
    loader = LpdbLoader(conn, aliases)
    loader.load_placements(json.loads(PLACEMENTS_PATH.read_text()))
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
