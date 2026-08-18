"""player_rating: the open composite player rating. Spec: /methodology#player-rating.

Steps 1 and 2 below are the whole pipeline's front half and are shared by both
estimators. Steps 3 and 4 describe the *z-and-shrink* estimator, which this
module implements and which every rating published before `hierarchical.py`
used. It is no longer the published one: the site now shows the posterior of the
two-level model in that module, and this arm survives as the thing it is
compared against, live, on every run. `prepare` builds the shared half once so
the two can be told apart by exactly one step.

The pipeline, in order:

1. **Learn what wins maps.** For every (season × mode), each map becomes one
   observation: the difference between the two teams' stat profiles,
   standardized, regressed against which team won the map (L2 logistic, fit in
   regress.py). The coefficients are data-derived answers to "how much is a 1 SD
   edge in hill time worth vs a 1 SD edge in kills?" — per title, per mode.
   Every coefficient ships with a percentile bootstrap interval over the maps
   it was fitted on (see `bootstrap_mode_weights`), because cohorts are small
   and their features collinear.

2. **Score players with those weights.** Each player-season-mode aggregate is
   z-scored against its qualified cohort (>= MIN_MAPS maps, as in era.py) and
   dotted with the mode's learned weights, then standardized so modes are on
   a common scale.

3. **Shrink small samples.** Scores are pulled toward the league mean by
   m / (m + k) where m is maps played — empirical-Bayes partial pooling, so a
   hot 12-map season cannot outrank a great 200-map one. The prior strength k
   is the ratio of within-player to between-player score variance, estimated
   per cohort from the same maps (see `_estimate_shrinkage`), not assumed.
   `hierarchical.posterior` reaches the same expression from the model that
   implies it, which is why that module replaced this one rather than
   supplementing it.

4. **Normalize.** Season rating = 1.0 + RATING_SCALE × (maps-weighted blend
   of mode scores); the qualified cohort averages 1.0 by construction.
   rating_sd is a map-resampling bootstrap (B=200, fixed seed) — the sampling
   spread of the point estimate, which is not the same quantity as the
   posterior SD the published estimator now stores in that column.

Validation is walk-forward: within each (season × mode), each event's maps
are predicted using weights trained only on earlier events. That backtest
ships with the model, per the publishing rule.

**Feature sets are versioned data, not code.** One engine runs every version:

  1.0.0  four features per mode — kills, deaths, assists, and a single
         mode objective, all per 10 minutes.
  2.0.0  per-mode feature sets built from the metric layer's intangibles:
         first-blood and first-death rates, survival, time per life, hill
         captures, flag carry time. Denominators become per-mode too — SnD
         rates are per round, not per minute.
  2.1.0  adds the kill-feed tier (trades, man-advantage) to hardpoint and
         search-and-destroy, for the cohorts that have a feed.
  2.2.0  claims the columns both archives already populate and no version had
         named: damage, non-traded-kill share, contested hill time, accuracy,
         headshot rate and hill defends. No new fetch; the CDL-era cohorts stop
         being the shortest sets on the page.

Every feature declares the source columns it reads — including its denominator,
which is a measured column like any other — and a cohort keeps only the features
its title actually tracks (measured in maprows, never declared). So WWII
hardpoint drops hill captures, IW search-and-destroy drops first deaths and
survival, and BO4 cohorts fall back to the box-score set — each without a
hand-maintained per-title matrix.

Map time is the one denominator the archive does not carry everywhere: the CWL
years record it and the CDL box scores do not. A rate denominated in time is
therefore declared as a `Paced` chain and resolved per cohort — per 10 minutes
where the clock exists, per map where it does not — so the CDL era keeps its
Hardpoint and Control cohorts instead of losing them one zero denominator at a
time. The same chain fills a slot whose numerator one source splits and another
does not, which is what keeps Ghosts bomb plays and pre-2017 Hardpoint objective
play in the fit.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import numpy as np
import psycopg

from ..backtest import Prediction
from ..era import MIN_MAPS
from ..maprows import (
    DURATION_KEY,
    MODE_BLITZ,
    MODE_CONTROL,
    MODE_CTF,
    MODE_DOMINATION,
    MODE_HARDPOINT,
    MODE_OVERLOAD,
    MODE_SND,
    MODE_UPLINK,
    Coverage,
    MapRow,
    load_map_rows,
    record_coverage,
    reported,
    tracked,
)
from ..metrics import (
    FEED_DEATHS_SQL,
    FEED_TEAMS_SQL,
    KF_DEATHS,
    KF_THROWN,
    KF_TRADE_KILLS,
    KF_UNTRADED,
    RECON_MAPS_SQL,
    compute_map_clutch_adv,
    compute_map_trades,
)
from ..regress import FloatArray, LogisticFit, fit_logistic_l2
from ..resample import order as content_order
from ..resample import stream as resample_stream

L2 = 1.0  # ridge strength on standardized map diffs
SHRINK_FALLBACK = 15.0  # prior strength used only when a cohort's variance ratio is unusable
RATING_SCALE = 0.15  # rating = 1.0 + 0.15 × blended score (≈ league SD)
MIN_TRAIN_GAMES = 40  # walk-forward: skip events until this much history exists
BOOTSTRAP_B = 200
BOOTSTRAP_SEED = 20170812  # CWL Champs 2017 finals date; any fixed seed works

MIN_COHORT_FEATURES = 2  # a cohort with fewer surviving features is not rated


# ---------------------------------------------------------------- features

# Which rating may read a column. The two ratings are judged on different tests,
# so they cannot have the same leakage rule: a retrospective estimate is
# entitled to use the win condition, and a forecast is not. Carrying that as a
# table in a document drifts the first time a column is added, so it is a field
# on the feature and an assertion in the test suite instead.
#
#   BOTH        no closer to the outcome than any other box-score column
#   CONDITIONAL less leaky than the score itself, and not clean
#   VALUE_ONLY  is the win condition, or does not mean the same thing twice
#
# Every constructor takes it without a default. An omission is a type error at
# the call site rather than a column that quietly inherits the permissive tag.
ELIGIBLE_BOTH = "both"
ELIGIBLE_CONDITIONAL = "conditional"
ELIGIBLE_VALUE_ONLY = "value_only"
ELIGIBILITY: tuple[str, ...] = (ELIGIBLE_BOTH, ELIGIBLE_CONDITIONAL, ELIGIBLE_VALUE_ONLY)

# What a SKILL feature set may contain. CONDITIONAL is admitted here and carries
# its caveat in the artifact; VALUE_ONLY never is.
SKILL_ELIGIBLE: frozenset[str] = frozenset({ELIGIBLE_BOTH, ELIGIBLE_CONDITIONAL})


@dataclass(frozen=True)
class Feature:
    """One rate: a numerator summed over maps, over a denominator summed the same way.

    Sum-then-divide, never the mean of per-map ratios — the same discipline the
    metric layer uses, and the reason denominators travel with the numerator
    instead of being assumed to be time.
    """

    key: str
    label: str
    numerator: Callable[[MapRow], float]
    denominator: Callable[[MapRow], float]
    denom_kind: str
    sources: tuple[str, ...]
    eligibility: str
    slaying: bool = False  # part of the kills/deaths pair, for the gunfight-vs-rest reading
    needs_feed: bool = False  # only computable on a reconciled kill-feed map

    @property
    def skill_eligible(self) -> bool:
        return self.eligibility in SKILL_ELIGIBLE

    def available(self, coverage: Coverage, title: str, rows: Sequence[MapRow]) -> bool:
        """Tracked by the title, and reported by this cohort's own rows.

        Two questions, and the second is not implied by the first. A title
        records a quantity across every mode it played, so `tracked` can say
        yes on the strength of rows this cohort does not contain — Ghosts
        records first bloods on 158 rows out of 7,534, all of them in a handful
        of Search and Destroy series, and the title-wide count reads that as a
        column the mode has. `reported` asks the cohort instead.

        A feed feature is exempt from the second question, and only from it.
        The hazard `reported` exists to stop is an absent column summing into a
        rate as a zero, and `Cohort.accepts` already stops that one at the row
        rather than at the feature: a cohort using the feed reads reconciled
        maps and no others. Asking presence of every row as well would drop the
        family wherever reconciliation is partial, which is the coverage the
        row rule is there to handle.
        """
        if self.needs_feed:
            return all(tracked(coverage, title, src) for src in self.sources)
        return all(tracked(coverage, title, src) and reported(rows, src) for src in self.sources)

    def resolve(self, coverage: Coverage, title: str, rows: Sequence[MapRow]) -> Feature | None:
        return self if self.available(coverage, title, rows) else None


def skill_features(features: Sequence[Feature]) -> tuple[Feature, ...]:
    """The subset a forecast-judged rating may read.

    Nothing consumes this yet — the published composite is retrospective, and
    the forward-judged rating is a later phase. It exists now so the tag is
    enforced by a filter with a test behind it rather than by a promise, which
    is the whole reason the eligibility field is not a comment.
    """
    return tuple(f for f in features if f.skill_eligible)


@dataclass(frozen=True)
class Paced:
    """One slot in a feature set, as an ordered list of ways to fill it.

    A version names a quantity it wants from a mode. Which columns are on hand
    to express that quantity is a fact about the cohort, so the slot holds
    candidates, richest first, and the first one the cohort supports wins. The
    alternative is what this used to do: name a single form, and lose the whole
    mode wherever that form is unavailable.

    Both kinds of shortfall occur, and both are now the same mechanism.

    *A denominator the archive does not carry everywhere.* Map time is measured
    across the CWL years and absent from the CDL box scores. Round counts are
    measured from Infinite Warfare onward and absent from every pre-2017 title,
    whose wiki transcription reports a scoreboard and no round tally. Search and
    Destroy was denominated per round and nothing else, so all four pre-2017
    titles lost the mode outright — a third of that era's maps, on cohorts whose
    kills, deaths and bomb plays were all present.

    *A numerator one source splits and another does not.* Ghosts reports bomb
    plants on 79% of its Search and Destroy rows and defuses on 6%, so a feature
    reading plants + defuses drops, taking with it the only objective column
    that cohort has. Plants alone is the same quantity, worse resolved. And two
    pre-2017 titles record no hill clock at all, leaving the round score as the
    only reading of Hardpoint objective play they carry.

    Safe because a cohort is one (season × mode) and therefore resolves to
    exactly one candidate. Team differentials are standardized within a cohort
    and player aggregates z-scored within the same one, so the seam between two
    forms never falls inside a standardization; the choice reaches the fitted
    weights only through how the forms covary with the profile, which is the
    honest difference between the eras rather than an artefact.
    """

    options: tuple[Feature, ...]

    @property
    def preferred(self) -> Feature:
        return self.options[0]

    @property
    def fallback(self) -> Feature:
        """The last resort — the form that asks least of the source."""
        return self.options[-1]

    def resolve(self, coverage: Coverage, title: str, rows: Sequence[MapRow]) -> Feature | None:
        for feature in self.options:
            if feature.available(coverage, title, rows):
                return feature
        return None


# What a version's mode entry may hold: a feature, or an ordered list to choose
# between.
FeatureSpec = Feature | Paced


def _col(*keys: str) -> Callable[[MapRow], float]:
    return lambda row: sum(row.get(k) for k in keys)


def _net(positive: str, negative: str) -> Callable[[MapRow], float]:
    return lambda row: row.get(positive) - row.get(negative)


def _per10(row: MapRow) -> float:
    return row.duration_s / 600.0


def _per_map(_row: MapRow) -> float:
    return 1.0


def _time(key: str, label: str, *sources: str, eligibility: str, slaying: bool = False) -> Feature:
    """A per-10-minute rate.

    The denominator is a source like any other: map time is recorded for some
    titles and not others, so a feature that reads it is available only where it
    is tracked. Leaving DURATION_KEY undeclared let a cohort form on numerators
    alone and then empty itself one zero denominator at a time.
    """
    cols = sources or (key,)
    return Feature(
        key=f"{key}_p10",
        label=label,
        numerator=_col(*cols),
        denominator=_per10,
        denom_kind="minutes",
        sources=(*cols, DURATION_KEY),
        eligibility=eligibility,
        slaying=slaying,
    )


def _per_round(
    key: str,
    label: str,
    numerator: Callable[[MapRow], float],
    *sources: str,
    eligibility: str,
    slaying: bool = False,
) -> Feature:
    return Feature(
        key=key,
        label=label,
        numerator=numerator,
        denominator=_col("snd_rounds"),
        denom_kind="rounds",
        sources=(*sources, "snd_rounds"),
        eligibility=eligibility,
        slaying=slaying,
    )


def _per_ctrl_round(
    key: str, label: str, numerator: Callable[[MapRow], float], *sources: str, eligibility: str
) -> Feature:
    return Feature(
        key=key,
        label=label,
        numerator=numerator,
        denominator=_col("ctrl_rounds"),
        denom_kind="rounds",
        sources=(*sources, "ctrl_rounds"),
        eligibility=eligibility,
    )


def _per_map_feature(
    key: str, label: str, *sources: str, eligibility: str, slaying: bool = False
) -> Feature:
    cols = sources or (key,)
    return Feature(
        key=f"{key}_pm",
        label=label,
        numerator=_col(*cols),
        denominator=_per_map,
        denom_kind="maps",
        sources=cols,
        eligibility=eligibility,
        slaying=slaying,
    )


# The round score, as an objective column of last resort.
#
# Every pre-2017 title transcribes a scoreboard, and two of them record nothing
# else about objective play: Black Ops 2 and Advanced Warfare carry no hill
# clock, so their Hardpoint cohorts had the slaying pair and nothing more. The
# score is a title's own weighting of objective work and kills, which makes it
# a worse reading of the objective than a real column and a better one than
# none.
#
# Measured before it was admitted, at team-differential resolution. Against
# kills it runs 0.40 to 0.80 across the eleven pre-2017 cohorts, well under the
# 0.82 to 0.94 that put damage in the slaying pair rather than beside it, and
# against each mode's own objective column 0.09 to 0.54. So it is neither a
# duplicate of the gunfight nor a substitute for a real objective count: it is a
# third thing, and it is what those cohorts have.
#
# Value-only, and the reason is the eligibility rule rather than caution. Each
# title scores by its own formula, and Blitz scores differently from Domination
# inside one title, so the column does not mean the same thing twice and cannot
# carry across a seam into a forecast.
SCORE_PM = _per_map_feature(
    "round_score", "Round score per map", "player_score", eligibility=ELIGIBLE_VALUE_ONLY
)


def _paced(
    key: str, timed: str, per_map: str, *sources: str, eligibility: str, slaying: bool = False
) -> Paced:
    """Per 10 minutes where the title records map time, per map where it does not."""
    return Paced(
        (
            _time(key, timed, *sources, eligibility=eligibility, slaying=slaying),
            _per_map_feature(key, per_map, *sources, eligibility=eligibility, slaying=slaying),
        )
    )


def _then(*specs: Paced | Feature) -> Paced:
    """One slot, filled by the first form the cohort supports, in this order."""
    options: list[Feature] = []
    for spec in specs:
        options.extend(spec.options if isinstance(spec, Paced) else (spec,))
    return Paced(tuple(options))


def _rounded(
    key: str,
    label: str,
    per_map_key: str,
    per_map_label: str,
    numerator: Callable[[MapRow], float],
    *sources: str,
    eligibility: str,
    slaying: bool = False,
) -> Paced:
    """Per round where the title counts rounds, per map where it does not.

    The per-round key is unchanged from when it was the only form, so a cohort
    that has rounds fits and publishes exactly what it did before.
    """
    return Paced(
        (
            _per_round(key, label, numerator, *sources, eligibility=eligibility, slaying=slaying),
            Feature(
                key=per_map_key,
                label=per_map_label,
                numerator=numerator,
                denominator=_per_map,
                denom_kind="maps",
                sources=sources,
                eligibility=eligibility,
                slaying=slaying,
            ),
        )
    )


KILLS = _paced(
    "kills", "Kills per 10 min", "Kills per map", eligibility=ELIGIBLE_BOTH, slaying=True
)
DEATHS = _paced(
    "deaths", "Deaths per 10 min", "Deaths per map", eligibility=ELIGIBLE_BOTH, slaying=True
)
ASSISTS = _paced("assists", "Assists per 10 min", "Assists per map", eligibility=ELIGIBLE_BOTH)

# --- 1.0.0: one objective column per mode, everything per unit of pace ---

_OBJ_V1 = {
    MODE_HARDPOINT: _paced(
        "obj",
        "Hill time per 10 min",
        "Hill time per map",
        "hill_time",
        eligibility=ELIGIBLE_VALUE_ONLY,
    ),
    MODE_SND: _paced(
        "obj",
        "SnD objective per 10 min",
        "SnD objective per map",
        "first_bloods",
        "plants",
        "defuses",
        eligibility=ELIGIBLE_VALUE_ONLY,
    ),
    MODE_CONTROL: _paced(
        "obj",
        "Captures per 10 min",
        "Captures per map",
        "ctrl_captures",
        eligibility=ELIGIBLE_VALUE_ONLY,
    ),
    MODE_CTF: _paced(
        "obj",
        "Flag plays per 10 min",
        "Flag plays per map",
        "ctf_captures",
        "ctf_returns",
        eligibility=ELIGIBLE_VALUE_ONLY,
    ),
    MODE_UPLINK: _paced(
        "obj",
        "Uplink points per 10 min",
        "Uplink points per map",
        "uplink_points",
        eligibility=ELIGIBLE_VALUE_ONLY,
    ),
    MODE_DOMINATION: _paced(
        "obj",
        "Flag captures per 10 min",
        "Flag captures per map",
        "captures",
        eligibility=ELIGIBLE_VALUE_ONLY,
    ),
    MODE_BLITZ: _paced(
        "obj",
        "Blitz captures per 10 min",
        "Blitz captures per map",
        "blitz_caps",
        eligibility=ELIGIBLE_VALUE_ONLY,
    ),
}

# Overload is the one mode in the archive with no objective column of its own.
# Black Ops 7 reports kills, deaths, assists and damage for it and nothing else,
# so it is named here rather than in `_OBJ_V1` — a mode with no objective is
# still a mode, and leaving it out of the vocabulary is how it went unrated for
# a whole season.
_NO_OBJECTIVE: tuple[str, ...] = (MODE_OVERLOAD,)

# One objective slot per mode, and the round score is the last thing it tries.
# It changes nothing for a cohort whose own objective column is there, and it is
# the whole of what Black Ops 2 and Advanced Warfare Hardpoint have.
FEATURES_V1: dict[str, tuple[FeatureSpec, ...]] = {
    **{mode: (KILLS, DEATHS, ASSISTS, _then(obj, SCORE_PM)) for mode, obj in _OBJ_V1.items()},
    **{mode: (KILLS, DEATHS, ASSISTS, SCORE_PM) for mode in _NO_OBJECTIVE},
}

# --- 2.0.0: per-mode intangibles, per-mode denominators ---

TIME_PER_LIFE = Feature(
    key="time_per_life_s",
    label="Seconds alive per life",
    numerator=_col("time_alive_s"),
    denominator=_col("num_lives"),
    denom_kind="lives",
    sources=("time_alive_s", "num_lives"),
    eligibility=ELIGIBLE_BOTH,
)

FEATURES_V2: dict[str, tuple[FeatureSpec, ...]] = {
    MODE_HARDPOINT: (
        KILLS,
        DEATHS,
        # Occupancy is the mode's score, so this is the objective slot. Two
        # pre-2017 titles record no hill clock on any row, and for those the
        # round score is the only reading of objective play in the box score.
        _then(
            _paced(
                "hill_time",
                "Hill time per 10 min",
                "Hill seconds per map",
                eligibility=ELIGIBLE_VALUE_ONLY,
            ),
            SCORE_PM,
        ),
        _paced(
            "hill_captures",
            "Hill captures per 10 min",
            "Hill captures per map",
            eligibility=ELIGIBLE_CONDITIONAL,
        ),
        TIME_PER_LIFE,
    ),
    MODE_SND: (
        _rounded(
            "snd_kpr",
            "Kills per round",
            "snd_kills_pm",
            "Kills per map",
            _col("kills"),
            "kills",
            eligibility=ELIGIBLE_BOTH,
            slaying=True,
        ),
        _rounded(
            "snd_dpr",
            "Deaths per round",
            "snd_deaths_pm",
            "Deaths per map",
            _col("deaths"),
            "deaths",
            eligibility=ELIGIBLE_BOTH,
            slaying=True,
        ),
        _rounded(
            "snd_fb_rate",
            "First bloods per round",
            "snd_fb_pm",
            "First bloods per map",
            _col("first_bloods"),
            "first_bloods",
            eligibility=ELIGIBLE_BOTH,
        ),
        _rounded(
            "snd_fd_rate",
            "First deaths per round",
            "snd_fd_pm",
            "First deaths per map",
            _col("snd_firstdeaths"),
            "snd_firstdeaths",
            eligibility=ELIGIBLE_BOTH,
        ),
        _rounded(
            "snd_survival_rate",
            "Survivals per round",
            "snd_survives_pm",
            "Survivals per map",
            _col("snd_survives"),
            "snd_survives",
            eligibility=ELIGIBLE_CONDITIONAL,
        ),
        # Bomb plays, in whichever of the two forms the source splits them
        # into. Ghosts reports plants on 79% of its rows and defuses on 6%, so
        # the paired form drops and the plant on its own is what that cohort
        # carries — the same quantity, worse resolved, and the only objective
        # column in a 2,445-row cohort that would otherwise rate on the
        # gunfight alone.
        _then(
            _rounded(
                "snd_bomb_pr",
                "Plants + defuses per round",
                "snd_bomb_pm",
                "Plants + defuses per map",
                _col("plants", "defuses"),
                "plants",
                "defuses",
                eligibility=ELIGIBLE_VALUE_ONLY,
            ),
            _rounded(
                "snd_plants_pr",
                "Plants per round",
                "snd_plants_pm",
                "Plants per map",
                _col("plants"),
                "plants",
                eligibility=ELIGIBLE_VALUE_ONLY,
            ),
        ),
    ),
    MODE_CONTROL: (
        KILLS,
        DEATHS,
        _per_map_feature(
            "ctrl_caps", "Captures per map", "ctrl_captures", eligibility=ELIGIBLE_VALUE_ONLY
        ),
        # One reading of Control beyond the gunfight, and which one exists is a
        # fact about the source. The CWL archive counts first bloods and first
        # deaths on the round; the CDL box score counts neither, and neither the
        # captures above nor any round tally, so 2022-2025 Control fitted on the
        # slaying pair alone — fewer columns than 1.0.0 gave it, because the
        # per-mode set that replaced the box-score set assumed the per-mode
        # columns. Assists is what those cohorts carry instead. Black Ops Cold
        # War carries none of the three, and 2021 stays on the pair: a fact
        # about that season's box score rather than a slot left unfilled.
        _then(
            _per_ctrl_round(
                "ctrl_fb_net_pr",
                "First-blood net per round",
                _net("ctrl_firstbloods", "ctrl_firstdeaths"),
                "ctrl_firstbloods",
                "ctrl_firstdeaths",
                eligibility=ELIGIBLE_BOTH,
            ),
            ASSISTS,
        ),
    ),
    MODE_CTF: (
        KILLS,
        DEATHS,
        _per_map_feature(
            "ctf_caps", "Captures per map", "ctf_captures", eligibility=ELIGIBLE_VALUE_ONLY
        ),
        _per_map_feature(
            "ctf_returns", "Returns per map", "ctf_returns", eligibility=ELIGIBLE_CONDITIONAL
        ),
        _per_map_feature(
            "ctf_carry_time_s",
            "Flag carry seconds per map",
            "ctf_flag_carry_time_s",
            eligibility=ELIGIBLE_CONDITIONAL,
        ),
    ),
    MODE_UPLINK: (
        KILLS,
        DEATHS,
        _per_map_feature("uplink_points", "Uplink points per map", eligibility=ELIGIBLE_VALUE_ONLY),
    ),
    # The three modes below keep the 1.0.0 shape, assists included, and the
    # reason is the archive rather than a preference. 2.0.0 spends its extra
    # room on per-mode intangibles — hill captures, first-blood net, flag carry
    # time — and no source records an intangible for any of these three. Ghosts
    # reports a scoreboard, and Cito reports four columns for Overload. So the
    # slaying pair plus one objective count is the whole of what exists, and
    # assists is the one further column two of the three carry.
    MODE_DOMINATION: (
        KILLS,
        DEATHS,
        ASSISTS,
        _per_map_feature(
            "dom_caps", "Flag captures per map", "captures", eligibility=ELIGIBLE_VALUE_ONLY
        ),
    ),
    MODE_BLITZ: (
        KILLS,
        DEATHS,
        ASSISTS,
        _per_map_feature("blitz_caps", "Blitz captures per map", eligibility=ELIGIBLE_VALUE_ONLY),
    ),
    MODE_OVERLOAD: (KILLS, DEATHS, ASSISTS),
}

# --- 2.1.0: the kill-feed tier, on the modes where a trade means something ---
#
# Only quantities read off the death timeline are eligible. The man-advantage
# and clutch families are deliberately excluded: "rounds won while up a man"
# and "clutches won" contain the round outcome, and round wins are what decide
# maps, so regressing map wins on them would be close to circular and would
# flatter the backtest. Thrown deaths qualify because they are counted from
# alive-counts alone — this module computes them with an empty winner map, so
# outcome information cannot reach the feature even by accident.


def _feed(
    key: str,
    label: str,
    numerator: Callable[[MapRow], float],
    denominator: Callable[[MapRow], float],
    denom_kind: str,
    *sources: str,
    eligibility: str,
) -> Feature:
    return Feature(
        key=key,
        label=label,
        numerator=numerator,
        denominator=denominator,
        denom_kind=denom_kind,
        sources=sources,
        eligibility=eligibility,
        needs_feed=True,
    )


UNTRADED_DEATH_RATE = _feed(
    "untraded_death_rate",
    "Share of deaths nobody traded back",
    _col(KF_UNTRADED),
    _col(KF_DEATHS),
    "feed deaths",
    KF_UNTRADED,
    KF_DEATHS,
    eligibility=ELIGIBLE_BOTH,
)
TRADE_KILLS_P10 = _feed(
    "trade_kills_p10",
    "Trade kills per 10 min",
    _col(KF_TRADE_KILLS),
    _per10,
    "minutes",
    KF_TRADE_KILLS,
    DURATION_KEY,
    eligibility=ELIGIBLE_BOTH,
)
TRADE_KILLS_PR = _feed(
    "trade_kills_pr",
    "Trade kills per round",
    _col(KF_TRADE_KILLS),
    _col("snd_rounds"),
    "rounds",
    KF_TRADE_KILLS,
    "snd_rounds",
    eligibility=ELIGIBLE_BOTH,
)
THROWN_DEATHS_PR = _feed(
    "thrown_deaths_pr",
    "Deaths surrendering a man advantage, per round",
    _col(KF_THROWN),
    _col("snd_rounds"),
    "rounds",
    KF_THROWN,
    "snd_rounds",
    eligibility=ELIGIBLE_BOTH,
)

FEATURES_V21: dict[str, tuple[FeatureSpec, ...]] = {
    **FEATURES_V2,
    MODE_HARDPOINT: (*FEATURES_V2[MODE_HARDPOINT], UNTRADED_DEATH_RATE, TRADE_KILLS_P10),
    MODE_SND: (*FEATURES_V2[MODE_SND], UNTRADED_DEATH_RATE, TRADE_KILLS_PR, THROWN_DEATHS_PR),
}

# --- 2.2.0: the columns both archives populate and no version had claimed ---
#
# Nothing new is fetched here. Each column below was already loaded, already
# coverage-measured, and simply never named by a feature set. Which titles
# actually carry them is measured as always, so the seasons named in the
# comments are what the data said on the run that adopted them, not a matrix
# anyone maintains.
#
# Three of the five columns an earlier plan listed for this version are not
# here, and the reasons are measurements rather than judgement:
#
#   ctl_attack_rounds / ctl_defense_rounds  Not a player statistic. One team's
#     attack rounds are the other's defence rounds on 974 of 974 Control maps,
#     and 35% of maps tie exactly, so the differential encodes which side each
#     team started on and nothing else. It reaches the fit already, as the
#     `ctrl_rounds` denominator maprows derives from it; as a feature it would
#     put the coin toss in the rating. It belongs with the match-context terms.
#
#   damage is here, but in the slaying pair. At team-differential resolution it
#     correlates 0.82-0.94 with kills across every CDL cohort, and 0.68-0.78 on
#     Black Ops 4. It is a better-resolved reading of the gunfight, not a
#     quantity beyond it, and counting it as "the rest" would inflate the
#     beyond-the-gunfight ratio with a column that is mostly kills.
#
#   non_traded_kills enters as a *share of the player's own kills*, never as a
#     count. Per map it correlates 0.90-0.96 with kills — a duplicate column
#     that ridge would split weight with. As a rate it runs 0.13-0.39 in Search
#     & Destroy and 0.59-0.80 elsewhere, and it is the CDL-era source counting
#     directly what the 2017-2018 kill feed reconstructs.

DAMAGE = _paced(
    "damage",
    "Damage per 10 min",
    "Damage per map",
    eligibility=ELIGIBLE_BOTH,
    slaying=True,
)
DAMAGE_PR = _rounded(
    "snd_damage_pr",
    "Damage per round",
    "snd_damage_pm",
    "Damage per map",
    _col("damage"),
    "damage",
    eligibility=ELIGIBLE_BOTH,
    slaying=True,
)

# Trade economy without a kill feed: the share of a player's kills the opponent
# did not answer inside the source's trade window. 2022 onward — the column is
# declared and all-zero for 2020 and 2021.
NON_TRADED_KILL_RATE = Feature(
    key="non_traded_kill_rate",
    label="Share of kills nobody traded back",
    numerator=_col("non_traded_kills"),
    denominator=_col("kills"),
    denom_kind="kills",
    sources=("non_traded_kills", "kills"),
    eligibility=ELIGIBLE_BOTH,
)

# Occupancy while the hill was contested, rather than occupancy. Strictly less
# of the scoreboard than `hill_time` — the score is time on the hill, not time
# on a contested hill — and measured against it, a different axis rather than a
# sharper one: the two differentials correlate 0.13-0.30. Still not clean, so
# it is admitted to a forecast with its caveat rather than freely. 2022-2025;
# declared and all-zero for 2020, 2021 and 2026.
CONTESTED_HILL_TIME = _paced(
    "contested_hill_time",
    "Contested hill seconds per 10 min",
    "Contested hill seconds per map",
    eligibility=ELIGIBLE_CONDITIONAL,
)

# Accuracy, and how much of it converted. Both are CWL-only: Black Ops 4
# declares shots and hits on five rows out of 19,120, which is under the floor.
ACCURACY = Feature(
    key="accuracy",
    label="Share of shots that hit",
    numerator=_col("hits"),
    denominator=_col("shots"),
    denom_kind="shots",
    sources=("hits", "shots"),
    eligibility=ELIGIBLE_BOTH,
)

# Retrospective only, and on the portability gate rather than the leakage one.
# The sign rule puts it at 0.58 at best — barely off a coin flip — and its
# direction does not hold: negative on WWII and Black Ops 4, and disagreeing with
# itself across Infinite Warfare's three cohorts. A column that weak has nothing
# to carry across a title seam, so a cohort may fit it and a forecast may not.
HEADSHOT_RATE = Feature(
    key="headshot_rate",
    label="Headshots per kill",
    numerator=_col("headshots"),
    denominator=_col("kills"),
    denom_kind="kills",
    sources=("headshots", "kills"),
    eligibility=ELIGIBLE_VALUE_ONLY,
)

# Kills defending a hill the team held. Retrospective only, and the reason is
# the portability gate rather than leakage: its correlation with hill time is
# +0.72 on Infinite Warfare, +0.44 on WWII and -0.30 on Black Ops 4. A column
# whose sign against the same quantity flips between titles is not one quantity,
# so a cohort may fit it and a forecast may not carry it across a title seam.
HILL_DEFENDS = _paced(
    "hill_defends",
    "Hill defends per 10 min",
    "Hill defends per map",
    eligibility=ELIGIBLE_VALUE_ONLY,
)

FEATURES_V22: dict[str, tuple[FeatureSpec, ...]] = {
    **FEATURES_V21,
    MODE_HARDPOINT: (
        *FEATURES_V21[MODE_HARDPOINT],
        DAMAGE,
        NON_TRADED_KILL_RATE,
        CONTESTED_HILL_TIME,
        HILL_DEFENDS,
        ACCURACY,
        HEADSHOT_RATE,
    ),
    MODE_SND: (
        *FEATURES_V21[MODE_SND],
        DAMAGE_PR,
        NON_TRADED_KILL_RATE,
        ACCURACY,
        HEADSHOT_RATE,
    ),
    MODE_CONTROL: (
        *FEATURES_V21[MODE_CONTROL],
        DAMAGE,
        NON_TRADED_KILL_RATE,
        HEADSHOT_RATE,
    ),
    MODE_CTF: (*FEATURES_V21[MODE_CTF], ACCURACY, HEADSHOT_RATE),
    MODE_UPLINK: (*FEATURES_V21[MODE_UPLINK], ACCURACY, HEADSHOT_RATE),
    # Damage and the non-traded share are Cito columns, so they reach the three
    # CDL-era cohorts here — 2020 Domination and 2026 Overload — and resolve
    # away for Ghosts, which records neither. Overload needs them most: without
    # this version it rates on the slaying pair and assists alone.
    MODE_DOMINATION: (*FEATURES_V21[MODE_DOMINATION], DAMAGE, NON_TRADED_KILL_RATE),
    MODE_BLITZ: (*FEATURES_V21[MODE_BLITZ], DAMAGE, NON_TRADED_KILL_RATE),
    MODE_OVERLOAD: (*FEATURES_V21[MODE_OVERLOAD], DAMAGE, NON_TRADED_KILL_RATE),
}

VERSIONS: dict[str, dict[str, tuple[FeatureSpec, ...]]] = {
    "1.0.0": FEATURES_V1,
    "2.0.0": FEATURES_V2,
    "2.1.0": FEATURES_V21,
    "2.2.0": FEATURES_V22,
}

# Every version is fitted and backtested on each run; PUBLISHED is the one the
# site shows. It is a deliberate choice recorded here rather than "whichever ran
# last": run order must never decide what the leaderboard means. The comparison
# artifact (ratings/comparison.py) is the evidence for the choice.
ALL_VERSIONS: tuple[str, ...] = ("1.0.0", "2.0.0", "2.1.0", "2.2.0")
# 2.2.0 is fitted, backtested and compared, and is deliberately not published
# yet: the site pins this string in web/lib/analytics.ts, so promoting a version
# is a lockstep change across two languages and belongs with the publishing
# work, not with the phase that recovered the columns.
PUBLISHED_VERSION = "2.1.0"
DEFAULT_VERSION = PUBLISHED_VERSION

# Which estimator turns a season profile into a rating. Not a feature-set
# version: it applies identically to all three, so it is recorded in the run's
# params rather than in the version string. "z_shrink" is the estimator this
# module implements and every rating published before it; "hierarchical" is the
# posterior in hierarchical.py, and is what the site shows.
ESTIMATORS: tuple[str, ...] = ("hierarchical", "z_shrink")
PUBLISHED_ESTIMATOR = "hierarchical"


def resolve_features(
    version: str, mode_slug: str, coverage: Coverage, title: str, rows: Sequence[MapRow]
) -> tuple[Feature, ...]:
    """The feature set for one cohort: those whose every source column this
    title tracks and this cohort's own rows report. Availability is measured,
    never declared.

    A `Paced` entry contributes the first of its candidate forms the cohort
    supports, so a title with no map time, one with no round count and one that
    splits a numerator two ways each keep the quantity rather than losing the
    feature and, with enough of them, the mode."""
    spec = VERSIONS[version].get(mode_slug, ())
    resolved = (f.resolve(coverage, title, rows) for f in spec)
    return tuple(f for f in resolved if f is not None)


# ------------------------------------------------------------- kill feed


FEED_MAP = "kf_map"  # marker: this player-map reconciled against the kill feed

FEED_KEYS: tuple[str, ...] = (KF_DEATHS, KF_UNTRADED, KF_TRADE_KILLS, KF_THROWN, FEED_MAP)


def attach_kill_feed(
    conn: psycopg.Connection[tuple[object, ...]], rows: Sequence[MapRow], coverage: Coverage
) -> None:
    """Fold per-map trade counts onto the reconciled player-maps, in place.

    Only reconciled player-maps get feed columns, so coverage stays zero for a
    title with no feed and the resolver drops the feed features for it — the
    same mechanism the metric layer uses, with no title list anywhere.

    Thrown deaths come from compute_map_clutch_adv called with an empty
    winner map: alive-counts still produce them, while the advantage and clutch
    outcomes it would otherwise return stay empty. Round results cannot leak
    into a map-outcome feature through a value that was never computed.
    """
    by_map = {(r.game_id, r.player_id): r for r in rows}

    team_of: dict[int, dict[int, int]] = defaultdict(dict)
    for row in conn.execute(FEED_TEAMS_SQL):
        team_of[cast(int, row[0])][cast(int, row[1])] = cast(int, row[2])

    deaths_by_game: dict[int, list[tuple[int, int | None, int, int, int | None]]] = defaultdict(
        list
    )
    for row in conn.execute(FEED_DEATHS_SQL):
        deaths_by_game[cast(int, row[0])].append(
            (
                cast(int, row[1]),
                cast("int | None", row[2]),
                cast(int, row[3]),
                cast(int, row[4]),
                cast("int | None", row[5]),
            )
        )

    recon: dict[int, list[tuple[int, str, str]]] = defaultdict(list)
    for row in conn.execute(RECON_MAPS_SQL):
        recon[cast(int, row[0])].append((cast(int, row[1]), cast(str, row[4]), cast(str, row[5])))

    for game_id, players in recon.items():
        deaths = deaths_by_game.get(game_id, [])
        trades = compute_map_trades(deaths, team_of[game_id])

        thrown: dict[int, dict[str, float]] = {}
        if players and players[0][1] == MODE_SND:
            roster_by_team: dict[int, set[int]] = defaultdict(set)
            for pid, tid in team_of[game_id].items():
                roster_by_team[tid].add(pid)
            deaths_by_round: dict[int, list[tuple[int, int | None]]] = defaultdict(list)
            for rnd, _t, _seq, victim, killer in deaths:
                deaths_by_round[rnd].append((victim, killer))
            thrown = compute_map_clutch_adv(deaths_by_round, roster_by_team, team_of[game_id], {})

        for player_id, _mode_slug, title in players:
            map_row = by_map.get((game_id, player_id))
            if map_row is None:
                continue
            counts = trades.get(player_id, {})
            values = {
                KF_DEATHS: counts.get(KF_DEATHS, 0.0),
                KF_UNTRADED: counts.get(KF_UNTRADED, 0.0),
                KF_TRADE_KILLS: counts.get(KF_TRADE_KILLS, 0.0),
                KF_THROWN: thrown.get(player_id, {}).get(KF_THROWN, 0.0),
                FEED_MAP: 1.0,
            }
            map_row.values.update(values)
            for key in FEED_KEYS:
                record_coverage(coverage, title, key, values[key])


# ------------------------------------------------------------------ loading


@dataclass
class Cohort:
    """One (season × mode) slice and the feature set its title supports."""

    season_id: int
    mode_id: int
    mode_slug: str
    title: str
    features: tuple[Feature, ...]

    @property
    def key(self) -> tuple[int, int]:
        return (self.season_id, self.mode_id)

    @property
    def feature_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.features)

    @property
    def needs_feed(self) -> bool:
        return any(f.needs_feed for f in self.features)

    def accepts(self, row: MapRow) -> bool:
        """A cohort using feed features can only read maps that have a feed.
        Absent feed columns mean 'not reconciled', not 'zero', so those maps
        leave the cohort rather than being counted as clean ones."""
        return usable(row) and (not self.needs_feed or FEED_MAP in row.values)


def usable(row: MapRow) -> bool:
    """The rating needs a decided map and a real slaying line."""
    return row.winner_team_id is not None and "kills" in row.values and "deaths" in row.values


def build_cohorts(
    rows: Sequence[MapRow], coverage: Coverage, version: str
) -> dict[tuple[int, int], Cohort]:
    """One cohort per (season × mode), resolved against its own rows.

    The grouping happens before resolution rather than after, because a feature
    now asks two questions and the second one only the cohort can answer.
    """
    grouped: dict[tuple[int, int], list[MapRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.season_id, row.mode_id)].append(row)

    out: dict[tuple[int, int], Cohort] = {}
    for key, cohort_rows in grouped.items():
        first = cohort_rows[0]
        out[key] = Cohort(
            season_id=first.season_id,
            mode_id=first.mode_id,
            mode_slug=first.mode_slug,
            title=first.title,
            features=resolve_features(version, first.mode_slug, coverage, first.title, cohort_rows),
        )
    return {k: c for k, c in out.items() if len(c.features) >= MIN_COHORT_FEATURES}


def _profile(rows: Sequence[MapRow], features: Sequence[Feature]) -> FloatArray | None:
    """Sum numerators and denominators across maps, then divide once."""
    out = np.zeros(len(features))
    for j, f in enumerate(features):
        denom = sum(f.denominator(r) for r in rows)
        if denom <= 0:
            return None
        out[j] = sum(f.numerator(r) for r in rows) / denom
    return out


# ---------------------------------------------------------------- map games


@dataclass
class GameDiff:
    """One map as a single observation: team A minus team B."""

    game_id: int
    event_id: int
    when: date
    diff: FloatArray
    a_won: bool


def build_game_diffs(
    rows: Sequence[MapRow], cohorts: dict[tuple[int, int], Cohort]
) -> dict[tuple[int, int], list[GameDiff]]:
    """Per cohort: one differential observation per map, in played order.

    Maps where either team cannot form the full profile (a zero denominator —
    an untimed round count, a player-map with no lives) are dropped rather than
    imputed; a half-measured map is not an observation.
    """
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        cohort = cohorts.get((r.season_id, r.mode_id))
        if cohort is not None and cohort.accepts(r):
            per_game[r.game_id].append(r)

    out: dict[tuple[int, int], list[GameDiff]] = defaultdict(list)
    for game_id in sorted(per_game, key=lambda g: (per_game[g][0].played_at, g)):
        members = per_game[game_id]
        teams = sorted({m.team_id for m in members})
        if len(teams) != 2:
            continue
        first = members[0]
        cohort = cohorts[(first.season_id, first.mode_id)]
        a, b = teams
        prof_a = _profile([m for m in members if m.team_id == a], cohort.features)
        prof_b = _profile([m for m in members if m.team_id == b], cohort.features)
        if prof_a is None or prof_b is None:
            continue
        out[cohort.key].append(
            GameDiff(
                game_id=game_id,
                event_id=first.event_id,
                when=first.played_at,
                diff=np.asarray(prof_a - prof_b),
                a_won=next(m.won is True for m in members if m.team_id == a),
            )
        )
    return dict(out)


@dataclass
class ModeFit:
    n_games: int
    mu: FloatArray  # standardization of diffs
    sd: FloatArray
    fit: LogisticFit

    @property
    def weights(self) -> FloatArray:
        return self.fit.weights


def _standardize(diffs: Sequence[GameDiff]) -> tuple[FloatArray, FloatArray, FloatArray]:
    x = np.array([g.diff for g in diffs])
    mu = x.mean(axis=0)
    sd = x.std(axis=0, ddof=1)
    sd[sd == 0.0] = 1.0
    return (x - mu) / sd, mu, sd


def fit_mode_weights(
    diffs_by_cohort: dict[tuple[int, int], list[GameDiff]],
) -> dict[tuple[int, int], ModeFit]:
    out: dict[tuple[int, int], ModeFit] = {}
    for key, diffs in diffs_by_cohort.items():
        if len(diffs) < MIN_TRAIN_GAMES:
            continue
        x, mu, sd = _standardize(diffs)
        y = np.array([1.0 if g.a_won else 0.0 for g in diffs])
        out[key] = ModeFit(n_games=len(diffs), mu=mu, sd=sd, fit=fit_logistic_l2(x, y, l2=L2))
    return out


def rest_vs_slay(weights: Mapping[str, float], slaying: Sequence[str]) -> float | None:
    """Mean |weight| beyond the gunfight over mean |weight| in the slaying pair.

    The one comparison the model itself delimits: which features are the
    kills/deaths pair is a property of the cohort, and everything else is the
    remainder, whatever mix of objective, survival and trade economy that is for
    the title. Team kills mirror opponent deaths, so the pair is near-collinear
    and the ridge splits its shared weight — hence read jointly, as a mean.

    None when the cohort has nothing on one side of the boundary, or when the
    slaying pair carries no weight at all and the ratio would not be finite.
    insights.what_wins and web/lib/analytics.ts:getModeWeights compute the same
    number; all three must agree, because they publish the same claim.
    """
    others = [k for k in weights if k not in slaying]
    if not slaying or not others:
        return None
    slay = sum(abs(float(weights[k])) for k in slaying) / len(slaying)
    if slay <= 0.0:
        return None
    return sum(abs(float(weights[k])) for k in others) / len(others) / slay


@dataclass
class WeightCI:
    """Percentile bootstrap intervals for one cohort's learned weights."""

    weights: dict[str, tuple[float, float]]  # feature key -> (lo, hi)
    ratio: tuple[float, float] | None  # interval on rest_vs_slay
    draws: int  # usable draws, of BOOTSTRAP_B


