"""The frozen anchor set: the careers a published all-time board has to explain.

A ranking engine cannot be tested against its own output. The anchor set is the
outside referent — players other people published, before this project scored
anyone — and the face-validity tests in `facevalidity.py` are pass or fail
against it.

Two rules keep it from becoming a training label, and both are structural
rather than promised:

- The set carries **tiers, never an ordering to reproduce**. Tier A is the
  consensus core, tier B the strong case, tier C the honourable mention and the
  active tier. A tier is a count of how many published lists name a player, so
  it cannot be edited by admiring a career.
- It is **frozen with a digest before a formula changes**. `anchors.json` holds
  the transcribed lists; freezing resolves them to `player_id` and hashes the
  result. A later re-freeze keeps the label it replaced in `history`, the same
  contract `evalpop` uses for the evaluation population.

Membership is decided by published lists alone. Résumé facts are attached to a
row as evidence for a reader, and no test may read a field this module marks
incomplete.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg

from .. import artifacts
from ..maprows import PUBLISHED_FROM_YEAR

Conn = psycopg.Connection[tuple[object, ...]]

DIRNAME = "career_rank_anchors"
POINTER = "frozen.json"
LISTS_PATH = Path(__file__).with_name("anchors.json")

# An all-time list is evidence about a career. A current-form list ranks who was
# playing well the month it was written, so it can name the active tier and
# nothing above it.
ALL_TIME = "all_time"
CURRENT_FORM = "current_form"

# How many all-time lists have to name a player for each tier. Set before the
# first test ran, and not a knob: moving it would be choosing the answer.
TIER_A_LISTS = 3
TIER_B_LISTS = 2

RULE = (
    f"players named on published all-time lists in anchors.json; tier A is "
    f"{TIER_A_LISTS}+ lists, tier B is {TIER_B_LISTS}, tier C is one list or a "
    "current-form list only; named by player_id"
)

_HANDLE_SQL = "SELECT id, handle FROM players"

# Championships are only countable where a placement can be attributed to the
# players who earned it, which needs an event roster. `event_rosters` holds the
# 2013-2016 archive and nothing after it, so this number is a floor for anyone
# who played in the league era and is complete for anyone who did not.
_RINGS_SQL = """
SELECT r.player_id,
       count(*) FILTER (WHERE ep.placement_min = 1) AS wins,
       count(*)                                     AS events,
       min(s.year)                                  AS first_year,
       max(s.year)                                  AS last_year
FROM event_rosters r
JOIN event_placements ep ON ep.event_id = r.event_id AND ep.team_id = r.team_id
JOIN events e            ON e.id = r.event_id
JOIN seasons s           ON s.id = e.season_id
GROUP BY r.player_id
"""

_ROSTER_YEARS_SQL = """
SELECT min(s.year) AS first_year, max(s.year) AS last_year
FROM event_rosters r
JOIN events e  ON e.id = r.event_id
JOIN seasons s ON s.id = e.season_id
"""


def directory() -> Path:
    path = artifacts.root() / DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_lists() -> dict[str, Any]:
    loaded = json.loads(LISTS_PATH.read_text(encoding="utf-8"))
    return cast(dict[str, Any], loaded)


def _appearances(lists: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per handle: which lists name it, and at what rank."""
    seen: dict[str, dict[str, Any]] = {}
    for entry in lists:
        kind = str(entry.get("kind"))
        for rank, handle in enumerate(entry["order"], start=1):
            record = seen.setdefault(
                str(handle), {"handle": str(handle), "all_time": [], "current_form": []}
            )
            record[kind].append({"list": entry["id"], "rank": rank})
    return seen


def _tier(record: dict[str, Any]) -> str:
    count = len(record["all_time"])
    if count >= TIER_A_LISTS:
        return "A"
    if count >= TIER_B_LISTS:
        return "B"
    return "C"


def _mean_rank(record: dict[str, Any]) -> float | None:
    ranks = [entry["rank"] for entry in record["all_time"]]
    return sum(ranks) / len(ranks) if ranks else None


def resolve(conn: Conn, handles: list[str]) -> tuple[dict[str, int], list[str]]:
    """Handles to `player_id`, plus the handles no player row answers to.

    Case is folded, because a list writes a gamertag the way its house style
    does. Two players separated only by case would make that unsafe, so a
    handle that folds onto more than one row is reported unresolved instead.
    """
    rows = conn.execute(_HANDLE_SQL).fetchall()
    folded: dict[str, list[int]] = {}
    for player_id, handle in rows:
        folded.setdefault(str(handle).casefold(), []).append(cast(int, player_id))
    resolved: dict[str, int] = {}
    missing: list[str] = []
    for handle in handles:
        found = folded.get(handle.casefold(), [])
        if len(found) == 1:
            resolved[handle] = found[0]
        else:
            missing.append(handle)
    return resolved, missing


def resume(conn: Conn, player_ids: list[int]) -> dict[int, dict[str, Any]]:
    """Countable championships per player, with the years they can be counted over."""
    window = conn.execute(_ROSTER_YEARS_SQL).fetchone()
    covered_from, covered_to = window or (None, None)
    rows = {
        cast(int, row[0]): {
            "event_wins": cast(int, row[1]),
            "events_rostered": cast(int, row[2]),
            "first_year": cast(int, row[3]),
            "last_year": cast(int, row[4]),
        }
        for row in conn.execute(_RINGS_SQL).fetchall()
    }
    return {
        pid: {
            **rows.get(pid, {"event_wins": 0, "events_rostered": 0}),
            "wins_covered_from": covered_from,
            "wins_covered_to": covered_to,
        }
        for pid in player_ids
    }


