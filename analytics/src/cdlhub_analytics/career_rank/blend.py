"""The career blend: five components, each scaled across the qualified cohort
and combined at the weights fixed in the pre-registration.

CWL years count at full per-year weight, same sequence as CDL — a deliberate
departure from `career.py`'s plus-minus era pooling, justified in the
pre-registration doc: this engine's season unit (breadth score) is already
computed per year, so there is no repeated estimate to guard against
triple-counting. `_best_run`, `_season_order` and `replacement_by_season` are
reused from `career.py` unchanged; the windowing logic ("sitting one out costs
what it cost") and the replacement definition apply identically here.

**PEAK, PRIME and LONGEVITY read the performance season score; RESUME and
ACCOLADE enter here and not there.** The season score stays PERFORMANCE alone,
because a finish given a season weight as well would be credited twice — once
inside the peak season it lifted, and again at its own weight in the career —
and the double count would be invisible in the published number.

**A component is absent only where the archive cannot see it.** Never winning
an award is a zero and is scored as one; a career played entirely inside years
that named no season-level honour has no award axis at all, and that one
renormalizes away. The two read differently, and the caller supplies which
years are which rather than the absence being inferred from the value.

`season_total` is what `total` used to be, the plain sum of season scores, and
it keeps `total_sd` and `mean_season` attached to it. `total` is the blend.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .. import career
from ..ratings.preflight import Season
from .evalpop import MIN_SEASONS

PERFORMANCE = "PERFORMANCE"
RESUME = "RESUME"
ACCOLADE = "ACCOLADE"

PEAK = "PEAK"
PRIME = "PRIME"
LONGEVITY = "LONGEVITY"

# What a component is worth inside a season score, and the only place that is
# declared. PERFORMANCE is the whole of it: RESUME and ACCOLADE are built per
# season and stored on the row, and neither enters a season score at all. Both
# are weighted once, in the career blend below.
#
# Each component is held on its own scale, because the scales differ — breadth
# is 0..100 while resume and accolade are shares of the year, 0..1. Nothing
# reads a second component until the blend puts them on a common scale first,
# so the mismatch cannot reach a number from here.
SEASON_COMPONENT_WEIGHTS: Mapping[str, float] = {PERFORMANCE: 1.0}

# The career blend, from the pre-registration and not revisited after a result
# was seen. Every component is min-max scaled across the qualified cohort
# before this is applied, so the weights are shares of one comparable 0..100
# scale and not of five different units.
CAREER_COMPONENT_WEIGHTS: Mapping[str, float] = {
    PEAK: 20.0,
    PRIME: 25.0,
    LONGEVITY: 20.0,
    RESUME: 25.0,
    ACCOLADE: 10.0,
}

CAREER_BLEND_RULE = (
    "each component min-max scaled 0..100 across the qualified cohort, then "
    "the weighted mean over the components the career's coverage reaches: "
    + ", ".join(f"{name} {weight:g}" for name, weight in CAREER_COMPONENT_WEIGHTS.items())
)

LONGEVITY_RULE = (
    "sum over every scorable season of max(0, season score - replacement), "
    f"replacement being the minimum over that season's players with at least "
    f"{career.QUALIFIED_MAPS} maps, taken over the whole archive"
)


def renormalize(
    components: Mapping[str, float], weights: Mapping[str, float] = SEASON_COMPONENT_WEIGHTS
) -> float | None:
    """The weighted mean over the components a season actually has, with the
    weights rescaled to what is there.

    A component the archive does not reach is absent from the mean. It is
    never a zero inside it: a 2014 season with a finish record and no box
    score did not perform badly, it was not measured, and the two have to
    read differently. `None` comes back when nothing weighted is present,
    which is a season that cannot be scored rather than a season worth zero.
    """
    live = [(weights[name], value) for name, value in components.items() if weights.get(name)]
    total_weight = sum(w for w, _ in live)
    if not total_weight:
        return None
    return sum(w * v for w, v in live) / total_weight


@dataclass(frozen=True)
class SeasonScore:
    player_id: int
    season_id: int
    # Component name -> value, each on its own scale. PERFORMANCE is the
    # breadth score blended with VALUE, 0..100; RESUME is the season's share of
    # the year's winnable finish credit and ACCOLADE its share of the year's
    # awarded credit, both 0..1.
    components: Mapping[str, float] = field(default_factory=dict)
    sd: float | None = None  # from breadth.SeasonBreadth.sd

    @property
    def present(self) -> tuple[str, ...]:
        return tuple(sorted(self.components))

    @property
    def score(self) -> float | None:
        return renormalize(self.components)


@dataclass(frozen=True)
class CareerRank:
    player_id: int
    n_seasons: int
    qualified: bool  # clears the MIN_SEASONS floor
    # The blend: the weighted mean of the scaled components this career's
    # coverage reaches. This is what the board ranks on.
    total: float
    # The five components as the blend saw them, scaled 0..100 across the
    # qualified cohort. A component the career's coverage does not reach is
    # absent from this mapping and from the mean.
    career_components: Mapping[str, float]
    # The same five before scaling, in their own units, so a reader can see
    # what a scaled number was made of.
    peak: float
    peak_season_id: int
    best_three: float | None
    best_three_start_season_id: int | None
    longevity: float
    resume_total: float
    accolade_total: float
    # The plain sum of season scores, which is what `total` was until the blend
    # existed. Published rather than dropped: `total_sd` and `mean_season` are
    # both defined against it, and neither describes the blend.
    season_total: float
    total_sd: float | None
    mean_season: float
    # What the archive could see of this career. `n_seasons` counts every
    # season carrying any component; `seasons_covered` counts the ones the
    # board could actually rank. They are equal for a league-era career and
    # they are not equal for one that started in 2013.
    seasons_covered: int
    coverage_from_year: int | None
    components_present: tuple[str, ...]


@dataclass(frozen=True)
class _Raw:
    """One career before the cohort scales exist. The scales are fitted on the
    qualified cohort, which cannot be known until every career is built, so the
    blend is a second pass over these."""

    row: dict[str, Any]
    components: dict[str, float]
    qualified: bool


def _publication_order(seasons: dict[int, Season]) -> dict[int, int]:
    """Season id to its position in this board's own year sequence.

    `career._season_order` is not reused here, and the reason is structural.
    Its `SCOPE_ALL` covers the CWL and the CDL and drops 2013-2016, because on
    the plus-minus axis those years have no comparable replacement scale to be
    summed against. This board has no such problem: its season unit is a
    percentile taken inside the season's own field, so 2013 is on the same
    scale as 2023 by construction.

    Reusing the era-scoped order made PRIME blind to an entire era. Measured on
    the archive of 2026-08-22, the window covered 2017 to 2026 and PRIME
    reached 167 of 490 careers, 51 of them qualified careers with no prime at
    all and 201 careers holding pre-2017 seasons that no window could see. A
    component carrying a quarter of the ranking cannot be measurable in two
    eras and absent in the third, so the sequence is every published season.

    The windowing rule is unchanged and still `career._best_run`'s: the window
    is three seasons of the sequence, and sitting one out costs what it cost.
    """
    return {
        season.season_id: i
        for i, season in enumerate(sorted(seasons.values(), key=lambda s: (s.year, s.season_id)))
    }


def _spans(
    cohort: Sequence[tuple[bool, Mapping[str, float]]],
) -> dict[str, tuple[float, float]]:
    """Per component: the low and high of the qualified cohort.

    Fitted on qualified careers and applied to every career, so a career below
    the season floor can scale outside 0..100. Nothing ranks one, and clamping
    it would misreport what it was: a career the board does not rank.
    """
    out: dict[str, tuple[float, float]] = {}
    for name in CAREER_COMPONENT_WEIGHTS:
        values = [
            components[name] for qualified, components in cohort if qualified and name in components
        ]
        if values:
            out[name] = (min(values), max(values))
    return out


def _scale(value: float, span: tuple[float, float]) -> float:
    low, high = span
    # A component with no spread across the cohort distinguishes nobody. Every
    # career reads 0 on it and the weight moves to the components that do.
    if high <= low:
        return 0.0
    return 100.0 * (value - low) / (high - low)


def build(
    scores: list[SeasonScore],
    seasons: dict[int, Season],
    replacement: Mapping[int, float] | None = None,
    resume_years: set[int] | None = None,
    accolade_years: set[int] | None = None,
) -> list[CareerRank]:
    """One career row per player, ranked over the seasons that can be scored
    and reporting the seasons that cannot.

    A season with a finish record and no box score is kept as an entry and is
    not fed to the season sum as a zero. It moves `n_seasons` and never
    `season_total`, `peak` or `best_three`, and the gap between `n_seasons` and
    `seasons_covered` is what the row publishes about its own coverage.

    `replacement` is the per-season floor LONGEVITY is measured above, built
    over the whole archive by the caller: restricting a run must not change
    what a season is worth. A season whose cohort has no qualified player has
    no floor and contributes nothing to LONGEVITY, which is an absent key
    rather than a zero for the same reason `career.replacement_by_season`
    returns one.

    `resume_years` and `accolade_years` are the years each of those components
    can be seen in at all. A career with no season in them is scored without
    that component; a career with one and no credit is scored as a zero.

    A player with no scorable season at all cannot be ranked and is left out;
    `artifact` reports how many that was.
    """
    floors = replacement or {}
    by_player: dict[int, list[SeasonScore]] = {}
    for s in scores:
        by_player.setdefault(s.player_id, []).append(s)

    order = _publication_order(seasons)
    raws: list[_Raw] = []
    for player_id, unsorted in sorted(by_player.items()):
        entries = sorted(unsorted, key=lambda e: e.season_id)
        scorable = [(e, e.score) for e in entries if e.score is not None]
        if not scorable:
            continue
        season_total = sum(score for _, score in scorable)
        # Seasons are summed as independent, same understated-correlation
        # caveat career.py states for its own total_sd: the underlying
        # metric percentiles share a cohort across years, so this is a
        # floor on the true width, not an exact one.
        variances = [e.sd**2 for e, _ in scorable if e.sd is not None]
        total_sd = math.sqrt(sum(variances)) if len(variances) == len(scorable) else None
        peak_entry, peak_score = max(scorable, key=lambda pair: (pair[1], pair[0].season_id))
        run = career._best_run([(e.season_id, score) for e, score in scorable], order)
        # Every season the board can score contributes what it was worth above
        # replacement, whatever its map count. A season under the map floor can
        # fall below a floor built from seasons that cleared it; it contributes
        # nothing rather than subtracting, because a season too thin to have
        # qualified cannot take a career backwards.
        longevity = sum(
            max(0.0, score - floors[e.season_id]) for e, score in scorable if e.season_id in floors
        )
        resume_total = sum(e.components.get(RESUME, 0.0) for e in entries)
        accolade_total = sum(e.components.get(ACCOLADE, 0.0) for e in entries)

        years = {seasons[e.season_id].year for e in entries if e.season_id in seasons}
        covered_years = [
            seasons[e.season_id].year
            for e in entries
            if PERFORMANCE in e.components and e.season_id in seasons
        ]
        present: set[str] = set()
        for e in entries:
            present.update(e.present)

        components: dict[str, float] = {PEAK: peak_score, LONGEVITY: longevity}
        # A career with no three-season window in the league sequence has no
        # PRIME to weigh. It is an absence and not a zero: the seasons it would
        # be built from were never played.
        if run is not None:
            components[PRIME] = run[0]
        if resume_years is None or years & resume_years:
            components[RESUME] = resume_total
        if accolade_years is None or years & accolade_years:
            components[ACCOLADE] = accolade_total

        qualified = len(scorable) >= MIN_SEASONS
        raws.append(
            _Raw(
                row={
                    "player_id": player_id,
                    "n_seasons": len(entries),
                    # The floor is read against the seasons the board ranks,
                    # not against every season the career touches: admitting a
                    # career on seasons that carry no performance would qualify
                    # it on coverage it does not have.
                    "qualified": qualified,
                    "peak": peak_score,
                    "peak_season_id": peak_entry.season_id,
                    "best_three": None if run is None else run[0],
                    "best_three_start_season_id": None if run is None else run[1],
                    "longevity": longevity,
                    "resume_total": resume_total,
                    "accolade_total": accolade_total,
                    "season_total": season_total,
                    "total_sd": total_sd,
                    "mean_season": season_total / len(scorable),
                    "seasons_covered": len(covered_years),
                    "coverage_from_year": min(covered_years) if covered_years else None,
                    "components_present": tuple(sorted(present)),
                },
                components=components,
                qualified=qualified,
            )
        )

    spans = _spans([(raw.qualified, raw.components) for raw in raws])
    out: list[CareerRank] = []
    for raw in raws:
        scaled = {
            name: _scale(value, spans[name])
            for name, value in raw.components.items()
            if name in spans
        }
        total = renormalize(scaled, CAREER_COMPONENT_WEIGHTS)
        out.append(
            CareerRank(
                total=0.0 if total is None else total,
                career_components=scaled,
                **raw.row,
            )
        )
    return out


def artifact(
    rows: list[CareerRank],
    n_unrankable: int = 0,
    spans: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    qualified = [r for r in rows if r.qualified]
    return {
        "min_seasons_floor": MIN_SEASONS,
        "season_component_weights": dict(SEASON_COMPONENT_WEIGHTS),
        "career_component_weights": dict(CAREER_COMPONENT_WEIGHTS),
        "career_blend_rule": CAREER_BLEND_RULE,
        "longevity_rule": LONGEVITY_RULE,
        # How many careers each component's coverage reaches. A component
        # missing here is missing because the archive cannot see it, never
        # because the career scored nothing on it.
        "component_coverage": {
            name: sum(1 for r in rows if name in r.career_components)
            for name in CAREER_COMPONENT_WEIGHTS
        },
        "n_renormalized": sum(
            1 for r in rows if len(r.career_components) < len(CAREER_COMPONENT_WEIGHTS)
        ),
        # The cohort low and high each component was scaled against.
        "component_scale": {
            name: {"low": round(low, 4), "high": round(high, 4)}
            for name, (low, high) in sorted((spans or {}).items())
        },
        "n_players": len(rows),
        # A career the board holds seasons for and cannot rank any of.
        "n_unrankable": n_unrankable,
        "n_partial_coverage": sum(1 for r in rows if r.seasons_covered < r.n_seasons),
        "seasons_uncovered": sum(r.n_seasons - r.seasons_covered for r in rows),
        "n_qualified": len(qualified),
        "n_below_floor": len(rows) - len(qualified),
        "top_ten_by_total": [
            {
                "player_id": r.player_id,
                "total": round(r.total, 2),
                "components": {
                    name: round(value, 2) for name, value in sorted(r.career_components.items())
                },
            }
            for r in sorted(qualified, key=lambda r: (-r.total, r.player_id))[:10]
        ],
        "top_ten_by_season_total": [
            {
                "player_id": r.player_id,
                "season_total": round(r.season_total, 2),
                "total_sd": None if r.total_sd is None else round(r.total_sd, 2),
            }
            for r in sorted(qualified, key=lambda r: (-r.season_total, r.player_id))[:10]
        ],
        "top_ten_by_mean_season": [
            {"player_id": r.player_id, "mean_season": round(r.mean_season, 2)}
            for r in sorted(qualified, key=lambda r: (-r.mean_season, r.player_id))[:10]
        ],
        "top_ten_by_peak": [
            {"player_id": r.player_id, "peak": round(r.peak, 2)}
            for r in sorted(qualified, key=lambda r: (-r.peak, r.player_id))[:10]
        ],
        "top_ten_by_best_three": [
            {"player_id": r.player_id, "best_three": round(bt, 2)}
            for r, bt in sorted(
                ((r, r.best_three) for r in qualified if r.best_three is not None),
                key=lambda pair: (-pair[1], pair[0].player_id),
            )[:10]
        ],
        "top_ten_by_longevity": [
            {"player_id": r.player_id, "longevity": round(r.longevity, 2)}
            for r in sorted(qualified, key=lambda r: (-r.longevity, r.player_id))[:10]
        ],
    }


def _raw_components(row: CareerRank) -> dict[str, float]:
    """The five components in their own units, keyed the way `build` keyed
    them, over the components this career's coverage reaches."""
    values = {
        PEAK: row.peak,
        PRIME: row.best_three,
        LONGEVITY: row.longevity,
        RESUME: row.resume_total,
        ACCOLADE: row.accolade_total,
    }
    return {
        name: value
        for name, value in values.items()
        if name in row.career_components and value is not None
    }


def scales(rows: list[CareerRank]) -> dict[str, tuple[float, float]]:
    """The cohort spans a built board was scaled against, read back off the
    built rows for the artifact. Same function `build` fitted them with, over
    the same population, so this reports the spans rather than re-deriving
    them by a second rule."""
    return _spans([(row.qualified, _raw_components(row)) for row in rows])
