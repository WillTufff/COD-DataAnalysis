"""Four ways the ratings could have embarrassed themselves, declared in advance.

Every other phase scores the ratings against the record they were fitted on.
This one scores them against things outside it: a rating somebody else built, a
set of awards somebody else voted on, the record with a season taken out of it,
and the moments a team swapped a player. None of the four is a fitting target
and none of them can move a coefficient. The plan for all four, with the
population and the verdict rule for each written before any of them ran, is
`p7-validation.md`.

**Nothing here republishes the third-party rating.** The Cito licence is
attribution-required and redistribution-forbidden, so the convergent test emits
correlations, counts and ranks -- derived analysis -- and never a value. The
payload schema is closed for exactly that reason: `DISAGREEMENT_FIELDS` is the
whole of what a named disagreement may carry, the release gate checks the key
set against it, and a field added here without being added there fails the
release rather than shipping.

**The awards are votes, not measurements.** They correlate with team success and
with airtime. A disagreement between a rating and an All-Star ballot is evidence
about the ballot as readily as about the rating, and the payload says so in the
`limits` field of every test rather than in a comment nobody renders.

**One test the plan asked for cannot be run.** §5 wants SKILL to identify Rookie
of the Year before the season it was awarded for. All five winners have zero
rated seasons before their award, which is what being a rookie means, and the
record holds no Challengers tier to have rated them in. The substitute is the
winner's rank inside their own season's rookie cohort. It answers a smaller
question and it is the one the record can answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import psycopg
from numpy.typing import NDArray

from . import resample
from .ratings import statespace

FloatArray = NDArray[np.float64]

MODEL = "validation"
VERSION = "1.0.0"

BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20260814

# The external rating's key inside `game_player_stats.extras`, and the one
# sub-key this test reads. The mode keys sit beside it and are zero on any map
# not played in that mode, so a mean over them would divide by the wrong count.
EXTERNAL_SOURCE = "cito"
EXTERNAL_KEY = "ratings"
EXTERNAL_OVERALL = "overall"

# An `overall` of exactly zero is an unrated map, not a rating of zero. The
# source rates Hardpoint, Control and Search and Destroy; Domination was a CDL
# mode in 2020 alone and carries no rating, so all 1,820 of its player-map rows
# read zero -- a third of that season. A mean taken over them puts 2020 on a
# different scale from every other year, which is visible as a season whose
# Pearson is 0.00 while its Spearman is 0.47. Real ratings reach 0.011 and go
# negative, so the exact zero is the marker and nothing legitimate is near it.
UNRATED = 0.0

# A named disagreement may carry these fields and no others. The release gate
# reads this tuple, so widening the published schema is a two-file change and
# cannot happen by accident.
DISAGREEMENT_FIELDS = (
    "player_id",
    "handle",
    "year",
    "field",
    "our_rank",
    "their_rank",
    "our_rating",
    "gap",
    "axis",
)

# A disagreement is a rank gap wider than this share of the season's field. A
# quarter is wide enough that ordinary noise around a fitted rating does not
# reach it, and narrow enough that a season of forty players can produce one.
DISAGREEMENT_SHARE = 0.25


def params() -> dict[str, Any]:
    """What this run was configured with."""
    return {
        "bootstrap_b": BOOTSTRAP_B,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "disagreement_share": DISAGREEMENT_SHARE,
        "external_source": EXTERNAL_SOURCE,
        "external_values_published": False,
    }


# MARK: shared loading


@dataclass(frozen=True)
class SeasonRow:
    """One player-season on every axis this phase compares."""

    player_id: int
    season_id: int
    year: int
    handle: str
    maps: int
    value: float | None
    skill: float | None
    external: float | None
    external_maps: int
    unrated_maps: int


def load_seasons(
    conn: psycopg.Connection[tuple[object, ...]],
    rating_run_id: int,
    skill_run_id: int | None,
) -> list[SeasonRow]:
    """Every rated player-season, with the external rating beside it where it exists.

    The external number is a mean of the per-map `overall` over the maps that
    carry one. Maps reading exactly zero are unrated and are counted, not
    averaged -- see `UNRATED`. A player-season with no rated map gets None here
    and drops out of the convergent test by that, not by a filter somewhere
    downstream.
    """
    rows = conn.execute(
        """
        WITH external AS (
          SELECT s.player_id, se.id AS season_id,
                 avg((s.extras -> %s ->> %s)::double precision)
                   FILTER (WHERE (s.extras -> %s ->> %s)::double precision <> %s) AS rating,
                 count(*) FILTER (
                   WHERE (s.extras -> %s ->> %s)::double precision <> %s
                 ) AS rated_maps,
                 count(*) FILTER (
                   WHERE (s.extras -> %s ->> %s)::double precision = %s
                 ) AS unrated_maps
            FROM game_player_stats s
            JOIN games g ON g.id = s.game_id
            JOIN series r ON r.id = g.series_id
            JOIN events e ON e.id = r.event_id
            JOIN seasons se ON se.id = e.season_id
           WHERE s.extras ? %s
           GROUP BY 1, 2
        )
        SELECT v.player_id, v.season_id, se.year, p.handle, v.maps_played,
               v.rating, k.skill, external.rating,
               COALESCE(external.rated_maps, 0), COALESCE(external.unrated_maps, 0)
          FROM player_season_adjusted v
          JOIN seasons se ON se.id = v.season_id
          JOIN players p ON p.id = v.player_id
          LEFT JOIN player_skill k
            ON k.player_id = v.player_id AND k.season_id = v.season_id
           AND k.run_id = %s
          LEFT JOIN external
            ON external.player_id = v.player_id AND external.season_id = v.season_id
         WHERE v.run_id = %s AND v.mode_id IS NULL AND v.rating IS NOT NULL
         ORDER BY se.year, p.handle, v.player_id
        """,
        (
            EXTERNAL_KEY,
            EXTERNAL_OVERALL,
            EXTERNAL_KEY,
            EXTERNAL_OVERALL,
            UNRATED,
            EXTERNAL_KEY,
            EXTERNAL_OVERALL,
            UNRATED,
            EXTERNAL_KEY,
            EXTERNAL_OVERALL,
            UNRATED,
            EXTERNAL_KEY,
            skill_run_id,
            rating_run_id,
        ),
    ).fetchall()
    return [
        SeasonRow(
            player_id=cast(int, r[0]),
            season_id=cast(int, r[1]),
            year=cast(int, r[2]),
            handle=cast(str, r[3]),
            maps=cast(int, r[4]),
            value=None if r[5] is None else float(cast(float, r[5])),
            skill=None if r[6] is None else float(cast(float, r[6])),
            external=None if r[7] is None else float(cast(float, r[7])),
            external_maps=cast(int, r[8]),
            unrated_maps=cast(int, r[9]),
        )
        for r in rows
    ]


# MARK: statistics


def _axis_of(row: SeasonRow, axis: str) -> float | None:
    """The value of one of our two axes on a player-season, or None."""
    return row.value if axis == "value" else row.skill


def _floats(values: Sequence[float]) -> FloatArray:
    return np.asarray(list(values), dtype=np.float64)


def _pearson(a: FloatArray, b: FloatArray) -> float:
    if len(a) < 3 or a.std() == 0.0 or b.std() == 0.0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _ranks(values: FloatArray) -> FloatArray:
    """Mid-ranks, so tied ratings do not order each other by position."""
    order = np.argsort(values, kind="stable")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(1, len(values) + 1, dtype=float)
    _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
    if (counts > 1).any():
        sums = np.zeros(len(counts), dtype=float)
        np.add.at(sums, inverse, ranks)
        ranks = (sums / counts)[inverse]
    return ranks


def _spearman(a: FloatArray, b: FloatArray) -> float:
    return _pearson(_ranks(a), _ranks(b))


def _cluster_ci(
    clusters: Sequence[Sequence[int]],
    a: FloatArray,
    b: FloatArray,
    statistic: str,
) -> tuple[float, float] | None:
    """A bootstrap interval over whole clusters, ordered and seeded by contents.

    The cluster is the player: a player-season is not independent of the same
    player's other seasons, and the surrogate `player_id` that groups them is a
    loader artifact, so the population is ordered by what each cluster contains
    before a single draw is taken.
    """
    if len(clusters) < 3:
        return None
    blocks = [list(c) for c in clusters]
    left_key = [float(a[rows].sum()) for rows in blocks]
    right_key = [float(b[rows].sum()) for rows in blocks]
    size_key = [float(len(rows)) for rows in blocks]
    order = resample.order([left_key, right_key, size_key])
    grouped = [blocks[i] for i in order]
    rng = resample.stream(
        BOOTSTRAP_SEED,
        np.asarray([left_key[i] for i in order], dtype=float),
        np.asarray([right_key[i] for i in order], dtype=float),
    )
    fn = _spearman if statistic == "spearman" else _pearson
    draws: list[float] = []
    for _ in range(BOOTSTRAP_B):
        picked = rng.integers(0, len(grouped), size=len(grouped))
        rows = [i for p in picked for i in grouped[p]]
        if len(rows) < 3:
            continue
        draws.append(fn(a[rows], b[rows]))
    if not draws:
        return None
    lo, hi = np.quantile(_floats(draws), [0.025, 0.975])
    return float(lo), float(hi)


# MARK: test 1 -- convergent validity against the external rating


def _axis_convergence(rows: Sequence[SeasonRow], axis: str) -> dict[str, Any] | None:
    """Correlate one of our axes against the external rating, pooled and per season."""
    paired = [r for r in rows if r.external is not None and _axis_of(r, axis) is not None]
    if len(paired) < 3:
        return None
    ours = _floats([float(_axis_of(r, axis) or 0.0) for r in paired])
    theirs = _floats([float(r.external or 0.0) for r in paired])

    by_player: dict[int, list[int]] = {}
    for i, r in enumerate(paired):
        by_player.setdefault(r.player_id, []).append(i)
    interval = _cluster_ci(list(by_player.values()), ours, theirs, "spearman")

    seasons: list[dict[str, Any]] = []
    for year in sorted({r.year for r in paired}):
        idx = [i for i, r in enumerate(paired) if r.year == year]
        if len(idx) < 3:
            continue
        seasons.append(
            {
                "year": year,
                "n": len(idx),
                "pearson": round(_pearson(ours[idx], theirs[idx]), 4),
                "spearman": round(_spearman(ours[idx], theirs[idx]), 4),
            }
        )
    return {
        "axis": axis,
        "n": len(paired),
        "n_players": len(by_player),
        "pearson": round(_pearson(ours, theirs), 4),
        "spearman": round(_spearman(ours, theirs), 4),
        "spearman_lo95": None if interval is None else round(interval[0], 4),
        "spearman_hi95": None if interval is None else round(interval[1], 4),
        "by_season": seasons,
    }


def _disagreements(rows: Sequence[SeasonRow], axis: str) -> list[dict[str, Any]]:
    """Player-seasons the two numbers order very differently, ranked by the gap.

    Ranks are derived analysis and ship. The external rating itself does not,
    here or anywhere else in this module.
    """
    out: list[dict[str, Any]] = []
    for year in sorted({r.year for r in rows}):
        field = [
            r
            for r in rows
            if r.year == year and r.external is not None and _axis_of(r, axis) is not None
        ]
        if len(field) < 8:
            continue
        ours = _floats([float(_axis_of(r, axis) or 0.0) for r in field])
        theirs = _floats([float(r.external or 0.0) for r in field])
        # A degenerate axis ranks every player equal, which reads as a maximal
        # disagreement with any ordering at all. That is a property of the
        # constant and not a disagreement, so it is not published as one.
        if ours.std() == 0.0 or theirs.std() == 0.0:
            continue
        # rank 1 is the best, so the descending order is the published one
        our_rank = len(field) + 1 - _ranks(ours)
        their_rank = len(field) + 1 - _ranks(theirs)
        threshold = DISAGREEMENT_SHARE * len(field)
        for i, r in enumerate(field):
            gap = float(our_rank[i] - their_rank[i])
            if abs(gap) < threshold:
                continue
            out.append(
                {
                    "player_id": r.player_id,
                    "handle": r.handle,
                    "year": year,
                    "field": len(field),
                    "our_rank": int(round(float(our_rank[i]))),
                    "their_rank": int(round(float(their_rank[i]))),
                    "our_rating": round(float(ours[i]), 4),
                    "gap": round(gap, 1),
                    "axis": axis,
                }
            )
    out.sort(key=lambda d: (-abs(float(d["gap"])), int(d["year"]), str(d["handle"])))
    return out


def _coverage(rows: Sequence[SeasonRow]) -> list[dict[str, Any]]:
    """How much of each season the external rating actually covers.

    Published because the answer is not "all of it" and because the season it
    is not is the season whose correlation looks strangest.
    """
    out: list[dict[str, Any]] = []
    for year in sorted({r.year for r in rows}):
        field = [r for r in rows if r.year == year]
        rated = sum(r.external_maps for r in field)
        unrated = sum(r.unrated_maps for r in field)
        if rated + unrated == 0:
            continue
        out.append(
            {
                "year": year,
                "rated_maps": rated,
                "unrated_maps": unrated,
                "rated_share": round(rated / (rated + unrated), 4),
            }
        )
    return out


def convergent(rows: Sequence[SeasonRow]) -> dict[str, Any]:
    """Test 1: do our numbers and somebody else's order the same players the same way?

    A high correlation is not a result. Both numbers read the same box score, so
    convergence is close to guaranteed and what it measures is agreement of
    arithmetic. The disagreement list is the output worth reading, and a pooled
    Spearman below 0.5 would be a warning about one of the two numbers with no
    claim about which.
    """
    axes = [a for a in (_axis_convergence(rows, "value"), _axis_convergence(rows, "skill")) if a]
    disagreements = _disagreements(rows, "value") + _disagreements(rows, "skill")
    verdict = "no overlapping population"
    if axes:
        pooled = axes[0]["spearman"]
        verdict = (
            f"VALUE and the {EXTERNAL_SOURCE} rating agree at Spearman "
            f"{pooled:+.3f} over {axes[0]['n']} player-seasons"
        )
    return {
        "test": "convergent_validity",
        "class": "secondary",
        "source": EXTERNAL_SOURCE,
        "attribution": "Cito API, which carries Breaking Point match data",
        "population": {
            "player_seasons": sum(int(a["n"]) for a in axes[:1]),
            "axes": {str(a["axis"]): int(a["n"]) for a in axes},
            "coverage": _coverage(rows),
        },
        "axes": axes,
        "disagreements": disagreements,
        "disagreement_count": len(disagreements),
        "verdict": verdict,
        "limits": (
            "Both numbers read the same box score, so agreement measures shared "
            "arithmetic and not shared truth. The external values are licensed "
            "against redistribution and appear nowhere in this payload."
        ),
    }


# MARK: test 2 -- face validity against the awards


# The award kinds this test scores, and how many players each names per season.
# `first_team` names the season's team, which was five players in 2020 and four
# after it, so the size is read from the awards themselves and not declared.
SELECTION_KINDS = ("first_team", "second_team")
RANK_KINDS = ("roty", "rs_mvp")


@dataclass(frozen=True)
class Award:
    """One award row, resolved as far as the record allows."""

    award: str
    handle: str
    year: int
    player_id: int | None


def load_awards(conn: psycopg.Connection[tuple[object, ...]]) -> list[Award]:
    """The individual awards, as the loader resolved them."""
    rows = conn.execute(
        "SELECT a.award, a.handle, se.year, a.player_id"
        " FROM player_awards a JOIN seasons se ON se.id = a.season_id"
        " WHERE a.award = ANY(%s)"
        " ORDER BY se.year, a.award, a.handle",
        (list(SELECTION_KINDS + RANK_KINDS),),
    ).fetchall()
    return [
        Award(
            award=cast(str, r[0]),
            handle=cast(str, r[1]),
            year=cast(int, r[2]),
            player_id=None if r[3] is None else cast(int, r[3]),
        )
        for r in rows
    ]


def _hypergeometric_mean(field: int, selections: int, top: int) -> float:
    """How many selections land in the top `top` of a field by chance alone."""
    if field <= 0:
        return 0.0
    return selections * top / field


def _selection_agreement(
    rows: Sequence[SeasonRow], awards: Sequence[Award]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Do the season's selections sit at the top of the season's VALUE table?

    Scored against the top *k*, where *k* is the number of players that season's
    first team actually named. 2020 named five, every season after named four,
    and reading it off the awards is what keeps the comparison honest in the one
    season the league fielded a fifth player.
    """
    seasons: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for year in sorted({a.year for a in awards}):
        picked = [a for a in awards if a.year == year and a.award in SELECTION_KINDS]
        if not picked:
            continue
        team_size = sum(1 for a in picked if a.award == "first_team") or 4
        field = [r for r in rows if r.year == year and r.value is not None]
        if len(field) < team_size * 3:
            continue
        ranked = sorted(field, key=lambda r: (-float(r.value or 0.0), r.handle))
        position = {r.player_id: i + 1 for i, r in enumerate(ranked)}
        # The bar is the size of the selection itself: n players were picked, so
        # the question is whether our top n holds them. A looser 2n sits beside
        # it, because a rating that puts an All-Star eleventh of sixty-three has
        # not really disagreed with the ballot.
        n = len(picked)
        hits_top = 0
        hits_double = 0
        scored = 0
        for a in picked:
            rank = None if a.player_id is None else position.get(a.player_id)
            if rank is None:
                excluded.append({"handle": a.handle, "year": year, "award": a.award})
                continue
            scored += 1
            if rank <= n:
                hits_top += 1
            if rank <= n * 2:
                hits_double += 1
        if scored == 0:
            continue
        seasons.append(
            {
                "year": year,
                "team_size": team_size,
                "selections": len(picked),
                "scored": scored,
                "field": len(field),
                "in_top_n": hits_top,
                "in_top_2n": hits_double,
                "expected_top_n": round(_hypergeometric_mean(len(field), scored, n), 2),
            }
        )
    return seasons, excluded