def bootstrap_mode_weights(
    diffs_by_cohort: dict[tuple[int, int], list[GameDiff]],
    cohorts: dict[tuple[int, int], Cohort],
    b: int = BOOTSTRAP_B,
) -> dict[tuple[int, int], WeightCI]:
    """How much of the learned weights is signal, by resampling the maps.

    Cohorts here span a few hundred to a thousand maps, and the features inside
    one are collinear by construction, so the coefficient ratios the site argues
    from carry wide and very unequal error bars. Nothing reported them until now.

    Each draw refits the cohort end to end — resample maps with replacement,
    restandardize, refit the ridge — so the interval carries the standardization's
    own sampling error rather than conditioning on it. The ratio is recomputed per
    draw instead of being propagated from the per-weight intervals, which would
    ignore that numerator and denominator move together.

    Draws landing on a single winner are dropped: that fit is degenerate rather
    than uncertain. The same map-resampling scheme and seed as `rating_sd`.
    """
    out: dict[tuple[int, int], WeightCI] = {}
    for key, diffs in sorted(diffs_by_cohort.items()):
        cohort = cohorts.get(key)
        if cohort is None or len(diffs) < MIN_TRAIN_GAMES:
            continue
        feature_keys = list(cohort.feature_keys)
        slaying = [f.key for f in cohort.features if f.slaying]
        x_all = np.array([g.diff for g in diffs])
        y_all = np.array([1.0 if g.a_won else 0.0 for g in diffs])
        # Two things here were being decided by keys rather than by data. The
        # maps arrived in the order the loader emitted them, and one generator
        # was advanced through cohorts ordered by (season_id, mode_id) — so a
        # cohort's interval depended on how many cohorts had been fitted before
        # it, and a renumbered season moved intervals for cohorts whose maps
        # never changed. Rows are ordered by their own contents, and each cohort
        # draws from a stream seeded by its own.
        rows = content_order([*x_all.T, y_all])
        x_all, y_all = x_all[rows], y_all[rows]
        rng = resample_stream(BOOTSTRAP_SEED, x_all, y_all)
        n = len(diffs)

        drawn: list[FloatArray] = []
        ratios: list[float] = []
        for row in rng.integers(0, n, size=(b, n)):
            y = y_all[row]
            if y.min() == y.max():
                continue
            x = x_all[row]
            mu, sd = x.mean(axis=0), x.std(axis=0, ddof=1)
            sd[sd == 0.0] = 1.0
            w = fit_logistic_l2(np.asarray((x - mu) / sd), np.asarray(y), l2=L2).weights
            drawn.append(w)
            r = rest_vs_slay(dict(zip(feature_keys, w, strict=True)), slaying)
            if r is not None:
                ratios.append(r)
        if len(drawn) < 2:
            continue

        lo, hi = np.percentile(np.array(drawn), [2.5, 97.5], axis=0)
        ratio_ci = None
        if len(ratios) >= 2:
            rlo, rhi = np.percentile(ratios, [2.5, 97.5])
            ratio_ci = (float(rlo), float(rhi))
        out[key] = WeightCI(
            weights={k: (float(lo[i]), float(hi[i])) for i, k in enumerate(feature_keys)},
            ratio=ratio_ci,
            draws=len(drawn),
        )
    return out


