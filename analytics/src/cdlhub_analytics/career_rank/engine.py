"""Orchestrator: build every career-rank row, scoped to the frozen evaluation
population until an explicit full-archive run. Locked against the
pre-registration; the full-archive task reuses this unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from .. import style, writeback
from ..maprows import PUBLISHED_FROM_YEAR
from ..ratings.preflight import load_seasons
from . import anchors, awards, blend, breadth, evalpop, resume, roster_strength

Conn = psycopg.Connection[tuple[object, ...]]

MODEL = "career_rank"
VERSION = "1.0.0"

# The one artifact this model publishes; carries the top-tens plus enough of
# each player's row for the metric-diff harness to key on. Following
# `metricdiff.run.REPORT_ARTIFACT`'s naming, not a name of its own invention.
ARTIFACT_NAME = "career_rank"

# The season score standardizes a player against everyone who cleared the map
# floor in their season and title, which is only a fair comparison where the
# field is comparable. `maprows.PUBLISHED_FROM_YEAR` holds the floor for the
# whole project, so what this model publishes and what the evaluation harness
# scores cannot drift apart. `seasons_withheld` publishes the count this run
# held back.
PUBLISH_FROM_YEAR = PUBLISHED_FROM_YEAR


def params() -> dict[str, Any]:
    return {
        "publish_from_year": PUBLISH_FROM_YEAR,
        "basket_size": len(breadth.gold_basket()),
        "min_seasons_floor": evalpop.MIN_SEASONS,
        "award_top_tier_points": awards.TOP_TIER_POINTS,
        "award_second_tier_points": awards.SECOND_TIER_POINTS,
        "award_rookie_points": awards.ROOKIE_POINTS,
        **resume.params(),
    }


@dataclass(frozen=True)
class PlayerRow:
    player_id: int
    career: blend.CareerRank
    seasons: dict[int, float]  # season_id -> breadth score, award applied
    season_sd: dict[int, float]  # season_id -> breadth.SeasonBreadth.sd
    net_of_teammates: dict[int, float]
    opponent_strength: dict[int, float]
    resume: dict[int, float]  # season_id -> finish credit, share of the year
    resume_credit: dict[int, float]  # the same, before the per-year division
    chips: int  # title wins over the whole career, not only published seasons
    rings: int


def build(
    conn: Conn, restrict_to: set[int] | None = None
) -> tuple[list[PlayerRow], dict[str, Any]]:
    seasons = load_seasons(conn)

    basket = breadth.gold_basket()
    points = breadth.load_metric_points(conn, basket)
    slice_maps = breadth.load_slice_maps(conn)
    season_breadth = breadth.build(points, slice_maps)

    award_credits = {(c.player_id, c.season_id): c for c in awards.load_award_credits(conn)}

    scored: list[blend.SeasonScore] = []
    season_score_by_key: dict[tuple[int, int], float] = {}
    season_sd_by_key: dict[tuple[int, int], float] = {}
    withheld = 0
    for sb in season_breadth:
        if restrict_to is not None and sb.player_id not in restrict_to:
            continue
        if seasons[sb.season_id].year < PUBLISH_FROM_YEAR:
            withheld += 1
            continue
        credit = award_credits.get((sb.player_id, sb.season_id))
        final = awards.apply(sb.score, credit)
        scored.append(blend.SeasonScore(sb.player_id, sb.season_id, final, sb.sd))
        season_score_by_key[(sb.player_id, sb.season_id)] = final
        if sb.sd is not None:
            season_sd_by_key[(sb.player_id, sb.season_id)] = sb.sd

    career_rows = blend.build(scored, seasons)

    season_value = roster_strength.load_season_value(conn)
    team_season_value = roster_strength.build_team_season_value(conn, season_value)
    net_rows = roster_strength.net_of_teammates(conn, season_value)
    opp_rows = roster_strength.opponent_strength(conn, team_season_value)

    net_by_player: dict[int, dict[int, float]] = {}
    for net_row in net_rows:
        net_by_player.setdefault(net_row.player_id, {})[net_row.season_id] = net_row.net
    opp_by_player: dict[int, dict[int, float]] = {}
    for opp_row in opp_rows:
        opp_by_player.setdefault(opp_row.player_id, {})[opp_row.season_id] = (
            opp_row.mean_opponent_value
        )

    # Finish credit is built over the whole archive and then attached to the
    # seasons this run publishes: the credit a 2015 win earned is a fact about
    # 2015, and withholding the season score does not change it.
    resume_by_player: dict[int, dict[int, float]] = {}
    resume_credit_by_player: dict[int, dict[int, float]] = {}
    for entry in resume.build(conn):
        resume_by_player.setdefault(entry.player_id, {})[entry.season_id] = entry.resume
        resume_credit_by_player.setdefault(entry.player_id, {})[entry.season_id] = entry.credit
    rings_covered_from = resume.coverage_from(conn)
    career_titles = anchors.resume(conn, [row.player_id for row in career_rows])

    seasons_by_player: dict[int, dict[int, float]] = {}
    for (player_id, season_id), score in season_score_by_key.items():
        seasons_by_player.setdefault(player_id, {})[season_id] = score
    season_sd_by_player: dict[int, dict[int, float]] = {}
    for (player_id, season_id), sd in season_sd_by_key.items():
        season_sd_by_player.setdefault(player_id, {})[season_id] = sd

    out: list[PlayerRow] = []
    for career_row in career_rows:
        out.append(
            PlayerRow(
                player_id=career_row.player_id,
                career=career_row,
                seasons=seasons_by_player.get(career_row.player_id, {}),
                season_sd=season_sd_by_player.get(career_row.player_id, {}),
                net_of_teammates=net_by_player.get(career_row.player_id, {}),
                opponent_strength=opp_by_player.get(career_row.player_id, {}),
                resume={
                    season_id: value
                    for season_id, value in resume_by_player.get(career_row.player_id, {}).items()
                    if season_id in seasons_by_player.get(career_row.player_id, {})
                },
                resume_credit={
                    season_id: value
                    for season_id, value in resume_credit_by_player.get(
                        career_row.player_id, {}
                    ).items()
                    if season_id in seasons_by_player.get(career_row.player_id, {})
                },
                chips=int(career_titles.get(career_row.player_id, {}).get("chips", 0)),
                rings=int(career_titles.get(career_row.player_id, {}).get("rings", 0)),
            )
        )

    payload = {
        "model": MODEL,
        "version": VERSION,
        "evaluation_population": evalpop.stamp(),
        "team_strength_proxy_check": roster_strength.proxy_check(conn, team_season_value),
        "restricted": restrict_to is not None,
        "n_players_scored": len(out),
        "publish_from_year": PUBLISH_FROM_YEAR,
        "seasons_withheld": withheld,
        "seasons_withheld_rule": (
            "a season before the floor is scored and not published: the field it "
            "would be standardized inside is not yet comparable to a league one"
        ),
        "basket_size": len(basket),
        # The finish component. It does not enter `career` — the fixed-weight
        # blend is R7 — so this is the whole of what the run says about it.
        "resume": {
            **resume.params(),
            "rings_covered_from": rings_covered_from,
            "n_player_seasons": sum(len(row.resume) for row in out),
        },
        "career": blend.artifact([r.career for r in out]),
        # Every scored player, not just the top ten: the metric-diff harness
        # keys a list by `player_id` (see `LIST_KEYS` in
        # `metricdiff/snapshot.py`), so this is what makes every player's
        # total/peak/best_three individually diffable across runs, not only
        # the leaderboard's head.
        "players": [
            {
                "player_id": row.player_id,
                "qualified": row.career.qualified,
                "n_seasons": row.career.n_seasons,
                "total": round(row.career.total, 2),
                "total_sd": None if row.career.total_sd is None else round(row.career.total_sd, 2),
                "peak": round(row.career.peak, 2),
                "peak_season_id": row.career.peak_season_id,
                "best_three": None
                if row.career.best_three is None
                else round(row.career.best_three, 2),
                "chips": row.chips,
                "rings": row.rings,
                "resume_total": round(sum(row.resume.values()), 4),
            }
            for row in out
        ],
    }
    return out, payload


def run_against_frozen(conn: Conn) -> tuple[list[PlayerRow], dict[str, Any]]:
    pointer = evalpop.frozen()
    if pointer is None:
        raise RuntimeError("no frozen evaluation population — run evalpop.freeze first")
    player_ids = set(evalpop.read_set(cast(str, pointer["cut"])))
    return build(conn, restrict_to=player_ids)


def run_full_archive(conn: Conn) -> tuple[list[PlayerRow], dict[str, Any]]:
    return build(conn, restrict_to=None)


def write(
    conn: Conn, restrict_to: set[int] | None = None
) -> tuple[list[PlayerRow], dict[str, Any], int]:
    """`build`, then publish it: a real `model_runs` row plus the artifact,
    so the metric-diff harness (`metricdiff/snapshot.py`, which snapshots
    every `model_runs` row's `model_artifacts`) picks this model up the same
    way it already picks up `career.py`'s. `build` itself stays pure — no
    connection writes — so tests can call it without touching `model_runs`.
    """
    out, payload = build(conn, restrict_to=restrict_to)
    run_id = writeback.open_run(conn, MODEL, VERSION, params(), style.data_through(conn))
    conn.execute(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        (run_id, ARTIFACT_NAME, json.dumps(payload, allow_nan=False)),
    )
    payload["run_id"] = run_id
    return out, payload, run_id
