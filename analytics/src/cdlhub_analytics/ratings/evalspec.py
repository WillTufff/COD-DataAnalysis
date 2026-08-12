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
VERSION = "1.0.0"

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
    predictors=("composite", "openskill"),
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
    "forecast_brier": {
        "rapm": 0.24609,
        "rapm_prior": 0.24675,
        "rating": 0.2478,
        "rating_zshrink": 0.24875,
        "glicko": 0.25076,
        "kd": 0.25156,
    },
}


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
# a predictor added, a unit relaxed, a primary test swapped for one the model
# passes — moves this and fails the release gate, which is the whole point of
# declaring it in advance rather than describing it afterwards.
PINNED_SHA256 = "1fbc24905d69b7ba140df2ed1e8af2bd0a79244636c340e925b73fa999dd3dd6"


def assert_forward(scope: str) -> None:
    """Guard every forward-test read of a plus-minus coefficient.

    Delegates to the estimator's own rule rather than restating it: P1 stores
    both families and raises on the wrong one, so the harness wires itself to
    that check instead of reimplementing a second copy that could drift from it.
    """
    statespace.require_filtered(scope)
