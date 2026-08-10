"""Player handles that may be one person, with the evidence either way.

Two rows in `players` are a candidate when their handles collapse to the same
key once case and punctuation are removed, or when they are one edit apart and
share a team. The evidence is what decides it: two handles that appear in the
same game are two people, and two that never overlap and hand a team over
between them are one.

Decisions are written to the pipeline's `aliases.json` and nowhere else. Rows
written straight into Postgres do not survive the next import; an alias entry
is replayed by every load.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from typing import Any

from . import ALIASES_PATH, Conn, rows_as_dicts

# Below this, one edit is most of the handle and the pair means nothing.
MIN_FUZZY_LENGTH = 4
# Teams shown per player: enough to see whose history is whose.
TOP_TEAMS = 6

KEPT_SEPARATE_COMMENT = (
    "'identity_kept_separate' lists handle pairs confirmed to be two different "
    "people. Nothing reads it at load time; it stops a pair being offered again "
    "as a merge candidate."
)

_PLAYERS_SQL = """
SELECT p.id                                   AS player_id,
       p.handle,
       count(*)                               AS maps,
       count(DISTINCT g.series_id)            AS series,
       min(s.played_at)::date                 AS first_date,
       max(s.played_at)::date                 AS last_date,
       array_agg(DISTINCT s.data_source)      AS sources
FROM players p
JOIN game_player_stats gps ON gps.player_id = p.id
JOIN games g               ON g.id = gps.game_id
JOIN series s              ON s.id = g.series_id
GROUP BY p.id, p.handle
ORDER BY p.handle
"""

_TEAMS_SQL = """
SELECT gps.player_id,
       t.name                 AS team,
       count(*)               AS maps,
       min(s.played_at)::date AS first_date,
       max(s.played_at)::date AS last_date
FROM game_player_stats gps
JOIN teams t  ON t.id = gps.team_id
JOIN games g  ON g.id = gps.game_id
JOIN series s ON s.id = g.series_id
GROUP BY gps.player_id, t.name
ORDER BY gps.player_id, count(*) DESC, t.name
"""

_STINTS_SQL = """
SELECT rs.player_id,
       t.name AS team,
       rs.start_date,
       rs.end_date,
       rs.source
FROM roster_stints rs
JOIN teams t ON t.id = rs.team_id
ORDER BY rs.player_id, rs.start_date
"""

# Games two players both played, whichever side they were on. The pair is the
# one piece of evidence that settles a candidate on its own.
_TOGETHER_SQL = """
SELECT a.player_id  AS left_id,
       b.player_id  AS right_id,
       count(*)     AS games,
       count(*) FILTER (WHERE a.team_id <> b.team_id) AS games_opposed,
       max(s.played_at)::date AS last_date
FROM game_player_stats a
JOIN game_player_stats b ON b.game_id = a.game_id AND b.player_id > a.player_id
JOIN games g  ON g.id = a.game_id
JOIN series s ON s.id = g.series_id
WHERE a.player_id = ANY(%s) AND b.player_id = ANY(%s)
GROUP BY a.player_id, b.player_id
"""


def normalize(handle: str) -> str:
    """Case, accents and punctuation removed — what two spellings share."""
    decomposed = unicodedata.normalize("NFKD", handle)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return "".join(c for c in stripped.lower() if c.isalnum())


def _within_one_edit(left: str, right: str) -> bool:
    """True when one insertion, deletion or substitution turns one into the other."""
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(1 for a, b in zip(left, right, strict=True) if a != b) <= 1
    longer, shorter = (left, right) if len(left) > len(right) else (right, left)
    return any(longer[:i] + longer[i + 1 :] == shorter for i in range(len(longer)))


def load_aliases() -> dict[str, Any]:
    try:
        loaded = json.loads(ALIASES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _kept_separate(aliases: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for entry in aliases.get("identity_kept_separate") or []:
        if isinstance(entry, list) and len(entry) == 2:
            pairs.add((str(entry[0]), str(entry[1])))
    return pairs


def _pair_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left <= right else (right, left)


def _span(player: dict[str, Any]) -> tuple[date, date] | None:
    first, last = player["first_date"], player["last_date"]
    if isinstance(first, date) and isinstance(last, date):
        return first, last
    return None


def _gap_days(left: dict[str, Any], right: dict[str, Any]) -> int | None:
    """Days between one handle's last map and the other's first. 0 if they overlap.

    A small gap either way is what a rename looks like from the box scores.
    """
    a, b = _span(left), _span(right)
    if a is None or b is None:
        return None
    return max(0, (max(a[0], b[0]) - min(a[1], b[1])).days)


def _overlap_days(left: dict[str, Any], right: dict[str, Any]) -> int | None:
    """Days both handles were active, negative when one ends before the other starts."""
    a, b = _span(left), _span(right)
    if a is None or b is None:
        return None
    return (min(a[1], b[1]) - max(a[0], b[0])).days + 1


def _stint_conflicts(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Stints that run at the same time on different teams."""
    conflicts: list[dict[str, Any]] = []
    for a in left:
        for b in right:
            if a["team"] == b["team"]:
                continue
            a_end = a["end_date"] or date.max
            b_end = b["end_date"] or date.max
            if a["start_date"] <= b_end and b["start_date"] <= a_end:
                conflicts.append(
                    {
                        "left": {"team": a["team"], "from": a["start_date"], "to": a["end_date"]},
                        "right": {"team": b["team"], "from": b["start_date"], "to": b["end_date"]},
                    }
                )
    return conflicts


