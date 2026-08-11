"""Three gates a column clears before it is allowed into a feature set.

Measured availability is what the resolver already enforces, and on its own it
admits columns it should not. A column can be populated on every row, carry real
signal, and still be the wrong thing to fit: because it is the scoreboard under
another name, or because it does not mean the same thing on two titles. So a
column clears three gates rather than one, and each is a number rather than a
judgement:

1. **Availability and completeness** — which titles track it, how much of each
   title's rows carry a real value, and how many team-maps it costs. A feature
   whose denominator is zero for one side drops the whole map from the cohort
   design, so a rate can be available and still expensive.

2. **Leakage risk** — the sign of the team differential, scored against the map
   result. The same rule `leakage.py` runs, read per column across every cohort
   that carries it rather than per cohort across every column: the question here
   is whether *this* column already knows the winner, anywhere.

3. **Stability and portability** — whether the relationship to the target holds
   across titles. A column whose sign-rule direction flips between titles is not
   one quantity measured twice, and a cohort may still fit it while a model that
   crosses a title seam may not carry it. This is the gate that separates a
   column that is merely available from one that travels.

The artifact is the record of the decision. Feature sets are versioned data, so
which columns were admitted to which version is a claim the project has to be
able to reproduce, and a paragraph in a document is not that.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from typing import Any

import psycopg

from ..maprows import Coverage, MapRow
from . import player_rating as pr
from .leakage import sign_rule

# A sign rule this far from a coin flip has told you the result rather than
# predicted it. The threshold is a reporting line, not a rejection: the objective
# columns sit above it deliberately and are tagged value_only for that reason.
LEAKY_ACCURACY = 0.75


def _denominator_failures(
    rows: Sequence[MapRow], cohorts: dict[tuple[int, int], pr.Cohort]
) -> dict[tuple[int, int], dict[str, int]]:
    """Per cohort, per feature: team-maps whose denominator sums to nothing.

    One such side is enough to drop the map, because a differential needs both
    profiles. Counting sides rather than maps keeps the number honest when both
    teams fail on the same map for the same reason.
    """
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for row in rows:
        cohort = cohorts.get((row.season_id, row.mode_id))
        if cohort is not None and cohort.accepts(row):
            per_game[row.game_id].append(row)

    out: dict[tuple[int, int], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for members in per_game.values():
        cohort = cohorts[(members[0].season_id, members[0].mode_id)]
        for team_id in {m.team_id for m in members}:
            side = [m for m in members if m.team_id == team_id]
            for feature in cohort.features:
                if sum(feature.denominator(m) for m in side) <= 0:
                    out[cohort.key][feature.key] += 1
    return {k: dict(v) for k, v in out.items()}


def verdicts(cohorts: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Gates 2 and 3 for one column, from its per-cohort rows.

    Separate from `measure` because this is the part with an opinion in it —
    everything above is arithmetic on a design matrix, and this is where a
    number becomes a verdict. It takes rows rather than a database so the
    verdicts can be tested against inputs whose answer is known.
    """
    accuracies = [c["sign_accuracy"] for c in cohorts if c["sign_accuracy"]]
    # Direction is a property of the title, not of the season: a column that
    # means one thing on WWII and another on Black Ops 4 shows up here as two
    # titles disagreeing, which is gate 3. Cohorts the rule had no opinion on
    # carry a null direction and are not evidence either way.
    directions: dict[str, set[int]] = {}
    for c in cohorts:
        if c["direction"] is not None:
            directions.setdefault(str(c["title"]), set()).add(int(c["direction"]))
    agreed = {d for ds in directions.values() for d in ds}
    return {
        "n_cohorts": len(cohorts),
        "titles": sorted(directions),
        # Gate 2: the most a single column knew, anywhere it was fitted.
        "max_sign_accuracy": max(accuracies) if accuracies else None,
        "leaky": bool(accuracies) and max(accuracies) >= LEAKY_ACCURACY,
        # Gate 3: one direction across every title, or not one quantity.
        "direction_by_title": {t: sorted(d) for t, d in sorted(directions.items())},
        "portable": len(agreed) <= 1,
        # Gate 1, the part measured availability does not cover.
        "sides_without_denominator": sum(int(c["sides_without_denominator"]) for c in cohorts),
    }


def measure(
    conn: psycopg.Connection[tuple[object, ...]],
    version: str,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> dict[str, Any]:
    """Per feature of one version: the three gates, and what each of them said."""
    if rows is None or coverage is None:
        rows, coverage = pr.load(conn)

    cohorts = pr.build_cohorts(rows, coverage, version)
    diffs = pr.build_game_diffs(rows, cohorts)
    failures = _denominator_failures(rows, cohorts)
    seasons, modes = pr.label_context(conn)

    # One feature key can appear in several modes under different denominators;
    # they are separate columns and are reported separately.
    gathered: dict[str, dict[str, Any]] = {}
    for key in sorted(cohorts, key=lambda k: (seasons[k[0]]["year"], modes[k[1]])):
        cohort = cohorts[key]
        scored = diffs.get(key, [])
        if not scored:
            continue
        for j, feature in enumerate(cohort.features):
            correct, n, direction = sign_rule(scored, j)
            entry = gathered.setdefault(
                feature.key,
                {
                    "key": feature.key,
                    "label": feature.label,
                    "eligibility": feature.eligibility,
                    "slaying": feature.slaying,
                    "denominator": feature.denom_kind,
                    "sources": list(feature.sources),
                    "cohorts": [],
                },
            )
            entry["cohorts"].append(
                {
                    "year": seasons[cohort.season_id]["year"],
                    "title": cohort.title,
                    "mode": modes[cohort.mode_id],
                    "n_maps": len(scored),
                    "sign_accuracy": round(correct / n, 4) if n else None,
                    "sign_n": n,
                    "direction": direction if n else None,
                    "sides_without_denominator": failures.get(key, {}).get(feature.key, 0),
                }
            )

    features: list[dict[str, Any]] = []
    for feature_key in sorted(gathered):
        entry = gathered[feature_key]
        features.append({**entry, **verdicts(entry["cohorts"])})

    return {
        "version": version,
        "rule": (
            "per column: measured availability and denominator cost, the sign-rule "
            "accuracy of the team differential, and whether its direction holds across titles"
        ),
        "leaky_accuracy": LEAKY_ACCURACY,
        "n_features": len(features),
        "n_leaky": sum(1 for f in features if f["leaky"]),
        "n_not_portable": sum(1 for f in features if not f["portable"]),
        "features": features,
    }