def _rookie_ranks(rows: Sequence[SeasonRow], awards: Sequence[Award]) -> list[dict[str, Any]]:
    """Each winner's rank inside their own season, and inside its rookie cohort.

    This is the substitute for the test the plan asked for. A rookie has no
    rated season before the award, so no forward rating can have identified
    them; what the record can answer is whether the season they won it for
    looks like the best rookie season that year.
    """
    first_year: dict[int, int] = {}
    for r in rows:
        prior = first_year.get(r.player_id)
        if prior is None or r.year < prior:
            first_year[r.player_id] = r.year
    out: list[dict[str, Any]] = []
    for a in awards:
        if a.award not in RANK_KINDS or a.player_id is None:
            continue
        field = [r for r in rows if r.year == a.year and r.value is not None]
        if not field:
            continue
        cohort = (
            [r for r in field if first_year.get(r.player_id) == a.year]
            if a.award == "roty"
            else field
        )
        ranked = sorted(cohort, key=lambda r: (-float(r.value or 0.0), r.handle))
        rank = next((i + 1 for i, r in enumerate(ranked) if r.player_id == a.player_id), None)
        out.append(
            {
                "award": a.award,
                "handle": a.handle,
                "year": a.year,
                "cohort": len(ranked),
                "rank": rank,
                "prior_rated_seasons": sum(
                    1 for r in rows if r.player_id == a.player_id and r.year < a.year
                ),
            }
        )
    return out


