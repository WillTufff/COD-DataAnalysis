"""Append-only NDJSON log of outbound API requests, one line per call.

    {"at": "2026-08-09T14:02:11.418Z", "endpoint": "/match", "status": 200,
     "waited_ms": 4998, "min_interval_s": 5.0}

`waited_ms` is the gap since the previous call by the same client, null for the
first call a process makes.

The file lives beside the source's snapshots and nothing in the pipeline reads
it back; it exists so the pace of a pull can be seen after the fact. Writing is
best effort — a log that cannot be written never interrupts a pull, and the
interval each client is configured for is recorded on every line rather than
restated by the reader.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FILENAME = "requests.ndjson"


def log_path(snapshot_root: Path) -> Path:
    return snapshot_root / FILENAME


def append(
    snapshot_root: Path,
    endpoint: str,
    status: int,
    waited_ms: int | None,
    min_interval_s: float,
) -> None:
    record: dict[str, Any] = {
        "at": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "endpoint": endpoint,
        "status": status,
        "waited_ms": waited_ms,
        "min_interval_s": min_interval_s,
    }
    try:
        path = log_path(snapshot_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass
