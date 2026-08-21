"""Post-ingest data-quality gate, run in CI.

Hard checks fail the run (exit 1). The coverage report is informational and
feeds /methodology — honesty about what each season's data does and doesn't
cover is part of the product.

    uv run python -m cdlhub_pipeline.quality [--dsn DSN] [--reports DIR]

Alongside the printed output, each run writes `report.json` (the whole result)
and appends one line to `history.ndjson` (per-check row counts) under the
reports directory, so a run's verdict outlives its stdout.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg

from . import venue

_DEFAULT_DSN = "postgres://cdlhub:cdlhub@localhost:54329/cdlhub"

REPO_ROOT = Path(__file__).resolve().parents[3]
REPORTS_DIR = REPO_ROOT / "pipeline" / "snapshots" / "quality"
REPORT_FILENAME = "report.json"
HISTORY_FILENAME = "history.ndjson"
# Enough runs to see a check start failing and when; the file is one short line
# per run, so this is a display bound rather than a storage one.
HISTORY_LIMIT = 200

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
        # Enforced only where the map record is complete (decided-game count
        # equals the declared series score total). Score-only series, partial
        # map coverage, and winner-nulled inconsistent map sets are a designed
        # state — counted by the series_with_partial_game_coverage soft check,
        # not an error here.
        "series_score_matches_game_wins",
        """
        SELECT s.id FROM series s
        JOIN LATERAL (
          SELECT count(*) FILTER (WHERE g.winner_team_id IS NOT NULL) AS n,
                 count(*) FILTER (WHERE g.winner_team_id = s.team1_id) AS w1,
                 count(*) FILTER (WHERE g.winner_team_id = s.team2_id) AS w2
          FROM games g WHERE g.series_id = s.id
        ) gw ON true
        WHERE s.team1_score IS NOT NULL
          AND gw.n = s.team1_score + s.team2_score
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
        SELECT source_uid FROM series WHERE source_uid IS NOT NULL
        GROUP BY source_uid HAVING count(*) > 1
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
    # A map score that cannot be a map score. One row today: a 2025 Control map
    # arriving from LPDB as -1 to -1, which is a sentinel for "unknown" wearing
    # the type of a real number. It matters because a score is a *signed*
    # quantity to anything reading margin, where a null is skipped and a -1 is
    # believed. Soft rather than hard: the value is upstream and inventing a
    # replacement locally would be worse than declaring it.
    Check(
        "impossible_game_score",
        "SELECT id FROM games WHERE team1_score < 0 OR team2_score < 0",
    ),
    # The two ways a decided map can disagree with itself: the scoreline points
    # one way and `winner_team_id` the other, or the scores are level and a
    # winner is declared anyway. Today: one 2020 MW19 Search & Destroy at 3-6
    # to the team recorded as winning it, and three ties — two 2017 Uplink maps
    # level at regulation with a winner the archive knows, plus the -1 to -1
    # Control map above, which the tie check catches for a second reason. The
    # Uplink pair are not errors; the other two are, and neither is fixable from
    # here. Every model that orients on a signed margin drops these four and
    # publishes their game ids.
    Check(
        "game_winner_contradicts_score",
        """
        SELECT g.id FROM games g JOIN series s ON s.id = g.series_id
        WHERE g.team1_score IS NOT NULL AND g.team2_score IS NOT NULL
          AND g.winner_team_id IS NOT NULL
          AND g.team1_score <> g.team2_score
          AND ((g.team1_score > g.team2_score) <> (g.winner_team_id = s.team1_id))
        """,
    ),
    Check(
        "game_tied_with_a_declared_winner",
        """
        SELECT id FROM games
        WHERE team1_score IS NOT NULL AND team1_score = team2_score
          AND winner_team_id IS NOT NULL
        """,
    ),
    # The CWL archive is missing the deciding map for a handful of series
    # (verified against the raw CSVs: e.g. 2019 pro-w6-12 OpTic-Splyce has 4
    # maps at 2-2 and no game 5). Undecided series are data, not errors; they
    # surface here so the count is visible, and rating models must skip them.
    Check(
        "undecided_series",
        "SELECT id FROM series WHERE team1_score IS NOT NULL AND team1_score = team2_score",
    ),
    # Series whose game rows don't cover every declared map: score-only records
    # (surviving codwiki entries have no stats snapshot) and matches whose
    # provider payload misses maps. Visible here, excluded from the hard
    # score-vs-wins gate above.
    Check(
        "series_with_partial_game_coverage",
        """
        SELECT s.id FROM series s
        LEFT JOIN games g ON g.series_id = s.id AND g.winner_team_id IS NOT NULL
        WHERE s.team1_score IS NOT NULL
        GROUP BY s.id
        HAVING count(g.id) <> s.team1_score + s.team2_score
        """,
    ),
    # A round should appear once from each side with exactly one winner between
    # them. Cito populates the two sides' round lists independently, so some
    # rounds arrive one-sided and a handful arrive with neither side claiming
    # the win. A one-sided round is still readable — `won` names the side that
    # took it — so these are counted rather than dropped, and any model reading
    # rounds has to decide what to do with them.
    Check(
        "segment_rounds_missing_a_side",
        """
        SELECT game_id, kind, ordinal FROM game_segments
        WHERE kind <> 'hill'
        GROUP BY game_id, kind, ordinal HAVING count(*) <> 2
        """,
    ),
    Check(
        "segment_rounds_without_one_winner",
        """
        SELECT game_id, kind, ordinal FROM game_segments
        WHERE kind <> 'hill'
        GROUP BY game_id, kind, ordinal
        HAVING count(*) = 2 AND count(*) FILTER (WHERE won) <> 1
        """,
    ),
)

