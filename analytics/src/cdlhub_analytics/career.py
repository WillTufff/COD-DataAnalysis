"""Career value over a replacement baseline. Spec: /methodology#career-value.

Every other published number describes a season. This one adds them up, which
sounds like arithmetic and is not, because the record offers two different
season quantities to add and the addition means something different in each.

**The two axes answer different questions and both are published.**

`player_season_adjusted.rating` — the composite VALUE — asks what a season was
worth on the scoreboard. It exists for all ten seasons, 2017-2026, and it
separates players cleanly, because it is measured from box scores that are
themselves measured precisely. What it is not is a measurement of a player: it
is fitted against map outcome, so a column that *names* the result earns weight
for naming it. A career total off this axis is a career of scoreboard
contribution, and that is what it is called here.

The `filtered` season plus-minus asks what a player's presence was worth in
score margin. It is the quantity anyone arguing about players is arguing about,
and on this record it is very quiet: P1 measured the spread between season
coefficients as indistinguishable from zero given their own standard errors.
Summing seven of them narrows the interval but cannot manufacture separation
that the seasons did not contain, so most totals on this axis overlap. **That is
the result, not a defect in the summation, and `total_sd` is published beside
every total so a reader sees it rather than being handed an ordering.**

**The credit rule exists only on the second axis, and both halves of it ship.**
A player's season coefficient is a deviation from their team-season, so a career
total either credits the deviation alone — which under-credits the four players
who *were* a great team — or the deviation plus a share of the team term, which
re-imports the ambiguity the team column was added to remove. Neither is
correct. The two orderings differ, and the difference is the finding: it is the
size of the question "was he good, or was he on a good team", asked of the
record rather than of an opinion. `TEAM_SHARE` is 1/4 because a team-season
column is identified by four slots at once and no per-slot attribution exists;
that is a stated convention and it is stated rather than buried.

**Eras cannot be summed on the plus-minus axis.** The CWL years store one pooled
coefficient per player per era, filed against each of the three seasons it
covers. Adding those three would count one estimate three times. So a
plus-minus total covers the CDL era, and the CWL contribution is stored as its
own row at era scope, read beside the total and never inside it. The composite
axis has no such constraint — every season there is its own estimate — so it
stores one row spanning all ten.

**Replacement is the qualified-cohort minimum, per season.** Not a percentile,
which would be a chosen number wearing a definition. The floor that qualifies a
player is `QUALIFIED_MAPS`, the same eight maps the era adjustment and the
plus-minus admission rule already use, so "enough maps to be a season" means one
thing across the project. The threshold is itself a selection on playing time,
which is an outcome and not a covariate; that is said here and again in the
aging module, because it is the same survivorship mechanism one level down.

**Peak, best-three and total are three columns because they are three
arguments.** The best single season, the best sustained run and the whole career
rank different players first, and a table that published one of them would be
answering a question it had not been asked.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import psycopg

from .ratings import statespace
from .ratings.preflight import Season, load_seasons

MODEL = "career_value"
VERSION = "1.0.0"

COMPOSITE = "composite"
PLUS_MINUS = "plus_minus"
AXES = (COMPOSITE, PLUS_MINUS)

CREDIT_NONE = "none"
CREDIT_DEVIATION = "deviation"
CREDIT_DEVIATION_PLUS_TEAM = "deviation_plus_team"

SCOPE_ALL = "all"
SCOPE_CDL = "cdl"
SCOPE_CWL = "cwl"
# A season belonging to neither published era. It is never summed, and
# SCOPE_ALL does not reach it.
SCOPE_OUT = "out"

# The share of a team-season effect credited to one player under the second
# credit rule. Four slots identify the column together and nothing in the record
# attributes it between them, so an equal split is the only division that does
# not invent an attribution. It is a convention, not a measurement.
TEAM_SHARE = 0.25

# What it takes to be in a season's cohort at all. Eight maps, matching
# `statespace.ADMISSION_MAPS` and the era adjustment's floor.
QUALIFIED_MAPS = statespace.ADMISSION_MAPS

# A run of three seasons has to be three *consecutive* seasons of the league,
# not three rows that happen to be adjacent in a player's own history: a player
# who sat out 2022 did not have a run through it.
RUN_LENGTH = 3


@dataclass(frozen=True)
class SeasonValue:
    """One player-season on one axis, before replacement is subtracted."""

    player_id: int
    season_id: int
    maps: int
    value: float
    sd: float | None
    resolution: str


@dataclass(frozen=True)
class CareerRow:
    """One career total, in the shape `player_career` takes it."""

    player_id: int
    axis: str
    credit: str
    era_scope: str
    seasons: int
    maps: int
    replacement: float
    total: float
    total_sd: float | None
    peak: float
    peak_season_id: int | None
    best_three: float | None
    best_three_start_season_id: int | None


def params() -> dict[str, Any]:
    return {
        "team_share": TEAM_SHARE,
        "qualified_maps": QUALIFIED_MAPS,
        "run_length": RUN_LENGTH,
        "replacement": "qualified cohort minimum per season",
        "axes": list(AXES),
    }


# MARK: loading


def load_composite(
    conn: psycopg.Connection[tuple[object, ...]], rating_run_id: int
) -> list[SeasonValue]:
    """The published season rating, all modes, for one rating run."""
    rows = conn.execute(
        "SELECT player_id, season_id, maps_played, rating, rating_sd"
        " FROM player_season_adjusted"
        " WHERE run_id = %s AND mode_id IS NULL AND rating IS NOT NULL"
        " ORDER BY player_id, season_id",
        (rating_run_id,),
    ).fetchall()
    return [
        SeasonValue(
            player_id=cast(int, r[0]),
            season_id=cast(int, r[1]),
            maps=cast(int, r[2]),
            value=cast(float, r[3]),
            sd=None if r[4] is None else cast(float, r[4]),
            resolution=statespace.SEASON,
        )
        for r in rows
    ]


def load_plus_minus(
    conn: psycopg.Connection[tuple[object, ...]], season_rapm_run_id: int
) -> list[SeasonValue]:
    """The one-sided season coefficients. The smoothed family is never read here.

    A career total is an accumulation of what was known at the time and is
    compared against forward quantities downstream, so the two-sided family —
    whose 2023 coefficient has already been pulled toward 2024 — is refused at
    the source rather than filtered later.
    """
    rows = conn.execute(
        "SELECT player_id, season_id, maps, coef, se, resolution"
        " FROM player_rapm"
        " WHERE run_id = %s AND scope = %s AND season_id IS NOT NULL"
        " ORDER BY player_id, season_id",
        (season_rapm_run_id, statespace.FILTERED),
    ).fetchall()
    return [
        SeasonValue(
            player_id=cast(int, r[0]),
            season_id=cast(int, r[1]),
            maps=cast(int, r[2]),
            value=cast(float, r[3]),
            sd=cast(float, r[4]),
            resolution=str(r[5]),
        )
        for r in rows
    ]


def load_team_effects(
    conn: psycopg.Connection[tuple[object, ...]], season_rapm_run_id: int
) -> dict[tuple[int, int], float]:
    """The team-season column each player coefficient is a deviation from."""
    rows = conn.execute(
        "SELECT team_id, season_id, coef FROM team_season_effect WHERE run_id = %s AND scope = %s",
        (season_rapm_run_id, statespace.FILTERED),
    ).fetchall()
    return {(cast(int, r[0]), cast(int, r[1])): cast(float, r[2]) for r in rows}


_MODAL_TEAM_SQL = """
SELECT gps.player_id, ev.season_id, gps.team_id, count(*) AS maps
FROM game_player_stats gps
JOIN games g   ON g.id = gps.game_id
JOIN series s  ON s.id = g.series_id
JOIN events ev ON ev.id = s.event_id
GROUP BY gps.player_id, ev.season_id, gps.team_id
ORDER BY gps.player_id, ev.season_id, count(*) DESC, gps.team_id
"""


def modal_teams(conn: psycopg.Connection[tuple[object, ...]]) -> dict[tuple[int, int], int]:
    """The team a player played most maps for in a season.

    A mid-season move gives a player two teams and one coefficient, and the
    coefficient was fitted against both of their team columns. The modal team is
    what the second credit rule charges the share to; the count of players for
    whom it is not their only team is published, because for those players the
    credited share is an approximation and the artifact says so rather than the
    number pretending otherwise.

    Ties break on the lower team id, which only matters for an exact even split
    and keeps the choice reproducible under renumbering of nothing — the ordering
    is by contents, not by a surrogate arrival order.
    """
    best: dict[tuple[int, int], int] = {}
    for row in conn.execute(_MODAL_TEAM_SQL):
        key = (cast(int, row[0]), cast(int, row[1]))
        if key not in best:
            best[key] = cast(int, row[2])
    return best


def split_players(conn: psycopg.Connection[tuple[object, ...]]) -> set[tuple[int, int]]:
    """Player-seasons spent across more than one team."""
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for row in conn.execute(_MODAL_TEAM_SQL):
        counts[(cast(int, row[0]), cast(int, row[1]))] += 1
    return {key for key, n in counts.items() if n > 1}


# MARK: the aggregation


def replacement_by_season(values: Sequence[SeasonValue]) -> dict[int, float]:
    """The qualified-cohort minimum per season.

    A season whose cohort is entirely below the map floor has no replacement
    level and no career row can be built through it. That is returned as an
    absent key rather than as a zero, because zero is a value on both of these
    axes and would silently credit a season that could not be measured.
    """
    pools: dict[int, list[float]] = defaultdict(list)
    for row in values:
        if row.maps >= QUALIFIED_MAPS:
            pools[row.season_id].append(row.value)
    return {season_id: min(pool) for season_id, pool in pools.items() if pool}


def _best_run(over: Sequence[tuple[int, float]], order: dict[int, int]) -> tuple[float, int] | None:
    """The best sum over `RUN_LENGTH` consecutive league seasons.

    `order` maps a season id to its position in the league's own sequence, so a
    player who missed a year does not have their two flanking seasons treated as
    adjacent. A gap is scored as the seasons actually played inside the window,
    which is the honest reading of "best three consecutive": the window is three
    seasons of the league, and sitting one out costs what it cost.
    """
    if not over:
        return None
    by_position = {order[season_id]: value for season_id, value in over if season_id in order}
    if not by_position:
        return None
    best: tuple[float, int] | None = None
    for start in sorted(by_position):
        window = [by_position.get(start + step, 0.0) for step in range(RUN_LENGTH)]
        # A window must open on a season actually played, and there must be a
        # full run of the league left for it to cover.
        if start + RUN_LENGTH - 1 > max(by_position):
            continue
        total = sum(window)
        if best is None or total > best[0]:
            best = (total, start)
    if best is None:
        return None
    position_to_season = {
        order[season_id]: season_id for season_id, _ in over if season_id in order
    }
    return best[0], position_to_season[best[1]]


def aggregate(
    values: Sequence[SeasonValue],
    seasons: dict[int, Season],
    axis: str,
    credit: str,
    era_scope: str,
    team_effect: dict[tuple[int, int], float] | None = None,
    teams: dict[tuple[int, int], int] | None = None,
) -> list[CareerRow]:
    """Sum one axis over replacement, for the seasons in one era scope.

    The replacement baseline is computed on the same population that is summed:
    a cohort minimum taken over both eras would subtract a CWL floor from a CDL
    season, and the two axes are not on a common scale across the league change.
    """
    in_scope = [
        row
        for row in values
        if _scope_of(seasons[row.season_id]) == era_scope
        or (era_scope == SCOPE_ALL and _scope_of(seasons[row.season_id]) != SCOPE_OUT)
    ]
    if not in_scope:
        return []
    replacement = replacement_by_season(in_scope)
    order = _season_order(seasons, era_scope)

    per_player: dict[int, list[tuple[SeasonValue, float]]] = defaultdict(list)
    for row in in_scope:
        base = replacement.get(row.season_id)
        if base is None or row.maps < QUALIFIED_MAPS:
            continue
        credited = row.value
        if credit == CREDIT_DEVIATION_PLUS_TEAM:
            share = _team_share(row, team_effect, teams)
            if share is None:
                continue
            credited += share
        per_player[row.player_id].append((row, credited - base))

    out: list[CareerRow] = []
    for player_id, entries in sorted(per_player.items()):
        overs = [over for _, over in entries]
        peak_row, peak = max(entries, key=lambda e: (e[1], e[0].season_id))
        variances = [row.sd**2 for row, _ in entries if row.sd is not None]
        # Seasons are treated as independent for the total's uncertainty. They
        # are not exactly: the filtered fits share maps through their common
        # past, so this understates the correlation and therefore the width. The
        # direction of the error is stated rather than corrected, because a
        # correction would need a covariance the fit does not store.
        total_sd = math.sqrt(sum(variances)) if len(variances) == len(entries) else None
        run = _best_run([(row.season_id, over) for row, over in entries], order)
        out.append(
            CareerRow(
                player_id=player_id,
                axis=axis,
                credit=credit,
                era_scope=era_scope,
                seasons=len(entries),
                maps=sum(row.maps for row, _ in entries),
                replacement=_mean(
                    [replacement[row.season_id] for row, _ in entries],
                ),
                total=sum(overs),
                total_sd=total_sd,
                peak=peak,
                peak_season_id=peak_row.season_id,
                best_three=None if run is None else run[0],
                best_three_start_season_id=None if run is None else run[1],
            )
        )
    return out


def _team_share(
    row: SeasonValue,
    team_effect: dict[tuple[int, int], float] | None,
    teams: dict[tuple[int, int], int] | None,
) -> float | None:
    if team_effect is None or teams is None:
        return None
    team_id = teams.get((row.player_id, row.season_id))
    if team_id is None:
        return None
    coef = team_effect.get((team_id, row.season_id))
    if coef is None:
        return None
    return TEAM_SHARE * coef


def _scope_of(season: Season) -> str:
    """Which era's scale a season is summed on, or `out` for one with none.

    Written as a two-way test while the record held two leagues, which quietly
    made every non-CDL season a CWL season. The 2013-2015 MLG seasons would
    then be summed against a CWL replacement level and reported under the CWL
    label, so the era row would say something about a league those seasons were
    not played in. They are out of scope here until the comparable-cohort rule
    gives them a scale of their own.

    Read from the era rather than the league brand. 2016 is branded Call of Duty
    World League and comes from the wiki, so summing it on the CWL scale would
    mix one archive's replacement level into another's.
    """
    league = season.era_key.upper()
    if league == "CDL":
        return SCOPE_CDL
    if league == "CWL":
        return SCOPE_CWL
    return SCOPE_OUT


def _season_order(seasons: dict[int, Season], era_scope: str) -> dict[int, int]:
    """Season id to its position in the league's own year sequence."""
    inside = [
        season
        for season in seasons.values()
        if _scope_of(season) == era_scope
        or (era_scope == SCOPE_ALL and _scope_of(season) != SCOPE_OUT)
    ]
    return {
        season.season_id: i
        for i, season in enumerate(sorted(inside, key=lambda s: (s.year, s.season_id)))
    }


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


