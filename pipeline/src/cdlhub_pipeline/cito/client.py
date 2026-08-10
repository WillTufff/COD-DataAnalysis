"""HTTP client for the Cito API with rate limiting, snapshots, and a monthly quota ledger.

The free tier allows 500 calls/month and the API exposes no quota-remaining
header, so every request is counted in a per-month ledger file and the client
refuses to exceed its budget. Finished responses are snapshotted to disk;
a snapshot on disk never costs a second call.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from .. import reqlog

BASE_URL = "https://api.citoapi.com/api/v1"
MIN_INTERVAL_S = 2.2  # paid tier allows 30 req/min; stay under it
DEFAULT_MONTHLY_BUDGET = 9500  # hard stop with margin under the 10k/mo tier

REPO_ROOT = Path(__file__).resolve().parents[4]
SNAPSHOT_ROOT = REPO_ROOT / "pipeline" / "snapshots" / "cito"


class QuotaExceeded(RuntimeError):
    """Raised when the monthly call budget would be exceeded."""


def _api_key() -> str:
    key = os.environ.get("CITO_API_KEY")
    if not key:
        env_file = REPO_ROOT / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("CITO_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"')
                    break
    if not key:
        raise RuntimeError("CITO_API_KEY not set (env or repo-root .env)")
    return key


class QuotaLedger:
    """Persistent per-month request counter."""

    def __init__(self, root: Path, budget: int) -> None:
        self.budget = budget
        self.path = root / f"quota-{datetime.now(UTC):%Y-%m}.json"
        self.count: int = 0
        if self.path.exists():
            self.count = json.loads(self.path.read_text())["calls"]

    def charge(self) -> None:
        if self.count >= self.budget:
            raise QuotaExceeded(
                f"{self.count} calls recorded in {self.path.name}, budget {self.budget}"
            )
        self.count += 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"calls": self.count}))

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.count)


class CitoClient:
    def __init__(self, budget: int = DEFAULT_MONTHLY_BUDGET) -> None:
        self.http = httpx.Client(
            base_url=BASE_URL,
            headers={"x-api-key": _api_key()},
            timeout=30.0,
        )
        self.ledger = QuotaLedger(SNAPSHOT_ROOT, budget)
        self.server_remaining: str | None = None
        self._last_call = 0.0

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Rate-limited GET with backoff on 429/5xx. Charges the quota ledger once
        per attempt-batch (retries after a failed send re-charge, since the server
        may have counted the failed call)."""
        wait = self._last_call + MIN_INTERVAL_S - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        for attempt in range(4):
            self.ledger.charge()
            gap = time.monotonic() - self._last_call if self._last_call else None
            self._last_call = time.monotonic()
            resp = self.http.get(path, params=params)
            reqlog.append(
                SNAPSHOT_ROOT,
                endpoint=path,
                status=resp.status_code,
                waited_ms=int(gap * 1000) if gap is not None else None,
                min_interval_s=MIN_INTERVAL_S,
            )
            remaining = resp.headers.get("x-ratelimit-remaining")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == 3:
                    # A 429 that survives minutes of backoff is the monthly cap,
                    # not the per-minute window.
                    if resp.status_code == 429:
                        raise QuotaExceeded(
                            f"persistent 429 on {path}; monthly quota likely exhausted"
                        )
                    resp.raise_for_status()
                time.sleep(float(resp.headers.get("retry-after", 30 * (attempt + 1))))
                continue
            resp.raise_for_status()
            if remaining is not None:
                self.server_remaining = remaining
            payload: dict[str, Any] = resp.json()
            return payload
        raise AssertionError("unreachable")

    def get_paged(
        self, path: str, params: dict[str, Any] | None = None, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Follow pagination until hasMore is false; returns the raw page payloads.

        pagination.total can be null on season queries, so hasMore is the only
        reliable termination signal.
        """
        pages: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self.get(path, {**(params or {}), "limit": limit, "page": page})
            pages.append(payload)
            pagination = payload.get("pagination") or {}
            if not pagination.get("hasMore"):
                return pages
            page += 1


def write_snapshot(relpath: str, payload: dict[str, Any]) -> Path:
    path = SNAPSHOT_ROOT / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1))
    return path
