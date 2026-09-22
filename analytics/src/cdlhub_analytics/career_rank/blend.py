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

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

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
# was seen. Every component is scaled across the qualified cohort against the
# pinned p1/p99 span before this is applied, so the weights are shares of one
# comparable 0..100 scale and not of five different units.
CAREER_COMPONENT_WEIGHTS: Mapping[str, float] = {
    PEAK: 20.0,
    PRIME: 25.0,
    LONGEVITY: 20.0,
    RESUME: 25.0,
    ACCOLADE: 10.0,
}

CAREER_BLEND_RULE = (
    "each component scaled 0..100 across the qualified cohort against the "
    "pinned p1/p99 span, then "
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


SPAN_LOW_Q = 0.01
SPAN_HIGH_Q = 0.99

# The pinned spans, frozen alongside `anchors.json` in the same package and by
# the same contract: a named base run, a digest over the values, and a
# re-freeze that keeps what it replaced in `history` rather than losing it.
SPANS_PATH = Path(__file__).with_name("spans.json")

FITTED = "fitted"
PINNED = "pinned"


def _q(sorted_vals: Sequence[float], p: float) -> float:
    """The value at percentile `p` (0..1) of an already-sorted sequence,
    linear interpolation between order statistics — `numpy.percentile`'s
    default method, implemented directly so this module carries no numpy
    dependency of its own."""
    i = p * (len(sorted_vals) - 1)
    lo = int(i)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (i - lo) * (sorted_vals[hi] - sorted_vals[lo])


def _spans(
    cohort: Sequence[tuple[bool, Mapping[str, float]]],
) -> dict[str, tuple[float, float]]:
    """Per component: a robust span fit over the qualified cohort, at the 1st
    and 99th percentiles rather than the raw min and max.

    Min-max let a single career set the whole scale: RESUME's span was
    Crimsix alone and ACCOLADE's was Simp alone (measured on run 1674), so
    that one career's own number moving rescaled every other career's
    contribution from the component with no weight having moved. The
    percentile fit is still taken over the qualified cohort only and applied
    to every career, so a career below the season floor — or, now, a
    qualified career sitting outside its own component's [p1, p99] — can
    still scale outside 0..100. That is expected, not an error: nothing
    ranks a career from clamping it, and misreporting an out-of-range value
    as a floor or ceiling it does not have would be the actual defect.
    """
    out: dict[str, tuple[float, float]] = {}
    for name in CAREER_COMPONENT_WEIGHTS:
        values = sorted(
            components[name] for qualified, components in cohort if qualified and name in components
        )
        if values:
            out[name] = (_q(values, SPAN_LOW_Q), _q(values, SPAN_HIGH_Q))
    return out


def _digest(spans: Mapping[str, tuple[float, float]]) -> str:
    """A hash over the component spans alone, the same convention `anchors.py`
    uses over membership: what is frozen is the values, not anything computed
    beside them."""
    body = "\n".join(f"{name}|{low!r}|{high!r}" for name, (low, high) in sorted(spans.items()))
    return hashlib.sha256(body.encode()).hexdigest()


def load_pinned_spans() -> dict[str, Any] | None:
    """The pinned spans pointer, or `None` when `spans.json` does not exist —
    a cold checkout without one falls back to fitting rather than failing."""
    try:
        loaded = json.loads(SPANS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


def _span_map(pointer: Mapping[str, Any]) -> dict[str, tuple[float, float]]:
    return {name: (float(pair[0]), float(pair[1])) for name, pair in pointer["spans"].items()}


def _labels_on_record(pointer: Mapping[str, Any] | None) -> set[str]:
    if pointer is None:
        return set()
    history = pointer.get("history")
    earlier = [entry.get("cut") for entry in history] if isinstance(history, list) else []
    return {str(label) for label in [pointer.get("cut"), *earlier] if label}


def refit_spans(
    rows: Sequence[CareerRank],
    cut: str,
    base_run: int,
    only: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Fit the p1/p99 spans against `rows` (a built board) and freeze them
    under `cut`, stamping `base_run` so a later reader knows which run's
    cohort set the scale. A label already on record is refused, the same
    re-freeze contract `anchors.freeze` and `evalpop.freeze` use; the entry it
    replaces moves into `history` rather than being lost.

    `only` re-cuts the named components and carries every other span over
    from the current pin unchanged. `base_runs` records which run each
    component's span was cut from.
    """
    previous = load_pinned_spans()
    if cut in _labels_on_record(previous):
        raise ValueError(f"span set '{cut}' is already on record; a re-freeze takes a new label")
    fitted = _spans([(row.qualified, _raw_components(row)) for row in rows])
    base_runs = {name: base_run for name in fitted}
    if only is not None:
        unknown = set(only) - set(CAREER_COMPONENT_WEIGHTS)
        if unknown:
            raise ValueError(f"unknown component(s): {', '.join(sorted(unknown))}")
        if previous is None:
            raise ValueError("a partial re-cut needs a pinned span set to carry over")
        carried = _span_map(previous)
        earlier = previous.get("base_runs")
        earlier_runs = earlier if isinstance(earlier, dict) else {}
        fitted = {**carried, **{name: fitted[name] for name in only if name in fitted}}
        base_runs = {
            name: base_run
            if name in only
            else int(cast(int, earlier_runs.get(name, previous.get("base_run"))))
            for name in fitted
        }
    history: list[dict[str, Any]] = []
    if previous is not None:
        earlier = previous.get("history")
        history = list(earlier) if isinstance(earlier, list) else []
        history.append(
            {
                "cut": previous.get("cut"),
                "base_run": previous.get("base_run"),
                "spans": previous.get("spans"),
                "base_runs": previous.get("base_runs"),
                "sha256": previous.get("sha256"),
                "frozen_at": previous.get("frozen_at"),
            }
        )
    pointer: dict[str, Any] = {
        "cut": cut,
        "base_run": base_run,
        "estimator": (
            f"quantile fit at p{SPAN_LOW_Q:g}/p{SPAN_HIGH_Q:g}, linear interpolation between "
            "order statistics, over the qualified cohort"
        ),
        "spans": {name: list(span) for name, span in sorted(fitted.items())},
        "base_runs": dict(sorted(base_runs.items())),
        "sha256": _digest(fitted),
        "frozen_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "supersedes": previous.get("cut") if previous else None,
        "history": history,
    }
    SPANS_PATH.write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    return pointer


def _scale(value: float, span: tuple[float, float]) -> float:
    low, high = span
    # A component with no spread across the cohort distinguishes nobody. Every
    # career reads 0 on it and the weight moves to the components that do.
    if high <= low:
        return 0.0
    # Deliberately unclamped: a value outside [low, high] reads outside
    # 0..100 and keeps its lead rather than being pulled to the edge. This is
    # expected whether the span was fitted fresh (a career past the p99 cut)
    # or pinned to an older run (any later career, since the archive keeps
    # growing against a fixed scale) — not an error condition either way.
    return 100.0 * (value - low) / (high - low)


def _resolve_spans(
    cohort: Sequence[tuple[bool, Mapping[str, float]]],
) -> tuple[dict[str, tuple[float, float]], dict[str, Any]]:
    """The spans this cohort scales against, and where they came from.

    Pinned spans (`spans.json`) are used for every component they name;
    a component the pin does not name is still fitted fresh, so a cold
    checkout with no pin at all is just the fitted case for every
    component. The provenance records which mode was used and, when
    pinned, the digest and base run a reader would need to reproduce it.
    """
    fitted = _spans(cohort)
    pointer = load_pinned_spans()
    if pointer is None:
        return fitted, {"mode": FITTED, "pinned_components": []}
    pinned = _span_map(pointer)
    return {**fitted, **pinned}, {
        "mode": PINNED,
        "cut": pointer.get("cut"),
        "base_run": pointer.get("base_run"),
        "sha256": pointer.get("sha256"),
        "pinned_components": sorted(pinned),
    }


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

    spans, _ = _resolve_spans([(raw.qualified, raw.components) for raw in raws])
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
    span_provenance: Mapping[str, Any] | None = None,
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
        # The same count over the careers the board actually ranks. A component
        # missing on 60 careers and a component missing on 60 ranked careers
        # are different facts, and only this one is about the published board.
        "component_coverage_qualified": {
            name: sum(1 for r in qualified if name in r.career_components)
            for name in CAREER_COMPONENT_WEIGHTS
        },
        "n_renormalized": sum(
            1 for r in rows if len(r.career_components) < len(CAREER_COMPONENT_WEIGHTS)
        ),
        "n_renormalized_qualified": sum(
            1 for r in qualified if len(r.career_components) < len(CAREER_COMPONENT_WEIGHTS)
        ),
        # The cohort low and high each component was scaled against. Nothing
        # else lives in this dict: it is published as component name -> span
        # and read that way downstream.
        "component_scale": {
            name: {"low": round(low, 4), "high": round(high, 4)}
            for name, (low, high) in sorted((spans or {}).items())
        },
        # Whether those spans were pinned to a named base run or fitted fresh
        # on this run's own cohort. A pin carries the run and digest it was
        # cut from; a fit carries neither.
        "span_provenance": {
            "mode": (span_provenance or {}).get("mode", FITTED),
            "cut": (span_provenance or {}).get("cut"),
            "base_run": (span_provenance or {}).get("base_run"),
            "sha256": (span_provenance or {}).get("sha256"),
            "pinned_components": (span_provenance or {}).get("pinned_components", []),
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
    built rows for the artifact. Same resolution `build` used — pinned spans
    preferred, fitted where nothing is pinned — over the same population, so
    this reports the spans rather than re-deriving them by a second rule."""
    spans, _ = _resolve_spans([(row.qualified, _raw_components(row)) for row in rows])
    return spans


def span_provenance(rows: list[CareerRank]) -> dict[str, Any]:
    """Whether the spans a built board was scaled against were pinned to a
    named base run or fitted fresh on this run's own cohort, for the run
    artifact. Same resolution `scales` reports the values for."""
    _, meta = _resolve_spans([(row.qualified, _raw_components(row)) for row in rows])
    return meta
