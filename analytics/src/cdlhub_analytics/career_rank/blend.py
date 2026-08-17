"""Career blend: peak / best-three / total over the award-weighted breadth
score, per the pre-registration.

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
from dataclasses import dataclass
from typing import Any

from .. import career
from ..ratings.preflight import Season
from .evalpop import MIN_SEASONS


@dataclass(frozen=True)
class SeasonScore:
    player_id: int
    season_id: int
    score: float  # breadth score, award credit applied, 0..100
    sd: float | None = None  # from breadth.SeasonBreadth.sd; award credit adds no variance


@dataclass(frozen=True)
class CareerRank:
    player_id: int
    n_seasons: int
    qualified: bool  # clears the MIN_SEASONS floor
    total: float
    total_sd: float | None
    peak: float
    peak_season_id: int
    best_three: float | None
    best_three_start_season_id: int | None


def build(scores: list[SeasonScore], seasons: dict[int, Season]) -> list[CareerRank]:
    by_player: dict[int, list[SeasonScore]] = {}
    for s in scores:
        by_player.setdefault(s.player_id, []).append(s)

    order = career._season_order(seasons, career.SCOPE_ALL)
    out: list[CareerRank] = []
    for player_id, entries in sorted(by_player.items()):
        entries = sorted(entries, key=lambda e: e.season_id)
        total = sum(e.score for e in entries)
        # Seasons are summed as independent, same understated-correlation
        # caveat career.py states for its own total_sd: the underlying
        # metric percentiles share a cohort across years, so this is a
        # floor on the true width, not an exact one.
        variances = [e.sd**2 for e in entries if e.sd is not None]
        total_sd = math.sqrt(sum(variances)) if len(variances) == len(entries) else None
        peak_entry = max(entries, key=lambda e: (e.score, e.season_id))
        run = career._best_run([(e.season_id, e.score) for e in entries], order)
        out.append(
            CareerRank(
                player_id=player_id,
                n_seasons=len(entries),
                qualified=len(entries) >= MIN_SEASONS,
                total=total,
                total_sd=total_sd,
                peak=peak_entry.score,
                peak_season_id=peak_entry.season_id,
                best_three=None if run is None else run[0],
                best_three_start_season_id=None if run is None else run[1],
            )
        )
    return out


def artifact(rows: list[CareerRank]) -> dict[str, Any]:
    qualified = [r for r in rows if r.qualified]
    return {
        "min_seasons_floor": MIN_SEASONS,
        "n_players": len(rows),
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
