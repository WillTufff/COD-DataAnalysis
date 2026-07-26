"""How much of the map backtest is the scoreboard predicting itself?

The player rating learns what wins maps by regressing map outcome on the two
teams' box-score differentials. Several of those columns are not correlates of
winning — they *are* the win condition. Capture the Flag is decided by captures;
Hardpoint scores a point per second of hill control; a Search and Destroy round
ends on a plant or defuse. So a high map accuracy is not evidence of predictive
skill, and publishing it as though it were would be the same mistake the
man-advantage exclusion (see player_rating's 2.1.0 notes) exists to avoid.

This module measures the size of the problem instead of arguing about it, with
the crudest possible baseline: for one feature at a time, take the sign of the
team-A-minus-team-B differential and call that the winner. No weights, no
fitting, nothing learned. A column whose sign alone picks the winner on 94% of
maps has told you the result.

The baseline is scored on exactly the maps the fitted model predicted, so the
two numbers are comparable. That costs the sign rule nothing — it has no
training set to speak of, so it could have used every map — and it removes the
objection that the comparison is against a different sample.

`direction` records which way the column points, because a leaked column can be
inverted: more deaths means fewer wins. It is read off the data rather than
declared, so a feature whose sign is not obvious in advance is still reported
correctly.

The artifact drives the leakage table on /methodology.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg

from ..maprows import Coverage, MapRow
from . import player_rating as pr


def _sign_rule(diffs: Sequence[pr.GameDiff], j: int) -> tuple[int, int, int]:
    """(correct, n, direction) for feature j, scored by the differential's sign.

    Maps where the two teams tie exactly on the column are skipped: the rule has
    no opinion there, and counting a coin flip either way would misreport it.
    """
    correct = n = 0
    for g in diffs:
        d = g.diff[j]
        if d == 0.0:
            continue
        n += 1
        if (d > 0) == g.a_won:
            correct += 1
    if not n:
        return (0, 0, 1)
    # Below half means the column points the other way; report the rule at the
    # direction the data actually supports rather than a sub-coin-flip number.
    if correct * 2 < n:
        return (n - correct, n, -1)
    return (correct, n, 1)


def measure(
    conn: psycopg.Connection[tuple[object, ...]],
    version: str,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> dict[str, Any]:
    """Per cohort: the fitted model's accuracy against each column's sign rule."""
    if rows is None or coverage is None:
        rows, coverage = pr.load(conn)

    cohorts = pr.build_cohorts(rows, coverage, version)
    diffs = pr.build_game_diffs(rows, cohorts)
    preds = pr.backtest_maps(diffs)

    predicted: dict[tuple[int, int], set[int]] = {}
    hits: dict[tuple[int, int], list[int]] = {}
    for mp in preds:
        predicted.setdefault(mp.cohort, set()).add(mp.game_id)
        tally = hits.setdefault(mp.cohort, [0, 0])
        tally[1] += 1
        if (mp.prediction.p > 0.5) == mp.prediction.won:
            tally[0] += 1

    seasons, modes = pr.label_context(conn)
    by_cohort: list[dict[str, Any]] = []
    for key in sorted(predicted):
        season_id, mode_id = key
        cohort = cohorts[key]
        scored = [g for g in diffs[key] if g.game_id in predicted[key]]
        if not scored:
            continue
        features: list[dict[str, Any]] = []
        for j, f in enumerate(cohort.features):
            correct, n, direction = _sign_rule(scored, j)
            if not n:
                continue
            features.append(
                {
                    "key": f.key,
                    "label": f.label,
                    "accuracy": round(correct / n, 4),
                    "n": n,
                    "direction": direction,
                    "slaying": f.slaying,
                }
            )
        features.sort(key=lambda d: -float(d["accuracy"]))
        model_correct, model_n = hits[key]
        by_cohort.append(
            {
                "season_id": season_id,
                "year": seasons[season_id]["year"],
                "title": seasons[season_id]["title"],
                "mode": modes[mode_id],
                "n_maps": len(scored),
                "model_accuracy": round(model_correct / model_n, 4),
                "features": features,
                # The headline per cohort: the single best column, and how much
                # the whole fitted model adds on top of it.
                "best_feature": features[0] if features else None,
                "model_gain": (
                    round(model_correct / model_n - float(features[0]["accuracy"]), 4)
                    if features
                    else None
                ),
            }
        )

    return {"version": version, "rule": "sign of the team differential", "by_cohort": by_cohort}
