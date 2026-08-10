"""Snapshot per-match player-stats for the scoped CDL-pro match set.

One API call per match — this is the quota driver. Resumable: a match is
skipped if its snapshot exists or it is in the attempted-empty ledger, so a
run is always a diff against disk. Stops cleanly at the monthly budget.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from .client import SNAPSHOT_ROOT, CitoClient, QuotaExceeded, write_snapshot
from .scope import MANIFEST_PATH

MATCHES_DIR = SNAPSHOT_ROOT / "matches"
EMPTY_LEDGER_PATH = SNAPSHOT_ROOT / "attempted-empty.json"


def run(max_calls: int | None = None) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    empty: dict[str, str] = (
        json.loads(EMPTY_LEDGER_PATH.read_text()) if EMPTY_LEDGER_PATH.exists() else {}
    )
    done = {p.stem for p in MATCHES_DIR.glob("*.json")} if MATCHES_DIR.exists() else set()
    todo = [m for m in manifest["matchIds"] if m not in done and m not in empty]
    print(
        f"{len(manifest['matchIds'])} scoped, {len(done)} done, "
        f"{len(empty)} empty, {len(todo)} todo"
    )

    client = CitoClient()
    pulled = 0
    try:
        for match_id in todo:
            if max_calls is not None and pulled >= max_calls:
                print(f"stopping at --max-calls {max_calls}")
                break
            try:
                payload = client.get(
                    f"/cod/matches/{match_id}/player-stats", {"includeMaps": "true"}
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    empty[match_id] = "404"
                    EMPTY_LEDGER_PATH.write_text(json.dumps(empty, indent=1))
                    pulled += 1
                    continue
                raise
            if _has_players(payload):
                write_snapshot(f"matches/{match_id}.json", payload)
            else:
                empty[match_id] = "no players in response"
                EMPTY_LEDGER_PATH.write_text(json.dumps(empty, indent=1))
            pulled += 1
            if pulled % 50 == 0:
                print(f"{pulled}/{len(todo)} pulled, quota remaining {client.ledger.remaining}")
    except QuotaExceeded as e:
        print(f"monthly budget reached: {e}")
    print(f"run complete: {pulled} calls, quota remaining {client.ledger.remaining}")


def _has_players(payload: dict[str, Any]) -> bool:
    data = payload.get("data")
    if isinstance(data, dict) and data.get("players"):
        return True
    return bool(payload.get("players"))