@dataclass
class MapPrediction:
    """A walk-forward prediction with the map and cohort it came from, so two
    feature sets can be scored on exactly the maps both of them predicted."""

    cohort: tuple[int, int]
    game_id: int
    prediction: Prediction


def backtest_maps(
    diffs_by_cohort: dict[tuple[int, int], list[GameDiff]],
) -> list[MapPrediction]:
    """Walk-forward by event: predict each event's maps from weights trained
    only on earlier events in the same (season × mode)."""
    preds: list[MapPrediction] = []
    for cohort_key, diffs in diffs_by_cohort.items():
        event_order: list[int] = []
        for g in diffs:
            if g.event_id not in event_order:
                event_order.append(g.event_id)
        for i, event_id in enumerate(event_order):
            if i == 0:
                continue
            train = [g for g in diffs if g.event_id in event_order[:i]]
            test = [g for g in diffs if g.event_id == event_id]
            if len(train) < MIN_TRAIN_GAMES:
                continue
            x, mu, sd = _standardize(train)
            y = np.array([1.0 if g.a_won else 0.0 for g in train])
            fit = fit_logistic_l2(x, y, l2=L2)
            xt = (np.array([g.diff for g in test]) - mu) / sd
            for g, p in zip(test, fit.predict(np.asarray(xt)), strict=True):
                preds.append(
                    MapPrediction(
                        cohort=cohort_key,
                        game_id=g.game_id,
                        prediction=Prediction(p=float(p), won=g.a_won, when=g.when),
                    )
                )
    return preds