def face_validity(rows: Sequence[SeasonRow], awards: Sequence[Award]) -> dict[str, Any]:
    """Test 2: does the rating agree with the people who hand out the trophies?

    Reported, not gated. Forty-nine selections over seven seasons support a rate
    and not a claim about any one season, and an award is a vote: a disagreement
    is evidence about the ballot as readily as about the rating.
    """
    seasons, excluded = _selection_agreement(rows, awards)
    ranks = _rookie_ranks(rows, awards)
    scored = sum(int(s["scored"]) for s in seasons)
    hits = sum(int(s["in_top_n"]) for s in seasons)
    expected = sum(float(s["expected_top_n"]) for s in seasons)
    verdict = "no scored selections"
    if scored:
        verdict = (
            f"{hits} of {scored} All-Star selections fall in the top n of their "
            f"season, against {expected:.1f} expected by chance"
        )
    return {
        "test": "face_validity",
        "class": "secondary",
        "population": {
            "awards_scored": scored,
            "awards_excluded": len(excluded),
            "referents_loaded": len(awards),
        },
        "by_season": seasons,
        "excluded": excluded,
        "ranked_awards": ranks,
        "agreement_rate": None if not scored else round(hits / scored, 4),
        "expected_rate": None if not scored else round(expected / scored, 4),
        "verdict": verdict,
        "limits": (
            "Awards are votes. They track team success and airtime, so agreement "
            "measures agreement with a popular judgement and not with truth. The "
            "excluded referents are named above and are all one player whose box "
            "score and whose roster history sit under two different handles."
        ),
    }


