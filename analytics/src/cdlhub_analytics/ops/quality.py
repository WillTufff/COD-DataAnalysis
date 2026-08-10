"""The quality gate's last verdict and the trend behind it.

Read from the files the gate writes, never by re-running its SQL: what this
reports is what the gate actually said, including when it last said it. A gate
that has never run is a state to render, not an error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DIRECTORY = "quality"
REPORT_FILENAME = "report.json"
HISTORY_FILENAME = "history.ndjson"
# The strip in the app reads left to right; older runs than this are history
# nobody scrolls back to.
HISTORY_LIMIT = 30
# Past this, the verdict describes a database that has moved on.
STALE_AFTER_HOURS = 24


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _parse_at(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _history(path: Path) -> list[dict[str, Any]]:
    """The last runs, oldest first. Unreadable lines are skipped."""
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[dict[str, Any]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and _parse_at(entry.get("at")) is not None:
            entries.append(entry)
    return entries[-HISTORY_LIMIT:]


def _age_hours(at: datetime | None) -> float | None:
    if at is None:
        return None
    return round((datetime.now(UTC) - at).total_seconds() / 3600, 2)


def report(snapshots: Path) -> dict[str, Any]:
    root = snapshots / DIRECTORY
    latest = _load_json(root / REPORT_FILENAME)
    history = _history(root / HISTORY_FILENAME)

    if latest is None:
        return {
            "has_run": False,
            "passed": None,
            "generated_at": None,
            "age_hours": None,
            "stale": True,
            "failing": [],
            "hard_checks": [],
            "soft_checks": [],
            "coverage": [],
            "reconciliation": {},
            "history": history,
        }

    hard = latest.get("hard_checks") or []
    soft = latest.get("soft_checks") or []
    age = _age_hours(_parse_at(latest.get("generated_at")))
    return {
        "has_run": True,
        "passed": bool(latest.get("passed")),
        "generated_at": latest.get("generated_at"),
        "age_hours": age,
        "stale": age is None or age > STALE_AFTER_HOURS,
        "duration_s": latest.get("duration_s"),
        "failing": [check["name"] for check in hard if not check.get("passed")],
        "warning": [check["name"] for check in soft if check.get("rows")],
        "hard_checks": hard,
        "soft_checks": soft,
        "coverage": latest.get("coverage") or [],
        "reconciliation": latest.get("reconciliation") or {},
        "history": history,
    }