def backtest_weights(
    diffs_by_cohort: dict[tuple[int, int], list[GameDiff]],
) -> list[Prediction]:
    return [m.prediction for m in backtest_maps(diffs_by_cohort)]


# ------------------------------------------------------------ player scores


@dataclass
class PlayerModeAgg:
    player_id: int
    season_id: int
    mode_id: int
    maps: int
    feats: FloatArray  # aggregate profile
    numerators: FloatArray  # (maps × F), for the bootstrap
    denominators: FloatArray  # (maps × F)


def stat_order(agg: PlayerModeAgg) -> tuple[FloatArray, FloatArray]:
    """One player-season-mode's maps, ordered by what they contain.

    Every bootstrap over a player's own maps draws positions in these two
    arrays, and the rows arrive in whatever order the loader emitted the maps.
    Ordering them by their statistics makes the draw a function of the numbers
    rather than of the row ids. The aggregate profile is a column sum and does
    not move.
    """
    rows = content_order([*agg.numerators.T, *agg.denominators.T])
    return agg.numerators[rows], agg.denominators[rows]


def aggregate_players(
    rows: Sequence[MapRow], cohorts: dict[tuple[int, int], Cohort]
) -> list[PlayerModeAgg]:
    grouped: dict[tuple[int, int, int], list[MapRow]] = defaultdict(list)
    for r in rows:
        cohort = cohorts.get((r.season_id, r.mode_id))
        if cohort is not None and cohort.accepts(r):
            grouped[(r.player_id, r.season_id, r.mode_id)].append(r)

    out: list[PlayerModeAgg] = []
    for (pid, season_id, mode_id), maps in grouped.items():
        features = cohorts[(season_id, mode_id)].features
        num = np.array([[f.numerator(m) for f in features] for m in maps])
        den = np.array([[f.denominator(m) for f in features] for m in maps])
        totals = den.sum(axis=0)
        if not np.all(totals > 0):
            continue  # a season with no rounds played in a round-denominated mode
        out.append(
            PlayerModeAgg(
                player_id=pid,
                season_id=season_id,
                mode_id=mode_id,
                maps=len(maps),
                feats=np.asarray(num.sum(axis=0) / totals),
                numerators=num,
                denominators=den,
            )
        )
    return out


