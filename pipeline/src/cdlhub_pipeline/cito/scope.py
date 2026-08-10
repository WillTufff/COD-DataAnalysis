"""Scope the CDL-pro match set: tournament classifier + all-season match enumeration.

The tournament catalog merges three upstream sources with no dedup key, so
tournaments are never the ingest spine — they only feed a boolean classifier.
Matches are enumerated across every season /cod/seasons lists (season tags are
unreliable: the 2020 CDL season is filed under season=2019) and scoped by the
classifier; matchId is the stable key and matchDate assigns the true season.
"""

from __future__ import annotations

import json
from typing import Any

from .client import SNAPSHOT_ROOT, CitoClient, write_snapshot

CDL_ORGANIZER = "Call of Duty League"
NAME_DENYLIST = ("esports world cup", "kaysan", "showdown")

MANIFEST_PATH = SNAPSHOT_ROOT / "cdl-pro-manifest.json"


def is_cdl_pro(tournament: dict[str, Any]) -> bool:
    if not tournament.get("countsForStats"):
        return False
    if tournament.get("organizer") != CDL_ORGANIZER:
        return False
    name = (tournament.get("name") or "").lower()
    return not any(term in name for term in NAME_DENYLIST)


def build_classifier(client: CitoClient) -> dict[str, bool]:
    """Map every tournamentId to its CDL-pro flag; logs exclusions for review."""
    pages = client.get_paged("/cod/tournaments")
    classifier: dict[str, bool] = {}
    excluded_with_organizer: list[str] = []
    for i, page in enumerate(pages, 1):
        write_snapshot(f"tournaments/page-{i}.json", page)
        for t in page.get("data", []):
            flag = is_cdl_pro(t)
            classifier[t["tournamentId"]] = flag
            if not flag and t.get("organizer") == CDL_ORGANIZER:
                excluded_with_organizer.append(f"{t['tournamentId']}: {t.get('name')}")
    if excluded_with_organizer:
        print("excluded despite CDL organizer (review these):")
        for line in sorted(set(excluded_with_organizer)):
            print(f"  {line}")
    return classifier


def enumerate_matches(client: CitoClient, classifier: dict[str, bool]) -> list[dict[str, Any]]:
    """List matches for every advertised season; keep CDL-pro, dedup by matchId."""
    seasons_payload = client.get("/cod/seasons")
    write_snapshot("seasons.json", seasons_payload)
    seasons = [s["season"] for s in seasons_payload["data"]]

    kept: dict[str, dict[str, Any]] = {}
    for season in seasons:
        pages = client.get_paged("/cod/matches", {"season": season})
        for i, page in enumerate(pages, 1):
            write_snapshot(f"match-lists/season-{season}-page-{i}.json", page)
            for m in page.get("data", []):
                tid = (m.get("tournament") or {}).get("tournamentId")
                if tid and classifier.get(tid) and m["matchId"] not in kept:
                    kept[m["matchId"]] = m
        n_rows = sum(len(p.get("data", [])) for p in pages)
        print(f"season {season}: {n_rows} listed, {len(kept)} CDL-pro cumulative")
    return sorted(kept.values(), key=lambda m: m.get("matchDate") or "")


def run() -> None:
    client = CitoClient()
    classifier = build_classifier(client)
    print(f"classifier: {len(classifier)} tournamentIds, {sum(classifier.values())} CDL-pro")
    matches = enumerate_matches(client, classifier)
    manifest = {
        "matchIds": [m["matchId"] for m in matches],
        "count": len(matches),
        "byYear": _count_by_year(matches),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=1))
    print(f"CDL-pro matches: {len(matches)}  ({manifest['byYear']})")
    print(f"manifest: {MANIFEST_PATH}")
    print(
        f"quota used this month: {client.ledger.count}, remaining budget: {client.ledger.remaining}"
    )


def _count_by_year(matches: list[dict[str, Any]]) -> dict[str, int]:
    by_year: dict[str, int] = {}
    for m in matches:
        year = (m.get("matchDate") or "????")[:4]
        by_year[year] = by_year.get(year, 0) + 1
    return dict(sorted(by_year.items()))
