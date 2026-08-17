"""Net-of-teammates and opponent-strength: two separate stats, never blended.

**Deviation from the pre-registration text, made for a reason recorded here.**
The pre-registration names `roster_stints` as the join. `roster_stints` is
event-window, not match-exact, so it reports rosters as concurrent that were
never on the field together. `career.py` solved the identical
"who was on this player's team this season" problem with `modal_teams`,
built from `game_player_stats` box scores instead: the team a player
actually played the most maps for. That is the join used here too, for the
same reason `career.py` uses it — it is a measurement, not a stated
availability window. This does not change what is asked for (a teammate's
own season VALUE, averaged), only how "teammate" is resolved.
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

    The proxy is checked against an independent signal every run rather than
    once: `proxy_check` correlates it with season map win rate, taken from
    `games.winner_team_id`, and the result goes into the artifact the site
    reads. A number published on a page and measured once is a number nobody
    can audit.
    """
    teams = modal_teams(conn)
    by_team_season: dict[tuple[int, int], list[float]] = defaultdict(list)
    for (player_id, season_id), team_id in teams.items():
        v = season_value.get((player_id, season_id))
        if v is not None:
            by_team_season[(team_id, season_id)].append(v)
    return {key: sum(vals) / len(vals) for key, vals in by_team_season.items()}


# A team-season enters the proxy check at this many maps or more. Below it the
# win rate is mostly the schedule, and the check would measure noise.
PROXY_CHECK_MIN_MAPS = 10

_TEAM_MAP_RESULTS_SQL = """
SELECT e.season_id,
       side.team_id,
       count(*)                                                   AS maps,
       sum((g.winner_team_id = side.team_id)::int)                AS wins
FROM games g
JOIN series s ON s.id = g.series_id
JOIN events e ON e.id = s.event_id
CROSS JOIN LATERAL (VALUES (s.team1_id), (s.team2_id)) AS side(team_id)
WHERE g.winner_team_id IS NOT NULL
  AND side.team_id IS NOT NULL
  AND e.season_id IS NOT NULL
GROUP BY 1, 2
"""


def _ranks(values: list[float]) -> list[float]:
    """Ranks with ties averaged, so a tie does not order itself arbitrarily."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def _correlation(left: list[float], right: list[float]) -> float | None:
    n = len(left)
    if n < 3:
        return None
    mean_l, mean_r = sum(left) / n, sum(right) / n
    cov = sum((a - mean_l) * (b - mean_r) for a, b in zip(left, right, strict=True))
    var_l = sum((a - mean_l) ** 2 for a in left)
    var_r = sum((b - mean_r) ** 2 for b in right)
    if var_l <= 0.0 or var_r <= 0.0:
        return None
    return float(cov / (var_l**0.5 * var_r**0.5))


def proxy_check(
    conn: Conn,
    team_season_value: dict[tuple[int, int], float],
    min_maps: int = PROXY_CHECK_MIN_MAPS,
) -> dict[str, object]:
    """How well the team-strength proxy agrees with season map win rate.

    The project has no independent team rating, so the proxy is the mean VALUE
    of a team's modal-team players. Win rate is not part of that construction
    at any point, which is what makes it a check rather than a restatement.
    """
    pairs: list[tuple[float, float]] = []
    for season_id, team_id, maps, wins in conn.execute(_TEAM_MAP_RESULTS_SQL).fetchall():
        if int(cast(int, maps)) < min_maps:
            continue
        value = team_season_value.get((cast(int, team_id), cast(int, season_id)))
        if value is None:
            continue
        pairs.append((value, int(cast(int, wins)) / int(cast(int, maps))))
    values = [p[0] for p in pairs]
    rates = [p[1] for p in pairs]
    pearson = _correlation(values, rates)
    spearman = _correlation(_ranks(values), _ranks(rates))
    return {
        "signal": "season map win rate from games.winner_team_id",
        "min_maps": min_maps,
        "n_team_seasons": len(pairs),
        "pearson": None if pearson is None else round(pearson, 4),
        "spearman": None if spearman is None else round(spearman, 4),
    }


def opponent_strength(
    conn: Conn, team_season_value: dict[tuple[int, int], float]
) -> list[OpponentStrength]:
    """Mean opposing-team season VALUE across a player's maps that season.

    Display only. The opponent-adjustment phase measured opponent correction
    as a null at the season grain, so this does not
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