@dataclass
class CohortScale:
    """Frozen calibration for one (season × mode) cohort: the standardization
    the scores are expressed in, and the shrinkage prior estimated from them.

    Both are fitted from the same maps, so a caller refitting the pipeline on a
    prefix of the season (the out-of-sample harness does exactly this) gets a
    prior estimated from that prefix too, with no extra plumbing.
    """

    feat_mu: FloatArray
    feat_sd: FloatArray
    score_mu: float
    score_sd: float
    shrink_maps: float  # the k in m / (m + k)
    within_var: float  # σ̂², per-map score variance around a player's own mean
    between_var: float  # τ̂², true spread between players
    n_players: int
    n_maps: int
    shrink_estimated: bool  # False when the variance ratio was unusable and k fell back


def _score(agg_feats: FloatArray, scale: CohortScale, weights: FloatArray) -> float:
    """Weights-dot-z, standardized to the qualified cohort's score scale."""
    z = (agg_feats - scale.feat_mu) / scale.feat_sd
    return (float(z @ weights) - scale.score_mu) / scale.score_sd


def _shrink(score: float, maps: int, k: float) -> float:
    return score * maps / (maps + k)


def map_scores(
    agg: PlayerModeAgg, feat_mu: FloatArray, feat_sd: FloatArray, weights: FloatArray
) -> FloatArray:
    """This player's score computed one map at a time, on the cohort's scale.

    Public because the hierarchical model reads the same per-map replication to
    estimate σ², and two implementations of "what a single map said about this
    player" would be two definitions of the noise the rating discounts.

    Maps with a zero denominator on any feature are dropped rather than imputed,
    the same rule `_profile` applies to a team's map. Left unstandardized by
    score_sd: k is a ratio of two variances in the same units, so any common
    rescaling of the score cancels out of it.
    """
    ok = np.all(agg.denominators > 0, axis=1)
    if not ok.any():
        return np.zeros(0)
    z = (agg.numerators[ok] / agg.denominators[ok] - feat_mu) / feat_sd
    return np.asarray(z @ weights)