COVERAGE_SQL = """
SELECT se.year, t.short_name, s.data_source,
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
GROUP BY se.year, t.short_name, s.data_source
ORDER BY se.year, s.data_source
"""


# Which maps carry a within-map time axis (migration 0016). Reported per
# (season, mode) over every map of that mode, so a season with no segments
# shows as a zero row rather than as an absent one — the 2021-2023 hole is a
# fact about the source and has to be visible, not interpolated. The Cito
# breakdown is absent for BOCW, Vanguard and Modern Warfare II; there is no
# fetch that fixes it, because the payloads carrying it are already on disk.
SEGMENT_COVERAGE_SQL = """
SELECT se.year, t.short_name, gm.slug AS mode,
       count(DISTINCT g.id)                                        AS maps,
       count(DISTINCT g.id) FILTER (WHERE sg.game_id IS NOT NULL)  AS maps_with_segments,
       count(sg.*)                                                 AS segment_rows
FROM seasons se
JOIN titles t     ON t.id = se.title_id
JOIN events e     ON e.season_id = se.id
JOIN series s     ON s.event_id = e.id
JOIN games g      ON g.series_id = s.id
JOIN game_modes gm ON gm.id = g.mode_id
LEFT JOIN game_segments sg ON sg.game_id = g.id
GROUP BY se.year, t.short_name, gm.slug
HAVING count(DISTINCT g.id) > 0
ORDER BY se.year, gm.slug
"""

# How many players an age curve can be fitted on, stated here rather than
# discovered by the phase that needs it. A birthdate is not recoverable for the
# 79 without one: none of them carries a usable one in the LPDB player snapshot
# under the same handle, so this is a ceiling, not a loading gap. The per-season
# split is the part that matters — the CDL era is near-complete and the CWL era
# is not, which is exactly where a longevity argument would be made.
AGES_SQL = """
SELECT se.year,
       count(DISTINCT gps.player_id)                                       AS box_players,
       count(DISTINCT gps.player_id) FILTER (WHERE p.birthdate IS NOT NULL) AS with_birthdate
FROM game_player_stats gps
JOIN players p  ON p.id = gps.player_id
JOIN games g    ON g.id = gps.game_id
JOIN series s   ON s.id = g.series_id
JOIN events e   ON e.id = s.event_id
JOIN seasons se ON se.id = e.season_id
GROUP BY se.year ORDER BY se.year
"""

