"""Pull Cargo tables from the Call of Duty Esports Wiki to disk.

Field lists are read from the wiki with action=cargofields, never hard-coded, so
a renamed column fails the pull rather than silently dropping a value. Reference
tables page by offset. `PlayerStats` pages by `TournamentPage` instead, because
a deep offset over 232,880 rows is slow and shifts between calls; one event is a
few hundred to a few thousand rows. Every finished table and every finished
event is recorded in `pull-state.json`, so an interrupted pull restarts where it
stopped.

Two windows are pulled separately. `playerstats` covers 2013 through 2016, the
years the database holds no row for, and it is the only window that loads.
`overlap` covers 2017 onward, which `cwl_archive` and `cito` already carry; it
is pulled for the reconciliation check and is never loaded.

Raw pages land in snapshots/codwiki/<table>/; consolidated row lists land next
to them for the loader.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .client import SNAPSHOT_ROOT, CodWikiClient, RateLimited, read_state, write_state

# The owner set both floors at 2013: box scores and results start together.
SCOPE_START = "2013-01-01"
# The load window ends where cwl_archive begins.
LOAD_END = "2017-01-01"

# Reference tables, pulled whole under the scope floor. The order_by field is
# unique per table so offset pages do not shift between calls.
REFERENCE_TABLES: dict[str, dict[str, Any]] = {
    "Tournaments": {"where": f"Date >= '{SCOPE_START}'", "order_by": "OverviewPage"},
    "Players": {"where": None, "order_by": "OverviewPage"},
    "PlayerRenames": {"where": None, "order_by": "_pageName,OriginalName"},
    "PlayerRedirects": {"where": None, "order_by": "AllName"},
    "Awards": {"where": None, "order_by": "_pageID,PlayerName"},
    "TournamentResults": {"where": f"Date >= '{SCOPE_START}'", "order_by": "UniqueLine"},
    "TournamentRosters": {"where": None, "order_by": "UniqueLine"},
}

AWARD_TYPES_PATH = SNAPSHOT_ROOT / "awards-types.json"

# One page list and one row list per window.
WINDOWS: dict[str, tuple[str, str | None]] = {
    "playerstats": (SCOPE_START, LOAD_END),
    "overlap": (LOAD_END, None),
}


def _pages_path(window: str) -> Any:
    return SNAPSHOT_ROOT / f"tournament-pages-{window}.json"


def _banner(stage: str) -> None:
    print(f"== {stage} ==", flush=True)


def pull_reference(client: CodWikiClient, tables: list[str] | None = None) -> None:
    """Pull each reference table whole and write one consolidated row list."""
    state = read_state()
    for table in tables or list(REFERENCE_TABLES):
        _banner(table.lower())
        spec = REFERENCE_TABLES[table]
        fields = ",".join(client.table_fields(table))
        rows = client.query_all(
            table,
            fields=fields,
            where=spec["where"],
            order_by=spec["order_by"],
        )
        path = SNAPSHOT_ROOT / f"{table.lower()}.json"
        path.write_text(json.dumps(rows, indent=1))
        state["done"][f"reference:{table}"] = len(rows)
        write_state(state)
        print(f"  {table}: {len(rows)} rows -> {path}", flush=True)


def inventory_awards(client: CodWikiClient) -> list[dict[str, Any]]:
    """Count Awards rows per Type so ranking lists can be told from awards."""
    _banner("awards inventory")
    rows = client.query_all(
        "Awards",
        fields="Type,COUNT(*)=n",
        group_by="Type",
        order_by="Type",
    )
    AWARD_TYPES_PATH.parent.mkdir(parents=True, exist_ok=True)
    AWARD_TYPES_PATH.write_text(json.dumps(rows, indent=1))
    print(f"  Awards: {len(rows)} distinct types -> {AWARD_TYPES_PATH}", flush=True)
    return rows


def tournament_pages(client: CodWikiClient, window: str) -> list[str]:
    """List the TournamentPage values carrying PlayerStats rows in a window."""
    _banner(f"{window} tournament list")
    start, end = WINDOWS[window]
    where = f"Date >= '{start}'"
    if end:
        where += f" AND Date < '{end}'"
    rows = client.query_all(
        "PlayerStats",
        fields="TournamentPage,COUNT(*)=n,MIN(Date)=first_date",
        where=where,
        group_by="TournamentPage",
        order_by="TournamentPage",
    )
    path = _pages_path(window)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=1))
    total = sum(int(row["n"]) for row in rows)
    print(f"  {window}: {len(rows)} tournament pages, {total} rows -> {path}", flush=True)
    return [row["TournamentPage"] for row in rows]


def pull_playerstats(client: CodWikiClient, window: str) -> None:
    """Pull PlayerStats one tournament at a time, resuming from pull-state."""
    path = _pages_path(window)
    if path.exists():
        pages = [row["TournamentPage"] for row in json.loads(path.read_text())]
    else:
        pages = tournament_pages(client, window)
    _banner(f"{window} box scores")
    state = read_state()
    skipped: dict[str, str] = state.setdefault("skipped", {})
    fields = ",".join(client.table_fields("PlayerStats"))
    out = SNAPSHOT_ROOT / "PlayerStats" / window
    out.mkdir(parents=True, exist_ok=True)
    for index, page in enumerate(pages, start=1):
        key = f"playerstats:{window}:{page}"
        if key in state["done"]:
            continue
        escaped = page.replace("'", "''")
        try:
            rows = client.query_all(
                "PlayerStats",
                fields=fields,
                where=f"TournamentPage = '{escaped}'",
                order_by="UniqueLine",
            )
        except RateLimited as exc:
            skipped[page] = str(exc)
            write_state(state)
            print(f"  skipped {page}: rate limit", flush=True)
            continue
        digest = hashlib.sha1(page.encode()).hexdigest()[:12]
        (out / f"{digest}.json").write_text(
            json.dumps({"tournament": page, "rows": rows}, indent=1)
        )
        state["done"][key] = len(rows)
        write_state(state)
        print(f"  [{index}/{len(pages)}] {page}: {len(rows)} rows", flush=True)


def consolidate_playerstats(window: str) -> int:
    """Merge the per-tournament files of one window into a single row list."""
    _banner(f"{window} consolidate")
    out = SNAPSHOT_ROOT / "PlayerStats" / window
    rows: list[dict[str, Any]] = []
    for path in sorted(out.glob("*.json")):
        rows.extend(json.loads(path.read_text())["rows"])
    target = SNAPSHOT_ROOT / f"playerstats-{window}.json"
    target.write_text(json.dumps(rows, indent=1))
    print(f"  {window}: {len(rows)} rows -> {target}", flush=True)
    return len(rows)


def run(what: str, tables: list[str] | None = None) -> None:
    client = CodWikiClient()
    SNAPSHOT_ROOT.mkdir(parents=True, exist_ok=True)
    if what == "reference":
        pull_reference(client, tables)
        inventory_awards(client)
    elif what in WINDOWS:
        pull_playerstats(client, what)
        consolidate_playerstats(what)
    else:
        raise ValueError(f"unknown pull target: {what}")
    print(f"{client.calls} API calls, {client.cached} pages from snapshots", flush=True)