def _estimate_shrinkage(
    members: Sequence[PlayerModeAgg],
    feat_mu: FloatArray,
    feat_sd: FloatArray,
    weights: FloatArray,
) -> tuple[float, float, float, int, int, bool]:
    """The empirical-Bayes prior strength k for one cohort, from its own maps.

    A player's m-map score is an average of m noisy per-map scores, so its
    sampling variance is σ²/m around a true score drawn from a population of
    variance τ². The posterior mean is then the observed score times
    τ² / (τ² + σ²/m) = m / (m + σ²/τ²) — the shrinkage this module already
    applies, with k = σ²/τ² rather than a constant chosen by hand.

    σ̂² and τ̂² come from a one-way random-effects decomposition with players as
    groups, in the unbalanced form (Searle's estimator), since map counts range
    from one to a full season. Every player in the cohort counts, not just the
    qualified ones: the shrinkage is applied to short seasons, so estimating its
    strength from long ones only would be measuring a different population.

    Returns (k, σ̂², τ̂², n_players, n_maps, estimated). `estimated` is False when
    the cohort cannot support the estimate — fewer than two players, no
    within-player replication, or τ̂² ≤ 0, which says the observed spread between
    players is entirely explained by per-map noise. That last case is a real
    answer (shrink everything to the mean) but not one a rating can publish, so
    it falls back and says so rather than silently flattening the cohort.
    """
    groups = [g for g in (map_scores(a, feat_mu, feat_sd, weights) for a in members) if len(g)]
    n = len(groups)
    counts = np.array([len(g) for g in groups], dtype=float)
    total = float(counts.sum())
    if n < 2 or total <= n:
        return (SHRINK_FALLBACK, 0.0, 0.0, n, int(total), False)

    means = np.array([float(g.mean()) for g in groups])
    grand = float(np.concatenate(groups).mean())
    within = sum(float(((g - g.mean()) ** 2).sum()) for g in groups) / (total - n)
    between = float((counts * (means - grand) ** 2).sum()) / (n - 1)
    # Effective group size for the unbalanced case; equals m when balanced.
    m0 = (total - float((counts**2).sum()) / total) / (n - 1)
    tau2 = (between - within) / m0
    if tau2 <= 0.0:
        return (SHRINK_FALLBACK, within, tau2, n, int(total), False)
    return (within / tau2, within, tau2, n, int(total), True)