AGES_TOTALS_SQL = """
SELECT (SELECT count(*) FROM players),
       (SELECT count(*) FROM players WHERE birthdate IS NOT NULL),
       (SELECT count(DISTINCT player_id) FROM game_player_stats),
       (SELECT count(*) FROM players p WHERE p.birthdate IS NOT NULL
          AND EXISTS (SELECT 1 FROM game_player_stats g WHERE g.player_id = p.id))
"""

# Where the venue flag came from, per event, and how many maps ride on it.
# `is_lan` is a covariate the rating plan wants to condition on, and until this
# report existed it mixed a derived value with two loader defaults. Reading the
# location column instead is the trap it is published to prevent: nine of the
# 2020 weeks kept a host city after moving online, and CDL Major 4 2022 carries
# Brooklyn while every LPDB match under it is recorded Online.
VENUE_SQL = """
SELECT se.year, e.name, e.is_lan, e.location,
       count(g.id) AS maps
FROM events e
LEFT JOIN seasons se ON se.id = e.season_id
LEFT JOIN series s   ON s.event_id = e.id
LEFT JOIN games g    ON g.series_id = s.id
GROUP BY se.year, e.name, e.is_lan, e.location
ORDER BY se.year, e.name
"""

# The Control and SnD round taxonomy, which is a finding in its own right: a
# round won on ticks is a different event from one won on kills, and no
# published Call of Duty analysis has separated them.
WIN_TYPE_SQL = """
SELECT kind, coalesce(win_type, '(unreported)') AS win_type, count(*) AS rounds
FROM game_segments WHERE kind <> 'hill'
GROUP BY kind, win_type ORDER BY kind, count(*) DESC
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


# A page Liquipedia publishes a team finish on, at a tier this project models,
# that no local event answers to. It is the one hole a roster-coverage check
# cannot see: coverage measures the events that exist, so a title whose event
# was never created reads as a year with nothing missing. Soft rather than
# hard, because the wiki gains pages between ingests and a new one is news,
# not a defect.
# The circuit this project models. A tier-2 page outside it is a third-party
# invitational, a regional league or a creator event, and its absence from
# `events` is the scope working rather than a hole.
CATALOG_PREFIXES = ("Call_of_Duty_World_League/", "Call_of_Duty_League/")
CATALOG_TIERS = {"1", "2"}
CATALOG_TIER_TYPES = {"Qualifier", "Showmatch"}
CATALOG_NAME_EXCLUSIONS = ("Regular_Season", "All-Star", "Relegation", "Play-In")


def catalog_payload(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any]:
    """LPDB pages with a team finish that model no local event, by season."""
    from .identity import Aliases
    from .lpdb.load import SKIP_PREFIXES
    from .lpdb.pull import GAME_SEASONS, PLACEMENTS_PATH

    if not PLACEMENTS_PATH.exists():
        return {"checked": False, "pages": []}
    aliases = Aliases.load()
    held = {
        cast(str, page)
        for (page,) in conn.execute(
            "SELECT liquipedia_page FROM events WHERE liquipedia_page IS NOT NULL"
        ).fetchall()
    }
    pages: dict[str, dict[str, Any]] = {}
    for row in json.loads(PLACEMENTS_PATH.read_text()):
        page = str(row["pagename"])
        if page in held or page in pages or page.startswith(SKIP_PREFIXES):
            continue
        if not page.startswith(CATALOG_PREFIXES):
            continue
        if str(row.get("opponenttype") or "team") != "team":
            continue
        if aliases.lpdb_events.get(page, "") is None:
            continue
        if str(row.get("liquipediatier") or "") not in CATALOG_TIERS:
            continue
        if str(row.get("liquipediatiertype") or "") in CATALOG_TIER_TYPES:
            continue
        if any(part in page for part in CATALOG_NAME_EXCLUSIONS):
            continue
        pages[page] = {
            "pagename": page,
            "tournament": row.get("tournament"),
            "season": GAME_SEASONS[row["game"]],
            "tier": row.get("liquipediatier"),
        }
    ordered = sorted(pages.values(), key=lambda page: (page["season"], page["pagename"]))
    return {"checked": True, "pages": ordered}


def venue_payload(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any]:
    """Every event's venue flag, with the evidence class behind it.

    An event whose verdict comes from a curated entry is named, and an entry
    still marked `reviewed: false` is named again under `provisional` — those
    are applied, so the only thing keeping them honest is being printed.
    """
    rules = venue.VenueRules.load()
    events: list[dict[str, Any]] = []
    for year, name, is_lan, location, maps in conn.execute(VENUE_SQL).fetchall():
        curated = rules.get(cast("int | None", year), cast(str, name))
        events.append(
            {
                "season": year,
                "event": name,
                "is_lan": is_lan,
                "location": location,
                "maps": maps,
                "source": venue.SOURCE_CURATED if curated else venue.SOURCE_LPDB,
                "reviewed": bool(curated.get("reviewed", False)) if curated else True,
                "reason": curated.get("reason") if curated else None,
            }
        )
    undecided = [e for e in events if e["is_lan"] is None]
    provisional = [e for e in events if e["source"] == venue.SOURCE_CURATED and not e["reviewed"]]
    return {
        "rule": "curated verdict, else LPDB tournament type; location is never consulted",
        "events": events,
        "maps_lan": sum(e["maps"] for e in events if e["is_lan"] is True),
        "maps_online": sum(e["maps"] for e in events if e["is_lan"] is False),
        "maps_undecided": sum(e["maps"] for e in undecided),
        "undecided": [
            {"season": e["season"], "event": e["event"], "maps": e["maps"]} for e in undecided
        ],
        "provisional": [
            {"season": e["season"], "event": e["event"], "reason": e["reason"]} for e in provisional
        ],
        # Events branded with a venue that were not played on one. The count is
        # the reason `location` is not a LAN proxy, so it is published rather
        # than treated as a discrepancy to reconcile.
        "branded_but_online": [
            {"season": e["season"], "event": e["event"], "location": e["location"]}
            for e in events
            if e["is_lan"] is False and e["location"]
        ],
    }


def venue_lines(payload: dict[str, Any]) -> list[str]:
    lines = [
        f"rule: {payload['rule']}",
        f"maps: {payload['maps_lan']:,} LAN, {payload['maps_online']:,} online, "
        f"{payload['maps_undecided']:,} undecided",
    ]
    for entry in payload["undecided"]:
        lines.append(f"undecided  {entry['season']} {entry['event']} ({entry['maps']} maps)")
    for entry in payload["provisional"]:
        lines.append(f"provisional {entry['season']} {entry['event']}: {entry['reason']}")
    for entry in payload["branded_but_online"]:
        lines.append(
            f"branded but online  {entry['season']} {entry['event']} [{entry['location']}]"
        )
    return lines


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


def write_report(reports: Path, result: dict[str, Any]) -> None:
    """Save the run's result and append its per-check counts to the history.

    Best effort: a gate that passed must not turn into a failure because its
    report could not be written.
    """
    try:
        reports.mkdir(parents=True, exist_ok=True)
        (reports / REPORT_FILENAME).write_text(
            json.dumps(result, indent=2, default=str) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"could not write {REPORT_FILENAME}: {exc}", file=sys.stderr)
        return

    entry = {
        "at": result["generated_at"],
        "passed": result["passed"],
        "hard": {check["name"]: check["rows"] for check in result["hard_checks"]},
        "soft": {check["name"]: check["rows"] for check in result["soft_checks"]},
    }
    path = reports / HISTORY_FILENAME
    try:
        kept = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        kept = [line for line in kept if line.strip()][-(HISTORY_LIMIT - 1) :]
        kept.append(json.dumps(entry, default=str))
        path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"could not write {HISTORY_FILENAME}: {exc}", file=sys.stderr)


def run(dsn: str, reports: Path = REPORTS_DIR) -> int:
    started = datetime.now(UTC)
    failures = 0
    result: dict[str, Any] = {
        "generated_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "hard_checks": [],
        "soft_checks": [],
    }
    with psycopg.connect(dsn) as conn:
        print("== hard checks ==")
        for check in HARD_CHECKS:
            rows = conn.execute(check.sql).fetchall()
            status = "PASS" if not rows else f"FAIL ({len(rows)} rows, e.g. {rows[:3]})"
            if rows:
                failures += 1
            result["hard_checks"].append(
                {
                    "name": check.name,
                    "passed": not rows,
                    "rows": len(rows),
                    "examples": [str(row) for row in rows[:3]],
                }
            )
            print(f"  {check.name:<35} {status}")

        print("== soft checks (warnings) ==")
        for check in SOFT_CHECKS:
            rows = conn.execute(check.sql).fetchall()
            status = "ok" if not rows else f"WARN ({len(rows)} rows)"
            result["soft_checks"].append({"name": check.name, "rows": len(rows)})
            print(f"  {check.name:<35} {status}")

        print("== coverage by season ==")
        cov = conn.execute(COVERAGE_SQL)
        cols = [d.name for d in cov.description or []]
        report = [dict(zip(cols, r, strict=True)) for r in cov.fetchall()]
        for row in report:
            print("  " + json.dumps(row, default=str))
        result["coverage"] = report

        print("== ages ceiling ==")
        totals = conn.execute(AGES_TOTALS_SQL).fetchone()
        assert totals is not None
        by_season = [
            {"season": year, "box_players": box, "with_birthdate": known}
            for year, box, known in conn.execute(AGES_SQL).fetchall()
        ]
        ages = {
            "players": totals[0],
            "players_with_birthdate": totals[1],
            "box_score_players": totals[2],
            "box_score_players_with_birthdate": totals[3],
            "by_season": by_season,
        }
        result["ages"] = ages
        print(
            f"  age curves can be fitted on {ages['box_score_players_with_birthdate']} of "
            f"{ages['box_score_players']} players with box-score rows "
            f"({ages['players_with_birthdate']} of {ages['players']} players overall)"
        )
        for row in by_season:
            print("  " + json.dumps(row, default=str))

        print("== title catalog ==")
        catalog = catalog_payload(conn)
        result["title_catalog"] = catalog
        if not catalog["checked"]:
            print("  no placement snapshot; skipped")
        elif not catalog["pages"]:
            print("  every in-scope page has a local event")
        else:
            print(f"  {len(catalog['pages'])} in-scope page(s) with no local event")
            for row in catalog["pages"]:
                print(f"    {row['season']}  tier {row['tier']}  {row['pagename']}")

        print("== venue derivation ==")
        result["venue"] = venue_payload(conn)
        for line in venue_lines(result["venue"]):
            print("  " + line)

        print("== within-map segment coverage ==")
        seg = conn.execute(SEGMENT_COVERAGE_SQL)
        seg_cols = [d.name for d in seg.description or []]
        segments = [dict(zip(seg_cols, r, strict=True)) for r in seg.fetchall()]
        for row in segments:
            print("  " + json.dumps(row, default=str))
        win = conn.execute(WIN_TYPE_SQL)
        win_cols = [d.name for d in win.description or []]
        win_types = [dict(zip(win_cols, r, strict=True)) for r in win.fetchall()]
        for row in win_types:
            print("  " + json.dumps(row, default=str))
        result["segment_coverage"] = {"by_season_mode": segments, "win_types": win_types}

        print("== kill-feed reconciliation ==")
        payload = reconciliation_payload(conn)
        for title_row in payload["by_title"]:
            print("  " + json.dumps(title_row, default=str))
        result["reconciliation"] = {
            "overall": payload["overall"],
            "by_title": payload["by_title"],
        }
        store_reconciliation(conn, payload)
        conn.commit()

    result["passed"] = failures == 0
    result["failures"] = failures
    result["duration_s"] = round((datetime.now(UTC) - started).total_seconds(), 1)
    write_report(reports, result)

    if failures:
        print(f"{failures} hard check(s) failed")
        return 1
    print("all hard checks passed")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL", _DEFAULT_DSN))
    ap.add_argument("--reports", type=Path, default=REPORTS_DIR)
    args = ap.parse_args(argv)
    return run(args.dsn, args.reports)


if __name__ == "__main__":
    sys.exit(main())
