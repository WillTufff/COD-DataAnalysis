"""Award weight, per `ai/career-rank-preregistration.md` section 4 (owner-
confirmed 2026-08-15): folded into the season score, not shown only as a
label. A `player_id`-null award row is unresolved, never a loss.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import cast

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]

TOP_TIER = {"first_team", "rs_mvp", "fmvp", "captains_mvp"}
SECOND_TIER = {"second_team", "event_mvp", "mode_best"}
ROOKIE = {"roty"}
# unmapped rows are excluded from scoring entirely.

TOP_TIER_POINTS = 8.0
SECOND_TIER_POINTS = 4.0
ROOKIE_POINTS = 4.0

_AWARDS_SQL = """
SELECT player_id, season_id, award
FROM player_awards
WHERE player_id IS NOT NULL
ORDER BY player_id, season_id
"""


@dataclass(frozen=True)
class AwardCredit:
    player_id: int
    season_id: int
    points: float
    awards: tuple[str, ...]


def load_award_credits(conn: Conn) -> list[AwardCredit]:
    """One row per (player, season) with an award, points additive within the
    season but each capped by tier before the season sum, so five second-team
    mentions in a season cannot out-credit a genuine first-team season alone.
    ROTY only ever fires once per player across a career (first qualifying
    season), enforced by taking the earliest ROTY row and dropping the rest.
    """
    rows = conn.execute(_AWARDS_SQL).fetchall()
    by_player_season: dict[tuple[int, int], list[str]] = defaultdict(list)
    for r in rows:
        player_id, season_id, award = cast(int, r[0]), cast(int, r[1]), cast(str, r[2])
        by_player_season[(player_id, season_id)].append(award)

    # ROTY: keep only the earliest season per player.
    roty_seasons: dict[int, int] = {}
    for (player_id, season_id), awards in by_player_season.items():
        if any(a in ROOKIE for a in awards):
            roty_seasons[player_id] = min(roty_seasons.get(player_id, season_id), season_id)

    out: list[AwardCredit] = []
    for (player_id, season_id), awards in sorted(by_player_season.items()):
        points = 0.0
        kept: list[str] = []
        if any(a in TOP_TIER for a in awards):
            points += TOP_TIER_POINTS
            kept += [a for a in awards if a in TOP_TIER]
        if any(a in SECOND_TIER for a in awards):
            points += SECOND_TIER_POINTS
            kept += [a for a in awards if a in SECOND_TIER]
        if any(a in ROOKIE for a in awards) and roty_seasons.get(player_id) == season_id:
            points += ROOKIE_POINTS
            kept += [a for a in awards if a in ROOKIE]
        if points > 0.0:
            out.append(
                AwardCredit(
                    player_id=player_id,
                    season_id=season_id,
                    points=points,
                    awards=tuple(kept),
                )
            )
    return out


def apply(score: float, credit: AwardCredit | None) -> float:
    if credit is None:
        return score
    return min(100.0, score + credit.points)
