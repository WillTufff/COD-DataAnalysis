"""The newest metric-diff report, shaped for the app's data health tab.

Read from the stored artifact, never recomputed: what this reports is what the
run actually said. A harness that has never run is a state to render, not an
error.
"""

from __future__ import annotations

import json
from typing import Any, cast

from ..metricdiff import MODEL
from ..metricdiff.run import POPULATION_ARTIFACT, REPORT_ARTIFACT
from . import Conn

# Movers and flips handed to the app. The report keeps more; the counts beside
# them are over every move, not over these.
NAMED_LIMIT = 25


def _payload(conn: Conn, run_id: int, name: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = %s",
        (run_id, name),
    ).fetchone()
    if row is None:
        return None
    got = row[0]
    if isinstance(got, dict):
        return cast(dict[str, Any], got)
    return cast(dict[str, Any], json.loads(cast(str, got)))


def report(conn: Conn) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, created_at, data_through FROM model_runs WHERE model = %s "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (MODEL,),
    ).fetchone()
    if row is None:
        return {"has_run": False, "reason": "the metric diff has never run here"}

    run_id = cast(int, row[0])
    diff = _payload(conn, run_id, REPORT_ARTIFACT)
    if diff is None:
        return {"has_run": False, "reason": f"run {run_id} stored no {REPORT_ARTIFACT} artifact"}

    totals = diff.get("totals", {})
    return {
        "has_run": True,
        "run_id": run_id,
        "created_at": row[1],
        "data_through": row[2],
        "available": diff.get("available", False),
        "reason": diff.get("reason"),
        "threshold": diff.get("threshold", {}),
        "baseline": diff.get("baseline"),
        "current": diff.get("current"),
        "totals": totals,
        "families": [f for f in diff.get("families", []) if _touched(f)],
        "movers": diff.get("movers", [])[:NAMED_LIMIT],
        "movers_omitted": diff.get("movers_omitted", 0)
        + max(0, len(diff.get("movers", [])) - NAMED_LIMIT),
        "flips": diff.get("flips", [])[:NAMED_LIMIT],
        "flips_omitted": diff.get("flips_omitted", 0)
        + max(0, len(diff.get("flips", [])) - NAMED_LIMIT),
        "added_keys": diff.get("added_keys", [])[:NAMED_LIMIT],
        "added_omitted": diff.get("added_omitted", 0),
        "removed_keys": diff.get("removed_keys", [])[:NAMED_LIMIT],
        "removed_omitted": diff.get("removed_omitted", 0),
        "population": _payload(conn, run_id, POPULATION_ARTIFACT) or {"frozen": False},
    }


def _touched(family: dict[str, Any]) -> bool:
    return bool(family["moved"] or family["flipped"] or family["added"] or family["removed"])
