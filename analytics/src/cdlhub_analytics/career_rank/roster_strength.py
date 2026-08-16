"""Net-of-teammates and opponent-strength, per `ai/career-rank-preregistration.md`
section 2. Two separate stats, never blended, per the owner's decision.

**Deviation from the pre-registration text, made for a reason recorded here.**
The doc names `roster_stints` as the join. `roster_stints` is event-window,
not match-exact — [[cwl-stint-envelope-artifact]] already flags it as an
artifact that fakes concurrent rosters. `career.py` solved the identical
"who was on this player's team this season" problem with `modal_teams`,
built from `game_player_stats` box scores instead: the team a player
actually played the most maps for. That is the join used here too, for the
same reason `career.py` uses it — it is a measurement, not a stated
availability window. This does not change what section 2 asks for (a
teammate's own season VALUE, averaged), only how "teammate" is resolved.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import cast

import psycopg

from ..career import modal_teams

Conn = psycopg.Connection[tuple[object, ...]]

_VALUE_SQL = """
SELECT player_id, season_id, rating
FROM player_season_adjusted
WHERE run_id = (SELECT max(run_id) FROM player_season_adjusted)
  AND mode_id IS NULL AND rating IS NOT NULL
"""

_OPPONENT_MAPS_SQL = """
SELECT gps.player_id, ev.season_id, opp.team_id, count(*) AS maps
FROM game_player_stats gps
JOIN games g   ON g.id = gps.game_id
JOIN series s  ON s.id = g.series_id
JOIN events ev ON ev.id = s.event_id
JOIN game_player_stats opp
  ON opp.game_id = gps.game_id AND opp.team_id <> gps.team_id
GROUP BY gps.player_id, ev.season_id, opp.team_id
"""


def load_season_value(conn: Conn) -> dict[tuple[int, int], float]:
    rows = conn.execute(_VALUE_SQL).fetchall()
    return {(cast(int, r[0]), cast(int, r[1])): cast(float, r[2]) for r in rows}


@dataclass(frozen=True)
class NetOfTeammates:
    player_id: int
    season_id: int
    own_value: float
    teammate_mean: float
    net: float
    n_teammates: int


def net_of_teammates(
    conn: Conn, season_value: dict[tuple[int, int], float]
) -> list[NetOfTeammates]:
    """Own season VALUE minus the mean VALUE of the player's modal-team teammates.

    A teammate is anyone else whose modal team-season is the same team-season.
    Missing when the player has no VALUE row, or none of their teammates does
    (an all-rookie roster, or a teammate below the qualified-maps floor).
    """
    teams = modal_teams(conn)  # (player_id, season_id) -> team_id
    by_team_season: dict[tuple[int, int], list[int]] = defaultdict(list)
    for (player_id, season_id), team_id in teams.items():
        by_team_season[(team_id, season_id)].append(player_id)

    out: list[NetOfTeammates] = []
    for (player_id, season_id), team_id in sorted(teams.items()):
        own = season_value.get((player_id, season_id))
        if own is None:
            continue
        mates = [p for p in by_team_season[(team_id, season_id)] if p != player_id]
        mate_values = [
            season_value[(p, season_id)] for p in mates if (p, season_id) in season_value
        ]
        if not mate_values:
            continue
        teammate_mean = sum(mate_values) / len(mate_values)
        out.append(
            NetOfTeammates(
                player_id=player_id,
                season_id=season_id,
                own_value=own,
                teammate_mean=teammate_mean,
                net=own - teammate_mean,
                n_teammates=len(mate_values),
            )
        )
    return out


@dataclass(frozen=True)
class OpponentStrength:
    player_id: int
    season_id: int
    mean_opponent_value: float
    n_opponent_maps: int


def build_team_season_value(
    conn: Conn, season_value: dict[tuple[int, int], float]
) -> dict[tuple[int, int], float]:
    """(team_id, season_id) -> mean VALUE of players whose modal team that
    season is this team. The project has no separate team-level composite
    rating, so a team's own season VALUE is approximated as the mean of its
    modal-team players' VALUE — stated here rather than silently assumed.

    Spot-checked against an independent signal (season map win rate) before
    this engine used it: Pearson r = 0.77, Spearman r = 0.81 over 200
    team-season pairs with >= 10 maps (2026-08-15, run of this method
    against team win rate derived from `games.winner_team_id`). Strong
    enough to trust as a real proxy, not a coincidence of the join.
    """
    teams = modal_teams(conn)
    by_team_season: dict[tuple[int, int], list[float]] = defaultdict(list)
    for (player_id, season_id), team_id in teams.items():
        v = season_value.get((player_id, season_id))
        if v is not None:
            by_team_season[(team_id, season_id)].append(v)
    return {key: sum(vals) / len(vals) for key, vals in by_team_season.items()}


def opponent_strength(
    conn: Conn, team_season_value: dict[tuple[int, int], float]
) -> list[OpponentStrength]:
    """Mean opposing-team season VALUE across a player's maps that season.

    Display only, per the pre-registration doc: [[p2-opponent-adjustment-status]]
    found opponent correction a null at the season grain, so this does not
    correct anything — it reports how hard the season's slate of opponents
    was. `team_season_value` is the proxy from `build_team_season_value`,
    computed once and passed in so it is shared with `net_of_teammates`.
    """
    rows = conn.execute(_OPPONENT_MAPS_SQL).fetchall()
    acc: dict[tuple[int, int], list[tuple[float, int]]] = defaultdict(list)
    for r in rows:
        player_id, season_id, opp_team_id, maps = (
            cast(int, r[0]),
            cast(int, r[1]),
            cast(int, r[2]),
            cast(int, r[3]),
        )
        proxy = team_season_value.get((opp_team_id, season_id))
        if proxy is not None:
            acc[(player_id, season_id)].append((proxy, maps))

    out: list[OpponentStrength] = []
    for (player_id, season_id), entries in sorted(acc.items()):
        total_maps = sum(maps for _, maps in entries)
        if total_maps == 0:
            continue
        weighted_sum = sum(proxy * maps for proxy, maps in entries)
        out.append(
            OpponentStrength(
                player_id=player_id,
                season_id=season_id,
                mean_opponent_value=weighted_sum / total_maps,
                n_opponent_maps=total_maps,
            )
        )
    return out
