"""Player handles that may be one person, with the evidence either way.

Two rows in `players` are a candidate when their handles collapse to the same
key once case and punctuation are removed, when they are one edit apart and
share a team, or when they carry the same real name and birthdate. That third
rule is the only one that catches a rename to an unrelated gamertag, which no
amount of string distance will find. The evidence is what decides it: two
handles that appear in the same game are two people, and two that never overlap
and hand a team over between them are one.

Decisions are written to the pipeline's `aliases.json` and nowhere else. Rows
written straight into Postgres do not survive the next import; an alias entry
is replayed by every load.
"""

from __future__ import annotations

import json
import unicodedata
from datetime import date
from typing import Any, cast

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
SELECT p.id                                       AS player_id,
       p.handle,
       p.real_name,
       p.birthdate,
       count(gps.player_id)                       AS maps,
       count(DISTINCT g.series_id)                AS series,
       min(s.played_at)::date                     AS first_date,
       max(s.played_at)::date                     AS last_date,
       array_remove(array_agg(DISTINCT s.data_source), NULL) AS sources
FROM players p
LEFT JOIN game_player_stats gps ON gps.player_id = p.id
LEFT JOIN games g               ON g.id = gps.game_id
LEFT JOIN series s              ON s.id = g.series_id
GROUP BY p.id, p.handle, p.real_name, p.birthdate
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


def _bio_key(player: dict[str, Any]) -> tuple[str, date] | None:
    """The person a row claims to be, or None when it does not claim one.

    Both halves are required. A shared name alone is two people often enough to
    be worthless, and a shared birthdate alone is worth even less.
    """
    name, born = player.get("real_name"), player.get("birthdate")
    if not isinstance(name, str) or not name.strip() or not isinstance(born, date):
        return None
    return (" ".join(name.split()).casefold(), born)


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


def _map_windows(teams: list[dict[str, Any]]) -> dict[str, tuple[date, date]]:
    """First and last map date per team, from the box scores themselves."""
    windows: dict[str, tuple[date, date]] = {}
    for team in teams:
        first, last = team["first_date"], team["last_date"]
        if isinstance(first, date) and isinstance(last, date):
            windows[str(team["team"])] = (first, last)
    return windows


def _stint_conflicts(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    left_teams: dict[str, tuple[date, date]],
    right_teams: dict[str, tuple[date, date]],
) -> list[dict[str, Any]]:
    """Stints that run at the same time on different teams.

    An archive stint spans its whole event, so a player who changed teams
    inside one event gets two stints that overlap by construction. Where both
    teams have maps to check against, the conflict has to hold for the actual
    map dates too.
    """
    conflicts: list[dict[str, Any]] = []
    for a in left:
        for b in right:
            if a["team"] == b["team"]:
                continue
            a_maps = left_teams.get(str(a["team"]))
            b_maps = right_teams.get(str(b["team"]))
            if a_maps and b_maps and (a_maps[0] > b_maps[1] or b_maps[0] > a_maps[1]):
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
    two spellings, which is the whole shape this queue is looking for. A shared
    real name and birthdate says the same thing without the handles agreeing at
    all, so it decides a pair the string rules would never have offered.
    """
    if evidence["games_together"] > 0:
        return "keep_separate"
    if evidence["stint_conflicts"]:
        return "keep_separate"
    if evidence["shared_teams"]:
        return "merge"
    if evidence["kind"] in {"spelling", "bio"}:
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
            "stint_conflicts": _stint_conflicts(
                stints.get(left_id, []),
                stints.get(right_id, []),
                _map_windows(teams.get(left_id, [])),
                _map_windows(teams.get(right_id, [])),
            ),
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
    """Candidate pairs: same normalized handle, one edit apart, or one person's bio."""
    keys = {int(p["player_id"]): normalize(str(p["handle"])) for p in players}
    handles = {int(p["player_id"]): str(p["handle"]) for p in players}
    bios = {int(p["player_id"]): _bio_key(p) for p in players}
    # A row with no maps carries no box-score evidence, so a pair built on its
    # spelling alone can never be settled. Its biography still can settle one.
    played = {int(p["player_id"]) for p in players if int(p["maps"] or 0) > 0}
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
            bio = bios[left_id]
            if bio is not None and bio == bios[right_id]:
                pairs.append((left_id, right_id, "bio"))
                continue
            if not a or not b:
                continue
            if left_id not in played or right_id not in played:
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
    if not source.strip() or not canonical.strip():
        raise DecisionError("a merge needs two handles")
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


# MARK: replay onto the live database


# Every table that carries a player's own record, and how a row is scoped to the
# archive that wrote it. A source-scoped merge moves only the rows whose archive
# the decision names; a global merge moves all of them.
# Where one archive's name for itself differs by table, and which tables one
# archive wrote in full. `roster_stints` records the CWL archive as
# `cwl-archive`; the kill feed exists for that archive alone, and carries no
# column to say so.
SOURCE_COLUMN_VALUES: dict[str, dict[str, str]] = {
    "cwl_archive": {"roster_stints": "cwl-archive"},
}
WHOLE_TABLE_SOURCES: dict[str, tuple[str, ...]] = {"cwl_archive": ("kill_events",)}