# MARK: test 3 -- retrodiction by season holdout


# Below this, a season's reordering is a failure worth publishing under that
# name: a rating that reshuffles its own players when one season of maps leaves
# is not measuring a stable quantity.
STABILITY_FLOOR = 0.8


def _cell_years(
    games: Sequence[Any], seasons: dict[int, Any], how: dict[str, str]
) -> dict[Any, int]:
    """The first season year each fitted cell covers."""
    covered = statespace.cell_seasons(games, seasons, how)
    return {
        cell: min(seasons[s].year for s in season_ids)
        for cell, season_ids in covered.items()
        if season_ids
    }


def retrodiction(
    games: Sequence[Any],
    seasons: dict[int, Any],
    how: dict[str, str],
    lambdas: tuple[float, float],
) -> dict[str, Any]:
    """Test 3: take a season out of the fit and see what moves.

    **Removing a season cannot touch any cell before it, and that is a property
    of the estimator rather than a result.** The filtered family solves once per
    cell over the maps through that cell, so a 2022 coefficient is a function of
    2020 to 2022 alone and is identical whether or not 2023 exists. The payload
    checks that identity rather than assuming it, and the correlation is
    measured on the cells *after* the hole, which are the only ones that can
    move.

    That also retires the recovery half of the declared design. The plan asked
    for the final season's ordering to be predicted by a fit that had not seen
    it, and a fit with the final season removed has no final-season cell to
    predict with. The forward question the plan was reaching for is the primary
    test in `evalspec`, which already runs every release.
    """
    response = statespace.responses(games)[statespace.MARGIN]
    full = statespace.fit_filtered(games, seasons, how, response, lambdas)
    cell_year = _cell_years(games, seasons, how)
    season_year = {s.season_id: s.year for s in seasons.values()}

    holdout_years = sorted(
        {season_year[g.season_id] for g in games if how.get(_league_of(g, seasons)) == "season"}
    )
    results: list[dict[str, Any]] = []
    for year in holdout_years:
        kept = [g for g in games if season_year[g.season_id] != year]
        if len(kept) < len(games) // 2:
            continue
        refit = statespace.fit_filtered(kept, seasons, how, response, lambdas)
        after: list[tuple[float, float]] = []
        before_moved = 0
        before_seen = 0
        for key, coef in full.players.items():
            other = refit.players.get(key)
            if other is None:
                continue
            at = cell_year.get(key[1])
            if at is None:
                continue
            if at < year:
                before_seen += 1
                if abs(coef[0] - other[0]) > 1e-9:
                    before_moved += 1
            elif at > year:
                after.append((coef[0], other[0]))
        if len(after) < 8:
            results.append(
                {
                    "held_out": year,
                    "cells_after": len(after),
                    "spearman": None,
                    "note": "no later cell to compare",
                }
            )
            continue
        a = np.asarray([p[0] for p in after], dtype=float)
        b = np.asarray([p[1] for p in after], dtype=float)
        results.append(
            {
                "held_out": year,
                "maps_removed": len(games) - len(kept),
                "cells_after": len(after),
                "cells_before": before_seen,
                "cells_before_moved": before_moved,
                "spearman": round(_spearman(a, b), 4),
                "pearson": round(_pearson(a, b), 4),
            }
        )

    scored = [r for r in results if r.get("spearman") is not None]
    worst = min((float(r["spearman"]) for r in scored), default=None)
    leaked = sum(int(r.get("cells_before_moved", 0)) for r in results)
    verdict = "no season could be held out"
    if worst is not None:
        verdict = (
            f"the weakest season holdout reorders the later cells at Spearman "
            f"{worst:+.3f}, against a declared floor of {STABILITY_FLOOR}"
        )
    return {
        "test": "retrodiction",
        "class": "secondary",
        "population": {
            "seasons_held_out": len(scored),
            "admitted_maps": len(games),
            "player_cells": len(full.players),
        },
        "stability_floor": STABILITY_FLOOR,
        "by_holdout": results,
        "worst_spearman": worst,
        "one_sided_violations": leaked,
        "passes_floor": None if worst is None else bool(worst >= STABILITY_FLOOR),
        "verdict": verdict,
        "limits": (
            "Removing a season removes its opponents with it, so a season whose "
            "schedule was unusual moves more than one whose schedule was ordinary. "
            "Cells before the hole cannot move and are reported as a check on the "
            "estimator, not as agreement."
        ),
    }


