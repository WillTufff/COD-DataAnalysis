"""What the ratings are scored on, declared before there is a model to score.

A gate is only as good as the harness behind it, and a harness written after the
model it judges is written by whoever wants the model to pass. So this module is
the declaration: one primary test, a labelled secondary set, the metric, the
resampling unit, the seed, and the one coefficient family a forward test may
read. It is committed ahead of the model, hashed, and the hash is pinned below —
a declaration that moves after the fact fails the release gate rather than
quietly rescoring anything.

**One primary test.** Next-season persistence against era-adjusted K/D z, on the
off-diagonal cell: predicting *next season's K/D z*, which is K/D's own ground.
Fifteen tests with no declared primary is a licence to pick a winner afterwards,
so everything else here is secondary, labelled as such in the payload, and
reported without significance claims.

**The minimum detectable effect is computed, not asserted.** The pre-flight's
generator gives it in closed form from the sample size, the baseline correlation
and how much the two predictors agree. The record holds 561 consecutive
player-seasons against a baseline *r* of 0.564, which is a floor of about 0.08 —
and a floor computed for independent observations, which clustering widens. A
threshold declared under that cannot be met by a model that works, so the gate
reads the computed number rather than a chosen one.

**The resampling unit is declared per test family, and the plan's "series,
everywhere" does not survive contact with the primary test.** Maps within a
series share a lineup, a day, a patch and an opponent, so anything keyed by a map
or a series resamples whole series, identified by the `source_uid` prefix of
`map_key` rather than by any surrogate id. But a persistence observation is a
player-season transition assembled from tens of series, and no series contains a
whole one; a genuine series-cluster draw would need the whole pipeline refitted
per draw. The smallest cluster that contains whole observations there is the
**player**, which is strictly coarser than the per-observation draw the published
test uses today and therefore widens the interval — the direction the pre-flight
predicted. The design effect between the two is measured and published rather
than assumed.

**A declaration can be extended, and that is not the same act as editing one.**
Version 1.0.0 pinned a single digest over the whole thing, which made the pin
unpassable for any later phase that needed a new predictor — and an unpassable
rule gets edited, at which point it is not a rule. So the declaration is split:
`invariants()` holds what the test *is* — target, baseline, statistic, unit,
seed, threshold rule — and its digest never moves; the predictor list may grow,
and only grow, with every superseded version kept in `PIN_HISTORY` and each new
list checked to contain the last. P5 uses exactly that door and nothing else,
adding `skill` and one secondary before the model that produces them exists.

**And only the filtered family may be read forward.** The random-walk penalty is
two-sided, so a smoothed coefficient at season *t* has already seen *t+1*, and a
model trained on smoothed targets and scored on next-season persistence is scored
against a target that already contains the answer. `SCOPE` names the permitted
family and `assert_forward` is the enforcement — it raises, it does not warn.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from . import statespace

MODEL = "evaluation"
VERSION = "2.0.0"

# Resampling units. `SERIES` is the plan's declared default and applies to every
# map- and series-keyed test; `PLAYER` is what a player-season statistic can
# actually cluster on. See the module docstring.
SERIES = "series"
PLAYER = "player"

# The one coefficient family a forward test may read.
SCOPE = statespace.FILTERED

# Draws for every interval in the harness, and the seed they start from.
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20260811

# Two-sided 5% at 80% power, the convention every "detectable" number in the
# project is scaled by.
Z_ALPHA = 1.959964
Z_POWER = 0.841621
POWER_FACTOR = Z_ALPHA + Z_POWER

# How close a reproduction has to be before the harness is trusted to score
# anything new. The published artifacts are rounded to five decimals, so this is
# a rounding tolerance rather than a tolerance for disagreement.
REPRODUCTION_TOL = 5e-5


@dataclass(frozen=True)
class Test:
    """One declared test, primary or secondary."""

    name: str
    role: str
    what: str
    statistic: str
    target: str
    predictors: tuple[str, ...]
    baselines: tuple[str, ...]
    unit: str
    # Secondary tests are diagnostics: they get no interval-based verdict, and
    # the payload says so next to every number they publish.
    significance_claimed: bool

    def payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "what": self.what,
            "statistic": self.statistic,
            "target": self.target,
            "predictors": list(self.predictors),
            "baselines": list(self.baselines),
            "resampling_unit": self.unit,
            "significance_claimed": self.significance_claimed,
        }


PRIMARY = Test(
    name="next_season_persistence",
    role="primary",
    what=(
        "for every player with two consecutive qualified seasons, how well season N's "
        "rating predicts season N+1's era-adjusted K/D z — the off-diagonal cell, which "
        "is the baseline's own ground"
    ),
    statistic="pearson r, paired cluster bootstrap over players",
    target="era-adjusted K/D z, season N+1",
    predictors=("composite", "openskill", "skill"),
    baselines=("kd_z",),
    unit=PLAYER,
    significance_claimed=True,
)

SECONDARY: tuple[Test, ...] = (
    Test(
        name="leave_one_event_out",
        role="secondary",
        what=(
            "the map-level score with each event's maps withheld. Withheld from the map "
            "score rather than from a transition: an event's maps are spread across many "
            "transitions and none of them can be recomputed without refitting the pipeline"
        ),
        statistic="brier over the maps that remain, per withheld event",
        target="map winner",
        predictors=("openskill",),
        baselines=(),
        unit=SERIES,
        significance_claimed=False,
    ),
    Test(
        name="leave_one_title_out",
        role="secondary",
        what="persistence recomputed with each title's transitions withheld",
        statistic="pearson r per withheld title",
        target="era-adjusted K/D z, season N+1",
        predictors=("composite", "openskill"),
        baselines=("kd_z",),
        unit=SERIES,
        significance_claimed=False,
    ),
    Test(
        name="rookie_emergence",
        role="secondary",
        what="persistence restricted to a player's first qualified season as the predictor",
        statistic="pearson r over first-season transitions",
        target="era-adjusted K/D z, season N+1",
        predictors=("composite", "openskill"),
        baselines=("kd_z",),
        unit=PLAYER,
        significance_claimed=False,
    ),
    Test(
        name="roster_move_shock",
        role="secondary",
        what="persistence split by whether the player changed team between the two seasons",
        statistic="pearson r per stayed/moved group",
        target="era-adjusted K/D z, season N+1",
        predictors=("composite", "openskill"),
        baselines=("kd_z",),
        unit=PLAYER,
        significance_claimed=False,
    ),
    Test(
        name="season_plusminus_persistence",
        role="secondary",
        what=(
            "the season-varying plus-minus read forward, which is the read the scope rule "
            "exists to govern. Secondary because the plus-minus is not the rating being "
            "gated; it is here so the rule is exercised on real coefficients every run "
            "rather than sitting dormant until P5 needs it"
        ),
        statistic="pearson r over consecutive cells",
        target="era-adjusted K/D z, season N+1",
        predictors=("rapm_filtered",),
        baselines=("kd_z",),
        unit=PLAYER,
        significance_claimed=False,
    ),
    Test(
        name="prior_target_persistence",
        role="secondary",
        what=(
            "how well season N's SKILL rating predicts season N+1's filtered plus-minus — "
            "the quantity the box-score prior is fitted against, rather than the baseline's "
            "own. Declared with the predictor it scores and before that predictor exists, "
            "because the primary test asks a rating built to predict plus-minus to beat K/D z "
            "at predicting K/D z; if SKILL fails there and wins here, the phase has the reason "
            "for the failure rather than a shrug. Secondary, and it does not soften the gate"
        ),
        statistic="pearson r over consecutive cells",
        target="filtered plus-minus coefficient, season N+1",
        predictors=("skill", "composite"),
        baselines=("kd_z",),
        unit=PLAYER,
        significance_claimed=False,
    ),
    Test(
        name="calibration_by_bucket",
        role="secondary",
        what=(
            "the roster map forecast's observed win rate against its predicted probability, "
            "by predicted-probability decile, by era and by venue"
        ),
        statistic="observed minus predicted per bucket",
        target="map winner",
        predictors=("composite", "openskill"),
        baselines=("kd_z", "glicko"),
        unit=SERIES,
        significance_claimed=False,
    ),
)

# The published numbers the harness has to recover before it is trusted, named by
# the artifact they are stored in. The plan quotes the figures printed on
# /methodology instead — Δr = −0.26 over 541 transitions, Brier 0.24701 and
# 0.24517 over 9,030 maps — and those are a past run's. PD's identity merges, the
# lineup rule and feature set 2.2.0 all moved the population underneath them, and
# the same page already quotes 561 player-seasons in its pre-flight section. So
# the reproduction is against what the pipeline computes now, and the delta to the
# printed page is reported rather than absorbed.
REPRODUCE = (
    ("player_rating", "rating_persistence"),
    ("player_rating", "roster_forecast"),
)


# The validation figures printed on /methodology, pinned here so the page and the
# pipeline cannot drift apart unnoticed. They already had: the page's validation
# section quoted 541 transitions and 9,030 maps from a run before PD's identity
# merges, the lineup rule and feature set 2.2.0, while its own pre-flight section
# quoted the current 561. Nothing failed, because nothing was comparing them.
#
# Deliberately not part of `manifest()`. These are a mirror of a document and are
# expected to move whenever a repair legitimately moves a number; the manifest is
# a declaration that is not expected to move at all, and folding one into the
# other would mean re-pinning the declaration every time the page is refreshed.
# Same treatment `gates.PUBLISHED_BASES` gets, for the same reason.
PUBLISHED_FIGURES: dict[str, Any] = {
    "persistence_pairs": 561,
    "persistence_delta_r": -0.2286,
    "delta_r_tol": 5e-4,
    "forecast_maps": 9257,
    "brier_tol": 5e-5,
    # The panel the next rating will be gated on, and the floor computed for it
    # before that rating exists. Pinned for the same reason as everything else
    # here: a figure on the page that nothing compares against is a figure that
    # drifts, and this one is a threshold.
    "skill_panel": {
        "n": 267,
        "clusters": 90,
        "mde80_clustered": 0.1749,
        "distance_to_clear": 0.433,
    },
    # The plus-minus read forward, at the resolution the read is valid at, with
    # the pooled figure the page corrects and the era figure that inflated it.
    "plusminus_forward": {"n": 267, "r": 0.1974, "pooled_r": 0.2914, "era_r": 0.3641},
    # What the gate returned once the fourth predictor existed, from run 431/432.
    #
    # The panel is 218 rather than the 267 the floor was computed for, and the
    # difference is not a coverage failure: SKILL is predicted from the seasons
    # before it, so the earliest CDL season has no rating and its 49 transitions
    # cannot carry one. The floor that judges the result is therefore the one
    # this panel computes for itself, 0.1623, and the 0.1749 pinned above stays
    # what it always was — the number written before the model, against a panel
    # the model turned out not to fill.
    "skill_result": {"n": 218, "clusters": 75, "delta_r": -0.2416, "mde80": 0.1623},
    "forecast_brier": {
        "rapm": 0.24609,
        "rapm_prior": 0.24675,
        "rating": 0.2478,
        "rating_zshrink": 0.24875,
        "glicko": 0.25076,
        "kd": 0.25156,
    },
}


def invariants() -> dict[str, Any]:
    """The half of the declaration that is never allowed to move.

    A pinned manifest that no phase can legally extend has exactly one outcome:
    the phase that needs a new predictor edits the pin, and the pin stops meaning
    anything. P5 needs one — `skill` — so the declaration is split rather than
    reopened.

    What lives here is the *shape* of the test: what is predicted, what it is
    compared against, how the comparison is made, what is resampled and how the
    threshold is computed. Change any of it and this is a different evaluation,
    not an extended one, and the gate should say so. What deliberately does not
    live here is the predictor list, which may grow — and only grow; the gate
    checks each new version's list is a superset of the last.

    Every field below is byte-identical to what version 1.0.0 declared. The
    digest is new because the function is, not because a value moved.
    """
    return {
        "primary": {
            "name": PRIMARY.name,
            "what": PRIMARY.what,
            "statistic": PRIMARY.statistic,
            "target": PRIMARY.target,
            "baselines": list(PRIMARY.baselines),
            "resampling_unit": PRIMARY.unit,
            "significance_claimed": PRIMARY.significance_claimed,
        },
        "scope_permitted_forward": SCOPE,
        "bootstrap_b": BOOTSTRAP_B,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "power_alpha": 0.05,
        "power_target": 0.8,
        "reproduction_tolerance": REPRODUCTION_TOL,
    }


def invariants_sha256() -> str:
    return hashlib.sha256(
        json.dumps(invariants(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# The digest of the fixed half, pinned once and never re-pinned. A phase that
# needs this to change needs a new evaluation.
PINNED_INVARIANTS_SHA256 = "74cdb3f17cc79555e06c1f6beb1a1f7347634ee18d6fe2b855999dbf569a00a6"


# Every *superseded* version of the declaration, oldest first. The current one is
# not listed — its digest is `PINNED_SHA256` — because the manifest carries this
# tuple and a version cannot contain its own hash.
#
# Append-only, and the gate enforces it: each entry stays, and each version's
# predictor list must contain the one before it. A predictor can be added, never
# quietly removed, renamed, or swapped for one that scores better.
PIN_HISTORY: tuple[dict[str, Any], ...] = (
    {
        "version": "1.0.0",
        "sha256": "1fbc24905d69b7ba140df2ed1e8af2bd0a79244636c340e925b73fa999dd3dd6",
        "predictors": ["composite", "openskill"],
        "changed": "the declaration as first committed, ahead of the models it judges",
        "superseded_by": (
            "2.0.0 — P5 added the `skill` predictor and the `prior_target_persistence` "
            "secondary, both declared before the box-score prior that produces them exists. "
            "The primary test's target, baseline, statistic, unit and seed are untouched and "
            "are pinned separately as PINNED_INVARIANTS_SHA256"
        ),
    },
)


def manifest() -> dict[str, Any]:
    """The declaration, as the payload that gets stored with every run."""
    return {
        "version": VERSION,
        "what": (
            "the evaluation harness's committed declaration: one primary test, a labelled "
            "secondary set, the resampling unit per family, the seed, and the one "
            "coefficient family a forward test may read"
        ),
        "primary": PRIMARY.payload(),
        "secondary": [t.payload() for t in SECONDARY],
        "supersedes": [dict(entry) for entry in PIN_HISTORY],
        "invariants_sha256": invariants_sha256(),
        "scope_permitted_forward": SCOPE,
        "scopes_stored": list(statespace.SCOPES),
        "bootstrap_b": BOOTSTRAP_B,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "power_alpha": 0.05,
        "power_target": 0.8,
        "reproduction_tolerance": REPRODUCTION_TOL,
        "reproduces": [{"model": m, "artifact": a} for m, a in REPRODUCE],
        "resampling": {
            SERIES: "map- and series-keyed tests, clustered on the series' source_uid",
            PLAYER: (
                "player-season statistics, clustered on the player: no series contains a "
                "whole transition, so the series cannot be the unit here"
            ),
        },
        "mde": (
            "computed from the pre-flight's closed-form dependent-correlation variance at "
            "the measured sample size, baseline correlation and predictor agreement, then "
            "widened by the measured design effect of clustering"
        ),
    }


def sha256() -> str:
    """The manifest's digest, over its canonical JSON form."""
    return hashlib.sha256(
        json.dumps(manifest(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


# The digest of the declaration as committed. A manifest edited after the fact —
# a unit relaxed, a primary test swapped for one the model passes — moves this and
# fails the release gate, which is the whole point of declaring it in advance
# rather than describing it afterwards.
#
# Extending it is allowed and is not the same act: a new predictor moves this
# digest, so the old one goes into `PIN_HISTORY` and the fixed half of the
# declaration is checked against `PINNED_INVARIANTS_SHA256`, which does not move
# at all. Version 1.0.0's value is preserved there rather than overwritten here.
PINNED_SHA256 = "292ab3e47da3b72303eda88acbbe697bbaab13da233a64ba672d964bcec545ac"


def assert_forward(scope: str) -> None:
    """Guard every forward-test read of a plus-minus coefficient.

    Delegates to the estimator's own rule rather than restating it: P1 stores
    both families and raises on the wrong one, so the harness wires itself to
    that check instead of reimplementing a second copy that could drift from it.
    """
    statespace.require_filtered(scope)