def build_cohort_scales(
    aggs: Sequence[PlayerModeAgg], fits: dict[tuple[int, int], ModeFit]
) -> dict[tuple[int, int], CohortScale]:
    by_cohort: dict[tuple[int, int], list[PlayerModeAgg]] = defaultdict(list)
    for a in aggs:
        by_cohort[(a.season_id, a.mode_id)].append(a)
    out: dict[tuple[int, int], CohortScale] = {}
    for key, members in by_cohort.items():
        fit = fits.get(key)
        qualified = [a for a in members if a.maps >= MIN_MAPS]
        if fit is None or len(qualified) < 2:
            continue
        feats = np.array([a.feats for a in qualified])
        mu, sd = feats.mean(axis=0), feats.std(axis=0, ddof=1)
        sd[sd == 0.0] = 1.0
        scores = np.array([float(((a.feats - mu) / sd) @ fit.weights) for a in qualified])
        score_sd = float(scores.std(ddof=1))
        k, within, between, n_players, n_maps, estimated = _estimate_shrinkage(
            members, np.asarray(mu), np.asarray(sd), fit.weights
        )
        out[key] = CohortScale(
            feat_mu=np.asarray(mu),
            feat_sd=np.asarray(sd),
            score_mu=float(scores.mean()),
            score_sd=score_sd if score_sd > 0.0 else 1.0,
            shrink_maps=k,
            within_var=within,
            between_var=between,
            n_players=n_players,
            n_maps=n_maps,
            shrink_estimated=estimated,
        )
    return out


@dataclass
class SeasonRating:
    player_id: int
    season_id: int
    mode_id: int | None  # None = all-mode blend
    maps: int
    rating: float | None  # None = the cohort could not support a rating
    rating_sd: float | None


def compute_ratings(
    aggs: Sequence[PlayerModeAgg],
    fits: dict[tuple[int, int], ModeFit],
    scales: dict[tuple[int, int], CohortScale],
    bootstrap: bool = True,
) -> list[SeasonRating]:
    """Ratings for every player-season-mode, plus an all-mode blended row.

    `bootstrap=False` skips the 200-draw resampling and leaves `rating_sd` None.
    The published run always wants the interval; the out-of-sample harness refits
    this once per event and only needs the point estimate, where paying for
    ~17 discarded bootstraps would dominate its runtime.
    """
    by_player_season: dict[tuple[int, int], list[PlayerModeAgg]] = defaultdict(list)
    for a in aggs:
        if (a.season_id, a.mode_id) in scales:
            by_player_season[(a.player_id, a.season_id)].append(a)

    out: list[SeasonRating] = []
    for (pid, season_id), modes in sorted(by_player_season.items()):
        shrunk: list[float] = []
        weights_m: list[int] = []
        boot: FloatArray = np.zeros((BOOTSTRAP_B, len(modes)))
        for j, a in enumerate(modes):
            key = (a.season_id, a.mode_id)
            scale, fit = scales[key], fits[key]
            s = _shrink(_score(a.feats, scale, fit.weights), a.maps, scale.shrink_maps)
            out.append(
                SeasonRating(
                    player_id=pid,
                    season_id=season_id,
                    mode_id=a.mode_id,
                    maps=a.maps,
                    rating=1.0 + RATING_SCALE * s,
                    rating_sd=None,  # per-mode sd folds into the blended row
                )
            )
            shrunk.append(s)
            weights_m.append(a.maps)
            if not bootstrap:
                continue
            # This player-season-mode resamples its own maps, from its own
            # stream. One generator advanced across players in `player_id` order
            # made every player's interval depend on how many players were
            # numbered ahead of them, so a reload that renumbered the table moved
            # rating_sd for players whose maps were untouched. Rows are ordered
            # by what the maps contain for the same reason.
            numerators, denominators = stat_order(a)
            rng = resample_stream(BOOTSTRAP_SEED, numerators, denominators)
            idx = rng.integers(0, a.maps, size=(BOOTSTRAP_B, a.maps))
            for b in range(BOOTSTRAP_B):
                totals = denominators[idx[b]].sum(axis=0)
                if not np.all(totals > 0):
                    boot[b, j] = s
                    continue
                feats = np.asarray(numerators[idx[b]].sum(axis=0) / totals)
                boot[b, j] = _shrink(_score(feats, scale, fit.weights), a.maps, scale.shrink_maps)

        total_maps = sum(weights_m)
        blend = float(np.average(shrunk, weights=weights_m))
        sd = None
        if bootstrap:
            boot_blend = np.average(boot, axis=1, weights=weights_m)
            sd = RATING_SCALE * float(np.asarray(boot_blend).std(ddof=1))
        out.append(
            SeasonRating(
                player_id=pid,
                season_id=season_id,
                mode_id=None,
                maps=total_maps,
                rating=1.0 + RATING_SCALE * blend,
                rating_sd=sd,
            )
        )
    return out


# ------------------------------------------------------------------ orchestration


def label_context(
    conn: psycopg.Connection[tuple[object, ...]],
) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    seasons = {
        cast(int, r[0]): {"year": cast(int, r[1]), "title": cast(str, r[2])}
        for r in conn.execute(
            "SELECT se.id, se.year, t.short_name FROM seasons se"
            " JOIN titles t ON t.id = se.title_id"
        ).fetchall()
    }
    modes = {
        cast(int, r[0]): cast(str, r[1])
        for r in conn.execute("SELECT id, name FROM game_modes").fetchall()
    }
    return seasons, modes