# MARK: the era rule


def era_rows(
    values: Sequence[SeasonValue],
    seasons: dict[int, Season],
    credit: str,
    team_effect: dict[tuple[int, int], float] | None = None,
    teams: dict[tuple[int, int], int] | None = None,
) -> list[CareerRow]:
    """The CWL contribution on the plus-minus axis: one estimate, not three.

    The stored rows repeat one era-resolution coefficient against each season it
    covers. Summing them would count it three times, so the era row takes the
    coefficient once, over the era's own replacement level, and reports the
    seasons and maps it covers as context rather than as a count of estimates.
    """
    per_player: dict[int, list[SeasonValue]] = defaultdict(list)
    for row in values:
        if _scope_of(seasons[row.season_id]) == SCOPE_CWL and row.resolution == statespace.ERA:
            per_player[row.player_id].append(row)
    if not per_player:
        return []
    once = [max(rows, key=lambda r: r.season_id) for rows in per_player.values()]
    baseline = min(
        (row.value for row in once if row.maps >= QUALIFIED_MAPS),
        default=None,
    )
    if baseline is None:
        return []

    out: list[CareerRow] = []
    for player_id, rows in sorted(per_player.items()):
        one = max(rows, key=lambda r: r.season_id)
        if one.maps < QUALIFIED_MAPS:
            continue
        credited = one.value
        if credit == CREDIT_DEVIATION_PLUS_TEAM:
            share = _team_share(one, team_effect, teams)
            if share is None:
                continue
            credited += share
        over = credited - baseline
        out.append(
            CareerRow(
                player_id=player_id,
                axis=PLUS_MINUS,
                credit=credit,
                era_scope=SCOPE_CWL,
                seasons=len({row.season_id for row in rows}),
                maps=one.maps,
                replacement=baseline,
                total=over,
                total_sd=one.sd,
                peak=over,
                peak_season_id=None,
                best_three=None,
                best_three_start_season_id=None,
            )
        )
    return out


