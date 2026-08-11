"""HTTP client for the Liquipedia LPDB v3 API.

Deliberately slow: one request per five seconds, authenticated, identifying
User-Agent. Every response is snapshotted to disk before any transform touches
it. Columns are NOT shared across LPDB tables, so callers probe a table with
limit=1 before writing conditions against its fields.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

import httpx

from .. import reqlog
from ..cito.client import REPO_ROOT

BASE_URL = "https://api.liquipedia.net/api/v3"
WIKI = "callofduty"
MIN_INTERVAL_S = 5.0
PAGE_LIMIT = 1000  # the server maximum; larger values are silently capped
# A page of 1000 placements with rosters attached takes the server well past
# the 30s httpx default, so the read timeout is the query's, not the network's.
READ_TIMEOUT_S = 120.0
RETRY_BACKOFF_S = 30.0

SNAPSHOT_ROOT = REPO_ROOT / "pipeline" / "snapshots" / "lpdb"


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith(f"{name}="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    break
    if not value:
        raise RuntimeError(f"{name} not set (env or repo-root .env)")
    return value


class LpdbClient:
    def __init__(self) -> None:
        self.http = httpx.Client(
            base_url=BASE_URL,
            headers={
                "Authorization": f"Apikey {_env('LPDB_API_KEY')}",
                "User-Agent": _env("CDLHUB_UA"),
            },
            timeout=httpx.Timeout(30.0, read=READ_TIMEOUT_S),
        )
        self.calls = 0
        self._last_call = 0.0

    def get(self, table: str, params: dict[str, Any]) -> dict[str, Any]:
        """Rate-limited GET of one API v3 table; snapshots the response."""
        wait = self._last_call + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        merged = {"wiki": WIKI, **params}
        for attempt in range(4):
            gap = time.monotonic() - self._last_call if self._last_call else None
            self._last_call = time.monotonic()
            self.calls += 1
            try:
                resp = self.http.get(f"/{table}", params=merged)
            except httpx.TransportError as exc:
                # A wide query over a large page can take longer to build than
                # the read timeout allows, and the connection dropping is not
                # the server asking us to stop. Backed off like a 5xx and
                # counted like a call, because the server may well have served
                # it; four failures in a row is a real outage and propagates.
                if attempt == 3:
                    raise
                reqlog.append(
                    SNAPSHOT_ROOT,
                    endpoint=f"/{table}",
                    status=0,
                    waited_ms=int(gap * 1000) if gap is not None else None,
                    min_interval_s=MIN_INTERVAL_S,
                )
                print(f"  transport error on /{table} ({exc.__class__.__name__}), retrying")
                time.sleep(RETRY_BACKOFF_S * (attempt + 1))
                continue
            reqlog.append(
                SNAPSHOT_ROOT,
                endpoint=f"/{table}",
                status=resp.status_code,
                waited_ms=int(gap * 1000) if gap is not None else None,
                min_interval_s=MIN_INTERVAL_S,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == 3:
                    resp.raise_for_status()
                time.sleep(float(resp.headers.get("retry-after", 30 * (attempt + 1))))
                continue
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
            if payload.get("error"):
                raise RuntimeError(f"LPDB error on /{table}: {payload['error']}")
            write_snapshot(table, merged, payload)
            return payload
        raise AssertionError("unreachable")

    def get_all(
        self,
        table: str,
        conditions: str | None,
        query: str,
        order: str,
    ) -> list[dict[str, Any]]:
        """Follow offset pagination until a short page; returns merged rows.

        order must be stable (unique tiebreaker) so offset pages don't shift
        between calls.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {
                "query": query,
                "order": order,
                "limit": PAGE_LIMIT,
                "offset": offset,
            }
            if conditions:
                params["conditions"] = conditions
            payload = self.get(table, params)
            page = payload.get("result") or []
            rows.extend(page)
            if len(page) < PAGE_LIMIT:
                return rows
            offset += PAGE_LIMIT

    def probe(self, table: str) -> list[str]:
        """Discover a table's columns with a limit=1 pull."""
        payload = self.get(table, {"limit": 1})
        result = payload.get("result") or []
        return sorted(result[0].keys()) if result else []


def write_snapshot(table: str, params: dict[str, Any], payload: dict[str, Any]) -> None:
    key = hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:12]
    path = SNAPSHOT_ROOT / table / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"params": params, "payload": payload}, indent=1))
