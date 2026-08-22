"""ACCOLADE: what a season was recognised as, per season.

`breadth.py` measures how a player performed and `resume.py` measures what his
team finished. This measures what the season was named — first team, an MVP, a
rookie of the year — which is neither of those and used to be folded into the
first one. It is built from three declarations, all fixed in
`ai/career-rank-preregistration.md` before a result was read:

- **The tier points.** Owner-confirmed 2026-08-15 and unchanged here: 8 for a
  top-tier honour, 4 for a second-tier one, 4 for a rookie of the year on the
  first qualifying season only. Points are capped per tier before the season
  sum, so five event MVPs cannot out-credit one first team, and the tiers stack
  because three recognitions in one season is more than one.
- **The normalisation.** A season's credit is divided by every credited point
  that year, so a year is a fixed budget shared among the people it recognised
  and 2016's eighteen first-team slots and 2024's four produce comparable
  numbers. The denominator is built over the whole archive and never over a
  run's population: restricting a run must not change what a season is worth.
- **The thin-year floor.** A year contributes nothing unless it named a
  season-level honour, meaning any scored kind but `event_mvp`. A year whose
  whole record is one event MVP has nothing to normalise against, and dividing
  by it would hand that one player the entire year.

A `player_id`-null award row is unresolved, never a loss: no season is reduced
for an award the record cannot attach to a player. An `unmapped` kind is
excluded from scoring and logged rather than guessed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]

TOP_TIER = {"first_team", "rs_mvp", "fmvp", "captains_mvp"}
SECOND_TIER = {"second_team", "event_mvp", "mode_best"}
ROOKIE = {"roty"}
# unmapped rows are excluded from scoring entirely.

# An honour for one tournament rather than for the season. Everything else
# scored is a season-level honour, and a year with none of those is thin.
EVENT_HONOURS = {"event_mvp"}

TOP_TIER_POINTS = 8.0
SECOND_TIER_POINTS = 4.0
ROOKIE_POINTS = 4.0

TIER_RULE = (
    f"top tier {TOP_TIER_POINTS:g}, second tier {SECOND_TIER_POINTS:g}, rookie of the year "
    f"{ROOKIE_POINTS:g} on the first qualifying season; capped per tier before the season "
    "sum and additive across tiers"
)
NORMALISATION_RULE = "divided by every credited point that year, over the whole archive"
THIN_YEAR_RULE = (
    "a year with no season-level honour contributes nothing; a season-level honour is any "
    f"scored kind but {', '.join(sorted(EVENT_HONOURS))}"
)

# Year comes from the join so the normalisation and the thin-year floor read
# the same calendar the rest of the engine does, not a season id's order.
_AWARDS_SQL = """
SELECT pa.player_id, pa.season_id, s.year, pa.award
FROM player_awards pa
JOIN seasons s ON s.id = pa.season_id
WHERE pa.player_id IS NOT NULL
ORDER BY pa.player_id, pa.season_id, pa.award
"""

# Rows the record cannot attach to a player. Published as unresolved and never
# scored as anybody's absence.
_UNRESOLVED_SQL = """
SELECT s.year, pa.award
FROM player_awards pa
JOIN seasons s ON s.id = pa.season_id
WHERE pa.player_id IS NULL
ORDER BY s.year, pa.award
"""

# player, season, year, award kind.
AwardRow = tuple[int, int, int, str]


@dataclass(frozen=True)
class AwardCredit:
    player_id: int
    season_id: int
    points: float
    awards: tuple[str, ...]


@dataclass(frozen=True)
class SeasonAccolade:
    player_id: int
    season_id: int
    accolade: float  # credit as a share of the year's awarded credit, 0..1
    credit: float  # the raw tier points, before the division
    year_credit: float  # the denominator, published so the share can be undone
    awards: tuple[str, ...]


def load_award_rows(conn: Conn) -> list[AwardRow]:
    return [
        (cast(int, r[0]), cast(int, r[1]), int(cast(int, r[2])), cast(str, r[3]))
        for r in conn.execute(_AWARDS_SQL).fetchall()
    ]


def load_award_credits(conn: Conn) -> list[AwardCredit]:
    return credits(load_award_rows(conn))


def credits(rows: Sequence[AwardRow]) -> list[AwardCredit]:
    """One row per (player, season) with an award, points additive within the
    season but each capped by tier before the season sum, so five second-team
    mentions in a season cannot out-credit a genuine first-team season alone.
    ROTY only ever fires once per player across a career (first qualifying
    season), enforced by taking the earliest ROTY row and dropping the rest.
    """
    by_player_season: dict[tuple[int, int], list[str]] = defaultdict(list)
    for player_id, season_id, _year, award in rows:
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
                    awards=tuple(sorted(kept)),
                )
            )
    return out


def thin_years(rows: Sequence[AwardRow]) -> set[int]:
    """Years that named no season-level honour, so have nothing to normalise
    against. Measured on the archive of 2026-08-22 this is 2013, 2014 and 2015,
    whose whole award record is three, one and one event MVP.
    """
    scored_kinds = TOP_TIER | SECOND_TIER | ROOKIE
    years = {year for _p, _s, year, _a in rows}
    with_season_honour = {
        year for _p, _s, year, award in rows if award in scored_kinds and award not in EVENT_HONOURS
    }
    return years - with_season_honour


def score(rows: Sequence[AwardRow]) -> list[SeasonAccolade]:
    """Pure: the award rows and nothing else. The denominator is the year's own
    awarded credit, so a season's accolade is its share of what the year
    recognised.
    """
    season_year = {season_id: year for _p, season_id, year, _a in rows}
    thin = thin_years(rows)

    year_credit: dict[int, float] = defaultdict(float)
    earned = credits(rows)
    for credit in earned:
        year_credit[season_year[credit.season_id]] += credit.points

    out: list[SeasonAccolade] = []
    for credit in earned:
        year = season_year[credit.season_id]
        available = 0.0 if year in thin else year_credit[year]
        out.append(
            SeasonAccolade(
                player_id=credit.player_id,
                season_id=credit.season_id,
                accolade=credit.points / available if available > 0.0 else 0.0,
                credit=credit.points,
                year_credit=available,
                awards=credit.awards,
            )
        )
    return out


def build(conn: Conn) -> list[SeasonAccolade]:
    return score(load_award_rows(conn))


def density(
    rows: Sequence[AwardRow], unresolved: Sequence[tuple[int, str]]
) -> list[dict[str, Any]]:
    """Per year: how much award credit the record holds, how many seasons carry
    it, how far the tiers stack, and how many rows reach nobody. Published so a
    reader can see that a 2016 first team and a 2024 one were normalised against
    very different years rather than assume they were not.
    """
    scored = score(rows)
    by_year: dict[int, list[SeasonAccolade]] = defaultdict(list)
    season_year = {season_id: year for _p, season_id, year, _a in rows}
    for entry in scored:
        by_year[season_year[entry.season_id]].append(entry)
    unresolved_by_year: dict[int, int] = defaultdict(int)
    for year, _award in unresolved:
        unresolved_by_year[year] += 1
    thin = thin_years(rows)

    out: list[dict[str, Any]] = []
    for year in sorted(set(by_year) | set(unresolved_by_year)):
        entries = by_year.get(year, [])
        out.append(
            {
                "year": year,
                "award_rows": sum(1 for _p, _s, y, _a in rows if y == year),
                "credited_seasons": len(entries),
                "year_credit": round(sum(e.credit for e in entries), 2),
                "max_stack": max((e.credit for e in entries), default=0.0),
                "thin": year in thin,
                "unresolved_rows": unresolved_by_year.get(year, 0),
            }
        )
    return out


def load_unresolved(conn: Conn) -> list[tuple[int, str]]:
    return [
        (int(cast(int, r[0])), cast(str, r[1])) for r in conn.execute(_UNRESOLVED_SQL).fetchall()
    ]


def params() -> dict[str, Any]:
    return {
        "award_top_tier_points": TOP_TIER_POINTS,
        "award_second_tier_points": SECOND_TIER_POINTS,
        "award_rookie_points": ROOKIE_POINTS,
        "award_tier_rule": TIER_RULE,
        "award_normalisation_rule": NORMALISATION_RULE,
        "award_thin_year_rule": THIN_YEAR_RULE,
    }