def _side(
    player: dict[str, Any],
    teams: dict[int, list[dict[str, Any]]],
    stints: dict[int, list[dict[str, Any]]],
) -> dict[str, Any]:
    player_id = int(player["player_id"])
    return {
        "player_id": player_id,
        "handle": player["handle"],
        "maps": player["maps"],
        "series": player["series"],
        "first_date": player["first_date"],
        "last_date": player["last_date"],
        "sources": player["sources"],
        "teams": [
            {k: v for k, v in team.items() if k != "player_id"}
            for team in teams.get(player_id, [])[:TOP_TEAMS]
        ],
        "stints": [
            {k: v for k, v in stint.items() if k != "player_id"}
            for stint in stints.get(player_id, [])
        ],
    }


def _suggestion(evidence: dict[str, Any]) -> str:
    """What the evidence says, for the owner to accept or overrule.

    Sharing a map is two people; so is holding two rosters at once. A team both
    handles played for but never played a map on together is one person under
    two spellings, which is the whole shape this queue is looking for.
    """
    if evidence["games_together"] > 0:
        return "keep_separate"
    if evidence["stint_conflicts"]:
        return "keep_separate"
    if evidence["shared_teams"]:
        return "merge"
    if evidence["kind"] == "spelling":
        return "merge"
    return "review"


def candidates(conn: Conn) -> list[dict[str, Any]]:
    """Every unresolved pair, strongest evidence first."""
    aliases = load_aliases()
    resolved = {str(k) for k in (aliases.get("players") or {})}
    separated = _kept_separate(aliases)

    players = rows_as_dicts(conn.execute(_PLAYERS_SQL))
    by_id = {int(p["player_id"]): p for p in players}

    teams: dict[int, list[dict[str, Any]]] = {}
    for row in rows_as_dicts(conn.execute(_TEAMS_SQL)):
        teams.setdefault(int(row["player_id"]), []).append(row)
    stints: dict[int, list[dict[str, Any]]] = {}
    for row in rows_as_dicts(conn.execute(_STINTS_SQL)):
        stints.setdefault(int(row["player_id"]), []).append(row)

    pairs = _pair_up(players, resolved, separated)
    if not pairs:
        return []

    ids = sorted({pid for pair in pairs for pid in pair[:2]})
    together: dict[tuple[int, int], dict[str, Any]] = {
        (int(row["left_id"]), int(row["right_id"])): row
        for row in rows_as_dicts(conn.execute(_TOGETHER_SQL, (ids, ids)))
    }

    result: list[dict[str, Any]] = []
    for left_id, right_id, kind in pairs:
        left, right = by_id[left_id], by_id[right_id]
        shared = sorted(
            {t["team"] for t in teams.get(left_id, [])}
            & {t["team"] for t in teams.get(right_id, [])}
        )
        # The pair is ordered by handle here and by id in the query.
        met = together.get((min(left_id, right_id), max(left_id, right_id)), {})
        evidence: dict[str, Any] = {
            "kind": kind,
            "normalized": normalize(str(left["handle"])),
            "games_together": int(met.get("games") or 0),
            "games_opposed": int(met.get("games_opposed") or 0),
            "shared_teams": shared,
            "overlap_days": _overlap_days(left, right),
            "gap_days": _gap_days(left, right),
            "stint_conflicts": _stint_conflicts(stints.get(left_id, []), stints.get(right_id, [])),
        }
        result.append(
            {
                "id": f"{left['handle']}|{right['handle']}",
                "left": _side(left, teams, stints),
                "right": _side(right, teams, stints),
                "evidence": evidence,
                "suggestion": _suggestion(evidence),
            }
        )

    order = {"merge": 0, "review": 1, "keep_separate": 2}
    result.sort(key=lambda c: (order[str(c["suggestion"])], -int(c["left"]["maps"])))
    return result