PLAYER_TABLES: tuple[tuple[str, str | None], ...] = (
    ("game_player_stats", "data_source"),
    ("event_rosters", "data_source"),
    ("player_awards", "data_source"),
    ("transfers", "data_source"),
    ("roster_stints", "source"),
    ("kill_events", None),
    ("player_aliases", None),
)

# Rows a model wrote about a player. A merge invalidates them, and the next
# `run_all` writes them again, so they are deleted rather than moved.
PLAYER_MODEL_TABLES: tuple[str, ...] = (
    "career_curves",
    "player_career",
    "player_career_rank",
    "player_metric_season",
    "player_rapm",
    "player_role_season",
    "player_season_adjusted",
    "player_season_rank",
    "player_skill",
    "player_style_season",
)

TEAM_TABLES: tuple[tuple[str, str], ...] = (
    ("event_placements", "team_id"),
    ("event_rosters", "team_id"),
    ("game_player_stats", "team_id"),
    ("game_segments", "team_id"),
    ("games", "winner_team_id"),
    ("roster_stints", "team_id"),
    ("series", "team1_id"),
    ("series", "team2_id"),
    ("transfers", "to_team_id"),
    ("transfers", "from_team_id"),
)

# Rows a model wrote about a team. Two names merging into one would collide on
# the same run and season, so these are dropped for the next `run_all` as well.
TEAM_MODEL_TABLES: tuple[str, ...] = (
    "team_metric_season",
    "team_ratings",
    "team_season_effect",
)

# The columns a Liquipedia biography supplies. A handle pinned to no page keeps
# none of them: they describe whoever the page was about, not this player.
BIO_COLUMNS = ("real_name", "country", "birthdate", "earnings", "earnings_by_year")


def _player_by_handle(conn: Conn) -> dict[str, list[tuple[int, str]]]:
    """Folded handle to every player row spelled that way.

    A handle is not unique here: three rows read `Realize`, which is what an
    unresolved wiki page leaves behind. A merge on an ambiguous handle is
    refused; a biography decision applies to all of them, because the decision
    is about the name rather than about one row.
    """
    out: dict[str, list[tuple[int, str]]] = {}
    for pid, handle in conn.execute("SELECT id, handle FROM players ORDER BY id").fetchall():
        out.setdefault(str(handle).lower(), []).append((cast(int, pid), str(handle)))
    return out


def _one_player(players: dict[str, list[tuple[int, str]]], handle: str) -> tuple[int, str] | None:
    found = players.get(handle.lower(), [])
    return found[0] if len(found) == 1 else None


def _team_by_name(conn: Conn) -> dict[str, tuple[int, str]]:
    return {
        str(name).lower(): (cast(int, tid), str(name))
        for tid, name in conn.execute("SELECT id, name FROM teams").fetchall()
    }


def _merge_player(conn: Conn, loser: int, winner: int, source: str | None) -> dict[str, Any]:
    """Move one player's rows onto another, inside one archive or across all."""
    moved: dict[str, int] = {}
    dropped: dict[str, int] = {}
    for table, scope in PLAYER_TABLES:
        whole_table = source is not None and table in WHOLE_TABLE_SOURCES.get(source, ())
        if source and scope is None and not whole_table:
            continue
        scope_value = SOURCE_COLUMN_VALUES.get(source or "", {}).get(table, source)
        params = {"loser": loser, "winner": winner, "source": scope_value}
        if table == "kill_events":
            for column in ("killer_id", "victim_id"):
                cur = conn.execute(
                    f"UPDATE kill_events SET {column} = %(winner)s WHERE {column} = %(loser)s",
                    params,
                )
                if cur.rowcount:
                    moved[f"kill_events.{column}"] = cur.rowcount
            continue
        scoped = f" AND t.{scope} = %(source)s" if source and not whole_table else ""
        # A row the winner already holds cannot be moved onto it: the pair is
        # the same fact recorded twice, so the loser's copy goes.
        cur = conn.execute(
            f"DELETE FROM {table} t WHERE t.player_id = %(loser)s{scoped}"
            f" AND EXISTS (SELECT 1 FROM {table} w WHERE w.player_id = %(winner)s"
            f"{_row_identity(table)})",
            params,
        )
        if cur.rowcount:
            dropped[table] = cur.rowcount
        cur = conn.execute(
            f"UPDATE {table} t SET player_id = %(winner)s WHERE t.player_id = %(loser)s{scoped}",
            params,
        )
        if cur.rowcount:
            moved[table] = cur.rowcount
    for table in PLAYER_MODEL_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE player_id IN (%s, %s)", (loser, winner))
    left = _references(conn, loser)
    if not left:
        conn.execute("DELETE FROM players WHERE id = %s", (loser,))
    return {
        "loser": loser,
        "winner": winner,
        "source": source,
        "moved": {k: v for k, v in moved.items() if v},
        "dropped_as_duplicate": dropped,
        "loser_row": "deleted" if not left else f"kept, still referenced by {', '.join(left)}",
    }


