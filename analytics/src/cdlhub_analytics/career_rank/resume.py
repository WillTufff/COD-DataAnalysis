"""RESUME: what a player's finishes were worth, per season.

The season score in `breadth.py` measures how a player performed. This measures
what the team he was on finished, which is a different fact about the same year
and the one readers reach for first. It is built from three declarations, all
fixed before a result is read:

- **The curve.** `placement_curve` is steep and reaches exact zero at 16th, so
  a deep bracket run is worth something and a bracket appearance is worth
  nothing. A win is four times a second place.
- **The weight.** `event_weight` is the square root of the prize pool. Raw pool
  makes the 2020 Championship worth 46 times a $100k event inside one year,
  which lets a single tournament own a season; the root makes it 6.8 times.
- **The normalisation.** A season's credit is divided by the credit a team
  that won every title event that year would have earned, so 2020's thirteen
  titles and 2024's five produce comparable numbers.

Only title events earn credit, by `titles.TITLE_EVENT` — the same rule the chip
and ring counts read. Credit reaches a player through `event_rosters`, so a
finish nobody recorded a roster for pays nobody.

Two holes in the source, and what each one does here:

- `prize_pool` is null for all 57 title events in 2013-2016 and for three later
  ones. Only weight ratios inside a year matter, so a year with no known pool
  at all weights every event 1 and stays internally consistent; a lone unknown
  inside a year with pools takes that year's smallest known pool, which is the
  most conservative value the year supplies rather than a guess.
- A pooled finish is a pooled finish. The 2020 Launch Weekend published `1-4`,
  and reading its lower bound alone hands four teams a win. `pooled_placement`
  takes the mean of the curve across the range, which scores that finish 0.3534:
  more than a clean second place, nothing like a win, and the chip rule already
  refuses to call it a title.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from .titles import TITLE_EVENT

Conn = psycopg.Connection[tuple[object, ...]]

# A title event: id, season, year, prize pool where the source published one.
EventRow = tuple[int, int, int, float | None]
# A finish credited to a player: player, event, season, and the placement range.
FinishRow = tuple[int, int, int, int, int]

# The placement the curve reaches zero at. Sixteenth is the last place a
# bracket run distinguishes anyone: below it the field is the field.
CURVE_FLOOR = 16

CURVE_RULE = (
    f"(p^-2 - {CURVE_FLOOR}^-2) / (1 - {CURVE_FLOOR}^-2) for p <= {CURVE_FLOOR}, else 0; "
    "a pooled finish takes the mean of the curve across its range"
)
WEIGHT_RULE = (
    "sqrt(prize_pool); an unknown pool takes the year's smallest known pool, and a "
    "year with no known pool weights every event 1"
)
NORMALISATION_RULE = (
    "divided by the credit a team winning every title event that year would have earned"
)

_EVENTS_SQL = f"""
SELECT e.id, e.season_id, s.year, e.prize_pool
FROM events e
JOIN seasons s ON s.id = e.season_id
WHERE {TITLE_EVENT}
"""

# One row per player per finish: the roster is what attributes a placement to
# the people who earned it, and a finish with no roster row reaches nobody.
_EARNED_SQL = f"""
SELECT r.player_id, e.id, e.season_id, ep.placement_min, ep.placement_max
FROM event_rosters r
JOIN event_placements ep ON ep.event_id = r.event_id AND ep.team_id = r.team_id
JOIN events e            ON e.id = r.event_id
JOIN seasons s           ON s.id = e.season_id
WHERE {TITLE_EVENT}
"""

# Per year: the title wins that exist and the ones a roster can answer for.
# `anchors.chip_coverage` asks the same question from `PUBLISHED_FROM_YEAR`
# forward; a ring count is a career total, so this one starts at the archive.
_WIN_COVERAGE_SQL = f"""
SELECT s.year,
       count(*) AS wins,
       count(*) FILTER (
           WHERE EXISTS (SELECT 1 FROM event_rosters r
                         WHERE r.event_id = ep.event_id AND r.team_id = ep.team_id)
       ) AS attributable
FROM event_placements ep
JOIN events e  ON e.id = ep.event_id
JOIN seasons s ON s.id = e.season_id
WHERE {TITLE_EVENT} AND ep.placement_min = 1 AND ep.placement_max = 1
GROUP BY s.year
ORDER BY s.year
"""


@dataclass(frozen=True)
class SeasonResume:
    player_id: int
    season_id: int
    resume: float  # credit as a share of the year's winnable credit, 0..1
    credit: float  # the raw sum, in weighted curve units
    year_credit: float  # the denominator, published so the share can be undone
    events: int


def placement_curve(placement: int) -> float:
    """What a single finishing position is worth, before the event's weight."""
    if placement < 1:
        raise ValueError(f"placement must be 1 or better, got {placement}")
    if placement > CURVE_FLOOR:
        return 0.0
    floor = 1.0 / float(CURVE_FLOOR) ** 2
    return (1.0 / float(placement) ** 2 - floor) / (1.0 - floor)


