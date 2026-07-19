"""Import the structured event tier: the kill feed and per-round scores.

The box-score spine (cwl_archive) carries per-map totals; this reads the JSON
event tarballs in place and populates the per-event grain underneath it —
``kill_events`` (one row per death) and ``game_rounds`` (round-boundary scores).

Only Infinite Warfare (2017) and WWII (2018) ship a feed. BO4 (2019) games have
box scores but empty event lists, so they are skipped here and simply have no
rows. Games join the spine on ``games.source_uid`` = the feed's ``game["id"]``.

Two death shapes are normalized into one row (see ``schema_probe``):

    WWII  nested ``attacker`` object, 2D positions, ``time_ms`` in milliseconds
    IW    flat ``attacker_*`` fields, 3D positions, ``time`` in **deciseconds**

Player handles in the feed are resolved against each game's own box-score roster
(handles + aliases), which also yields the team membership needed to classify
team kills. Handles that do not resolve are logged and their events dropped;
those player-maps then fail reconciliation and are excluded downstream — never
patched.

    uv run python -m cdlhub_pipeline.cwl_structured [--reset] [--dir PATH] [--dsn DSN]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import psycopg

from ..identity import Aliases
from .schema_probe import SCHEMA_NONE, death_schema, iter_games

SOURCE = "cwl-structured"

# Event time is normalized to milliseconds. WWII carries ``time_ms`` already in
# ms; IW carries ``time`` in deciseconds (measured: event clock runs ~10x the
# game's duration_s, vs ~1000x for WWII). The key present picks the scale.
_TIME_MS = "time_ms"
_TIME_DS = "time"
_TIME_SCALE = {_TIME_MS: 1, _TIME_DS: 100}
_ROUND_TIME = {_TIME_MS: "round_time_ms", _TIME_DS: "round_time"}

# death_kind values (kept in sync with the CHECK in 0006_events.sql)
NORMAL = "normal"
SUICIDE = "suicide"
TEAMKILL = "teamkill"


@dataclass
class Roster:
    """One game's box-score roster, for resolving feed handles locally."""

    game_id: int
    handle_to_pid: dict[str, int] = field(default_factory=dict)  # lowercased spelling -> id
    pid_to_team: dict[int, int] = field(default_factory=dict)

    def resolve(self, handle: str | None) -> int | None:
        if not handle:
            return None
        return self.handle_to_pid.get(handle.lower())


@dataclass
class DeathRow:
    seq: int
    round: int
    time_ms: int | None
    round_time_ms: int | None
    victim_id: int
    killer_id: int | None
    victim_handle: str
    killer_handle: str | None
    victim_life: int | None
    killer_life: int | None
    death_kind: str
    weapon: str | None
    means_of_death: str | None
    weapon_class: str | None
    kill_distance: float | None
    victim_x: float | None
    victim_y: float | None
    victim_z: float | None
    killer_x: float | None
    killer_y: float | None
    killer_z: float | None


