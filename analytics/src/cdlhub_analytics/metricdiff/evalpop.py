"""The frozen evaluation population: which maps every version is scored on.

A rating version is only comparable to the version before it if both were
scored on the same maps. The repairs queued behind this harness all change
which maps are eligible — an identity merge splits and joins players, a lineup
rule declares maps in or out — so the eligible set and the evaluation set have
to stop being the same object.

**The policy is: the evaluation set is held fixed.** It is cut once, hashed, and
every run records that hash. Repairs change the modelling population and leave
the ruler alone. Re-cutting is an explicit act (`--freeze`), it takes a new label,
and every prior version is re-scored on the new cut before the new one is
published — a comparison across a silently changed population is the same defect
as a comparison across a silently changed model, one level up.

A map is in the population when its series carries a kickoff time, the map has a
winner, and both sides of the series are known: the maps a backtest can score.

Each is named by its series' `source_uid` and its ordinal. The first cut named
maps by their `liquipedia_match_id` where they had one and by a composite of
event, kickoff and `series.id` where they did not, on the stated reasoning that
this avoided `games.id`. It did — and it depended on three things that move
anyway. Linking the CWL era to LPDB gave 711 series a match id they had not had,
which renamed 2,743 maps at once; `series.id` is itself a surrogate key that a
reload renumbers; and `series_fix` corrects kickoff times. `source_uid` is the
provider-native series id, unique, populated on every series, and already the
conflict target every loader upserts on — it is the one identifier here that a
reload is designed not to change.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg

from .. import artifacts

Conn = psycopg.Connection[tuple[object, ...]]

DIRNAME = "evalpop"
POINTER = "frozen.json"

# The cut rule, stored beside the hash so a set can be read without this file.
RULE = (
    "maps whose series has a source_uid, a kickoff time and two known teams, "
    "and whose own winner is recorded; named <source_uid>#<ordinal>"
)

_ELIGIBLE_SQL = """
SELECT s.source_uid || '#' || g.ordinal::text AS map_key,
       se.year, t.short_name
FROM games g
JOIN series s ON s.id = g.series_id
JOIN events e ON e.id = s.event_id
LEFT JOIN seasons se ON se.id = e.season_id
LEFT JOIN titles t ON t.id = se.title_id
WHERE s.source_uid IS NOT NULL
  AND s.played_at IS NOT NULL
  AND s.team1_id IS NOT NULL
  AND s.team2_id IS NOT NULL
  AND g.winner_team_id IS NOT NULL
ORDER BY map_key
"""


def directory() -> Path:
    path = artifacts.root() / DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def eligible(conn: Conn) -> tuple[list[str], dict[str, int]]:
    """Every map that meets the cut rule now, and the per-season counts."""
    keys: list[str] = []
    by_season: dict[str, int] = {}
    for row in conn.execute(_ELIGIBLE_SQL).fetchall():
        keys.append(cast(str, row[0]))
        label = f"{row[1]} {row[2]}" if row[1] is not None else "unassigned"
        by_season[label] = by_season.get(label, 0) + 1
    return keys, by_season


def digest(keys: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(keys)).encode()).hexdigest()


def set_path(cut: str) -> Path:
    return directory() / f"{cut}.ndjson.gz"


def frozen() -> dict[str, Any] | None:
    """The pointer to the frozen set, or None when nothing has been cut."""
    try:
        loaded = json.loads((directory() / POINTER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


def read_set(cut: str) -> Iterator[str]:
    with gzip.open(set_path(cut), "rt", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                yield text


def freeze(conn: Conn, cut: str) -> dict[str, Any]:
    """Cut the population under `cut` and make it the frozen one."""
    keys, by_season = eligible(conn)
    path = set_path(cut)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for map_key in sorted(keys):
            handle.write(map_key + "\n")
    pointer: dict[str, Any] = {
        "cut": cut,
        "rule": RULE,
        "sha256": digest(keys),
        "n_maps": len(keys),
        "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "by_season": dict(sorted(by_season.items())),
        "path": artifacts.relative(path),
    }
    (directory() / POINTER).write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    return pointer


def drift(conn: Conn) -> dict[str, Any]:
    """How far the eligible set has moved from the frozen one.

    Drift is reported, never applied. A repair that adds maps is expected to
    show here; the evaluation set does not follow it until someone re-cuts.
    """
    pointer = frozen()
    if pointer is None:
        return {"frozen": False, "reason": "no evaluation population has been cut"}

    current, by_season = eligible(conn)
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
        "eligible_by_season": dict(sorted(by_season.items())),
        "n_added": len(added),
        "n_removed": len(removed),
        "added_sample": added[:20],
        "removed_sample": removed[:20],
    }


def stamp() -> dict[str, Any] | None:
    """The block every model run records: which ruler it was scored against."""
    pointer = frozen()
    if pointer is None:
        return None
    return {
        "cut": pointer.get("cut"),
        "sha256": pointer.get("sha256"),
        "n_maps": pointer.get("n_maps"),
    }
