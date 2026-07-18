"""Insight atoms. Spec: /methodology#insights.

Five kinds generated from real model outputs, each with plain-English headline,
backing numbers in detail (including evidence-link params), and a 0..1 score
for ranking. Never fabricates: every number is read back from model outputs or
the spine.

  outlier      season K/D >= 2 SD from cohort mean (era run)
  trend        monotonic season-over-season K/D percentile move across 3 seasons
  milestone    career maps-played thresholds; team all-time peak rating (Elo run)
  era_context  league slaying pace shifts between consecutive seasons per mode
  h2h_edge     lopsided head-to-head records (>= 8 decided series, >= 70%)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import psycopg

MIN_MAPS_SEASON = 30  # outlier/trend eligibility: real seasons, not cameos


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


@dataclass
class Atom:
    subject_type: str
    subject_id: int
    kind: str
    headline: str
    detail: dict[str, Any]
    score: float


def _rows(
    conn: psycopg.Connection[tuple[object, ...]], sql: str, params: dict[str, Any]
) -> list[tuple[object, ...]]:
    return conn.execute(sql, params).fetchall()


def outliers(conn: psycopg.Connection[tuple[object, ...]], era_run: int) -> list[Atom]:
    sql = """
    SELECT psa.player_id, p.handle, se.year, t.short_name, gm.name,
           psa.kd_raw, psa.kd_z, psa.kd_pctl, psa.maps_played
    FROM player_season_adjusted psa
    JOIN players p ON p.id = psa.player_id
    JOIN seasons se ON se.id = psa.season_id
    JOIN titles t ON t.id = se.title_id
    LEFT JOIN game_modes gm ON gm.id = psa.mode_id
    WHERE psa.run_id = %(run)s AND abs(psa.kd_z) >= 2.0
      AND psa.maps_played >= %(min_maps)s
    ORDER BY abs(psa.kd_z) DESC
    """
    out = []
    for r in _rows(conn, sql, {"run": era_run, "min_maps": MIN_MAPS_SEASON}):
        pid, handle, year, title, mode = (
            cast(int, r[0]),
            cast(str, r[1]),
            cast(int, r[2]),
            cast(str, r[3]),
            cast("str | None", r[4]),
        )
        kd, z, pctl, maps = (
            float(cast(float, r[5])),
            float(cast(float, r[6])),
            float(cast(float, r[7])),
            cast(int, r[8]),
        )
        scope = f"{mode} " if mode else ""
        direction = "best" if z > 0 else "worst"
        out.append(
            Atom(
                "player",
                pid,
                "outlier",
                f"{handle}'s {year} {scope}K/D of {kd:.2f} sat {abs(z):.1f} standard "
                f"deviations {'above' if z > 0 else 'below'} the {title} cohort — "
                f"among the {direction} qualified seasons of that era.",
                {
                    "season_year": year,
                    "title": title,
                    "mode": mode,
                    "kd_raw": round(kd, 3),
                    "kd_z": round(z, 2),
                    "kd_pctl": round(pctl, 3),
                    "maps_played": maps,
                    "era_run_id": era_run,
                },
                min(abs(z) / 3.5, 1.0),
            )
        )
    return out


def trends(conn: psycopg.Connection[tuple[object, ...]], era_run: int) -> list[Atom]:
    sql = """
    SELECT psa.player_id, p.handle,
           array_agg(se.year ORDER BY se.year) AS years,
           array_agg(psa.kd_pctl ORDER BY se.year) AS pctls
    FROM player_season_adjusted psa
    JOIN players p ON p.id = psa.player_id
    JOIN seasons se ON se.id = psa.season_id
    WHERE psa.run_id = %(run)s AND psa.mode_id IS NULL
      AND psa.maps_played >= %(min_maps)s AND psa.kd_pctl IS NOT NULL
    GROUP BY psa.player_id, p.handle
    HAVING count(*) >= 3
    """
    out = []
    for r in _rows(conn, sql, {"run": era_run, "min_maps": MIN_MAPS_SEASON}):
        pid, handle = cast(int, r[0]), cast(str, r[1])
        years = cast("list[int]", r[2])
        pctls = [float(x) for x in cast("list[float]", r[3])]
        deltas = [b - a for a, b in zip(pctls, pctls[1:], strict=False)]
        if all(d > 0 for d in deltas) or all(d < 0 for d in deltas):
            total = pctls[-1] - pctls[0]
            if abs(total) < 0.15:
                continue
            word = "climbed" if total > 0 else "slid"
            out.append(
                Atom(
                    "player",
                    pid,
                    "trend",
                    f"{handle}'s era-adjusted K/D percentile {word} every season across "
                    f"{years[0]}–{years[-1]}: "
                    + " → ".join(_ordinal(round(p * 100)) for p in pctls)
                    + ".",
                    {
                        "years": years,
                        "kd_pctls": [round(p, 3) for p in pctls],
                        "delta": round(total, 3),
                        "era_run_id": era_run,
                    },
                    min(abs(total) * 1.8, 1.0),
                )
            )
    return out


def milestones(conn: psycopg.Connection[tuple[object, ...]], elo_run: int) -> list[Atom]:
    out = []
    # Career map-count thresholds.
    sql = """
    SELECT gps.player_id, p.handle, count(*) AS maps
    FROM game_player_stats gps JOIN players p ON p.id = gps.player_id
    GROUP BY gps.player_id, p.handle HAVING count(*) >= 250
    ORDER BY maps DESC
    """
    for r in _rows(conn, sql, {}):
        pid, handle, maps = cast(int, r[0]), cast(str, r[1]), cast(int, r[2])
        threshold = 500 if maps >= 500 else 250
        out.append(
            Atom(
                "player",
                pid,
                "milestone",
                f"{handle} logged {maps} career maps in the CWL archive — past the "
                f"{threshold}-map mark, a top-volume career of the 2017–2019 era.",
                {"career_maps": maps, "threshold": threshold},
                0.35 + min(maps / 2000.0, 0.3),
            )
        )
    # All-time peak team ratings from the Elo run.
    sql = """
    SELECT tr.team_id, t.name, max(tr.rating_post) AS peak
    FROM team_ratings tr JOIN teams t ON t.id = tr.team_id
    WHERE tr.run_id = %(run)s
    GROUP BY tr.team_id, t.name
    ORDER BY peak DESC LIMIT 5
    """
    for rank, r in enumerate(_rows(conn, sql, {"run": elo_run}), start=1):
        tid, name, peak = cast(int, r[0]), cast(str, r[1]), float(cast(float, r[2]))
        out.append(
            Atom(
                "team",
                tid,
                "milestone",
                f"{name}'s peak Elo of {peak:.0f} is the #{rank} team strength "
                f"recorded across the 2017–2019 archive.",
                {"peak_elo": round(peak, 1), "rank": rank, "elo_run_id": elo_run},
                0.8 - 0.08 * (rank - 1),
            )
        )
    return out


def era_context(conn: psycopg.Connection[tuple[object, ...]]) -> list[Atom]:
    sql = """
    SELECT se.id, se.year, t.short_name, gm.name,
           sum(gps.kills + gps.deaths) / (sum(g.duration_s) / 600.0) AS pace
    FROM game_player_stats gps
    JOIN games g ON g.id = gps.game_id
    JOIN series s ON s.id = g.series_id
    JOIN events e ON e.id = s.event_id
    JOIN seasons se ON se.id = e.season_id
    JOIN titles t ON t.id = se.title_id
    JOIN game_modes gm ON gm.id = g.mode_id
    GROUP BY se.id, se.year, t.short_name, gm.name
    ORDER BY gm.name, se.year
    """
    by_mode: dict[str, list[tuple[int, int, str, float]]] = {}
    for r in _rows(conn, sql, {}):
        by_mode.setdefault(cast(str, r[3]), []).append(
            (cast(int, r[0]), cast(int, r[1]), cast(str, r[2]), float(cast(float, r[4])))
        )
    out = []
    for mode, seasons in by_mode.items():
        for (_, y1, t1, p1), (sid2, y2, t2, p2) in zip(seasons, seasons[1:], strict=False):
            change = (p2 - p1) / p1
            if abs(change) < 0.05:
                continue
            word = "rose" if change > 0 else "fell"
            out.append(
                Atom(
                    "season",
                    sid2,
                    "era_context",
                    f"League-wide {mode} engagement pace {word} {abs(change) * 100:.0f}% "
                    f"from {y1} {t1} to {y2} {t2} "
                    f"({p1:.1f} → {p2:.1f} kills+deaths per player per 10 min) — raw "
                    f"stats across these titles are not comparable without adjustment.",
                    {
                        "mode": mode,
                        "from": {"year": y1, "title": t1, "pace": round(p1, 2)},
                        "to": {"year": y2, "title": t2, "pace": round(p2, 2)},
                        "pct_change": round(change, 3),
                    },
                    min(abs(change) * 2.5, 1.0),
                )
            )
    return out


def h2h_edges(conn: psycopg.Connection[tuple[object, ...]]) -> list[Atom]:
    sql = """
    WITH decided AS (
      SELECT least(team1_id, team2_id) AS a, greatest(team1_id, team2_id) AS b,
             CASE WHEN (team1_score > team2_score) = (team1_id < team2_id)
                  THEN 1 ELSE 0 END AS a_won
      FROM series
      WHERE team1_score IS NOT NULL AND team1_score <> team2_score
    )
    SELECT a, b, count(*) AS n, sum(a_won) AS a_wins FROM decided
    GROUP BY a, b HAVING count(*) >= 8
    """
    out = []
    names = dict(
        (cast(int, r[0]), cast(str, r[1]))
        for r in conn.execute("SELECT id, name FROM teams").fetchall()
    )
    for r in _rows(conn, sql, {}):
        a, b, n, a_wins = (cast(int, r[0]), cast(int, r[1]), cast(int, r[2]), cast(int, r[3]))
        for winner, loser, wins in ((a, b, a_wins), (b, a, n - a_wins)):
            rate = wins / n
            if rate < 0.7:
                continue
            out.append(
                Atom(
                    "team",
                    winner,
                    "h2h_edge",
                    f"{names[winner]} won {wins} of {n} decided series against "
                    f"{names[loser]} across 2017–2019 ({rate * 100:.0f}%).",
                    {
                        "opponent_id": loser,
                        "opponent": names[loser],
                        "wins": wins,
                        "n": n,
                        "win_rate": round(rate, 3),
                    },
                    min((rate - 0.5) * 1.6 + n / 40.0, 1.0),
                )
            )
    return out


def generate(
    conn: psycopg.Connection[tuple[object, ...]], run_id: int, era_run: int, elo_run: int
) -> int:
    atoms = (
        outliers(conn, era_run)
        + trends(conn, era_run)
        + milestones(conn, elo_run)
        + era_context(conn)
        + h2h_edges(conn)
    )
    conn.cursor().executemany(
        "INSERT INTO insights (run_id, subject_type, subject_id, kind, headline, detail, score)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [
            (
                run_id,
                a.subject_type,
                a.subject_id,
                a.kind,
                a.headline,
                json.dumps(a.detail),
                a.score,
            )
            for a in atoms
        ],
    )
    return len(atoms)