class Importer:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]], aliases: Aliases):
        self.conn = conn
        self.aliases = aliases
        self.counts: Counter[str] = Counter()
        self.unresolved: Counter[str] = Counter()  # feed handle -> dropped-event count
        self._rosters = self._load_rosters()

    # ---- roster index -------------------------------------------------------

    def _load_rosters(self) -> dict[str, Roster]:
        """Map feed game id (source_uid) -> that game's roster.

        A feed handle resolves if it matches, case-insensitively, any spelling
        the box score already knows for a player in that game: the canonical
        handle, any stored alias, or the alias-map's canonical form of either.
        Resolution is game-local so identical handles in different games (or a
        stray global collision) can never cross-contaminate.
        """
        aliases_of: dict[int, set[str]] = defaultdict(set)
        for row in self.conn.execute("SELECT player_id, alias FROM player_aliases"):
            pid, alias = cast(int, row[0]), cast(str, row[1])
            aliases_of[pid].add(alias)

        rosters: dict[str, Roster] = {}
        rows = self.conn.execute(
            """
            SELECT g.source_uid, g.id, gps.player_id, gps.team_id, p.handle
            FROM games g
            JOIN game_player_stats gps ON gps.game_id = g.id
            JOIN players p ON p.id = gps.player_id
            WHERE g.source_uid IS NOT NULL
            """
        )
        for raw in rows:
            source_uid = cast(str, raw[0])
            game_id, pid, team_id = cast(int, raw[1]), cast(int, raw[2]), cast(int, raw[3])
            handle = cast(str, raw[4])
            r = rosters.get(source_uid)
            if r is None:
                r = rosters[source_uid] = Roster(game_id=game_id)
            r.pid_to_team[pid] = team_id
            for spelling in {handle, *aliases_of.get(pid, set())}:
                for form in (spelling, self.aliases.player(spelling)):
                    r.handle_to_pid[form.lower()] = pid
        return rosters

    # ---- per-game parsing ---------------------------------------------------

    def import_all(self, structured_dir: Path, *, reset: bool) -> None:
        if reset:
            self.conn.execute("TRUNCATE kill_events, game_rounds")
            print("reset: kill_events and game_rounds truncated")
        for tarball in sorted(structured_dir.glob("structured-*.tar.gz")):
            self._import_tarball(tarball)

    def _import_tarball(self, tarball: Path) -> None:
        slug = tarball.name[len("structured-") : -len(".tar.gz")]
        seen = 0
        for _name, game in iter_games(tarball):
            if game is None:
                self.counts["unreadable"] += 1
                continue
            self._import_game(game)
            seen += 1
        print(f"  {slug}: {seen} games read")

    def _import_game(self, game: dict[str, Any]) -> None:
        roster = self._rosters.get(game.get("id", ""))
        if roster is None:
            # Feed game not present in the box-score spine (e.g. the 2019
            # pro-league tail the archive never covered). Nothing to attach to.
            self.counts["no_spine_join"] += 1
            return

        events = game.get("events") or []
        deaths = [e for e in events if e.get("type") == "death"]
        if not deaths:
            # BO4's 543 empty games and the odd feed-less map land here.
            self.counts["no_events"] += 1
            return

        # Idempotent per game: clear then repopulate, so re-imports converge.
        self.conn.execute("DELETE FROM kill_events WHERE game_id = %s", (roster.game_id,))
        self.conn.execute("DELETE FROM game_rounds WHERE game_id = %s", (roster.game_id,))

        scale, round_key = self._time_format(events)
        self._import_rounds(roster.game_id, events, scale, round_key)
        self._import_deaths(roster, deaths, scale, round_key)
        self.counts["games"] += 1

    @staticmethod
    def _time_format(events: list[dict[str, Any]]) -> tuple[int, str]:
        """(ms multiplier, round-time key) for this game's time unit."""
        key = _TIME_MS if _TIME_MS in events[0] else _TIME_DS
        return _TIME_SCALE[key], _ROUND_TIME[key]

    def _import_rounds(
        self, game_id: int, events: list[dict[str, Any]], scale: int, round_key: str
    ) -> None:
        starts: dict[int, int | None] = {}  # round -> start time (ms)
        ends: dict[int, dict[str, Any]] = {}  # round -> last roundend event
        for e in events:
            rnd = e.get("round")
            if rnd is None:
                continue
            if e.get("type") == "roundstart":
                starts.setdefault(rnd, self._ms(e, scale, round_key))
            elif e.get("type") == "roundend":
                ends[rnd] = e

        # Round winner is the score delta vs the prior round's cumulative total
        # (SnD/CTF carry cumulative round wins; single-round HP just compares the
        # two hill scores). The feed hands us the scores, so no death-clock guess.
        prev1 = prev2 = 0
        rows = []
        for rnd in sorted(set(starts) | set(ends)):
            end = ends.get(rnd)
            data = (end or {}).get("data") or {}
            s1, s2 = data.get("score1"), data.get("score2")
            winner = None
            if s1 is not None and s2 is not None:
                d1, d2 = s1 - prev1, s2 - prev2
                winner = 1 if d1 > d2 else 2 if d2 > d1 else None
                prev1, prev2 = s1, s2
            rows.append(
                (
                    game_id,
                    rnd,
                    s1,
                    s2,
                    starts.get(rnd),
                    self._ms(end, scale, round_key) if end else None,
                    winner,
                )
            )
        if rows:
            self.conn.cursor().executemany(
                "INSERT INTO game_rounds "
                "(game_id, round, score1, score2, start_time_ms, end_time_ms, winner_side) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                rows,
            )
            self.counts["rounds"] += len(rows)

    def _import_deaths(
        self, roster: Roster, deaths: list[dict[str, Any]], scale: int, round_key: str
    ) -> None:
        rows: list[tuple[object, ...]] = []
        for seq, death in enumerate(deaths):
            row = self._death_row(roster, seq, death, scale, round_key)
            if row is None:
                continue
            rows.append(
                (
                    roster.game_id,
                    row.seq,
                    row.round,
                    row.time_ms,
                    row.round_time_ms,
                    row.victim_id,
                    row.killer_id,
                    row.victim_handle,
                    row.killer_handle,
                    row.victim_life,
                    row.killer_life,
                    row.death_kind,
                    row.weapon,
                    row.means_of_death,
                    row.weapon_class,
                    row.kill_distance,
                    row.victim_x,
                    row.victim_y,
                    row.victim_z,
                    row.killer_x,
                    row.killer_y,
                    row.killer_z,
                )
            )
        if rows:
            self.conn.cursor().executemany(
                """
                INSERT INTO kill_events
                  (game_id, seq, round, time_ms, round_time_ms,
                   victim_id, killer_id, victim_handle, killer_handle,
                   victim_life, killer_life, death_kind,
                   weapon, means_of_death, weapon_class, kill_distance,
                   victim_x, victim_y, victim_z, killer_x, killer_y, killer_z)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                rows,
            )
            self.counts["kills"] += len(rows)

    def _death_row(
        self, roster: Roster, seq: int, death: dict[str, Any], scale: int, round_key: str
    ) -> DeathRow | None:
        schema = death_schema(death)
        if schema == SCHEMA_NONE:
            self.counts["death_no_schema"] += 1
            return None
        data = death.get("data") or {}

        victim_handle = data.get("id")
        victim_id = roster.resolve(victim_handle)
        if victim_id is None:
            # Cannot attribute the death; drop it and record the handle. The
            # victim's player-map will then under-count and fail reconciliation.
            self.unresolved[str(victim_handle)] += 1
            self.counts["victim_unresolved"] += 1
            return None

        # Attacker fields live nested (WWII) or flat with an attacker_ prefix (IW).
        if schema == "flat":
            killer_handle = data.get("attacker_id")
            killer_life = data.get("attacker_life")
            killer_pos = data.get("attacker_pos") or {}
            weapon = data.get("attacker_weapon")
            weapon_class = data.get("attacker_weapon_class")
            means = data.get("means_of_death")
            kill_distance = data.get("kill_distance")
        else:  # nested
            attacker = data.get("attacker") or {}
            killer_handle = attacker.get("id")
            killer_life = attacker.get("life")
            killer_pos = attacker.get("pos") or {}
            weapon = attacker.get("weapon")
            weapon_class = None  # WWII's nested attacker carries no class
            means = attacker.get("means_of_death")
            kill_distance = None  # nor a distance

        killer_id = roster.resolve(killer_handle)
        death_kind = self._classify(roster, victim_id, victim_handle, killer_handle, killer_id)

        victim_pos = data.get("pos") or {}
        rt = death.get(round_key)
        return DeathRow(
            seq=seq,
            round=death.get("round", 0),
            time_ms=self._ms(death, scale, round_key.replace("round_", "")),
            round_time_ms=rt * scale if rt is not None else None,
            victim_id=victim_id,
            killer_id=killer_id,
            victim_handle=str(victim_handle),
            killer_handle=str(killer_handle) if killer_handle is not None else None,
            victim_life=data.get("life"),
            killer_life=killer_life,
            death_kind=death_kind,
            weapon=weapon,
            means_of_death=means,
            weapon_class=weapon_class,
            kill_distance=kill_distance,
            victim_x=victim_pos.get("x"),
            victim_y=victim_pos.get("y"),
            victim_z=victim_pos.get("z"),
            killer_x=killer_pos.get("x"),
            killer_y=killer_pos.get("y"),
            killer_z=killer_pos.get("z"),
        )

    def _classify(
        self,
        roster: Roster,
        victim_id: int,
        victim_handle: str | None,
        killer_handle: str | None,
        killer_id: int | None,
    ) -> str:
        # Suicide: no attacker, or the attacker is the victim. Handle comparison
        # covers the self-kill case even when the killer otherwise resolves.
        if not killer_handle:
            return SUICIDE
        if killer_id is not None and killer_id == victim_id:
            return SUICIDE
        if victim_handle and killer_handle.lower() == victim_handle.lower():
            return SUICIDE
        # Teamkill: attacker resolved and shares the victim's box-score team.
        if killer_id is not None:
            vt, kt = roster.pid_to_team.get(victim_id), roster.pid_to_team.get(killer_id)
            if vt is not None and vt == kt:
                return TEAMKILL
        return NORMAL

    @staticmethod
    def _ms(event: dict[str, Any] | None, scale: int, key: str) -> int | None:
        if event is None:
            return None
        raw = event.get(key)
        return raw * scale if raw is not None else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dsn",
        default=os.environ.get("DATABASE_URL", "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"),
    )
    ap.add_argument(
        "--dir",
        type=Path,
        default=Path(__file__).resolve().parents[3] / "snapshots" / "cwl-structured",
    )
    ap.add_argument("--reset", action="store_true", help="truncate event tables first")
    args = ap.parse_args(argv)

    aliases = Aliases.load()
    with psycopg.connect(args.dsn) as conn:
        importer = Importer(conn, aliases)
        importer.import_all(args.dir, reset=args.reset)
        counts = dict(sorted(importer.counts.items()))
        conn.execute(
            "INSERT INTO ingest_runs (kind, params, status, rows_upserted) "
            "VALUES (%s, %s, 'success', %s)",
            (SOURCE, json.dumps({"reset": args.reset}), json.dumps(counts)),
        )
        conn.commit()

    print("imported:", counts)
    if importer.unresolved:
        top = importer.unresolved.most_common(15)
        total = sum(importer.unresolved.values())
        print(f"unresolved handles: {len(importer.unresolved)} distinct, {total} dropped deaths")
        for handle, n in top:
            print(f"  {handle:20} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
