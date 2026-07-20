"""Does the intangible rating actually predict better than the box-score one?

The publishing rule says every model ships with its backtest, and that negative
results ship too. This module answers the version question honestly:

- Every version is scored on **the same maps**. Feature sets have different
  data requirements — a per-round denominator needs a round count, a trade
  feature needs a reconciled feed — so each version's walk-forward covers a
  slightly different set of maps. Comparing raw totals would let a version look
  better by quietly predicting an easier subset. Only maps that *every* version
  predicted enter the comparison.
- Results are reported per cohort as well as overall, because "v2 wins" is a
  weaker claim than "v2 wins in seven of nine season-modes".

The artifact drives the /methodology comparison table.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

import psycopg

from ..backtest import Prediction, evaluate
from ..maprows import Coverage, MapRow
from . import player_rating as pr


def _score(preds: Sequence[Prediction]) -> dict[str, float | int]:
    report = evaluate(list(preds))
    return {
        "n": report.n,
        "brier": round(report.brier, 5),
        "log_loss": round(report.log_loss, 5),
        "accuracy": round(report.accuracy, 4),
    }


def compare(
    conn: psycopg.Connection[tuple[object, ...]],
    versions: Sequence[str],
    published: str,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> dict[str, Any]:
    """Walk-forward comparison of several feature-set versions on common maps."""
    if rows is None or coverage is None:
        rows, coverage = pr.load(conn)

    by_version: dict[str, dict[int, pr.MapPrediction]] = {}
    cohort_of: dict[int, tuple[int, int]] = {}
    for version in versions:
        _ratings, preds, _artifact = pr.compute(conn, version, rows, coverage)
        by_version[version] = {m.game_id: m for m in preds}
        for m in preds:
            cohort_of[m.game_id] = m.cohort

    common = set.intersection(*(set(v) for v in by_version.values())) if by_version else set()

    seasons, modes = pr.label_context(conn)
    cohort_keys = sorted({cohort_of[g] for g in common})
    by_cohort = []
    for season_id, mode_id in cohort_keys:
        games = [g for g in common if cohort_of[g] == (season_id, mode_id)]
        if not games:
            continue
        by_cohort.append(
            {
                "season_id": season_id,
                "year": seasons[season_id]["year"],
                "title": seasons[season_id]["title"],
                "mode": modes[mode_id],
                "n_maps": len(games),
                "versions": {
                    v: _score([by_version[v][g].prediction for g in games]) for v in versions
                },
            }
        )

    overall = {v: _score([by_version[v][g].prediction for g in sorted(common)]) for v in versions}
    baseline = versions[0]
    deltas = {
        v: {
            "brier": round(
                cast(float, overall[v]["brier"]) - cast(float, overall[baseline]["brier"]), 5
            ),
            "log_loss": round(
                cast(float, overall[v]["log_loss"]) - cast(float, overall[baseline]["log_loss"]), 5
            ),
        }
        for v in versions
    }

    return {
        "versions": list(versions),
        "baseline": baseline,
        "published": published,
        "common_maps": len(common),
        "maps_predicted": {v: len(by_version[v]) for v in versions},
        "overall": overall,
        "delta_vs_baseline": deltas,
        "by_cohort": by_cohort,
        "features": {
            v: {
                f"{seasons[c.season_id]['year']} {modes[c.mode_id]}": list(c.feature_keys)
                for c in sorted(
                    pr.build_cohorts(rows, coverage, v).values(),
                    key=lambda c: (c.season_id, c.mode_id),
                )
            }
            for v in versions
        },
    }