def weights_artifact(
    conn: psycopg.Connection[tuple[object, ...]],
    fits: dict[tuple[int, int], ModeFit],
    cohorts: dict[tuple[int, int], Cohort],
    version: str,
    cis: dict[tuple[int, int], WeightCI] | None = None,
) -> dict[str, Any]:
    """The learned weights, labeled for /methodology and the findings layer.

    Feature sets differ per cohort, so each entry carries its own feature list
    and flags which of them are the slaying pair — the gunfight-vs-everything-else
    reading has to be computed against the features that cohort actually used.

    `rest_vs_slay` and its interval ship on the entry because both consumers of
    this artifact make that one claim, and neither should be recomputing a ratio
    the fit can state once. Entries written before the bootstrap existed carry
    neither key, so consumers must treat a missing interval as "not published"
    rather than as a wide one.
    """
    seasons, modes = label_context(conn)
    cis = cis or {}
    entries = []
    for key, fit in sorted(fits.items()):
        cohort = cohorts[key]
        named = list(zip(cohort.feature_keys, fit.weights, strict=True))
        slaying = [f.key for f in cohort.features if f.slaying]
        ratio = rest_vs_slay(dict(named), slaying)
        ci = cis.get(key)
        entry: dict[str, Any] = {
            "season_id": cohort.season_id,
            "year": seasons[cohort.season_id]["year"],
            "title": seasons[cohort.season_id]["title"],
            "mode_id": cohort.mode_id,
            "mode": modes[cohort.mode_id],
            "n_maps": fit.n_games,
            "features": list(cohort.feature_keys),
            "slaying_features": slaying,
            "labels": {f.key: f.label for f in cohort.features},
            # Which denominator each feature resolved to for this title, so the
            # doc's feature table is generated rather than transcribed and the
            # per-10-minute/per-map seam is visible where it actually falls.
            "denominators": {f.key: f.denom_kind for f in cohort.features},
            "weights": {f: round(float(w), 4) for f, w in named},
            "odds_per_sd": {f: round(float(np.exp(w)), 3) for f, w in named},
        }
        if ratio is not None:
            entry["rest_vs_slay"] = round(ratio, 4)
        if ci is not None:
            entry["weight_ci"] = {
                k: [round(lo, 4), round(hi, 4)] for k, (lo, hi) in ci.weights.items()
            }
            entry["ci_draws"] = ci.draws
            if ci.ratio is not None:
                entry["rest_vs_slay_ci"] = [round(ci.ratio[0], 4), round(ci.ratio[1], 4)]
        entries.append(entry)
    return {
        "version": version,
        "l2": L2,
        "bootstrap_b": BOOTSTRAP_B,
        "ci": "95% percentile bootstrap, maps resampled within cohort",
        "cohorts": entries,
    }


def shrinkage_artifact(
    conn: psycopg.Connection[tuple[object, ...]],
    scales: dict[tuple[int, int], CohortScale],
    version: str,
) -> dict[str, Any]:
    """The estimated prior strength per cohort, against the constant it replaced.

    `half_signal_maps` is k restated in the units a reader has: the number of
    maps at which a season keeps half of whatever it measured. `vs_fallback` is
    how far the cohort sits from the 15 this pipeline used to assert for all of
    them, which is the whole point of estimating it.
    """
    seasons, modes = label_context(conn)
    entries = []
    for (season_id, mode_id), scale in sorted(scales.items()):
        entries.append(
            {
                "season_id": season_id,
                "year": seasons[season_id]["year"],
                "title": seasons[season_id]["title"],
                "mode_id": mode_id,
                "mode": modes[mode_id],
                "n_players": scale.n_players,
                "n_maps": scale.n_maps,
                "within_var": round(scale.within_var, 4),
                "between_var": round(scale.between_var, 4),
                "shrink_maps": round(scale.shrink_maps, 2),
                "half_signal_maps": round(scale.shrink_maps, 1),
                "vs_fallback": round(scale.shrink_maps - SHRINK_FALLBACK, 2),
                "estimated": scale.shrink_estimated,
            }
        )
    estimated = [e["shrink_maps"] for e in entries if e["estimated"]]
    return {
        "version": version,
        "estimator": "one-way random effects, unbalanced (Searle); k = within/between",
        "fallback": SHRINK_FALLBACK,
        "n_fell_back": sum(1 for e in entries if not e["estimated"]),
        "median": round(float(np.median(estimated)), 2) if estimated else None,
        "min": min(estimated) if estimated else None,
        "max": max(estimated) if estimated else None,
        "cohorts": entries,
    }


def load(
    conn: psycopg.Connection[tuple[object, ...]],
) -> tuple[list[MapRow], Coverage]:
    """Every player-map with the kill-feed columns folded in. Callers running
    several versions load once and pass the result to each."""
    loaded = load_map_rows(conn)
    attach_kill_feed(conn, loaded.rows, loaded.coverage)
    return loaded.rows, loaded.coverage


@dataclass
class Fitted:
    """Everything a rating estimator needs, fitted once.

    The pipeline up to this point — which cohorts exist, what wins a map in each
    of them, and each player's season profile — is shared by both estimators, so
    it is built once and handed to whichever one is being run. That is what makes
    the comparison between them a comparison of estimators and not of pipelines.
    """

    version: str
    cohorts: dict[tuple[int, int], Cohort]
    diffs: dict[tuple[int, int], list[GameDiff]]
    fits: dict[tuple[int, int], ModeFit]
    preds: list[MapPrediction]
    aggs: list[PlayerModeAgg]
    scales: dict[tuple[int, int], CohortScale]


def prepare(rows: Sequence[MapRow], coverage: Coverage, version: str) -> Fitted:
    cohorts = build_cohorts(rows, coverage, version)
    diffs = build_game_diffs(rows, cohorts)
    fits = fit_mode_weights(diffs)
    aggs = aggregate_players(rows, cohorts)
    return Fitted(
        version=version,
        cohorts=cohorts,
        diffs=diffs,
        fits=fits,
        preds=backtest_maps(diffs),
        aggs=aggs,
        scales=build_cohort_scales(aggs, fits),
    )


def fit_artifacts(
    conn: psycopg.Connection[tuple[object, ...]], fitted: Fitted, version: str
) -> dict[str, dict[str, Any]]:
    """What the shared fit learned about itself, whichever estimator ran."""
    return {
        "mode_weights": weights_artifact(
            conn,
            fitted.fits,
            fitted.cohorts,
            version,
            bootstrap_mode_weights(fitted.diffs, fitted.cohorts),
        ),
        "rating_shrinkage": shrinkage_artifact(conn, fitted.scales, version),
    }


def compute(
    conn: psycopg.Connection[tuple[object, ...]],
    version: str,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> tuple[list[SeasonRating], list[MapPrediction], dict[str, dict[str, Any]]]:
    """Fit weights and rate players for one feature-set version, by the z-and-shrink
    estimator this module describes. Callers that run several versions load the map
    rows once and pass them in.

    The published rating is `hierarchical.compute`; this arm survives as the thing
    it is compared against, and as the estimator every prior published rating used.

    The third element maps artifact name to payload, so a new thing the fit
    learned about itself ships with the run instead of needing its own path."""
    if rows is None or coverage is None:
        rows, coverage = load(conn)
    fitted = prepare(rows, coverage, version)
    ratings = compute_ratings(fitted.aggs, fitted.fits, fitted.scales)
    return ratings, fitted.preds, fit_artifacts(conn, fitted, version)


def write(
    conn: psycopg.Connection[tuple[object, ...]],
    run_id: int,
    ratings: Sequence[SeasonRating],
    artifacts: dict[str, dict[str, Any]],
) -> int:
    conn.cursor().executemany(
        "INSERT INTO player_season_adjusted (run_id, player_id, season_id, mode_id,"
        " maps_played, rating, rating_sd, completeness)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s, 1.0)",
        [
            (run_id, r.player_id, r.season_id, r.mode_id, r.maps, r.rating, r.rating_sd)
            for r in ratings
        ],
    )
    conn.cursor().executemany(
        "INSERT INTO model_artifacts (run_id, name, payload) VALUES (%s, %s, %s)",
        [(run_id, name, json.dumps(payload)) for name, payload in sorted(artifacts.items())],
    )
    return len(ratings)


def compute_and_write(
    conn: psycopg.Connection[tuple[object, ...]],
    run_id: int,
    version: str = DEFAULT_VERSION,
    rows: Sequence[MapRow] | None = None,
    coverage: Coverage | None = None,
) -> tuple[int, list[Prediction], dict[str, dict[str, Any]]]:
    """Fit, rate, write rows + artifacts. Returns
    (n_rating_rows, walk-forward predictions, artifacts by name)."""
    ratings, preds, artifacts = compute(conn, version, rows, coverage)
    return (
        write(conn, run_id, ratings, artifacts),
        [m.prediction for m in preds],
        artifacts,
    )