def _row_identity(table: str) -> str:
    """What makes the winner's row the same row as the loser's."""
    return {
        "game_player_stats": " AND w.game_id = t.game_id",
        "event_rosters": " AND w.event_id = t.event_id AND w.team_id = t.team_id",
        "player_awards": " AND w.pagename = t.pagename AND w.raw_award = t.raw_award"
        " AND w.handle = t.handle",
        "player_aliases": " AND w.alias = t.alias",
    }.get(table, " AND false")


def _references(conn: Conn, pid: int) -> list[str]:
    left = []
    for table, _scope in PLAYER_TABLES:
        column = "killer_id" if table == "kill_events" else "player_id"
        row = conn.execute(f"SELECT 1 FROM {table} WHERE {column} = %s LIMIT 1", (pid,)).fetchone()
        if row is None and table == "kill_events":
            row = conn.execute(
                "SELECT 1 FROM kill_events WHERE victim_id = %s LIMIT 1", (pid,)
            ).fetchone()
        if row is not None:
            left.append(table)
    return left


def _merge_team(conn: Conn, loser: int, winner: int) -> dict[str, Any]:
    moved: dict[str, int] = {}
    for table, column in TEAM_TABLES:
        cur = conn.execute(f"UPDATE {table} SET {column} = %s WHERE {column} = %s", (winner, loser))
        if cur.rowcount:
            moved[f"{table}.{column}"] = cur.rowcount
    for table in TEAM_MODEL_TABLES:
        conn.execute(f"DELETE FROM {table} WHERE team_id IN (%s, %s)", (loser, winner))
    conn.execute("DELETE FROM teams WHERE id = %s", (loser,))
    return {"loser": loser, "winner": winner, "moved": moved}


def apply_decisions(conn: Conn) -> dict[str, Any]:
    """Replay every decision in `aliases.json` onto the database in place.

    A load replays these decisions from the file, so a full reimport would
    reach the same state. This reaches it without one: the merges move rows,
    the pins clear a biography that belongs to somebody else, and the model
    tables for a touched player are dropped for the next `run_all` to write.
    Idempotent — a decision already applied moves nothing.
    """
    aliases = load_aliases()
    players = _player_by_handle(conn)
    teams = _team_by_name(conn)
    report: dict[str, Any] = {
        "player_merges": [],
        "team_merges": [],
        "real_names": [],
        "pages_cleared": [],
        "skipped": [],
    }

    scoped: list[tuple[str, str, str | None]] = [
        (spelling, canonical, source)
        for source, mapping in (aliases.get("players_by_source") or {}).items()
        for spelling, canonical in mapping.items()
    ]
    scoped += [
        (spelling, canonical, None)
        for spelling, canonical in (aliases.get("players") or {}).items()
    ]
    for spelling, canonical, source in scoped:
        loser = _one_player(players, spelling)
        winner = _one_player(players, canonical)
        if (
            len(players.get(spelling.lower(), [])) > 1
            or len(players.get(canonical.lower(), [])) > 1
        ):
            report["skipped"].append(
                f"players: {spelling!r} or {canonical!r} names more than one row; not merged"
            )
            continue
        if loser is None or winner is None or loser[0] == winner[0]:
            continue
        report["player_merges"].append(
            {"from": loser[1], "to": winner[1], **_merge_player(conn, loser[0], winner[0], source)}
        )

    for name, canonical in (aliases.get("teams") or {}).items():
        loser = teams.get(name.lower())
        winner = teams.get(canonical.lower())
        if loser is None or winner is None or loser[0] == winner[0]:
            continue
        report["team_merges"].append(
            {"from": loser[1], "to": winner[1], **_merge_team(conn, loser[0], winner[0])}
        )

    for handle, page in (aliases.get("player_pages") or {}).items():
        found = players.get(handle.lower(), [])
        if not found:
            report["skipped"].append(f"player_pages: no player called {handle!r}")
            continue
        for pid, spelling in found:
            # Only a page that is bound and is not the pinned one is cleared,
            # so a second run of this command changes nothing.
            cur = conn.execute(
                "UPDATE players SET liquipedia_page = NULL, "
                + ", ".join(f"{column} = NULL" for column in BIO_COLUMNS)
                + " WHERE id = %s AND liquipedia_page IS NOT NULL"
                + " AND liquipedia_page IS DISTINCT FROM %s",
                (pid, page),
            )
            if cur.rowcount:
                report["pages_cleared"].append({"handle": spelling, "pinned_to": page})

    for handle, real_name in (aliases.get("real_names") or {}).items():
        found = players.get(handle.lower(), [])
        if not found:
            report["skipped"].append(f"real_names: no player called {handle!r}")
            continue
        for pid, spelling in found:
            cur = conn.execute(
                "UPDATE players SET real_name = %s WHERE id = %s AND real_name IS DISTINCT FROM %s",
                (real_name, pid, real_name),
            )
            if cur.rowcount:
                report["real_names"].append({"handle": spelling, "real_name": real_name})

    report["reload_note"] = (
        "model tables for every merged player were dropped; run_all writes them again"
    )
    return report
