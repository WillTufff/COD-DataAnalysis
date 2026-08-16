"""Load transformed wiki series/games/stat lines into Postgres.

Same idempotent natural-key pattern as the CWL archive and Cito loaders: titles
by name, seasons by (year, title, league), events by (season, name), teams by
name, series by source_uid, games by (series, ordinal), stats by primary key.
Re-running the load converges.

Rows carry data_source='codwiki' and never overwrite another source. A series
whose source_uid already exists under a different data_source is left alone and
counted as a collision, and a game or stat row under it is skipped with it.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, cast

import psycopg

from ..identity import Aliases
from .transform import SEASON_LEAGUES, SOURCE, Series, TransformResult

_TITLE_SHORT = {
    "Black Ops 2": "BO2",
    "Ghosts": "GHO",
    "Advanced Warfare": "AW",
    "Black Ops 3": "BO3",
    "Infinite Warfare": "IW",
}


class CodWikiLoader:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]], aliases: Aliases):
        self.conn = conn
        self.aliases = aliases
        self.counts: dict[str, int] = defaultdict(int)
        self.collisions: list[dict[str, str]] = []
        self._season_ids: dict[int, int] = {}
        self._title_ids: dict[str, int] = {}
        self._event_ids: dict[tuple[int, str], int] = {}
        self._team_ids: dict[str, int] = {}
        self._mode_ids: dict[str, int] = {}
        self._map_ids: dict[tuple[str, int], int] = {}

    def _one(self, sql: str, params: tuple[object, ...]) -> int:
        row = self.conn.execute(sql, params).fetchone()
        assert row is not None
        return cast(int, row[0])

    def title_id(self, title: str) -> int:
        if title not in self._title_ids:
            self._title_ids[title] = self._one("SELECT id FROM titles WHERE name = %s", (title,))
        return self._title_ids[title]

    def season_id(self, season: int, title: str) -> int:
        if season not in self._season_ids:
            self._season_ids[season] = self._one(
                "SELECT id FROM seasons WHERE year = %s AND title_id = %s AND league = %s",
                (season, self.title_id(title), SEASON_LEAGUES[season]),
            )
        return self._season_ids[season]

    def event_id(self, season_id: int, name: str, played: Any) -> int:
        key = (season_id, name)
        if key not in self._event_ids:
            row = self.conn.execute(
                "SELECT id FROM events WHERE season_id = %s AND name = %s", (season_id, name)
            ).fetchone()
            if row is None:
                self.counts["events"] += 1
                self._event_ids[key] = self._one(
                    "INSERT INTO events (season_id, name, start_date, end_date) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (season_id, name, played, played),
                )
            else:
                self._event_ids[key] = cast(int, row[0])
        eid = self._event_ids[key]
        self.conn.execute(
            "UPDATE events SET start_date = least(start_date, %s), "
            "end_date = greatest(end_date, %s) WHERE id = %s",
            (played, played, eid),
        )
        return eid

    def team_id(self, name: str) -> int:
        canonical = self.aliases.team(name)
        if canonical not in self._team_ids:
            row = self.conn.execute(
                "SELECT id FROM teams WHERE lower(name) = lower(%s)", (canonical,)
            ).fetchone()
            if row is None:
                self.counts["teams"] += 1
                self._team_ids[canonical] = self._one(
                    "INSERT INTO teams (name) VALUES (%s) RETURNING id", (canonical,)
                )
            else:
                self._team_ids[canonical] = cast(int, row[0])
        return self._team_ids[canonical]

    def mode_id(self, slug: str) -> int:
        if slug not in self._mode_ids:
            self._mode_ids[slug] = self._one("SELECT id FROM game_modes WHERE slug = %s", (slug,))
        return self._mode_ids[slug]

    def map_id(self, name: str, title_id: int) -> int | None:
        if not name:
            return None
        key = (name, title_id)
        if key not in self._map_ids:
            self.conn.execute(
                "INSERT INTO maps (name, title_id) VALUES (%s, %s) "
                "ON CONFLICT (name, title_id) DO NOTHING",
                (name, title_id),
            )
            self._map_ids[key] = self._one(
                "SELECT id FROM maps WHERE name = %s AND title_id = %s", key
            )
        return self._map_ids[key]

    def load_series(self, s: Series) -> None:
        source_uid = f"{SOURCE}:{s.series_id}"
        existing = self.conn.execute(
            "SELECT id, data_source FROM series WHERE source_uid = %s", (source_uid,)
        ).fetchone()
        if existing is not None and existing[1] != SOURCE:
            self.collisions.append({"source_uid": source_uid, "held_by": str(existing[1])})
            return

        season_id = self.season_id(s.season, s.title)
        title_id = self.title_id(s.title)
        event_id = self.event_id(season_id, s.event, s.played_on)
        t1, t2 = self.team_id(s.team1), self.team_id(s.team2)
        series_id = self._one(
            """
            INSERT INTO series (event_id, team1_id, team2_id, team1_score, team2_score,
                                played_at, source_uid, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_uid) DO UPDATE SET
              team1_score = EXCLUDED.team1_score, team2_score = EXCLUDED.team2_score
            RETURNING id
            """,
            (event_id, t1, t2, s.team1_score, s.team2_score, s.played_on, source_uid, SOURCE),
        )
        self.counts["series"] += 1

        for game in s.games:
            winner = self.team_id(game.winner_team) if game.winner_team else None
            game_uid = f"{SOURCE}:{s.series_id}:{game.ordinal}"
            held = self.conn.execute(
                "SELECT data_source FROM games WHERE source_uid = %s", (game_uid,)
            ).fetchone()
            if held is not None and held[0] != SOURCE:
                self.collisions.append({"source_uid": game_uid, "held_by": str(held[0])})
                continue
            game_id = self._one(
                """
                INSERT INTO games (series_id, ordinal, map_id, mode_id, winner_team_id,
                                   source_uid, data_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (series_id, ordinal) DO UPDATE SET
                  map_id = EXCLUDED.map_id, mode_id = EXCLUDED.mode_id,
                  winner_team_id = EXCLUDED.winner_team_id
                RETURNING id
                """,
                (
                    series_id,
                    game.ordinal,
                    self.map_id(game.map_name, title_id),
                    self.mode_id(game.mode_slug),
                    winner,
                    game_uid,
                    SOURCE,
                ),
            )
            self.counts["games"] += 1
            for line in game.lines:
                cur = self.conn.execute(
                    """
                    INSERT INTO game_player_stats
                      (game_id, player_id, team_id, kills, deaths, hill_time, plants, defuses,
                       first_bloods, first_deaths, captures, extras, data_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (game_id, player_id) DO NOTHING
                    """,
                    (
                        game_id,
                        line.player_id,
                        self.team_id(line.team_name),
                        line.kills,
                        line.deaths,
                        line.hill_time,
                        line.plants,
                        line.defuses,
                        line.first_bloods,
                        line.first_deaths,
                        line.captures,
                        json.dumps(line.extras),
                        SOURCE,
                    ),
                )
                # Rows written, not rows offered: two wiki lines can name the
                # same player on one map, and the second is dropped.
                self.counts["stat_lines"] += cur.rowcount


def load(
    conn: psycopg.Connection[tuple[object, ...]],
    result: TransformResult,
    aliases: Aliases,
) -> dict[str, Any]:
    loader = CodWikiLoader(conn, aliases)
    for series in result.series:
        loader.load_series(series)
    return {
        "counts": dict(loader.counts),
        "collisions": loader.collisions,
        "dropped": result.dropped,
        "quarantined": len(result.quarantine),
    }