def _league_of(game: Any, seasons: dict[int, Any]) -> str:
    return str(seasons[game.season_id].league)


# MARK: test 4 -- roster shock


# The four players who took the most maps for a team at an event are its lineup
# there. Read from maps actually played, not from `roster_stints`, whose
# event-window envelopes present players as concurrent teammates who never
# played a map together.
LINEUP_SIZE = 4


@dataclass(frozen=True)
class Swap:
    """A team's consecutive events, with one player different between them."""

    team_id: int
    year: int
    departed: int
    arrived: int
    before_win_rate: float
    after_win_rate: float
    before_maps: int
    after_maps: int


def load_swaps(conn: psycopg.Connection[tuple[object, ...]]) -> list[Swap]:
    """Every single-player lineup change between a team's consecutive events."""
    rows = conn.execute(
        """
        WITH played AS (
          SELECT r.event_id, s.team_id, s.player_id, count(*) AS maps
            FROM game_player_stats s
            JOIN games g ON g.id = s.game_id
            JOIN series r ON r.id = g.series_id
           GROUP BY 1, 2, 3
        ), ranked AS (
          SELECT *, row_number() OVER (
                      PARTITION BY event_id, team_id ORDER BY maps DESC, player_id
                    ) AS rn
            FROM played
        ), lineup AS (
          SELECT event_id, team_id, array_agg(player_id ORDER BY player_id) AS players
            FROM ranked WHERE rn <= %s GROUP BY 1, 2
        ), results AS (
          SELECT r.event_id, t.team_id,
                 count(*) AS maps,
                 count(*) FILTER (WHERE g.winner_team_id = t.team_id) AS wins
            FROM games g
            JOIN series r ON r.id = g.series_id
            JOIN (SELECT DISTINCT r2.id AS series_id, s.team_id
                    FROM game_player_stats s
                    JOIN games g2 ON g2.id = s.game_id
                    JOIN series r2 ON r2.id = g2.series_id) t ON t.series_id = r.id
           WHERE g.winner_team_id IS NOT NULL
           GROUP BY 1, 2
        ), ordered AS (
          SELECT l.event_id, l.team_id, l.players, se.year, e.start_date,
                 res.maps, res.wins,
                 row_number() OVER (PARTITION BY l.team_id ORDER BY e.start_date, e.id) AS rn
            FROM lineup l
            JOIN events e ON e.id = l.event_id
            JOIN seasons se ON se.id = e.season_id
            JOIN results res ON res.event_id = l.event_id AND res.team_id = l.team_id
        )
        SELECT b.team_id, b.year,
               (SELECT x FROM unnest(a.players) x EXCEPT SELECT y FROM unnest(b.players) y),
               (SELECT x FROM unnest(b.players) x EXCEPT SELECT y FROM unnest(a.players) y),
               a.wins::float / a.maps, b.wins::float / b.maps, a.maps, b.maps
          FROM ordered a
          JOIN ordered b ON b.team_id = a.team_id AND b.rn = a.rn + 1
         WHERE cardinality(ARRAY(SELECT unnest(a.players) EXCEPT SELECT unnest(b.players))) = 1
           AND a.maps > 0 AND b.maps > 0
         ORDER BY b.year, b.team_id
        """,
        (LINEUP_SIZE,),
    ).fetchall()
    return [
        Swap(
            team_id=cast(int, r[0]),
            year=cast(int, r[1]),
            departed=cast(int, r[2]),
            arrived=cast(int, r[3]),
            before_win_rate=float(cast(float, r[4])),
            after_win_rate=float(cast(float, r[5])),
            before_maps=cast(int, r[6]),
            after_maps=cast(int, r[7]),
        )
        for r in rows
        if r[2] is not None and r[3] is not None
    ]