def pooled_placement(placement_min: int, placement_max: int) -> float:
    """A finish published as a range, scored as the mean of the range.

    `1-4` is not a win and not a fourth. Taking the mean is the only reading
    that neither invents a title nor throws away a deep run.
    """
    if placement_max < placement_min:
        raise ValueError(f"placement range {placement_min}-{placement_max} runs backwards")
    scoring = range(placement_min, min(placement_max, CURVE_FLOOR) + 1)
    return sum(placement_curve(p) for p in scoring) / (placement_max - placement_min + 1)


def event_weight(prize_pool: float | None, year_fallback: float | None) -> float:
    """The tournament's scale. Ratios inside a year are all that is used."""
    pool = prize_pool if prize_pool is not None else year_fallback
    if pool is None or pool <= 0.0:
        return 1.0
    return float(math.sqrt(pool))


def year_fallbacks(events: Sequence[EventRow]) -> dict[int, float | None]:
    """Per year: the smallest known prize pool, or None where none is known."""
    smallest: dict[int, float | None] = {}
    for _event_id, _season_id, year, pool in events:
        if pool is None or pool <= 0.0:
            smallest.setdefault(year, None)
            continue
        known = smallest.get(year)
        smallest[year] = pool if known is None else min(known, pool)
    return smallest


def load_events(conn: Conn) -> list[EventRow]:
    return [
        (
            cast(int, row[0]),
            cast(int, row[1]),
            int(cast(int, row[2])),
            None if row[3] is None else float(cast(float, row[3])),
        )
        for row in conn.execute(_EVENTS_SQL).fetchall()
    ]


def load_finishes(conn: Conn) -> list[FinishRow]:
    return [
        (
            cast(int, row[0]),
            cast(int, row[1]),
            cast(int, row[2]),
            int(cast(int, row[3])),
            int(cast(int, row[4])),
        )
        for row in conn.execute(_EARNED_SQL).fetchall()
    ]


def build(conn: Conn) -> list[SeasonResume]:
    events = load_events(conn)
    finishes = load_finishes(conn)
    return score(events, finishes)


def score(events: Sequence[EventRow], finishes: Sequence[FinishRow]) -> list[SeasonResume]:
    """Pure: the events with their pools, the finishes, and nothing else."""
    fallbacks = year_fallbacks(events)
    weights: dict[int, float] = {}
    season_year: dict[int, int] = {}
    year_credit: dict[int, float] = {}
    for event_id, season_id, year, pool in events:
        weight = event_weight(pool, fallbacks.get(year))
        weights[event_id] = weight
        season_year[season_id] = year
        # A win is `placement_curve(1)`, which is 1, so the year's winnable
        # credit is the sum of its weights.
        year_credit[year] = year_credit.get(year, 0.0) + weight

    credit: dict[tuple[int, int], float] = {}
    counted: dict[tuple[int, int], int] = {}
    for player_id, event_id, season_id, pmin, pmax in finishes:
        key = (player_id, season_id)
        earned = pooled_placement(pmin, pmax) * weights[event_id]
        credit[key] = credit.get(key, 0.0) + earned
        counted[key] = counted.get(key, 0) + 1

    out: list[SeasonResume] = []
    for (player_id, season_id), earned in sorted(credit.items()):
        available = year_credit.get(season_year[season_id], 0.0)
        out.append(
            SeasonResume(
                player_id=player_id,
                season_id=season_id,
                resume=earned / available if available > 0.0 else 0.0,
                credit=earned,
                year_credit=available,
                events=counted[(player_id, season_id)],
            )
        )
    return out


def coverage_from(conn: Conn) -> int | None:
    """The earliest year a ring count may be read as a career total.

    Walks back from the most recent year while every year's title wins reach a
    roster, and stops at the first that does not. A count published beside that
    year is a fact about a career; one published without it is a fact about how
    much of the archive happens to be loaded.
    """
    rows = [
        (int(cast(int, row[0])), int(cast(int, row[1])), int(cast(int, row[2])))
        for row in conn.execute(_WIN_COVERAGE_SQL).fetchall()
    ]
    if not rows:
        return None
    earliest: int | None = None
    previous: int | None = None
    for year, wins, attributable in reversed(rows):
        if attributable < wins or (previous is not None and year != previous - 1):
            break
        earliest, previous = year, year
    return earliest


def params() -> dict[str, Any]:
    return {
        "curve_floor": CURVE_FLOOR,
        "curve_rule": CURVE_RULE,
        "weight_rule": WEIGHT_RULE,
        "normalisation_rule": NORMALISATION_RULE,
    }