# MARK: the published object


def build(
    conn: psycopg.Connection[tuple[object, ...]],
    rating_run_id: int,
    season_rapm_run_id: int,
) -> tuple[list[CareerRow], dict[str, Any]]:
    """Every career row, and the artifact that says what they disagree about."""
    seasons = load_seasons(conn)
    composite = load_composite(conn, rating_run_id)
    plus_minus = load_plus_minus(conn, season_rapm_run_id)
    team_effect = load_team_effects(conn, season_rapm_run_id)
    teams = modal_teams(conn)
    split = split_players(conn)

    rows: list[CareerRow] = []
    rows += aggregate(composite, seasons, COMPOSITE, CREDIT_NONE, SCOPE_ALL)
    season_scoped = [row for row in plus_minus if row.resolution == statespace.SEASON]
    for credit in (CREDIT_DEVIATION, CREDIT_DEVIATION_PLUS_TEAM):
        rows += aggregate(
            season_scoped,
            seasons,
            PLUS_MINUS,
            credit,
            SCOPE_CDL,
            team_effect,
            teams,
        )
        rows += era_rows(plus_minus, seasons, credit, team_effect, teams)
    return rows, artifact(rows, composite, plus_minus, split, seasons)


def artifact(
    rows: Sequence[CareerRow],
    composite: Sequence[SeasonValue],
    plus_minus: Sequence[SeasonValue],
    split: set[tuple[int, int]],
    seasons: dict[int, Season],
) -> dict[str, Any]:
    """What the two credit rules disagree about, and what the totals establish.

    The headline is deliberately not a leaderboard. It is the rank correlation
    between the two credit rules and the share of totals whose interval clears
    zero, because those two numbers together say how much of the ordering is a
    measurement and how much is the convention in `TEAM_SHARE`.
    """
    by_key: dict[tuple[str, str, str], list[CareerRow]] = defaultdict(list)
    for row in rows:
        by_key[(row.axis, row.credit, row.era_scope)].append(row)

    deviation = {
        row.player_id: row.total for row in by_key[(PLUS_MINUS, CREDIT_DEVIATION, SCOPE_CDL)]
    }
    with_team = {
        row.player_id: row.total
        for row in by_key[(PLUS_MINUS, CREDIT_DEVIATION_PLUS_TEAM, SCOPE_CDL)]
    }
    shared = sorted(set(deviation) & set(with_team))
    rho = _spearman([deviation[p] for p in shared], [with_team[p] for p in shared])

    return {
        "available": bool(rows),
        "team_share": TEAM_SHARE,
        "qualified_maps": QUALIFIED_MAPS,
        "replacement": "qualified cohort minimum per season",
        "populations": {
            "composite_player_seasons": len(composite),
            "plus_minus_player_seasons": len(plus_minus),
            "seasons": len(seasons),
            "split_player_seasons": len(split),
        },
        "rows_by_key": {
            f"{axis}.{credit}.{scope}": len(group)
            for (axis, credit, scope), group in sorted(by_key.items())
        },
        "credit_rules": {
            "n_players": len(shared),
            "rank_correlation": None if rho is None else round(rho, 4),
            "top_ten_overlap": _top_overlap(deviation, with_team, 10),
            "largest_moves": _largest_moves(deviation, with_team),
        },
        "separation": {
            key: _separation(group)
            for key, group in sorted((f"{a}.{c}.{s}", g) for (a, c, s), g in by_key.items())
        },
        "statement": _statement(by_key, rho),
    }