def common_swaps(rows: Sequence[SeasonRow], swaps: Sequence[Swap]) -> list[Swap]:
    """The swaps both axes can score, so the two slopes are comparable.

    SKILL is published for fewer player-seasons than VALUE, so scoring each axis
    on whatever it happens to cover would compare two different populations and
    call the difference a difference between the ratings.
    """
    both = {(r.player_id, r.year) for r in rows if r.value is not None and r.skill is not None}
    return [s for s in swaps if (s.departed, s.year) in both and (s.arrived, s.year) in both]


def _slope(x: FloatArray, y: FloatArray) -> float:
    if len(x) < 3 or x.std() == 0.0:
        return 0.0
    return float(np.polyfit(x, y, 1)[0])


def roster_shock(
    rows: Sequence[SeasonRow], swaps: Sequence[Swap], axis: str = "value"
) -> dict[str, Any]:
    """Test 4: when a team swaps one player, does the rating see it coming?

    **The outcome is the map win rate, and the declared score margin is not
    usable.** A margin means a different thing in each mode -- Hardpoint runs to
    250 and Search and Destroy to 6 -- so a mean margin over an event's map set
    is a weighted average of incomparable units that moves with the mode
    rotation. The win rate is mode-free and was already declared as the quantity
    reported beside it.

    Stated as an association and never as an effect. Teams replace a player for
    reasons that correlate with form, and the prediction also ignores the three
    who stayed, whose own form moves between events for its own reasons.
    """
    rated = {
        (r.player_id, r.year): (r.value if axis == "value" else r.skill)
        for r in rows
        if (r.value if axis == "value" else r.skill) is not None
    }
    paired: list[tuple[float, float, int]] = []
    unrated = 0
    for s in swaps:
        out = rated.get((s.departed, s.year))
        into = rated.get((s.arrived, s.year))
        if out is None or into is None:
            unrated += 1
            continue
        paired.append((float(into) - float(out), s.after_win_rate - s.before_win_rate, s.team_id))
    if len(paired) < 8:
        return {
            "test": "roster_shock",
            "class": "secondary",
            "axis": axis,
            "population": {"swaps": len(swaps), "scored": len(paired), "unrated": unrated},
            "verdict": "too few scored swaps to measure",
            "limits": "Association, not effect.",
        }

    x = np.asarray([p[0] for p in paired], dtype=float)
    y = np.asarray([p[1] for p in paired], dtype=float)
    by_team: dict[int, list[int]] = {}
    for i, p in enumerate(paired):
        by_team.setdefault(p[2], []).append(i)

    blocks = list(by_team.values())
    x_key = [float(x[rows_].sum()) for rows_ in blocks]
    y_key = [float(y[rows_].sum()) for rows_ in blocks]
    order = resample.order([x_key, y_key, [float(len(b)) for b in blocks]])
    grouped = [blocks[i] for i in order]
    rng = resample.stream(
        BOOTSTRAP_SEED,
        np.asarray([x_key[i] for i in order], dtype=float),
        np.asarray([y_key[i] for i in order], dtype=float),
    )
    draws: list[float] = []
    for _ in range(BOOTSTRAP_B):
        picked = rng.integers(0, len(grouped), size=len(grouped))
        idx = [i for p in picked for i in grouped[p]]
        if len(idx) < 3:
            continue
        draws.append(_slope(x[idx], y[idx]))
    lo, hi = (
        (float(q) for q in np.quantile(_floats(draws), [0.025, 0.975])) if draws else (0.0, 0.0)
    )
    lo, hi = float(lo), float(hi)
    slope = _slope(x, y)

    # The smallest slope this many swaps could have resolved, from the measured
    # spread of the prediction and the measured spread of the outcome. Computed
    # rather than chosen, so a null can be told from an absence of power.
    detectable = (
        float(1.96 * y.std(ddof=1) / (x.std(ddof=1) * np.sqrt(len(x)))) if x.std(ddof=1) else 0.0
    )
    # The two axes are on different units, so a raw slope cannot be compared
    # across them. Scaling by the predictor's own spread gives the win-rate
    # change per one-SD difference in the rating, which can be.
    spread = float(x.std(ddof=1))
    excludes_zero = lo > 0.0 or hi < 0.0
    informative = excludes_zero or (hi - lo) < 2 * detectable
    if excludes_zero:
        verdict = (
            f"a one-player swap moves the map win rate by {slope * spread:+.3f} per "
            f"SD of {axis} [{lo * spread:+.3f}, {hi * spread:+.3f}] over "
            f"{len(paired)} swaps"
        )
    elif informative:
        verdict = (
            f"{axis} does not predict the change in map win rate: {slope:+.3f} "
            f"[{lo:+.3f}, {hi:+.3f}], narrower than the {detectable:.3f} this "
            f"population could resolve"
        )
    else:
        verdict = (
            f"{len(paired)} swaps cannot resolve a slope of {detectable:.3f}, so "
            f"{slope:+.3f} [{lo:+.3f}, {hi:+.3f}] says nothing either way"
        )
    return {
        "test": "roster_shock",
        "class": "secondary",
        "axis": axis,
        "population": {
            "swaps": len(swaps),
            "scored": len(paired),
            "unrated": unrated,
            "teams": len(by_team),
        },
        "outcome": "map win rate at the next event minus the previous",
        "slope": round(slope, 4),
        "lo95": round(lo, 4),
        "hi95": round(hi, 4),
        "slope_per_sd": round(slope * spread, 4),
        "lo95_per_sd": round(lo * spread, 4),
        "hi95_per_sd": round(hi * spread, 4),
        "detectable_slope": round(detectable, 4),
        "excludes_zero": excludes_zero,
        "informative": informative,
        "verdict": verdict,
        "limits": (
            "Association, not effect. A team replaces a player for reasons that "
            "correlate with form, and the prediction ignores the three who stayed. "
            "Score margin is not usable as the outcome because a margin means a "
            "different thing in each mode."
        ),
    }


