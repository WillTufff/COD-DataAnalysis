"""HTTP client for the Call of Duty Esports Wiki Cargo API.

One request every twenty seconds, anonymous, identifying User-Agent. The
anonymous endpoint answers `ratelimited` under sustained use, as HTTP 200
carrying an error body, and the window it opens lasts minutes, so each refusal
doubles the wait and the client retries up to six times.

Every response is snapshotted to disk before any transform reads it, and a page
already on disk is read from there rather than requested again. A rerun after a
failure therefore costs a request only for the pages that never arrived.

`Special:CargoExport` returns a Cloudflare challenge and the S3 dumps return
403, so this API is the only route.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .. import reqlog
from ..cito.client import REPO_ROOT

BASE_URL = "https://cod-esports.fandom.com/api.php"
MIN_INTERVAL_S = 20.0
PAGE_LIMIT = 500  # the Cargo maximum for an anonymous caller
# A refusal arrives as HTTP 200 carrying error code `ratelimited`. The window it
# opens is minutes long and a fixed 45-second wait does not clear it, so each
# refusal doubles the wait: 45, 90, 180, 360, 720 seconds.
RATELIMIT_BACKOFF_S = 45.0
MAX_ATTEMPTS = 6
READ_TIMEOUT_S = 120.0

SNAPSHOT_ROOT = REPO_ROOT / "pipeline" / "snapshots" / "codwiki"
STATE_PATH = SNAPSHOT_ROOT / "pull-state.json"


class RateLimited(RuntimeError):
    """Raised when the API answers `ratelimited` on every attempt."""


def _user_agent() -> str:
    value = os.environ.get("CDLHUB_UA")
    if not value:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("CDLHUB_UA="):
                    value = line.split("=", 1)[1].strip().strip('"')
                    break
    if not value:
        raise RuntimeError("CDLHUB_UA not set (env or repo-root .env)")
    return value


class CodWikiClient:
    def __init__(self) -> None:
        self.http = httpx.Client(
            headers={"User-Agent": _user_agent()},
            timeout=httpx.Timeout(30.0, read=READ_TIMEOUT_S),
        )
        self.calls = 0
        self.cached = 0
        self.gaps: list[str] = []
        self._last_call = 0.0

    def query(
        self,
        table: str,
        fields: str,
        where: str | None = None,
        group_by: str | None = None,
        order_by: str | None = None,
        limit: int = PAGE_LIMIT,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """One paced cargoquery call; returns the row list and snapshots it."""
        params: dict[str, Any] = {
            "action": "cargoquery",
            "format": "json",
            "formatversion": "2",
            "tables": table,
            "fields": fields,
            "limit": limit,
            "offset": offset,
        }
        if where:
            params["where"] = where
        if group_by:
            params["group_by"] = group_by
        if order_by:
            params["order_by"] = order_by

        cached = read_snapshot(table, params)
        if cached is not None:
            self.cached += 1
            return [row["title"] for row in cached.get("cargoquery", [])]

        for attempt in range(MAX_ATTEMPTS):
            wait = self._last_call + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            gap = time.monotonic() - self._last_call if self._last_call else None
            self._last_call = time.monotonic()
            self.calls += 1
            status = 0
            payload: dict[str, Any] = {}
            try:
                resp = self.http.get(BASE_URL, params=params)
                status = resp.status_code
                if status == 200:
                    payload = resp.json()
            except httpx.TransportError as exc:
                payload = {"error": {"code": "transport", "info": repr(exc)}}
            reqlog.append(
                SNAPSHOT_ROOT,
                endpoint=f"/api.php?{table}",
                status=status,
                waited_ms=int(gap * 1000) if gap is not None else None,
                min_interval_s=MIN_INTERVAL_S,
            )
            code = payload.get("error", {}).get("code") if payload else "http"
            if status == 200 and not code:
                write_snapshot(table, params, payload)
                return [row["title"] for row in payload.get("cargoquery", [])]
            backoff = RATELIMIT_BACKOFF_S * (2**attempt)
            print(f"  {table}: {code or status}, backing off {backoff:.0f}s", flush=True)
            time.sleep(backoff)

        gap_key = json.dumps({"table": table, "where": where, "offset": offset})
        self.gaps.append(gap_key)
        raise RateLimited(f"{table} gave up after {MAX_ATTEMPTS} attempts: {gap_key}")

    def query_all(
        self,
        table: str,
        fields: str,
        where: str | None = None,
        group_by: str | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """Follow offset paging until a short page; returns the merged rows."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.query(
                table,
                fields,
                where=where,
                group_by=group_by,
                order_by=order_by,
                limit=PAGE_LIMIT,
                offset=offset,
            )
            rows.extend(page)
            if len(page) < PAGE_LIMIT:
                return rows
            offset += PAGE_LIMIT

    def table_fields(self, table: str) -> list[str]:
        """Read a table's declared field names through action=cargofields."""
        params = {
            "action": "cargofields",
            "format": "json",
            "formatversion": "2",
            "table": table,
        }
        cached = read_snapshot(f"{table}.fields", params)
        if cached is not None:
            self.cached += 1
            declared_cache = cached.get("cargofields", {})
            names_cache = (
                list(declared_cache)
                if isinstance(declared_cache, dict)
                else [f["field"] for f in declared_cache]
            )
            return [name for name in names_cache if not name.startswith("_")]
        wait = self._last_call + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()
        self.calls += 1
        resp = self.http.get(BASE_URL, params=params)
        reqlog.append(
            SNAPSHOT_ROOT,
            endpoint=f"/api.php?cargofields={table}",
            status=resp.status_code,
            waited_ms=None,
            min_interval_s=MIN_INTERVAL_S,
        )
        resp.raise_for_status()
        payload = resp.json()
        write_snapshot(f"{table}.fields", params, payload)
        declared = payload.get("cargofields", {})
        names = list(declared) if isinstance(declared, dict) else [f["field"] for f in declared]
        return [name for name in names if not name.startswith("_")]


def snapshot_path(table: str, params: dict[str, Any]) -> Path:
    key = hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return SNAPSHOT_ROOT / table / f"{key}.json"


def read_snapshot(table: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Return a page already on disk, so a rerun costs no request."""
    path = snapshot_path(table, params)
    if not path.exists():
        return None
    payload: dict[str, Any] = json.loads(path.read_text())["payload"]
    return payload


def write_snapshot(table: str, params: dict[str, Any], payload: dict[str, Any]) -> None:
    path = snapshot_path(table, params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"params": params, "payload": payload}, indent=1))


def read_state() -> dict[str, Any]:
    if STATE_PATH.exists():
        state: dict[str, Any] = json.loads(STATE_PATH.read_text())
        return state
    return {"done": {}}


def write_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=1))
