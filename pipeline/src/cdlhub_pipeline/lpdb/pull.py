"""Pull LPDB tables to disk.

Placements, tournaments, and matches are pulled per game code — the ten
CWL/CDL-era codes only, which keeps CODM and pre-2017 titles out — scoped to
the premier circuit via publishertier/liquipediatier. Teams, squad players,
transfers, and player bios are pulled whole; the loaders filter to modeled
teams/players, so the snapshots stay complete while the DB stays scoped.
Raw pages land in snapshots/lpdb/<table>/ via the client; consolidated row
lists land next to them for the loader.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .client import SNAPSHOT_ROOT, LpdbClient

# game code -> season label year (Nov/Dec starts belong to the next season)
GAME_SEASONS = {
    "iw": 2017,
    "wwii": 2018,
    "bo4": 2019,
    "mw2019": 2020,
    "bocw": 2021,
    "vg": 2022,
    "mwii": 2023,
    "mwiii": 2024,
    "bo6": 2025,
    "bo7": 2026,
}

PLACEMENT_QUERY = (
    "pagename,tournament,series,parent,startdate,date,placement,prizemoney,"
    "individualprizemoney,opponentname,opponenttemplate,liquipediatier,"
    "liquipediatiertype,publishertier,game,mode,type,objectname,extradata"
)

TEAM_QUERY = (
    "name,pagename,template,region,earnings,earningsbyyear,createdate,disbanddate,status,locations"
)

TOURNAMENT_QUERY = (
    "pagename,name,shortname,seriespage,game,type,organizers,startdate,enddate,"
    "sortdate,locations,prizepool,participantsnumber,liquipediatier,"
    "liquipediatiertype,publishertier,status,format,mode"
)

SQUADPLAYER_QUERY = (
    "pagename,id,link,name,nationality,role,position,type,status,joindate,"
    "leavedate,inactivedate,teamtemplate,newteam,newteamtemplate,extradata,objectname"
)

TRANSFER_QUERY = (
    "pagename,player,nationality,fromteam,toteam,fromteamtemplate,toteamtemplate,"
    "role1,role2,date,wholeteam,reference,extradata,objectname"
)

PLAYER_QUERY = (
    "pagename,id,alternateid,name,nationality,region,birthdate,deathdate,status,"
    "earnings,earningsbyyear,teampagename,links,extradata"
)

MATCH_QUERY = (
    "pagename,match2id,date,dateexact,finished,winner,walkover,resulttype,status,"
    "bestof,game,mode,type,liquipediatier,publishertier,tournament,parent,"
    "match2opponents,match2games,extradata"
)

PREMIER = "([[publishertier::true]] OR [[liquipediatier::1]])"

PLACEMENTS_PATH = SNAPSHOT_ROOT / "placements.json"
TEAMS_PATH = SNAPSHOT_ROOT / "teams.json"
TOURNAMENTS_PATH = SNAPSHOT_ROOT / "tournaments.json"
SQUADPLAYERS_PATH = SNAPSHOT_ROOT / "squadplayers.json"
TRANSFERS_PATH = SNAPSHOT_ROOT / "transfers.json"
PLAYERS_PATH = SNAPSHOT_ROOT / "players.json"
MATCHES_PATH = SNAPSHOT_ROOT / "matches.json"


def _per_game(
    client: LpdbClient, table: str, extra: str, query: str, order: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for game in GAME_SEASONS:
        conditions = f"[[game::{game}]] AND {PREMIER}"
        if extra:
            conditions = f"[[game::{game}]] AND {extra} AND {PREMIER}"
        page = client.get_all(table, conditions=conditions, query=query, order=order)
        print(f"{table} {game}: {len(page)} rows")
        rows.extend(page)
    return rows


def _whole(client: LpdbClient, table: str, query: str, order: str) -> list[dict[str, Any]]:
    rows = client.get_all(table, conditions=None, query=query, order=order)
    print(f"{table}: {len(rows)} rows")
    return rows


PULLS: dict[str, tuple[Path, Callable[[LpdbClient], list[dict[str, Any]]]]] = {
    "placements": (
        PLACEMENTS_PATH,
        lambda c: _per_game(
            c, "placement", "[[opponenttype::team]]", PLACEMENT_QUERY, "date ASC, objectname ASC"
        ),
    ),
    "teams": (TEAMS_PATH, lambda c: _whole(c, "team", TEAM_QUERY, "pagename ASC")),
    "tournaments": (
        TOURNAMENTS_PATH,
        lambda c: _per_game(c, "tournament", "", TOURNAMENT_QUERY, "pagename ASC"),
    ),
    "squadplayers": (
        SQUADPLAYERS_PATH,
        lambda c: _whole(c, "squadplayer", SQUADPLAYER_QUERY, "pagename ASC, objectname ASC"),
    ),
    "transfers": (
        TRANSFERS_PATH,
        lambda c: _whole(c, "transfer", TRANSFER_QUERY, "date ASC, objectname ASC"),
    ),
    "players": (PLAYERS_PATH, lambda c: _whole(c, "player", PLAYER_QUERY, "pagename ASC")),
    "matches": (
        MATCHES_PATH,
        lambda c: _per_game(c, "match", "", MATCH_QUERY, "date ASC, match2id ASC"),
    ),
}


def run(tables: list[str] | None = None) -> None:
    client = LpdbClient()
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    for name in tables or list(PULLS):
        path, fetch = PULLS[name]
        path.write_text(json.dumps(fetch(client), indent=1))
        print(f"wrote {path}")
    print(f"{client.calls} API calls")
