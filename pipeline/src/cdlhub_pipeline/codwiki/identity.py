"""Resolve wiki player pages to player rows, or quarantine them.

Resolution tries four keys in order and stops at the first that names exactly
one player: the wiki page name, every name that redirects or renames to it,
every display name its own box scores use, and the wiki real name against
`players.real_name`. Two candidates means ambiguous, and an ambiguous page is
never merged.

A page that matches nothing is a player the database has never held. It earns a
new row when it appears at two or more events, and is quarantined otherwise.
`aliases.json` carries `codwiki_players`, which overrides any of this: a handle
resolves a page by hand, and null quarantines it by hand.

One page at a time is not enough. Three handles in this window belong to two or
three different people each, and the wiki says so by naming the pages `Realize
(Derrek Jordan)` and `Realize (Josh Taylor)`. Resolved one at a time, both land
on our single `Realize` row and one player's maps go onto the other's career
page. The last pass therefore looks at the whole map: where several pages claim
one player row and the wiki gives them different real names, only the page whose
real name is ours survives, and the rest are quarantined. Where none matches,
none survives.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, cast

import psycopg

from .client import SNAPSHOT_ROOT

MIN_EVENTS_FOR_NEW_PLAYER = 2
# `Realize (Josh Taylor)`: the wiki disambiguates a shared handle in parentheses.
DISAMBIGUATED = re.compile(r"\((.+)\)\s*$")


def _snapshot(name: str) -> Any:
    return json.loads((SNAPSHOT_ROOT / name).read_text())


def resolve(
    conn: psycopg.Connection[tuple[object, ...]],
    rows: list[dict[str, Any]],
    overrides: dict[str, str | None] | None = None,
    create: bool = True,
) -> tuple[dict[str, int], dict[str, Any]]:
    """Map PlayerLink to player id; returns the map and a report.

    With `create` false, a page that matches nothing is reported instead of
    earning a player row, so a caller that only reads can resolve the same way.
    """
    overrides = overrides or {}
    db_by_handle: dict[str, int] = {}
    db_by_real: dict[str, set[int]] = defaultdict(set)
    for dbrow in conn.execute("SELECT id, handle, real_name FROM players").fetchall():
        pid = cast(int, dbrow[0])
        handle = cast(str, dbrow[1])
        real = cast("str | None", dbrow[2])
        db_by_handle[handle.lower()] = pid
        if real:
            db_by_real[real.lower()].add(pid)

    wiki = {w["OverviewPage"]: w for w in _snapshot("players.json")}
    alias_of: dict[str, set[str]] = defaultdict(set)
    for redirect in _snapshot("playerredirects.json"):
        alias_of[redirect["OverviewPage"]].add(redirect["AllName"])
    for rename in _snapshot("playerrenames.json"):
        alias_of[rename["NewName"]].add(rename["OriginalName"])

    display: dict[str, set[str]] = defaultdict(set)
    events: dict[str, set[str]] = defaultdict(set)
    maps: dict[str, int] = defaultdict(int)
    for stat in rows:
        link = stat.get("PlayerLink")
        if not link:
            continue
        display[link].add(stat.get("PlayerName") or "")
        events[link].add(stat.get("TournamentPage") or "")
        maps[link] += 1

    player_ids: dict[str, int] = {}
    report: dict[str, Any] = {
        "resolved": 0,
        "created": 0,
        "ambiguous": [],
        "quarantined": [],
        "overridden": 0,
        "unshared": 0,
    }

    for link in sorted(maps):
        if link in overrides:
            chosen = overrides[link]
            report["overridden"] += 1
            if chosen is None:
                report["quarantined"].append({"page": link, "maps": maps[link], "why": "override"})
                continue
            player_ids[link] = db_by_handle[chosen.lower()]
            continue

        candidates = {
            db_by_handle[name.lower()]
            for name in {link, *alias_of.get(link, set()), *display[link]}
            if name and name.lower() in db_by_handle
        }
        real = (wiki.get(link, {}).get("NameFull") or "").lower()
        if len(candidates) != 1 and real:
            by_real = db_by_real.get(real, set())
            if len(by_real) == 1 or (by_real and not candidates):
                candidates = set(by_real)

        if len(candidates) == 1:
            player_ids[link] = candidates.pop()
            report["resolved"] += 1
        elif candidates:
            report["ambiguous"].append(
                {"page": link, "maps": maps[link], "candidates": sorted(candidates)}
            )
        elif len(events[link]) >= MIN_EVENTS_FOR_NEW_PLAYER and create:
            new_handle = wiki.get(link, {}).get("ID") or link
            inserted = conn.execute(
                "INSERT INTO players (handle) VALUES (%s) RETURNING id", (new_handle,)
            ).fetchone()
            assert inserted is not None
            player_ids[link] = cast(int, inserted[0])
            report["created"] += 1
        else:
            why = "no match" if len(events[link]) >= MIN_EVENTS_FOR_NEW_PLAYER else "single event"
            report["quarantined"].append({"page": link, "maps": maps[link], "why": why})

    _unshare(player_ids, wiki, conn, maps, report)
    return player_ids, report


def _unshare(
    player_ids: dict[str, int],
    wiki: dict[str, Any],
    conn: psycopg.Connection[tuple[object, ...]],
    maps: dict[str, int],
    report: dict[str, Any],
) -> None:
    """Undo every merge of two people onto one player row.

    Several pages may legitimately share a row: `ACHES` and `Aches` are one
    person spelled two ways. What is never legitimate is two pages the wiki
    gives different real names. Those groups are cut back to the one page whose
    real name is the one we hold, and cut to nothing when we hold none.
    """
    by_id: dict[int, list[str]] = defaultdict(list)
    for link, pid in player_ids.items():
        by_id[pid].append(link)

    for pid, links in sorted(by_id.items()):
        if len(links) < 2:
            continue
        named = {link: _real_name(link, wiki) for link in links}
        distinct = {name for name in named.values() if name}
        if len(distinct) < 2:
            continue  # one person, spelled several ways
        row = conn.execute("SELECT handle, real_name FROM players WHERE id = %s", (pid,)).fetchone()
        ours = ((cast("str | None", row[1]) if row else None) or "").lower()
        keep = {link for link, name in named.items() if ours and name == ours}
        for link in sorted(links):
            if link in keep:
                continue
            del player_ids[link]
            report["unshared"] += 1
            report["quarantined"].append(
                {
                    "page": link,
                    "maps": maps[link],
                    "why": "shares one player row with another person's page",
                    "player_id": pid,
                    "handle": str(row[0]) if row else None,
                    "kept": sorted(keep),
                }
            )


def _real_name(link: str, wiki: dict[str, Any]) -> str:
    """The person a wiki page names, folded for comparison.

    The `Players` table is the first answer. A page that has no row there is
    usually a redirect the box scores still use, and then the parenthetical is
    the only claim about who it is — which is exactly what it is there for.
    """
    entry = wiki.get(link)
    if entry and (entry.get("NameFull") or "").strip():
        return str(entry["NameFull"]).strip().lower()
    found = DISAMBIGUATED.search(link)
    return found.group(1).strip().lower() if found else ""
