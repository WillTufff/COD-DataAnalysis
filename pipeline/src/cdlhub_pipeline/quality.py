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
import subprocess
import sys
from dataclasses import dataclass
from typing import Any, cast

import psycopg

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"

# The kill feed reconciles a player-map when its box-score deaths equal its
# NORMAL feed deaths. WWII lands at exactly 100% under this rule (see
# kill_feed_recon in 0007); IW carries a residual, mostly from feed deaths the
# archive box never recorded. The residual is data, not error: those player-maps
# are excluded from kill-feed metrics via the view, never patched.
RECON_MODEL = "kill_feed_reconciliation"
RECON_VERSION = "1.0.0"


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
    Check(
        # WWII reconciles perfectly; a break means the importer or the death
        # classification regressed. IW's residual is expected and not gated.
        "kill_feed_wwii_fully_reconciled",
        "SELECT game_id, player_id FROM kill_feed_recon WHERE title = 'WWII' AND NOT reconciled",
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


_RECON_BREAKDOWN = """
SELECT {dims},
       count(*)                                    AS player_maps,
       count(*) FILTER (WHERE reconciled)          AS reconciled,
       sum(box_deaths)                             AS box_deaths,
       sum(feed_deaths)                            AS feed_deaths,
       round(avg(reconciled::int)::numeric, 4)     AS rate
FROM kill_feed_recon
GROUP BY {dims}
ORDER BY {dims}
"""


def _breakdown(conn: psycopg.Connection[tuple[object, ...]], dims: str) -> list[dict[str, Any]]:
    cur = conn.execute(_RECON_BREAKDOWN.format(dims=dims))  # noqa: S608 (dims is a literal)
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def reconciliation_payload(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any]:
    """The kill_feed_reconciliation artifact: overall plus per-tier breakdowns.

    Per-event (title tier), per-mode, and per-tournament rollups, each carrying
    both the player-map match rate and the raw death totals behind it.
    """
    overall = _breakdown(conn, "1")  # single group; the constant collapses to all rows
    return {
        "rule": (
            "a player-map reconciles when box-score deaths equal its normal "
            "kill-feed deaths; suicides and team kills are excluded from both"
        ),
        "overall": overall[0] if overall else {},
        "by_title": _breakdown(conn, "title"),
        "by_mode": _breakdown(conn, "title, mode"),
        "by_tournament": _tournament_breakdown(conn),
    }


def _tournament_breakdown(conn: psycopg.Connection[tuple[object, ...]]) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT e.name AS event, r.title,
               count(*)                               AS player_maps,
               round(avg(r.reconciled::int)::numeric, 4) AS rate
        FROM kill_feed_recon r
        JOIN games g   ON g.id = r.game_id
        JOIN series s  ON s.id = g.series_id
        JOIN events e  ON e.id = s.event_id
        GROUP BY e.name, r.title
        ORDER BY r.title, e.name
        """
    )
    cols = [d.name for d in cur.description or []]
    return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def store_reconciliation(
    conn: psycopg.Connection[tuple[object, ...]], payload: dict[str, Any]
) -> None:
    """Persist the reconciliation summary as a model_artifacts row.

    Replaces any prior run for (model, version, data_through) in place, matching
    the analytics writeback convention.
    """
    row = conn.execute(
        "SELECT max(ended_at)::date FROM games g "
        "WHERE EXISTS (SELECT 1 FROM kill_events k WHERE k.game_id = g.id)"
    ).fetchone()
    data_through = row[0] if row else None

    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        sha = None

    existing = conn.execute(
        "SELECT id FROM model_runs WHERE model = %s AND version = %s AND data_through = %s",
        (RECON_MODEL, RECON_VERSION, data_through),
    ).fetchone()
    if existing is not None:
        conn.execute("DELETE FROM model_runs WHERE id = %s", (existing[0],))
    run_id = cast(
        int,
        conn.execute(
            "INSERT INTO model_runs (model, version, code_ref, params, data_through) "
            "VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (RECON_MODEL, RECON_VERSION, sha, json.dumps({}), data_through),
        ).fetchone()[0],  # type: ignore[index]
    )
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, RECON_MODEL, json.dumps(payload, default=str)),
    )


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

        print("== kill-feed reconciliation ==")
        payload = reconciliation_payload(conn)
        for title_row in payload["by_title"]:
            print("  " + json.dumps(title_row, default=str))
        store_reconciliation(conn, payload)
        conn.commit()
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
