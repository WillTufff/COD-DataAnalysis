"""Career blend: peak / best-three / total over the season score, per the
pre-registration.

CWL years count at full per-year weight, same sequence as CDL — a deliberate
departure from `career.py`'s plus-minus era pooling, justified in the
pre-registration doc: this engine's season unit (breadth score) is already
computed per year, so there is no repeated estimate to guard against
triple-counting. `_best_run` and `_season_order` are reused from `career.py`
unchanged; the windowing logic ("sitting one out costs what it cost") applies
identically here.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .. import career
from ..ratings.preflight import Season
from .evalpop import MIN_SEASONS

PERFORMANCE = "PERFORMANCE"
RESUME = "RESUME"
ACCOLADE = "ACCOLADE"

# What a component is worth inside a season score, and the only place that is
# declared. PERFORMANCE is the whole of it here: RESUME and ACCOLADE are built
# per season and stored on the row, and neither enters the ranking until the
# career blend fixes its weight against the other components. A component with
# no weight is recorded and never scored.
#
# Each component is held on its own scale, because the scales differ — breadth
# is 0..100 while resume and accolade are shares of the year, 0..1. Nothing
# reads a second component until the blend puts them on a common scale first,
# so the mismatch cannot reach a number from here.
SEASON_COMPONENT_WEIGHTS: Mapping[str, float] = {PERFORMANCE: 1.0}


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
    total: float
    total_sd: float | None
    # `total` over the seasons that carry a score. Published beside the sum so
    # a reader can see how much of a total is rate and how much is length;
    # nothing ranks on it.
    mean_season: float
    peak: float
    peak_season_id: int
    best_three: float | None
    best_three_start_season_id: int | None
    # What the archive could see of this career. `n_seasons` counts every
    # season carrying any component; `seasons_covered` counts the ones the
    # board could actually rank. They are equal for a league-era career and
    # they are not equal for one that started in 2013.
    seasons_covered: int
    coverage_from_year: int | None
    components_present: tuple[str, ...]


def build(scores: list[SeasonScore], seasons: dict[int, Season]) -> list[CareerRank]:
    """One career row per player, ranked over the seasons that can be scored
    and reporting the seasons that cannot.

    A season with a finish record and no box score is kept as an entry and is
    not fed to the total as a zero. It moves `n_seasons` and never `total`,
    `peak` or `best_three`, and the gap between `n_seasons` and
    `seasons_covered` is what the row publishes about its own coverage.

    A player with no scorable season at all cannot be ranked and is left out;
    `artifact` reports how many that was.
    """
    by_player: dict[int, list[SeasonScore]] = {}
    for s in scores:
        by_player.setdefault(s.player_id, []).append(s)

    order = career._season_order(seasons, career.SCOPE_ALL)
    out: list[CareerRank] = []
    for player_id, entries in sorted(by_player.items()):
        entries = sorted(entries, key=lambda e: e.season_id)
        scorable = [(e, e.score) for e in entries if e.score is not None]
        if not scorable:
            continue
        total = sum(score for _, score in scorable)
        # Seasons are summed as independent, same understated-correlation
        # caveat career.py states for its own total_sd: the underlying
        # metric percentiles share a cohort across years, so this is a
        # floor on the true width, not an exact one.
        variances = [e.sd**2 for e, _ in scorable if e.sd is not None]
        total_sd = math.sqrt(sum(variances)) if len(variances) == len(scorable) else None
        peak_entry, peak_score = max(scorable, key=lambda pair: (pair[1], pair[0].season_id))
        run = career._best_run([(e.season_id, score) for e, score in scorable], order)
        covered_years = [
            seasons[e.season_id].year
            for e in entries
            if PERFORMANCE in e.components and e.season_id in seasons
        ]
        present: set[str] = set()
        for e in entries:
            present.update(e.present)
        out.append(
            CareerRank(
                player_id=player_id,
                n_seasons=len(entries),
                # The floor is read against the seasons the board ranks, not
                # against every season the career touches: admitting a career
                # on seasons that carry no performance would qualify it on
                # coverage it does not have.
                qualified=len(scorable) >= MIN_SEASONS,
                total=total,
                total_sd=total_sd,
                mean_season=total / len(scorable),
                peak=peak_score,
                peak_season_id=peak_entry.season_id,
                best_three=None if run is None else run[0],
                best_three_start_season_id=None if run is None else run[1],
                seasons_covered=len(covered_years),
                coverage_from_year=min(covered_years) if covered_years else None,
                components_present=tuple(sorted(present)),
            )
        )
    return out


def artifact(rows: list[CareerRank], n_unrankable: int = 0) -> dict[str, Any]:
    qualified = [r for r in rows if r.qualified]
    return {
        "min_seasons_floor": MIN_SEASONS,
        "season_component_weights": dict(SEASON_COMPONENT_WEIGHTS),
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
                "total_sd": None if r.total_sd is None else round(r.total_sd, 2),
            }
            for r in sorted(qualified, key=lambda r: (-r.total, r.player_id))[:10]
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
    }
