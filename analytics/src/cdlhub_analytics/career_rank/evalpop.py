"""The frozen evaluation population: which careers this engine is tuned against.

A formula that credits peak, longevity, team strength and awards can always be
adjusted after the fact to move a favorite career up the list. The guard is
the same one the metric-diff harness uses for maps: cut the population once,
before the formula sees where anyone lands, hash it, and only compare against
that fixed set until an explicit re-cut. Repairs to the formula are allowed;
silently swapping which careers it is judged against is not.

A player is in the population when they have at least `MIN_SEASONS` qualified
seasons (`maps_played >= QUALIFIED_MAPS` on the all-modes row) in
`player_season_adjusted`, on the latest run. That is the same floor
`career.py` already uses per season, applied here as a count over a career
instead of a map count within one. Named by `player_id`, which every
career-scoped table in this project already keys on.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg

from .. import artifacts
from ..career import QUALIFIED_MAPS

Conn = psycopg.Connection[tuple[object, ...]]

DIRNAME = "career_rank_evalpop"
POINTER = "frozen.json"

MIN_SEASONS = 3

RULE = (
    f"players with at least {MIN_SEASONS} seasons of "
    f">= {QUALIFIED_MAPS} maps_played on the all-modes row of "
    "player_season_adjusted, latest run; named by player_id"
)

_ELIGIBLE_SQL = """
SELECT psa.player_id, count(*) AS n_seasons
FROM player_season_adjusted psa
WHERE psa.mode_id IS NULL
  AND psa.maps_played >= %(qualified_maps)s
  AND psa.run_id = (SELECT max(run_id) FROM player_season_adjusted)
GROUP BY psa.player_id
HAVING count(*) >= %(min_seasons)s
ORDER BY psa.player_id
"""


def directory() -> Path:
    path = artifacts.root() / DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def eligible(conn: Conn) -> dict[int, int]:
    """Every player meeting the cut rule now, mapped to their qualified-season count."""
    rows = conn.execute(
        _ELIGIBLE_SQL,
        {"qualified_maps": QUALIFIED_MAPS, "min_seasons": MIN_SEASONS},
    ).fetchall()
    return {cast(int, row[0]): cast(int, row[1]) for row in rows}


def digest(player_ids: list[int]) -> str:
    return hashlib.sha256("\n".join(str(pid) for pid in sorted(player_ids)).encode()).hexdigest()


def set_path(cut: str) -> Path:
    return directory() / f"{cut}.ndjson.gz"


def frozen() -> dict[str, Any] | None:
    """The pointer to the frozen set, or None when nothing has been cut."""
    try:
        loaded = json.loads((directory() / POINTER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


def read_set(cut: str) -> list[int]:
    with gzip.open(set_path(cut), "rt", encoding="utf-8") as handle:
        return [int(line.strip()) for line in handle if line.strip()]


def _labels_on_record(pointer: dict[str, Any] | None) -> set[str]:
    """Every label the pointer knows about: the frozen one and its history."""
    if pointer is None:
        return set()
    history = pointer.get("history")
    earlier = [e.get("cut") for e in history] if isinstance(history, list) else []
    return {str(label) for label in [pointer.get("cut"), *earlier] if label}


def freeze(conn: Conn, cut: str) -> dict[str, Any]:
    """Cut the population under `cut` and make it the frozen one.

    The label it replaces goes into `supersedes` and the full chain into
    `history`, so no cut this engine was ever tuned against is lost. A label
    already on record is refused: reusing one would overwrite the set an
    earlier version was scored on.
    """
    previous = frozen()
    if cut in _labels_on_record(previous):
        raise ValueError(
            f"evaluation population '{cut}' is already on record; a re-cut takes a new label"
        )
    counts = eligible(conn)
    player_ids = sorted(counts)
    path = set_path(cut)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for pid in player_ids:
            handle.write(f"{pid}\n")
    pointer: dict[str, Any] = {
        "cut": cut,
        "rule": RULE,
        "sha256": digest(player_ids),
        "n_players": len(player_ids),
        "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season_count_histogram": _histogram(counts),
        "path": artifacts.relative(path),
        "supersedes": previous.get("cut") if previous else None,
        "history": artifacts.cut_history(previous, "n_players"),
    }
    (directory() / POINTER).write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    return pointer


def _histogram(counts: dict[int, int]) -> dict[str, int]:
    hist: dict[str, int] = {}
    for n in counts.values():
        key = str(n) if n < 10 else "10+"
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: (kv[0] == "10+", kv[0])))


def drift(conn: Conn) -> dict[str, Any]:
    """How far the eligible set has moved from the frozen one.

    Drift is reported, never applied. New qualified seasons are expected to
    show here as the archive grows; the evaluation set does not follow them
    until someone re-cuts.
    """
    pointer = frozen()
    if pointer is None:
        return {"frozen": False, "reason": "no evaluation population has been cut"}

    current = eligible(conn)
    try:
        stored = set(read_set(cast(str, pointer["cut"])))
    except OSError:
        return {
            "frozen": True,
            **pointer,
            "readable": False,
            "reason": "the frozen set's file is missing",
        }

    now = set(current)
    added = sorted(now - stored)
    removed = sorted(stored - now)
    return {
        "frozen": True,
        **pointer,
        "readable": True,
        "matches": not added and not removed,
        "eligible_now": len(now),
        "n_added": len(added),
        "n_removed": len(removed),
        "added_sample": added[:20],
        "removed_sample": removed[:20],
    }


def stamp() -> dict[str, Any] | None:
    """The block every run of this engine records: which population it was tuned against."""
    pointer = frozen()
    if pointer is None:
        return None
    return {
        "cut": pointer.get("cut"),
        "sha256": pointer.get("sha256"),
        "n_players": pointer.get("n_players"),
    }
