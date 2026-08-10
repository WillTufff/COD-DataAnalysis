"""How fast each source is being called, read from the clients' request logs.

Each source appends one NDJSON line per outbound call to
`snapshots/<source>/requests.ndjson`, carrying the interval that client is
configured for. Nothing here restates those intervals: a pull that is going too
fast is one whose measured gaps fall under the interval its own log reports.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SOURCES = ("lpdb", "cito")
# The calls a pull is judged on: enough to average out one slow response,
# short enough that the number moves while a pull is running.
WINDOW = 20
# A source with a call this recent is being pulled right now.
ACTIVE_WITHIN_S = 120.0
HOURS = 24


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _read(path: Path) -> list[dict[str, Any]]:
    """Every well-formed line, oldest first. A truncated tail is skipped."""
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and _parse_at(record.get("at")) is not None:
            records.append(record)
    records.sort(key=lambda r: str(r["at"]))
    return records


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gaps(records: list[dict[str, Any]]) -> list[float]:
    """Seconds between consecutive calls, from the timestamps themselves."""
    times = [_parse_at(r["at"]) for r in records]
    return [
        (later - earlier).total_seconds()
        for earlier, later in zip(times, times[1:], strict=False)
        if earlier is not None and later is not None
    ]


def _recent(records: list[dict[str, Any]], min_interval: float) -> dict[str, Any]:
    window = records[-WINDOW:]
    gaps = _gaps(window)
    if not gaps:
        return {"calls": len(window), "gaps": 0}
    span = sum(gaps)
    return {
        "calls": len(window),
        "gaps": len(gaps),
        "span_s": round(span, 1),
        "mean_gap_s": round(span / len(gaps), 2),
        "min_gap_s": round(min(gaps), 2),
        "per_minute": round(60 * len(gaps) / span, 2) if span else None,
        # Calls that went out sooner after the last one than the client's own
        # configured spacing. A pull holding its pace reports zero.
        "under_min_interval": sum(1 for gap in gaps if gap < min_interval - 0.05),
    }


def _by_hour(records: list[dict[str, Any]], now: datetime) -> list[dict[str, Any]]:
    floor = (now - timedelta(hours=HOURS - 1)).replace(minute=0, second=0, microsecond=0)
    counts: dict[datetime, int] = {floor + timedelta(hours=i): 0 for i in range(HOURS)}
    for record in records:
        moment = _parse_at(record["at"])
        if moment is None:
            continue
        hour = moment.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if hour in counts:
            counts[hour] += 1
    return [{"hour": _stamp(hour), "calls": counts[hour]} for hour in sorted(counts)]


def _source(name: str, snapshots: Path, now: datetime) -> dict[str, Any]:
    records = _read(snapshots / name / "requests.ndjson")
    if not records:
        return {"source": name, "calls_total": 0, "calls_today": 0, "active": False, "by_hour": []}

    day = now.date()
    today = [r for r in records if (m := _parse_at(r["at"])) and m.astimezone(UTC).date() == day]
    last_interval = records[-1].get("min_interval_s")
    min_interval = float(last_interval) if isinstance(last_interval, int | float) else 0.0
    last_at = _parse_at(records[-1]["at"])
    since_last = (now - last_at).total_seconds() if last_at else None

    return {
        "source": name,
        "min_interval_s": min_interval or None,
        "calls_total": len(records),
        "calls_today": len(today),
        "errors_today": sum(1 for r in today if int(r.get("status") or 0) >= 400),
        "first_at": _stamp(first) if (first := _parse_at(records[0]["at"])) else None,
        "last_at": _stamp(last_at) if last_at else None,
        "since_last_s": round(since_last, 1) if since_last is not None else None,
        "active": since_last is not None and since_last <= ACTIVE_WITHIN_S,
        "recent": _recent(records, min_interval),
        "by_hour": _by_hour(records, now),
    }


def report(snapshots: Path) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "generated_at": _stamp(now),
        "window": WINDOW,
        "sources": [_source(name, snapshots, now) for name in SOURCES],
    }