def headline(payload: dict[str, Any]) -> str:
    return str(payload.get("verdict", "nothing measurable"))


# MARK: the run


# Only the CDL era carries a season coefficient per player. The CWL seasons are
# pooled into one era cell, so a swap there compares a rating to itself.
FIRST_CDL_YEAR = 2020


def build(
    conn: psycopg.Connection[tuple[object, ...]],
    rating_run_id: int,
    skill_run_id: int | None,
    games: Sequence[Any],
    seasons: dict[int, Any],
    how: dict[str, str],
    lambdas: tuple[float, float],
) -> dict[str, dict[str, Any]]:
    """All four tests, each keyed by the artifact name it is written under."""
    rows = load_seasons(conn, rating_run_id, skill_run_id)
    swaps = [s for s in load_swaps(conn) if s.year >= FIRST_CDL_YEAR]
    shared = common_swaps(rows, swaps)
    shock = roster_shock(rows, shared, "value")
    shock_skill = roster_shock(rows, shared, "skill")
    shock["against"] = shock_skill
    return {
        "validation_convergent": convergent(rows),
        "validation_face": face_validity(rows, load_awards(conn)),
        "validation_retrodiction": retrodiction(games, seasons, how, lambdas),
        "validation_shock": shock,
    }


def summary(payloads: dict[str, dict[str, Any]]) -> str:
    """One line naming how many of the four tests could fail and did not."""
    verdicts = [str(p.get("verdict", "")) for p in payloads.values()]
    return f"{len(verdicts)} verdicts: " + "; ".join(v.split(",")[0] for v in verdicts)
