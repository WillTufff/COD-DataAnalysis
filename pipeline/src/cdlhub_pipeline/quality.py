"""Post-ingest data-quality gate, run in CI.

Hard checks fail the run (exit 1). The coverage report is informational and
feeds /methodology — honesty about what each season's data does and doesn't
cover is part of the product.

    uv run python -m cdlhub_pipeline.quality [--dsn DSN]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass

import psycopg

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"


@dataclass
class Check:
    name: str
    sql: str  # must return zero rows to pass


HARD_CHECKS: tuple[Check, ...] = (
    Check(
        "stat_line_teams_in_series",
        """
        SELECT gps.game_id, gps.team_id
        FROM game_player_stats gps
        JOIN games g ON g.id = gps.game_id
        JOIN series s ON s.id = g.series_id
        WHERE gps.team_id NOT IN (s.team1_id, s.team2_id)
        """,
    ),
    Check(
        "game_winner_in_series_teams",
        """
        SELECT g.id FROM games g JOIN series s ON s.id = g.series_id
        WHERE g.winner_team_id IS NOT NULL
          AND g.winner_team_id NOT IN (s.team1_id, s.team2_id)
        """,
    ),
    Check(
        "series_score_matches_game_wins",
        """
        SELECT s.id FROM series s
        JOIN LATERAL (
          SELECT count(*) FILTER (WHERE g.winner_team_id = s.team1_id) AS w1,
                 count(*) FILTER (WHERE g.winner_team_id = s.team2_id) AS w2
          FROM games g WHERE g.series_id = s.id
        ) gw ON true
        WHERE s.team1_score IS NOT NULL
          AND (s.team1_score <> gw.w1 OR s.team2_score <> gw.w2)
        """,
    ),
    Check(
        "negative_stats",
        """
        SELECT game_id, player_id FROM game_player_stats
        WHERE kills < 0 OR deaths < 0 OR assists < 0 OR damage < 0 OR hill_time < 0
        """,
    ),
    Check(
        "duplicate_series_key",
        """
        SELECT liquipedia_match_id FROM series WHERE liquipedia_match_id IS NOT NULL
        GROUP BY liquipedia_match_id HAVING count(*) > 1
        """,
    ),
    Check(
        "game_stat_rows_side_balance",
        """
        -- every game with stats must have stat lines for exactly 2 teams
        SELECT game_id FROM game_player_stats
        GROUP BY game_id HAVING count(DISTINCT team_id) <> 2
        """,
    ),
    Check(
        "orphan_events",
        "SELECT id FROM events WHERE season_id IS NULL",
    ),
    Check(
        "player_alias_collisions",
        """
        -- an alias must not equal another player's canonical handle
        SELECT pa.alias FROM player_aliases pa
        JOIN players p ON lower(p.handle) = lower(pa.alias) AND p.id <> pa.player_id
        """,
    ),
)

SOFT_CHECKS: tuple[Check, ...] = (
    # The CWL archive is missing the deciding map for a handful of series
    # (verified against the raw CSVs: e.g. 2019 pro-w6-12 OpTic-Splyce has 4
    # maps at 2-2 and no game 5). Undecided series are data, not errors; they
    # surface here so the count is visible, and rating models must skip them.
    Check(
        "undecided_series",
        "SELECT id FROM series WHERE team1_score IS NOT NULL AND team1_score = team2_score",
    ),
)

COVERAGE_SQL = """
SELECT se.year, t.short_name,
       count(DISTINCT e.id)  AS events,
       count(DISTINCT s.id)  AS series,
       count(DISTINCT g.id)  AS games,
       count(gps.player_id)  AS stat_lines,
       round(avg((gps.kills IS NOT NULL)::int)::numeric, 3)   AS kills_cov,
       round(avg((gps.damage IS NOT NULL)::int)::numeric, 3)  AS damage_cov,
       round(
         count(DISTINCT g.id) FILTER (WHERE gps.player_id IS NOT NULL)::numeric
         / NULLIF(count(DISTINCT g.id), 0), 3)                AS games_with_stats
FROM seasons se
JOIN titles t   ON t.id = se.title_id
JOIN events e   ON e.season_id = se.id
JOIN series s   ON s.event_id = e.id
JOIN games g    ON g.series_id = s.id
LEFT JOIN game_player_stats gps ON gps.game_id = g.id
GROUP BY se.year, t.short_name
ORDER BY se.year
"""


def run(dsn: str) -> int:
    failures = 0
    with psycopg.connect(dsn) as conn:
        print("== hard checks ==")
        for check in HARD_CHECKS:
            rows = conn.execute(check.sql).fetchall()
            status = "PASS" if not rows else f"FAIL ({len(rows)} rows, e.g. {rows[:3]})"
            if rows:
                failures += 1
            print(f"  {check.name:<35} {status}")

        print("== soft checks (warnings) ==")
        for check in SOFT_CHECKS:
            rows = conn.execute(check.sql).fetchall()
            status = "ok" if not rows else f"WARN ({len(rows)} rows)"
            print(f"  {check.name:<35} {status}")

        print("== coverage by season ==")
        cov = conn.execute(COVERAGE_SQL)
        cols = [d.name for d in cov.description or []]
        report = [dict(zip(cols, r, strict=True)) for r in cov.fetchall()]
        for row in report:
            print("  " + json.dumps(row, default=str))
    if failures:
        print(f"{failures} hard check(s) failed")
        return 1
    print("all hard checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    args = ap.parse_args(argv)
    return run(args.dsn)


if __name__ == "__main__":
    sys.exit(main())
