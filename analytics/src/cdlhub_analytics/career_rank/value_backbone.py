"""The VALUE backbone: `player_season_adjusted.rating` blended onto the breadth
score at a weight fixed before the run.

Breadth is this engine's declared season unit, and its basket is not the same
basket in every era — measured, no era carries all six metric families. VALUE
is built the same way in every era, which is the one thing breadth cannot
claim, so it enters as a corroborant rather than as a second axis. The weight
says so: 0.75 breadth to 0.25 VALUE, declared in the pre-registration before
this ran and not revisited after seeing where anyone landed.

The two are not independent. Within season they already agree at a Spearman of
0.785 in 2013-2016, 0.769 in the CDL and 0.550 in the CWL, so a larger VALUE
weight would buy less new information than its size suggests while displacing
the declared unit.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .breadth import MIN_SHRINK_COHORT, SeasonBreadth

BREADTH_WEIGHT = 0.75
VALUE_WEIGHT = 0.25

BLEND_RULE = (
    f"season score = {BREADTH_WEIGHT} * shrunk breadth + {VALUE_WEIGHT} * VALUE, "
    "VALUE mapped onto the breadth score's own location and scale within the "
    "same season; a season with no VALUE row, or a field too small or too flat "
    "to map against, is scored on breadth alone at full weight"
)


@dataclass(frozen=True)
class Blended:
    player_id: int
    season_id: int
    score: float
    breadth: float
    value_scaled: float | None  # None where the season is scored on breadth alone
    sd: float | None


def _moments(values: Sequence[float]) -> tuple[float, float]:
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, 0.0
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return mean, math.sqrt(variance)


def blend(
    rows: Sequence[SeasonBreadth],
    season_value: dict[tuple[int, int], float],
    breadth_weight: float = BREADTH_WEIGHT,
    value_weight: float = VALUE_WEIGHT,
) -> list[Blended]:
    """One blended score per season, VALUE put on the breadth scale first.

    The map is `mean(breadth) + (value - mean(value)) / sd(value) * sd(breadth)`
    over that season's own field: a declared linear map with no free parameter,
    applied within season. Like the shrinkage, it can never move a season
    against another era — it moves it against the players it played.

    A missing half renormalizes rather than scoring zero. A season with no VALUE
    row is the breadth score; so is a season whose field is smaller than
    `MIN_SHRINK_COHORT` or whose VALUE has no spread to standardize against,
    because there is then no field to map onto.
    """
    by_season: dict[int, list[SeasonBreadth]] = defaultdict(list)
    for row in rows:
        by_season[row.season_id].append(row)

    out: list[Blended] = []
    for season_id, cohort in sorted(by_season.items()):
        pairs = [
            (row, season_value.get((row.player_id, season_id)))
            for row in sorted(cohort, key=lambda r: r.player_id)
        ]
        rated = [(row, value) for row, value in pairs if value is not None]
        scalable = len(rated) >= MIN_SHRINK_COHORT
        if scalable:
            value_mean, value_sd = _moments([value for _, value in rated])
            breadth_mean, breadth_sd = _moments([row.score for row, _ in rated])
            scalable = value_sd > 0.0
        for row, value in pairs:
            if value is None or not scalable:
                out.append(
                    Blended(
                        player_id=row.player_id,
                        season_id=season_id,
                        score=row.score,
                        breadth=row.score,
                        value_scaled=None,
                        sd=row.sd,
                    )
                )
                continue
            scaled = breadth_mean + (value - value_mean) / value_sd * breadth_sd
            out.append(
                Blended(
                    player_id=row.player_id,
                    season_id=season_id,
                    score=breadth_weight * row.score + value_weight * scaled,
                    breadth=row.score,
                    value_scaled=scaled,
                    # The blend is a fixed multiple of the breadth score plus a
                    # term with no published width of its own, so the width
                    # that is carried forward is the breadth half's, scaled by
                    # its weight. Understating it would be the worse error.
                    sd=None if row.sd is None else row.sd * breadth_weight,
                )
            )
    return sorted(out, key=lambda row: (row.player_id, row.season_id))


def coverage(rows: Sequence[Blended]) -> dict[str, Any]:
    """How much of the run the VALUE half actually reached."""
    blended = sum(1 for row in rows if row.value_scaled is not None)
    return {
        "breadth_weight": BREADTH_WEIGHT,
        "value_weight": VALUE_WEIGHT,
        "rule": BLEND_RULE,
        "n_seasons": len(rows),
        "n_with_value": blended,
        "n_breadth_only": len(rows) - blended,
    }