def _pair_up(
    players: list[dict[str, Any]],
    resolved: set[str],
    separated: set[tuple[str, str]],
) -> list[tuple[int, int, str]]:
    """Candidate pairs: same normalized handle, then one edit apart."""
    keys = {int(p["player_id"]): normalize(str(p["handle"])) for p in players}
    handles = {int(p["player_id"]): str(p["handle"]) for p in players}
    ids = sorted(keys, key=lambda i: handles[i])

    pairs: list[tuple[int, int, str]] = []
    for index, left_id in enumerate(ids):
        for right_id in ids[index + 1 :]:
            left, right = handles[left_id], handles[right_id]
            if left in resolved or right in resolved:
                continue
            if _pair_key(left, right) in separated:
                continue
            a, b = keys[left_id], keys[right_id]
            if not a or not b:
                continue
            if a == b:
                pairs.append((left_id, right_id, "spelling"))
            elif min(len(a), len(b)) >= MIN_FUZZY_LENGTH and _within_one_edit(a, b):
                pairs.append((left_id, right_id, "one_edit"))
    return pairs


# MARK: writes


class DecisionError(LookupError):
    """A decision that would make aliases.json say two things at once."""


def _write(aliases: dict[str, Any]) -> None:
    ALIASES_PATH.write_text(
        json.dumps(aliases, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def merge(source: str, canonical: str) -> dict[str, Any]:
    """Map one spelling onto another in `players`."""
    if source == canonical:
        raise DecisionError("a handle cannot be merged into itself")
    aliases = load_aliases()
    players: dict[str, Any] = dict(aliases.get("players") or {})
    existing = players.get(source)
    if existing is not None and existing != canonical:
        raise DecisionError(f"{source!r} already maps to {existing!r}")
    if canonical in players:
        raise DecisionError(
            f"{canonical!r} is itself mapped to {players[canonical]!r}; merge into that instead"
        )
    # Appended rather than sorted in: the file is hand-edited too, and a
    # decision should read as one added line.
    players[source] = canonical
    aliases["players"] = players
    _write(aliases)
    return {"merged": {"from": source, "to": canonical}, "players": len(players)}


def keep_separate(left: str, right: str) -> dict[str, Any]:
    """Record a pair as two people so it stops being offered."""
    if left == right:
        raise DecisionError("a handle cannot be kept separate from itself")
    aliases = load_aliases()
    pair = list(_pair_key(left, right))
    kept: list[Any] = list(aliases.get("identity_kept_separate") or [])
    if pair not in kept:
        kept.append(pair)
        kept.sort(key=lambda entry: (str(entry[0]), str(entry[1])))
    aliases.setdefault("_comment_identity", KEPT_SEPARATE_COMMENT)
    aliases["identity_kept_separate"] = kept
    _write(aliases)
    return {"kept_separate": pair, "decisions": len(kept)}


def report(conn: Conn, applied: dict[str, Any] | None = None) -> dict[str, Any]:
    aliases = load_aliases()
    return {
        "aliases_path": str(ALIASES_PATH),
        "applied": applied,
        "resolved": {
            "players": len(aliases.get("players") or {}),
            "kept_separate": len(aliases.get("identity_kept_separate") or []),
        },
        "reload_job": "reload",
        "candidates": candidates(conn),
    }
