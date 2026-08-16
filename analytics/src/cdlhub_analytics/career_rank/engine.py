"""Orchestrator: build every career-rank row, scoped to the frozen evaluation
population until an explicit full-archive run. Locked against
`ai/career-rank-preregistration.md`; task 8 (full archive) reuses this
unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from .. import style, writeback
from ..ratings.preflight import load_seasons
from . import awards, blend, breadth, evalpop, roster_strength

Conn = psycopg.Connection[tuple[object, ...]]

MODEL = "career_rank"
VERSION = "1.0.0"

# The one artifact this model publishes; carries the top-tens plus enough of
# each player's row for the metric-diff harness to key on. Following
# `metricdiff.run.REPORT_ARTIFACT`'s naming, not a name of its own invention.
ARTIFACT_NAME = "career_rank"


def params() -> dict[str, Any]:
    return {
        "basket_size": len(breadth.gold_basket()),
        "min_seasons_floor": evalpop.MIN_SEASONS,
        "award_top_tier_points": awards.TOP_TIER_POINTS,
        "award_second_tier_points": awards.SECOND_TIER_POINTS,
        "award_rookie_points": awards.ROOKIE_POINTS,
    }


@dataclass(frozen=True)
class PlayerRow:
    player_id: int
    career: blend.CareerRank
    seasons: dict[int, float]  # season_id -> breadth score, award applied
    season_sd: dict[int, float]  # season_id -> breadth.SeasonBreadth.sd
    net_of_teammates: dict[int, float]
    opponent_strength: dict[int, float]


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
    for sb in season_breadth:
        if restrict_to is not None and sb.player_id not in restrict_to:
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
            )
        )

    payload = {
        "model": MODEL,
        "version": VERSION,
        "evaluation_population": evalpop.stamp(),
        "restricted": restrict_to is not None,
        "n_players_scored": len(out),
        "basket_size": len(basket),
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
