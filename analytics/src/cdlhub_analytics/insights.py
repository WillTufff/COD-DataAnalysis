"""Insight atoms. Spec: /methodology#insights.

Each atom carries a plain-English headline, the backing numbers in detail
(including evidence-link params), and a 0..1 score for ranking. Never
fabricates: every number is read back from model outputs or the spine.

Five kinds read the era adjustment and the Elo run:

  outlier      season K/D >= 2 SD from cohort mean (era run)
  trend        monotonic season-over-season K/D percentile move across 3 seasons
  milestone    deepest career map counts; team all-time peak rating (Elo run)
  era_context  league slaying pace shifts between consecutive seasons per mode
  h2h_edge     lopsided head-to-head records (>= 8 decided series, >= 70%)

Three more read the newer models' outputs (player_rating, winprob):

  what_wins    per (season x mode): learned objective-vs-slaying map weights
  rating_top   highest open-player-rating seasons in the archive
  model_null   backtested non-results worth publishing (e.g. momentum)

Six more come from the metric layer; see insights_metrics.

Generation ends with two passes that keep the ledger from restating itself:
`best_per_season` collapses one season's mode slices to the strongest, and
`cap_per_subject` stops any one subject flooding a kind.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import psycopg

MIN_MAPS_SEASON = 30  # outlier/trend eligibility: real seasons, not cameos
MIN_CAREER_MAPS = 250  # floor for a career-volume milestone
TOP_CAREER_VOLUME = 25  # and only the deepest careers past that floor


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


# How many atoms of one kind a single subject may contribute. Without a cap the
# per-cohort kinds emit the (season x mode) cross-product of one underlying
# fact: a player with a strong season produces an all-modes K/D outlier plus one
# per mode they played, all restating the same claim. Nine of thirty outliers in
# an early run belonged to one player. Two per kind keeps the genuinely
# different case — a player who was an outlier in two separate seasons — while
# dropping the restatements.
MAX_PER_SUBJECT_KIND = 2

# Kinds where one row per subject is already one fact, so a cap would only
# discard real findings: league-wide rankings and per-cohort model summaries.
UNCAPPED_KINDS = frozenset({"what_wins", "era_context", "meta_shift", "model_null"})


def cap_per_subject(atoms: list[Atom], limit: int = MAX_PER_SUBJECT_KIND) -> list[Atom]:
    """Keep each subject's `limit` highest-scoring atoms of each kind.

    Ties break on headline so the result does not depend on row order coming
    back from Postgres — two runs over the same data emit the same feed.
    """
    ranked = sorted(atoms, key=lambda a: (-a.score, a.headline))
    kept: list[Atom] = []
    seen: dict[tuple[str, int, str], int] = {}
    for a in ranked:
        if a.kind in UNCAPPED_KINDS:
            kept.append(a)
            continue
        key = (a.subject_type, a.subject_id, a.kind)
        if seen.get(key, 0) >= limit:
            continue
        seen[key] = seen.get(key, 0) + 1
        kept.append(a)
    return kept


def best_per_season(atoms: list[Atom]) -> list[Atom]:
    """Collapse atoms that restate one season to the strongest of them.

    `outlier` reads `player_season_adjusted`, which carries an all-modes row
    plus one row per mode. All of them say "this season's K/D beat the cohort",
    so only the most extreme is a finding; the rest are the same finding sliced
    by mode. Atoms with no season in their detail pass through untouched.
    """
    best: dict[tuple[str, int, str, object], Atom] = {}
    passthrough: list[Atom] = []
    for a in atoms:
        year = a.detail.get("season_year")
        if year is None:
            passthrough.append(a)
            continue
        key = (a.subject_type, a.subject_id, a.kind, year)
        prior = best.get(key)
        if prior is None or (a.score, a.headline) > (prior.score, prior.headline):
            best[key] = a
    return passthrough + list(best.values())


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
                f"deviations {'above' if z > 0 else 'below'} the {title} cohort, "
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
    # Career volume, as a rank rather than a threshold. A bare "past the
    # 250-map mark" cleared for 75 of 273 players, which is a fact about the
    # threshold rather than about the player; the rank says how rare the career
    # actually was, and the cut keeps the claim to careers that stand out.
    sql = """
    SELECT gps.player_id, p.handle, count(*) AS maps
    FROM game_player_stats gps JOIN players p ON p.id = gps.player_id
    GROUP BY gps.player_id, p.handle HAVING count(*) >= %(min_maps)s
    ORDER BY maps DESC, p.handle
    LIMIT %(top_n)s
    """
    params = {"min_maps": MIN_CAREER_MAPS, "top_n": TOP_CAREER_VOLUME}
    for rank, r in enumerate(_rows(conn, sql, params), start=1):
        pid, handle, maps = cast(int, r[0]), cast(str, r[1]), cast(int, r[2])
        out.append(
            Atom(
                "player",
                pid,
                "milestone",
                f"{handle} logged {maps} career maps in the CWL archive, the "
                f"{_ordinal(rank)}-most of any player in it.",
                {"career_maps": maps, "rank": rank, "threshold": MIN_CAREER_MAPS},
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
                    f"({p1:.1f} → {p2:.1f} kills+deaths per player per 10 min).",
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


def what_wins(conn: psycopg.Connection[tuple[object, ...]], pr_run: int) -> list[Atom]:
    """One finding per (season × mode): what the map-outcome regression says
    a one-SD team edge is worth, objective play vs slaying."""
    row = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = 'mode_weights'",
        (pr_run,),
    ).fetchone()
    if row is None:
        return []
    payload = cast("dict[str, Any]", row[0])
    out = []
    for cohort in payload["cohorts"]:
        w = cohort["weights"]
        # Which features are the slaying pair is recorded by the model, because
        # feature sets differ per cohort — SnD counts kills and deaths per round,
        # respawn modes per 10 minutes. Team kills mirror opponent deaths, so the
        # pair is near-collinear and ridge splits it; read them jointly.
        slaying = cohort.get("slaying_features") or ["kills_p10", "deaths_p10"]
        others = [k for k in w if k not in slaying]
        if not slaying or not others:
            continue
        slay = sum(abs(float(w[k])) for k in slaying) / len(slaying)
        # Everything the cohort measured beyond the gunfight, by magnitude: a
        # first-death rate earns its weight through a negative coefficient.
        obj = sum(abs(float(w[k])) for k in others) / len(others)
        if slay <= 0.0:
            continue
        ratio = obj / slay
        year, title, mode = cohort["year"], cohort["title"], cohort["mode"]
        if ratio >= 1.3:
            reading = (
                f"the objective, not the gunfight, carried maps: a one-SD team edge in "
                f"objective play was worth {ratio:.1f}x the same edge in slaying"
            )
        elif ratio <= 0.6:
            reading = (
                f"slaying decided maps: a one-SD objective edge was worth only "
                f"{ratio:.1f}x the equivalent slaying edge"
            )
        else:
            reading = f"objective play and slaying carried nearly equal weight ({ratio:.1f}x)"
        out.append(
            Atom(
                "season",
                cast(int, cohort["season_id"]),
                "what_wins",
                f"In {year} {title} {mode}, {reading} (regression over {cohort['n_maps']} maps).",
                {
                    "year": year,
                    "title": title,
                    "mode": mode,
                    "n_maps": cohort["n_maps"],
                    "weights": w,
                    "obj_vs_slay": round(ratio, 2),
                    "player_rating_run_id": pr_run,
                },
                min(0.45 + abs(ratio - 1.0) * 0.3, 1.0),
            )
        )
    return out


def rating_top(conn: psycopg.Connection[tuple[object, ...]], pr_run: int) -> list[Atom]:
    sql = """
    SELECT psa.player_id, p.handle, se.year, t.short_name,
           psa.rating, psa.rating_sd, psa.maps_played
    FROM player_season_adjusted psa
    JOIN players p ON p.id = psa.player_id
    JOIN seasons se ON se.id = psa.season_id
    JOIN titles t ON t.id = se.title_id
    WHERE psa.run_id = %(run)s AND psa.mode_id IS NULL
      AND psa.rating IS NOT NULL AND psa.maps_played >= %(min_maps)s
    ORDER BY psa.rating DESC LIMIT 5
    """
    out = []
    for rank, r in enumerate(
        _rows(conn, sql, {"run": pr_run, "min_maps": MIN_MAPS_SEASON}), start=1
    ):
        pid, handle, year, title = (
            cast(int, r[0]),
            cast(str, r[1]),
            cast(int, r[2]),
            cast(str, r[3]),
        )
        rating, sd, maps = (
            float(cast(float, r[4])),
            float(cast(float, r[5])),
            cast(int, r[6]),
        )
        out.append(
            Atom(
                "player",
                pid,
                "rating_top",
                f"{handle}'s {year} {title} season rates {rating:.2f} ± {sd:.2f} on the "
                f"open player rating, the #{rank} qualified season in the archive "
                f"(league average 1.00).",
                {
                    "year": year,
                    "title": title,
                    "rating": round(rating, 3),
                    "rating_sd": round(sd, 3),
                    "maps_played": maps,
                    "rank": rank,
                    "player_rating_run_id": pr_run,
                },
                0.9 - 0.08 * (rank - 1),
            )
        )
    return out


def model_null(
    conn: psycopg.Connection[tuple[object, ...]], wp_run: int, glicko_run: int
) -> list[Atom]:
    """The momentum test: does anything beat Glicko-2 at series prediction?"""
    sql = "SELECT brier, n_predictions FROM backtests WHERE run_id = %s"
    wp = conn.execute(sql, (wp_run,)).fetchone()
    gl = conn.execute(sql, (glicko_run,)).fetchone()
    art = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = 'coefficients'",
        (wp_run,),
    ).fetchone()
    if wp is None or gl is None or art is None:
        return []
    wp_brier, n = float(cast(float, wp[0])), cast(int, wp[1])
    gl_brier = float(cast(float, gl[0]))
    payload = cast("dict[str, Any]", art[0])
    edge = gl_brier - wp_brier  # positive = challenger actually better
    if edge > 0.005:
        headline = (
            f"Adding recent form, head-to-head history, and rating uncertainty to "
            f"Glicko-2 improved series prediction: Brier {wp_brier:.4f} vs "
            f"{gl_brier:.4f} over {n} series."
        )
    else:
        headline = (
            f"Adding recent form and head-to-head history to Glicko-2 did not "
            f"improve series prediction: Brier {wp_brier:.4f} vs {gl_brier:.4f} "
            f"over {n} series."
        )
    return [
        Atom(
            "league",
            0,
            "model_null",
            headline,
            {
                "winprob_brier": round(wp_brier, 4),
                "glicko2_brier": round(gl_brier, 4),
                "n_series": n,
                "final_weights": payload.get("final_weights"),
                "winprob_run_id": wp_run,
            },
            0.85,
        )
    ]


def generate(
    conn: psycopg.Connection[tuple[object, ...]],
    run_id: int,
    era_run: int,
    elo_run: int,
    pr_run: int,
    wp_run: int,
    glicko_run: int,
    metric_run: int | None = None,
) -> int:
    from .insights_metrics import generate as metric_atoms

    atoms = (
        best_per_season(outliers(conn, era_run))
        + trends(conn, era_run)
        + milestones(conn, elo_run)
        + era_context(conn)
        + h2h_edges(conn)
        + what_wins(conn, pr_run)
        + rating_top(conn, pr_run)
        + model_null(conn, wp_run, glicko_run)
        + (metric_atoms(conn, metric_run) if metric_run is not None else [])
    )
    # One subject must not be able to flood a kind with restatements of the
    # same fact; see cap_per_subject.
    atoms = cap_per_subject(atoms)
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
