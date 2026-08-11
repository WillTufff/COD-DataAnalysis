"""Load transformed Cito series/games/stat lines into Postgres.

Same idempotent natural-key pattern as the CWL archive loader: titles by name,
seasons by (year, title, league), events by (season, name), teams by canonical
name (org-linked via aliases), players by lowercased handle, series by
source_uid (the Cito matchId), games by (series, ordinal), stats by primary
key. Re-running the load converges.

Rows carry data_source='cito'; stat-less series (surviving codwiki/score-only
records) load with no games — "series known, map stats unknown" is a designed
state, not an error.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from typing import Any, cast

import psycopg

from ..identity import Aliases, canonical_spellings
from .transform import MODE_SLUGS, SEASON_TITLES, Series, TransformResult

SOURCE = "cito"

_TITLE_NAMES = {  # short_name -> (full name, release_year); matches db/seeds
    "MW19": ("Modern Warfare (2019)", 2019),
    "BOCW": ("Black Ops Cold War", 2020),
    "VG": ("Vanguard", 2021),
    "MWII": ("Modern Warfare II", 2022),
    "MWIII": ("Modern Warfare III", 2023),
    "BO6": ("Black Ops 6", 2024),
    "BO7": ("Black Ops 7", 2025),
}

_MODE_NAMES = {
    "hardpoint": "Hardpoint",
    "search-and-destroy": "Search & Destroy",
    "control": "Control",
    "domination": "Domination",
    "overload": "Overload",
}


class CitoLoader:
    def __init__(self, conn: psycopg.Connection[tuple[object, ...]], aliases: Aliases):
        self.conn = conn
        self.aliases = aliases
        self.counts: dict[str, int] = defaultdict(int)
        self._team_ids: dict[str, int] = {}
        self._title_ids: dict[int, int] = {}
        self._season_ids: dict[int, int] = {}
        self._event_ids: dict[tuple[int, str], int] = {}
        self._mode_ids: dict[str, int] = {}
        self._map_ids: dict[tuple[str, int], int] = {}

    def _one(self, sql: str, params: tuple[object, ...]) -> int:
        row = self.conn.execute(sql, params).fetchone()
        assert row is not None
        return cast(int, row[0])

    def title_id(self, season: int) -> int:
        if season not in self._title_ids:
            short = SEASON_TITLES[season]
            name, release_year = _TITLE_NAMES[short]
            self.conn.execute(
                "INSERT INTO titles (name, short_name, release_year, era) "
                "VALUES (%s, %s, %s, 'cdl') ON CONFLICT (name) DO NOTHING",
                (name, short, release_year),
            )
            self._title_ids[season] = self._one("SELECT id FROM titles WHERE name = %s", (name,))
        return self._title_ids[season]

    def season_id(self, season: int) -> int:
        if season not in self._season_ids:
            title_id = self.title_id(season)
            self.conn.execute(
                "INSERT INTO seasons (year, title_id, league) VALUES (%s, %s, %s) "
                "ON CONFLICT (year, title_id, league) DO NOTHING",
                (season, title_id, "CDL"),
            )
            self._season_ids[season] = self._one(
                "SELECT id FROM seasons WHERE year = %s AND title_id = %s AND league = 'CDL'",
                (season, title_id),
            )
        return self._season_ids[season]

    def event_id(self, season: int, name: str, played: date) -> int:
        key = (season, name)
        if key not in self._event_ids:
            season_id = self.season_id(season)
            row = self.conn.execute(
                "SELECT id FROM events WHERE season_id = %s AND name = %s", (season_id, name)
            ).fetchone()
            if row is None:
                self.counts["events"] += 1
                eid = self._one(
                    "INSERT INTO events (season_id, name, start_date, end_date) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (season_id, name, played, played),
                )
            else:
                eid = cast(int, row[0])
            self._event_ids[key] = eid
        eid = self._event_ids[key]
        self.conn.execute(
            "UPDATE events SET start_date = least(start_date, %s), "
            "end_date = greatest(end_date, %s) WHERE id = %s",
            (played, played, eid),
        )
        return eid

    def mode_id(self, game_mode: str) -> int:
        slug = MODE_SLUGS[game_mode]
        if slug not in self._mode_ids:
            self.conn.execute(
                "INSERT INTO game_modes (name, slug) VALUES (%s, %s) ON CONFLICT (slug) DO NOTHING",
                (_MODE_NAMES[slug], slug),
            )
            self._mode_ids[slug] = self._one("SELECT id FROM game_modes WHERE slug = %s", (slug,))
        return self._mode_ids[slug]

    def map_id(self, name: str, title_id: int) -> int:
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

    def org_id(self, name: str) -> int:
        self.conn.execute(
            "INSERT INTO orgs (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (name,)
        )
        return self._one("SELECT id FROM orgs WHERE name = %s", (name,))

    def team_id(self, name: str) -> int:
        if name in self._team_ids:
            return self._team_ids[name]
        org = self.aliases.org_of(name)
        org_id = self.org_id(org) if org else None
        row = self.conn.execute("SELECT id FROM teams WHERE name = %s", (name,)).fetchone()
        if row is not None:
            tid = cast(int, row[0])
            # org_id follows the aliases 'orgs' map both ways: a team removed
            # from an org drops back to its own lineage (org_id NULL)
            self.conn.execute(
                "UPDATE teams SET org_id = %s WHERE id = %s AND org_id IS DISTINCT FROM %s",
                (org_id, tid, org_id),
            )
        else:
            self.counts["teams"] += 1
            tid = self._one(
                "INSERT INTO teams (name, org_id) VALUES (%s, %s) RETURNING id", (name, org_id)
            )
        self._team_ids[name] = tid
        return tid

    def build_player_ids(self, all_series: list[Series]) -> dict[str, int]:
        """One player id per lowercased handle, canonical casing corpus-wide.

        Handles that already exist in the DB (CWL-era players continuing into
        the CDL) join onto their existing row; variant spellings land in
        player_aliases.
        """
        spellings: dict[str, set[str]] = defaultdict(set)
        for s in all_series:
            for g in s.games:
                for ln in g.lines:
                    spellings[self.aliases.player(ln.player).lower()].add(
                        self.aliases.player(ln.player)
                    )
        canon = canonical_spellings(
            self.aliases.player(ln.player) for s in all_series for g in s.games for ln in g.lines
        )
        ids: dict[str, int] = {}
        for low in sorted(canon):
            row = self.conn.execute(
                "SELECT id FROM players WHERE lower(handle) = lower(%s)", (canon[low],)
            ).fetchone()
            if row is None:
                pid = self._one(
                    "INSERT INTO players (handle) VALUES (%s) RETURNING id", (canon[low],)
                )
                self.counts["players"] += 1
            else:
                pid = cast(int, row[0])
            for spelling in spellings[low]:
                if spelling != canon[low]:
                    self.conn.execute(
                        "INSERT INTO player_aliases (player_id, alias) VALUES (%s, %s) "
                        "ON CONFLICT DO NOTHING",
                        (pid, spelling),
                    )
            ids[low] = pid
        return ids

    def load_series(self, s: Series, player_ids: dict[str, int], warnings: list[str]) -> None:
        event_id = self.event_id(s.season, s.event, s.played_at.date())
        t1_id, t2_id = self.team_id(s.team1_name), self.team_id(s.team2_name)
        row = self.conn.execute(
            """
            INSERT INTO series (event_id, team1_id, team2_id, team1_score, team2_score,
                                best_of, played_at, round_label, source_uid, data_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (source_uid) DO UPDATE SET
              event_id = EXCLUDED.event_id, team1_id = EXCLUDED.team1_id,
              team2_id = EXCLUDED.team2_id, team1_score = EXCLUDED.team1_score,
              team2_score = EXCLUDED.team2_score, best_of = EXCLUDED.best_of,
              played_at = EXCLUDED.played_at, round_label = EXCLUDED.round_label
            RETURNING id
            """,
            (
                event_id,
                t1_id,
                t2_id,
                s.team1_score,
                s.team2_score,
                s.best_of,
                s.played_at,
                s.round_label,
                s.match_id,
                SOURCE,
            ),
        ).fetchone()
        assert row is not None
        series_id = cast(int, row[0])
        self.counts["series"] += 1

        title_id = self.title_id(s.season)
        slug_team = {s.team1_slug: t1_id, s.team2_slug: t2_id}
        for g in s.games:
            winner_id = slug_team.get(g.winner_slug) if g.winner_slug else None
            grow = self.conn.execute(
                """
                INSERT INTO games (series_id, ordinal, map_id, mode_id, team1_score,
                                   team2_score, winner_team_id, source_uid, data_source)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (series_id, ordinal) DO UPDATE SET
                  map_id = EXCLUDED.map_id, mode_id = EXCLUDED.mode_id,
                  team1_score = EXCLUDED.team1_score, team2_score = EXCLUDED.team2_score,
                  winner_team_id = EXCLUDED.winner_team_id, source_uid = EXCLUDED.source_uid
                RETURNING id
                """,
                (
                    series_id,
                    g.ordinal,
                    self.map_id(g.map_name, title_id),
                    self.mode_id(g.mode),
                    g.team1_score,
                    g.team2_score,
                    winner_id,
                    f"cito:{s.match_id}:{g.ordinal}",
                    SOURCE,
                ),
            ).fetchone()
            assert grow is not None
            game_id = cast(int, grow[0])
            self.counts["games"] += 1

            # Segments are rewritten whole rather than upserted: a map whose
            # breakdown shrinks between pulls must not keep the rows it lost.
            self.conn.execute("DELETE FROM game_segments WHERE game_id = %s", (game_id,))
            for seg in g.segments:
                seg_team = slug_team.get(seg.team_slug)
                if seg_team is None:
                    continue
                self.conn.execute(
                    """
                    INSERT INTO game_segments
                      (game_id, team_id, kind, ordinal, score, won, win_type, data_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (game_id, team_id, kind, ordinal) DO NOTHING
                    """,
                    (
                        game_id,
                        seg_team,
                        seg.kind,
                        seg.ordinal,
                        seg.score,
                        seg.won,
                        seg.win_type,
                        SOURCE,
                    ),
                )
                self.counts["game_segments"] += 1

            per_player: dict[int, Any] = {}
            for ln in g.lines:
                pid = player_ids[self.aliases.player(ln.player).lower()]
                if pid in per_player:
                    warnings.append(
                        f"{s.match_id} map {g.ordinal}: duplicate stat line for "
                        f"{ln.player}, keeping first"
                    )
                    continue
                per_player[pid] = ln
                tid = slug_team.get(ln.team_slug)
                if tid is None:
                    warnings.append(
                        f"{s.match_id} map {g.ordinal}: stat line team slug "
                        f"{ln.team_slug} not in series, skipped"
                    )
                    continue
                st = ln.stats
                clutches = st.get("clutches") or {}
                control = st.get("control") or {}
                ratings = st.get("ratings") or {}
                self.conn.execute(
                    """
                    INSERT INTO game_player_stats
                      (game_id, player_id, team_id, kills, deaths, assists, damage,
                       hill_time, contested_hill_time, first_bloods, first_deaths,
                       plants, defuses, captures, highest_streak, non_traded_kills,
                       snd_rounds, clutch_1v1, clutch_1v2, clutch_1v3, clutch_1v4,
                       ctl_attack_rounds, ctl_defense_rounds, ctl_zone_captures,
                       extras, data_source)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (game_id, player_id) DO UPDATE SET
                      team_id = EXCLUDED.team_id, kills = EXCLUDED.kills,
                      deaths = EXCLUDED.deaths, assists = EXCLUDED.assists,
                      damage = EXCLUDED.damage, hill_time = EXCLUDED.hill_time,
                      contested_hill_time = EXCLUDED.contested_hill_time,
                      first_bloods = EXCLUDED.first_bloods,
                      first_deaths = EXCLUDED.first_deaths,
                      plants = EXCLUDED.plants, defuses = EXCLUDED.defuses,
                      captures = EXCLUDED.captures,
                      highest_streak = EXCLUDED.highest_streak,
                      non_traded_kills = EXCLUDED.non_traded_kills,
                      snd_rounds = EXCLUDED.snd_rounds,
                      clutch_1v1 = EXCLUDED.clutch_1v1, clutch_1v2 = EXCLUDED.clutch_1v2,
                      clutch_1v3 = EXCLUDED.clutch_1v3, clutch_1v4 = EXCLUDED.clutch_1v4,
                      ctl_attack_rounds = EXCLUDED.ctl_attack_rounds,
                      ctl_defense_rounds = EXCLUDED.ctl_defense_rounds,
                      ctl_zone_captures = EXCLUDED.ctl_zone_captures,
                      extras = EXCLUDED.extras
                    """,
                    (
                        game_id,
                        pid,
                        tid,
                        st.get("kills"),
                        st.get("deaths"),
                        st.get("assists"),
                        st.get("damage"),
                        st.get("hillTime"),
                        st.get("contestedHillTime"),
                        st.get("firstBloods"),
                        st.get("firstDeaths"),
                        st.get("plants"),
                        st.get("defuses"),
                        st.get("captures"),
                        st.get("highestStreak"),
                        st.get("nonTradedKills"),
                        st.get("sndRounds"),
                        clutches.get("oneVsOne"),
                        clutches.get("oneVsTwo"),
                        clutches.get("oneVsThree"),
                        clutches.get("oneVsFour"),
                        control.get("attackRounds"),
                        control.get("defenseRounds"),
                        control.get("zoneCaptures"),
                        json.dumps({"ratings": ratings}) if ratings else None,
                        SOURCE,
                    ),
                )
                self.counts["game_player_stats"] += 1


def load(conn: psycopg.Connection[tuple[object, ...]], result: TransformResult) -> dict[str, int]:
    aliases = Aliases.load()
    loader = CitoLoader(conn, aliases)
    player_ids = loader.build_player_ids(result.series)
    for s in sorted(result.series, key=lambda s: (s.played_at, s.match_id)):
        loader.load_series(s, player_ids, result.warnings)
    # orgs no team references (after an aliases 'orgs' regroup) are dropped
    cur = conn.execute(
        "DELETE FROM orgs WHERE NOT EXISTS (SELECT 1 FROM teams t WHERE t.org_id = orgs.id)"
    )
    if cur.rowcount:
        loader.counts["orphan_orgs_removed"] = cur.rowcount
    return dict(sorted(loader.counts.items()))
