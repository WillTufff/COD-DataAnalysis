"""Assemble parsed archive stat lines into schema entities and upsert them.

Idempotent by natural keys: titles by name, seasons by (year, title, league),
events by (season, name), teams by name, players by canonical handle, series by
the synthetic unique key `cwl-archive:<event-slug>:<series id>`, games by
(series, ordinal), stats by primary key. Re-running the import converges.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast

import psycopg

from .manifest import LEAGUE, MODE_SLUGS, ArchiveEvent
from .parse import ArchiveStatLine, ParsedEvent

SOURCE = "cwl-archive"

_TITLE_NAMES = {  # short_name -> full name (matches db/seeds reference data)
    "IW": ("Infinite Warfare", 2016),
    "WWII": ("WWII", 2017),
    "BO4": ("Black Ops 4", 2018),
}

_MODE_NAMES = {  # slug -> display name
    "hardpoint": "Hardpoint",
    "search-and-destroy": "Search & Destroy",
    "capture-the-flag": "Capture the Flag",
    "uplink": "Uplink",
    "control": "Control",
}


@dataclass
class GameKey:
    match_id: str
    ordinal: int
    mode: str
    map_name: str
    ended_at: datetime
    duration_s: int


class Loader:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]]):
        self.conn = conn
        self.counts: dict[str, int] = defaultdict(int)

    # ---- reference lookups (insert-if-missing, cached) ----

    def _one(self, sql: str, params: tuple[object, ...]) -> int:
        row = self.conn.execute(sql, params).fetchone()
        assert row is not None
        return cast(int, row[0])

    def title_id(self, short: str) -> int:
        name, release_year = _TITLE_NAMES[short]
        self.conn.execute(
            "INSERT INTO titles (name, short_name, release_year, era) "
            "VALUES (%s, %s, %s, 'cwl') ON CONFLICT (name) DO NOTHING",
            (name, short, release_year),
        )
        return self._one("SELECT id FROM titles WHERE name = %s", (name,))

    def mode_id(self, slug: str) -> int:
        self.conn.execute(
            "INSERT INTO game_modes (name, slug) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
            (_MODE_NAMES[slug], slug),
        )
        return self._one("SELECT id FROM game_modes WHERE slug = %s", (slug,))

    def season_id(self, year: int, title_id: int) -> int:
        self.conn.execute(
            "INSERT INTO seasons (year, title_id, league) VALUES (%s, %s, %s) "
            "ON CONFLICT (year, title_id, league) DO NOTHING",
            (year, title_id, LEAGUE),
        )
        return self._one(
            "SELECT id FROM seasons WHERE year = %s AND title_id = %s AND league = %s",
            (year, title_id, LEAGUE),
        )

    def map_id(self, name: str, title_id: int) -> int:
        self.conn.execute(
            "INSERT INTO maps (name, title_id) VALUES (%s, %s) "
            "ON CONFLICT (name, title_id) DO NOTHING",
            (name, title_id),
        )
        return self._one("SELECT id FROM maps WHERE name = %s AND title_id = %s", (name, title_id))

    def team_id(self, name: str) -> int:
        row = self.conn.execute("SELECT id FROM teams WHERE name = %s", (name,)).fetchone()
        if row is not None:
            return cast(int, row[0])
        self.counts["teams"] += 1
        return self._one("INSERT INTO teams (name) VALUES (%s) RETURNING id", (name,))

    def player_id(self, handle: str, archive_spellings: set[str]) -> int:
        row = self.conn.execute(
            "SELECT id FROM players WHERE lower(handle) = lower(%s)", (handle,)
        ).fetchone()
        if row is None:
            pid = self._one("INSERT INTO players (handle) VALUES (%s) RETURNING id", (handle,))
            self.counts["players"] += 1
        else:
            pid = cast(int, row[0])
        for spelling in archive_spellings:
            if spelling != handle:
                self.conn.execute(
                    "INSERT INTO player_aliases (player_id, alias) VALUES (%s, %s) "
                    "ON CONFLICT DO NOTHING",
                    (pid, spelling),
                )
        return pid

    def event_id(self, ev: ArchiveEvent, season_id: int) -> int:
        row = self.conn.execute(
            "SELECT id FROM events WHERE season_id = %s AND name = %s", (season_id, ev.name)
        ).fetchone()
        if row is not None:
            return cast(int, row[0])
        self.counts["events"] += 1
        return self._one(
            "INSERT INTO events (season_id, name, tier, start_date, end_date, location, is_lan)"
            " VALUES (%s, %s, %s, %s, %s, %s, true) RETURNING id",
            (season_id, ev.name, ev.tier, ev.start_date, ev.end_date, ev.location),
        )

    # ---- event assembly ----

    def load_event(self, parsed: ParsedEvent, player_ids: dict[str, int]) -> None:
        ev = parsed.event
        title_id = self.title_id(ev.title_short)
        season_id = self.season_id(ev.season_year, title_id)
        event_id = self.event_id(ev, season_id)
        mode_ids = {m: self.mode_id(s) for m, s in MODE_SLUGS.items()}

        # Group stat lines into games, games into series.
        by_series: dict[str, dict[str, list[ArchiveStatLine]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for line in parsed.lines:
            by_series[line.series_id][line.match_id].append(line)

        for series_key, games in by_series.items():
            # Deterministic game order: by end time, then match id.
            ordered = sorted(games.items(), key=lambda kv: (kv[1][0].ended_at, kv[0]))
            # Series teams in first-appearance order.
            series_teams: list[str] = []
            for _, lines in ordered:
                for ln in lines:
                    if ln.team not in series_teams:
                        series_teams.append(ln.team)
            if len(series_teams) != 2:
                raise ValueError(f"{ev.slug}:{series_key}: expected 2 teams, got {series_teams}")
            t1, t2 = series_teams
            t1_id, t2_id = self.team_id(t1), self.team_id(t2)

            wins = {t1: 0, t2: 0}
            game_rows: list[tuple[GameKey, dict[str, list[ArchiveStatLine]]]] = []
            for ordinal, (match_id, lines) in enumerate(ordered, start=1):
                by_team: dict[str, list[ArchiveStatLine]] = defaultdict(list)
                for ln in lines:
                    by_team[ln.team].append(ln)
                winners = {t for t, lns in by_team.items() if lns[0].won}
                if len(winners) == 1:
                    wins[next(iter(winners))] += 1
                game_rows.append(
                    (
                        GameKey(
                            match_id,
                            ordinal,
                            lines[0].mode,
                            lines[0].map_name,
                            lines[0].ended_at,
                            lines[0].duration_s,
                        ),
                        by_team,
                    )
                )

            played_at = min(gk.ended_at for gk, _ in game_rows)
            lp_key = f"{SOURCE}:{ev.slug}:{series_key}"
            row = self.conn.execute(
                """
                INSERT INTO series (event_id, team1_id, team2_id, team1_score, team2_score,
                                    played_at, round_label, liquipedia_match_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (liquipedia_match_id) DO UPDATE SET
                  team1_score = EXCLUDED.team1_score, team2_score = EXCLUDED.team2_score,
                  played_at = EXCLUDED.played_at
                RETURNING id
                """,
                (event_id, t1_id, t2_id, wins[t1], wins[t2], played_at, series_key, lp_key),
            ).fetchone()
            assert row is not None
            series_id = cast(int, row[0])
            self.counts["series"] += 1

            for gk, by_team in game_rows:
                team_scores = {t: lns[0].team_score for t, lns in by_team.items()}
                winners = {t for t, lns in by_team.items() if lns[0].won}
                winner_id = self.team_id(next(iter(winners))) if len(winners) == 1 else None
                grow = self.conn.execute(
                    """
                    INSERT INTO games (series_id, ordinal, map_id, mode_id, team1_score,
                                       team2_score, winner_team_id, duration_s, ended_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (series_id, ordinal) DO UPDATE SET
                      map_id = EXCLUDED.map_id, mode_id = EXCLUDED.mode_id,
                      team1_score = EXCLUDED.team1_score, team2_score = EXCLUDED.team2_score,
                      winner_team_id = EXCLUDED.winner_team_id,
                      duration_s = EXCLUDED.duration_s, ended_at = EXCLUDED.ended_at
                    RETURNING id
                    """,
                    (
                        series_id,
                        gk.ordinal,
                        self.map_id(gk.map_name, title_id),
                        mode_ids[gk.mode],
                        team_scores.get(t1),
                        team_scores.get(t2),
                        winner_id,
                        gk.duration_s,
                        gk.ended_at,
                    ),
                ).fetchone()
                assert grow is not None
                game_id = cast(int, grow[0])
                self.counts["games"] += 1

                for team_name, lns in by_team.items():
                    tid = self.team_id(team_name)
                    for ln in lns:
                        self.conn.execute(
                            """
                            INSERT INTO game_player_stats
                              (game_id, player_id, team_id, kills, deaths, assists, damage,
                               hill_time, first_bloods, plants, defuses, extras)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (game_id, player_id) DO UPDATE SET
                              team_id = EXCLUDED.team_id, kills = EXCLUDED.kills,
                              deaths = EXCLUDED.deaths, assists = EXCLUDED.assists,
                              damage = EXCLUDED.damage, hill_time = EXCLUDED.hill_time,
                              first_bloods = EXCLUDED.first_bloods, plants = EXCLUDED.plants,
                              defuses = EXCLUDED.defuses, extras = EXCLUDED.extras
                            """,
                            (
                                game_id,
                                player_ids[ln.player.lower()],
                                tid,
                                ln.kills,
                                ln.deaths,
                                ln.assists,
                                ln.damage,
                                ln.hill_time,
                                ln.first_bloods,
                                ln.plants,
                                ln.defuses,
                                json.dumps(ln.extras) if ln.extras else None,
                            ),
                        )
                        self.counts["game_player_stats"] += 1


def derive_roster_stints(
    events: list[ParsedEvent], player_ids: dict[str, int], loader: Loader
) -> None:
    """A stint = a run of consecutive events a player played for the same team.

    Start/end dates are the first/last event dates of the run — participation
    evidence, not contract dates (recorded via source='cwl-archive').
    """
    # player -> [(event, team)] ordered by event start date
    timeline: dict[str, list[tuple[ArchiveEvent, str]]] = defaultdict(list)
    for pe in sorted(events, key=lambda p: p.event.start_date):
        per_player_teams: dict[str, set[str]] = defaultdict(set)
        for ln in pe.lines:
            per_player_teams[ln.player.lower()].add(ln.team)
        for pl, teams in per_player_teams.items():
            for team in sorted(teams):
                timeline[pl].append((pe.event, team))

    loader.conn.execute("DELETE FROM roster_stints WHERE source = %s", (SOURCE,))
    for pl, entries in timeline.items():
        runs: list[tuple[str, date, date]] = []
        for ev, team in entries:
            if runs and runs[-1][0] == team:
                team_, start, _ = runs[-1]
                runs[-1] = (team_, start, ev.end_date)
            else:
                runs.append((team, ev.start_date, ev.end_date))
        for team, start, end in runs:
            loader.conn.execute(
                "INSERT INTO roster_stints (player_id, team_id, start_date, end_date, source)"
                " VALUES (%s, %s, %s, %s, %s)",
                (player_ids[pl], loader.team_id(team), start, end, SOURCE),
            )
            loader.counts["roster_stints"] += 1
