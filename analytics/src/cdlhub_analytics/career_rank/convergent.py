"""Convergent check: the season score against a third-party per-map rating.

`game_player_stats.extras->'ratings'` carries a rating per map from the source
that supplies the modern archive. Nothing in this project reads it, which makes
it an outside referent that no part of the engine was fitted to.

It is not a check on the whole engine. Measured: all 53,832 rows carrying it
are CDL-era rows, and 2013-2016 and the CWL carry none. What it can say is
whether the engine's season score agrees with an independent reading of the
same seasons, in the one era where both exist.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import cast

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]

# The minimum field for a season's agreement to mean anything. Same floor the
# shrinkage uses for a cohort, for the same reason: a rank correlation over
# four players is noise with a number attached.
MIN_COHORT = 5

_RATINGS_SQL = """
SELECT gps.player_id,
       ev.season_id,
       avg((gps.extras -> 'ratings' ->> 'overall')::double precision) AS rating,
       count(*) AS maps
FROM game_player_stats gps
JOIN games g   ON g.id = gps.game_id
JOIN series s  ON s.id = g.series_id
JOIN events ev ON ev.id = s.event_id
WHERE gps.extras -> 'ratings' ->> 'overall' IS NOT NULL
GROUP BY gps.player_id, ev.season_id
"""


def load_third_party(conn: Conn) -> dict[tuple[int, int], float]:
    """Mean third-party overall rating per player-season, where one exists."""
    rows = conn.execute(_RATINGS_SQL).fetchall()
    return {(cast(int, r[0]), cast(int, r[1])): cast(float, r[2]) for r in rows}


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        index = 0
        while index < len(order):
            stop = index
            while stop + 1 < len(order) and values[order[stop + 1]] == values[order[index]]:
                stop += 1
            shared = (index + stop) / 2.0
            for position in range(index, stop + 1):
                out[order[position]] = shared
            index = stop + 1
        return out

    rank_x, rank_y = ranks(xs), ranks(ys)
    n = len(xs)
    mean_x = sum(rank_x) / n
    mean_y = sum(rank_y) / n
    covariance = sum((a - mean_x) * (b - mean_y) for a, b in zip(rank_x, rank_y, strict=True))
    spread_x = sum((a - mean_x) ** 2 for a in rank_x)
    spread_y = sum((b - mean_y) ** 2 for b in rank_y)
    denominator = (spread_x * spread_y) ** 0.5
    return covariance / denominator if denominator else 0.0


def check(
    season_scores: Mapping[tuple[int, int], float],
    third_party: Mapping[tuple[int, int], float],
    seasons: Mapping[int, object],
) -> dict[str, object]:
    """Within-season rank agreement between the engine's score and the outside one.

    Read within season and never pooled: the two numbers are on unrelated
    scales and a pooled correlation would mostly measure which seasons each
    covers.
    """
    by_season: dict[int, list[tuple[float, float]]] = defaultdict(list)
    covered_eras: set[str] = set()
    for key, outside in third_party.items():
        own = season_scores.get(key)
        if own is None:
            continue
        by_season[key[1]].append((own, outside))
        season = seasons.get(key[1])
        era = getattr(season, "era_key", None)
        if era is not None:
            covered_eras.add(cast(str, era))

    per_season: list[dict[str, object]] = []
    for season_id, pairs in sorted(by_season.items()):
        if len(pairs) < MIN_COHORT:
            continue
        rho = _spearman([a for a, _ in pairs], [b for _, b in pairs])
        per_season.append({"season_id": season_id, "n": len(pairs), "rho": round(rho, 4)})

    rhos = sorted(cast(float, row["rho"]) for row in per_season)
    median = 0.0
    if rhos:
        middle = len(rhos) // 2
        median = rhos[middle] if len(rhos) % 2 else (rhos[middle - 1] + rhos[middle]) / 2.0
    return {
        "source": "game_player_stats.extras->'ratings'->>'overall'",
        "eras_covered": sorted(covered_eras),
        "scope": (
            "one era only: every row carrying a third-party rating is a "
            "CDL-era row, so this checks the modern era and says nothing "
            "about 2013-2016 or the CWL"
        ),
        "n_player_seasons": sum(cast(int, row["n"]) for row in per_season),
        "n_seasons": len(per_season),
        "median_rho": round(median, 4),
        "min_rho": rhos[0] if rhos else None,
        "max_rho": rhos[-1] if rhos else None,
        "per_season": per_season,
    }