def _separation(rows: Sequence[CareerRow]) -> dict[str, Any]:
    """How many totals are more than two of their own standard deviations from zero.

    On the composite axis this is high and says little, because that axis
    measures a scoreboard precisely. On the plus-minus axis it is the whole
    question, and P1's result predicts it will be low.
    """
    with_sd = [row for row in rows if row.total_sd is not None and row.total_sd > 0.0]
    clear = sum(1 for row in with_sd if abs(row.total) > 2.0 * cast(float, row.total_sd))
    return {
        "n": len(rows),
        "n_with_interval": len(with_sd),
        "n_clear_of_zero": clear,
        "share_clear": round(clear / len(with_sd), 4) if with_sd else None,
    }


def _top_overlap(a: dict[int, float], b: dict[int, float], k: int) -> int | None:
    if len(a) < k or len(b) < k:
        return None
    top_a = {p for p, _ in sorted(a.items(), key=lambda kv: (-kv[1], kv[0]))[:k]}
    top_b = {p for p, _ in sorted(b.items(), key=lambda kv: (-kv[1], kv[0]))[:k]}
    return len(top_a & top_b)


def _largest_moves(a: dict[int, float], b: dict[int, float], k: int = 5) -> list[dict[str, Any]]:
    """The players the credit rule moves furthest, by rank rather than by value."""
    shared = sorted(set(a) & set(b))
    if not shared:
        return []
    rank_a = _ranks({p: a[p] for p in shared})
    rank_b = _ranks({p: b[p] for p in shared})
    moves = sorted(
        (
            {"player_id": p, "rank_deviation": rank_a[p], "rank_with_team": rank_b[p]}
            for p in shared
        ),
        key=lambda m: (-abs(m["rank_deviation"] - m["rank_with_team"]), m["player_id"]),
    )
    return moves[:k]