def rings_are_complete(conn: Conn) -> bool:
    """True when a win count may be read as a career total.

    `event_rosters` has to reach the last published season before a zero means
    a player won nothing rather than that nobody recorded who was on the team.
    """
    window = conn.execute(_ROSTER_YEARS_SQL).fetchone()
    if not window or window[1] is None:
        return False
    return int(cast(int, window[1])) >= _last_published_year(conn)


def _last_published_year(conn: Conn) -> int:
    row = conn.execute(
        "SELECT max(year) FROM seasons WHERE year >= %s", (PUBLISHED_FROM_YEAR,)
    ).fetchone()
    return int(cast(int, row[0])) if row and row[0] is not None else PUBLISHED_FROM_YEAR


def build(conn: Conn) -> dict[str, Any]:
    """The anchor set as it stands now, unfrozen."""
    catalog = load_lists()
    lists = cast(list[dict[str, Any]], catalog["lists"])
    seen = _appearances(lists)
    resolved, missing = resolve(conn, sorted(seen))
    facts = resume(conn, sorted(resolved.values()))

    players: list[dict[str, Any]] = []
    for handle in sorted(seen, key=lambda h: h.casefold()):
        record = seen[handle]
        player_id = resolved.get(handle)
        players.append(
            {
                "handle": handle,
                "player_id": player_id,
                "tier": _tier(record),
                "all_time_lists": len(record["all_time"]),
                "mean_all_time_rank": _mean_rank(record),
                "appearances": record["all_time"] + record["current_form"],
                "resume": facts.get(player_id) if player_id is not None else None,
            }
        )
    players.sort(key=lambda p: (p["tier"], p["mean_all_time_rank"] or 99.0, p["handle"]))
    return {
        "rule": RULE,
        "lists": [
            {k: v for k, v in entry.items() if k != "order"} | {"n_ranked": len(entry["order"])}
            for entry in lists
        ],
        "players": players,
        "unresolved": missing,
        "rings_complete": rings_are_complete(conn),
        "tier_counts": _tier_counts(players),
    }


def _tier_counts(players: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for player in players:
        tier = str(player["tier"])
        counts[tier] = counts.get(tier, 0) + 1
    return dict(sorted(counts.items()))


def digest(players: list[dict[str, Any]]) -> str:
    """A hash over who is in the set and at what tier, and nothing else.

    Résumé counts change with every load, so they are deliberately outside the
    digest: the frozen thing is the membership, not the evidence beside it.
    """
    body = "\n".join(
        f"{player['tier']}|{player['player_id']}|{player['handle']}"
        for player in sorted(players, key=lambda p: (p["tier"], str(p["handle"]).casefold()))
    )
    return hashlib.sha256(body.encode()).hexdigest()


def set_path(cut: str) -> Path:
    return directory() / f"{cut}.json"


def frozen() -> dict[str, Any] | None:
    try:
        loaded = json.loads((directory() / POINTER).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


def read_set(cut: str) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(set_path(cut).read_text(encoding="utf-8")))


def _labels_on_record(pointer: dict[str, Any] | None) -> set[str]:
    if pointer is None:
        return set()
    history = pointer.get("history")
    earlier = [entry.get("cut") for entry in history] if isinstance(history, list) else []
    return {str(label) for label in [pointer.get("cut"), *earlier] if label}


def freeze(conn: Conn, cut: str) -> dict[str, Any]:
    """Write the anchor set under `cut` and make it the frozen one."""
    previous = frozen()
    if cut in _labels_on_record(previous):
        raise ValueError(f"anchor set '{cut}' is already on record; a re-cut takes a new label")
    built = build(conn)
    if built["unresolved"]:
        raise ValueError(
            "every anchor must resolve to a player before freezing; unresolved: "
            + ", ".join(built["unresolved"])
        )
    set_path(cut).write_text(json.dumps(built, indent=2) + "\n", encoding="utf-8")
    pointer: dict[str, Any] = {
        "cut": cut,
        "rule": RULE,
        "sha256": digest(cast(list[dict[str, Any]], built["players"])),
        "n_players": len(built["players"]),
        "tier_counts": built["tier_counts"],
        "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lists": [entry["id"] for entry in built["lists"]],
        "path": artifacts.relative(set_path(cut)),
        "supersedes": previous.get("cut") if previous else None,
        "history": artifacts.cut_history(previous, "n_players"),
    }
    (directory() / POINTER).write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    return pointer


def load(conn: Conn) -> dict[str, Any]:
    """The frozen set, re-resolved against the database it will be tested on.

    Freezing stores `player_id`, and an identity merge can retire one. The
    handles are what the published lists actually said, so they are re-resolved
    and the digest is recomputed; a caller that cares compares it to the
    pointer's.
    """
    pointer = frozen()
    if pointer is None:
        raise LookupError("no anchor set has been frozen")
    stored = read_set(cast(str, pointer["cut"]))
    resolved, missing = resolve(conn, [str(p["handle"]) for p in stored["players"]])
    players = [
        {**player, "player_id": resolved.get(str(player["handle"]))} for player in stored["players"]
    ]
    return {
        **stored,
        "cut": pointer["cut"],
        "frozen_sha256": pointer["sha256"],
        "sha256": digest(players),
        "players": players,
        "unresolved": missing,
    }


def stamp() -> dict[str, Any] | None:
    pointer = frozen()
    if pointer is None:
        return None
    return {
        "cut": pointer.get("cut"),
        "sha256": pointer.get("sha256"),
        "n_players": pointer.get("n_players"),
    }