def _ranks(values: dict[int, float]) -> dict[int, int]:
    order = sorted(values.items(), key=lambda kv: (-kv[1], kv[0]))
    return {player_id: i + 1 for i, (player_id, _) in enumerate(order)}


def _spearman(a: Sequence[float], b: Sequence[float]) -> float | None:
    if len(a) < 3:
        return None
    ra = _rank_vector(a)
    rb = _rank_vector(b)
    n = len(ra)
    mean_a = sum(ra) / n
    mean_b = sum(rb) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb, strict=True))
    var_a = sum((x - mean_a) ** 2 for x in ra)
    var_b = sum((y - mean_b) ** 2 for y in rb)
    if var_a <= 0.0 or var_b <= 0.0:
        return None
    return cov / math.sqrt(var_a * var_b)


def _rank_vector(values: Sequence[float]) -> list[float]:
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


def _statement(by_key: dict[tuple[str, str, str], list[CareerRow]], rho: float | None) -> str:
    composite = by_key[(COMPOSITE, CREDIT_NONE, SCOPE_ALL)]
    cdl = by_key[(PLUS_MINUS, CREDIT_DEVIATION, SCOPE_CDL)]
    if not composite and not cdl:
        return "no career totals could be built from this database"
    parts = [
        f"{len(composite)} careers on the composite axis over ten seasons",
        f"{len(cdl)} on the CDL plus-minus axis",
    ]
    if rho is not None:
        parts.append(f"the two credit rules agree at rank correlation {rho:.3f}")
    return "; ".join(parts)


def headline(payload: dict[str, Any]) -> str:
    return cast(str, payload.get("statement", "career value unavailable"))
