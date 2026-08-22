"""Match context for the box score. Spec: /methodology#match-context.

Four populated columns describe the circumstances of a map and no analytics
module reads any of them: `events.is_lan`, `series.round_label`, `games.map_id`
and `events.prize_pool`. A player's cohort z-score therefore treats a map played
online in a qualifier as the same kind of event as a grand final on a LAN stage
in front of a crowd.

**What this adjusts, and what it does not.** The same rule the opponent module
states: the plus-minus already conditions on who was on the server, so nothing
here touches it. What is modelled is the **box score** — the per-map rates every
published per-player statistic is built from. The opponent block sits in the
same design as the context terms, so a context coefficient is what is left after
the opposition is accounted for, and the same fit without that block says how
much of it the opposition was carrying.

**Every term is an adjusted association, never a cause.** "Venue-associated
deviation" is what the data supports. "Plays better on LAN" is not, because who
attends a LAN, which teams qualify, and which stage is played there are all
selected. Nothing in this module's output is a treatment effect.

**Two measured facts constrain what may be asked of the venue term.**

1. The CWL era carries no venue contrast at all: 2017, 2018 and 2019 are 298,
   2,881 and 1,912 maps, every one of them LAN. Any LAN/online comparison is
   identified inside the Call of Duty League era, and the era/venue confound
   across the 2018/2020 seam is a caveat every cross-era claim inherits.
2. Inside that era, venue and stakes are half the same contrast — LAN is where
   the Major bracket is played and online is where the qualifier is. Regressing
   `is_lan` on the stakes classes returns R² near one half. The venue term is
   estimable at roughly twice the variance the raw contrast suggests, and the
   published interval is what carries that.

**Families earn their place one at a time.** Each is fitted with and without,
and judged on two numbers: how far it moves the leaderboard, in cohort standard
deviations, and whether it lowers out-of-fold error on the rate it claims to
explain. A family that moves the table without predicting is a family fitting
its own noise, and it is published as one.

**Map identity is a random effect, not one dummy per map.** The rotation changes
every title, several maps carry a few hundred rows, and the aim is a statement
about a map rather than about the maps that happened to be in one rotation. The
per-player venue effect is pooled the same way and for the same reason: the
tenth-percentile player has fourteen balanced rows, and an unpooled estimate on
fourteen rows is noise with a rank next to it.
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any, cast

import numpy as np

from ..regress import FloatArray
from . import hierarchical, opponent

MODEL = "match_context"
VERSION = "1.0.0"

# ------------------------------------------------------------ stakes taxonomy

STAKES_REGULAR = "regular"
STAKES_GROUP = "group"
STAKES_BRACKET = "bracket"
STAKES_GRAND_FINAL = "grand_final"
STAKES_UNCLASSIFIED = "unclassified"

# `regular` is the base level every other class is measured against: it is the
# largest, and a league qualifier map is the closest thing the record has to an
# ordinary one.
STAKES_LEVELS: tuple[str, ...] = (
    STAKES_REGULAR,
    STAKES_GROUP,
    STAKES_BRACKET,
    STAKES_GRAND_FINAL,
    STAKES_UNCLASSIFIED,
)

# `series.round_label` carries three vocabularies over 742 distinct values: CWL
# archive slugs (`champs-winners-1-2`, `pool-B-4`, `pro1-a1-7`), Call of Duty
# League prose (`Winners Round 1`, `Major Qualifier`) and short codes (`GF`,
# `QF`, `LR1`). All three are matched here rather than one being treated as the
# format and the others as exceptions.
_SLUG_GRAND_FINAL = re.compile(r"^(champs?|pro\d?)-.*grand-finals-\d+$")
_SLUG_BRACKET = re.compile(
    r"^(champs?|pro\d?|rel|plq|playin)\b.*-(winners|losers|bracket|lr\d|wr\d|\d)"
)
_SLUG_POOL = re.compile(r"^(champs-)?pool-[a-z](-tie)?-\d+$")
_SLUG_LEAGUE = re.compile(r"^pro\d?-([ab]\d|w\d+)-\d+$")

_PROSE_GRAND_FINAL = frozenset({"grand finals", "gf", "finals", "lf"})
_PROSE_GROUP = frozenset({"group stage", "play-in"})
_CODE_BRACKET = frozenset({"wr1", "wr2", "wr3", "lr1", "lr2", "r1", "r2", "qf", "sf", "wf", "tb"})


def classify_stakes(label: str | None) -> str:
    """One of STAKES_LEVELS for a round label, in any of the three vocabularies."""
    low = (label or "").strip().lower()
    if not low:
        return STAKES_UNCLASSIFIED
    if low in _PROSE_GRAND_FINAL or _SLUG_GRAND_FINAL.match(low):
        return STAKES_GRAND_FINAL
    if low.startswith(("major qualifier", "week ", "day ")) or _SLUG_LEAGUE.match(low):
        return STAKES_REGULAR
    if _SLUG_POOL.match(low) or low.startswith(("group play", "group ")) or low in _PROSE_GROUP:
        return STAKES_GROUP
    if _SLUG_BRACKET.match(low):
        return STAKES_BRACKET
    if (
        low.startswith(("winners ", "elimination ", "round "))
        or low in _CODE_BRACKET
        or low == "semifinals"
    ):
        return STAKES_BRACKET
    return STAKES_UNCLASSIFIED


_ELIM_SLUG = re.compile(r"-(losers|lr\d)")
_ELIM_PROSE = ("elimination ", "lower ")
_ELIM_CODES = frozenset({"lr1", "lr2", "r1", "r2", "qf", "sf", "round 1", "round 2", "semifinals"})


def elimination_facing(label: str | None) -> bool:
    """Both sides can be knocked out by losing this series.

    The grand final is excluded: only the lower-bracket side faces elimination
    there, and a series-level flag would be wrong for one of the two teams.
    """
    low = (label or "").strip().lower()
    if classify_stakes(low) == STAKES_GRAND_FINAL:
        return False
    if _ELIM_SLUG.search(low) or low in _ELIM_CODES:
        return True
    return any(marker in low for marker in _ELIM_PROSE)


# ------------------------------------------------------------- host markets


@dataclass(frozen=True)
class HostMarkets:
    """Which franchises call a LAN event's city home, keyed '<year>:<event>'."""

    events: dict[str, dict[str, Any]]

    @classmethod
    def load(cls) -> HostMarkets:
        raw = json.loads(
            resources.files("cdlhub_analytics.ratings").joinpath("host_markets.json").read_text()
        )
        return cls(events=dict(raw.get("events", {})))

    def get(self, season_year: int | None, event_name: str) -> dict[str, Any] | None:
        return self.events.get(f"{season_year}:{event_name}")


# --------------------------------------------------------------- the context


@dataclass(frozen=True)
class MapContext:
    """The circumstances of one map, joined to lines by the map's natural key."""

    # None where no source decides it. An undecided venue is its own state and
    # never folded into online, which is what reading `location` would have done.
    is_lan: bool | None
    stakes: str
    elimination: bool
    map_name: str | None
    # log1p of the event's prize pool in dollars. None where the event carries
    # none, which is 39 of 99 events and not a zero.
    log_prize: float | None
    # Team ids whose franchise city is this venue's market. Empty at a neutral
    # site and at every online map.
    host_team_ids: frozenset[int]
    host_confidence: str


CONTEXT_SQL = """
SELECT s.source_uid || '#' || g.ordinal::text AS map_key,
       e.is_lan, s.round_label, m.name AS map_name, e.prize_pool,
       se.year, e.name AS event_name
FROM games g
JOIN series s   ON s.id = g.series_id
JOIN events e   ON e.id = s.event_id
JOIN seasons se ON se.id = e.season_id
LEFT JOIN maps m ON m.id = g.map_id
WHERE s.source_uid IS NOT NULL
"""

TEAM_SQL = "SELECT id, name FROM teams"


def load_context(conn: Any) -> dict[str, MapContext]:
    """One MapContext per map, keyed by the map's natural key.

    Keyed by `<source_uid>#<ordinal>` rather than `games.id` for the reason the
    rest of the stack is: the loader renumbers ids on any reload that deletes
    and recreates rows.
    """
    markets = HostMarkets.load()
    team_ids = {cast(str, name): cast(int, tid) for tid, name in conn.execute(TEAM_SQL).fetchall()}
    out: dict[str, MapContext] = {}
    for row in conn.execute(CONTEXT_SQL).fetchall():
        map_key = cast(str, row[0])
        is_lan = cast("bool | None", row[1])
        prize = row[4]
        entry = markets.get(cast("int | None", row[5]), cast(str, row[6])) or {}
        hosts = frozenset(team_ids[name] for name in entry.get("teams", []) if name in team_ids)
        out[map_key] = MapContext(
            is_lan=is_lan,
            stakes=classify_stakes(cast("str | None", row[2])),
            elimination=elimination_facing(cast("str | None", row[2])),
            map_name=cast("str | None", row[3]),
            log_prize=math.log1p(float(prize)) if prize is not None else None,
            # A host market only means anything where the match was played at
            # the venue. An online map branded with a host city is the trap
            # `venue.py` exists to prevent, so the flag is gated on the venue.
            host_team_ids=hosts if is_lan else frozenset(),
            host_confidence=str(entry.get("confidence") or "clear"),
        )
    return out


# ------------------------------------------------------------------ families

FAMILY_VENUE = "venue"
FAMILY_STAKES = "stakes"
FAMILY_ELIMINATION = "elimination"
FAMILY_PRIZE = "prize_pool"
FAMILY_HOST = "host_team"
FAMILY_MAP = "map_identity"

# Ordered as the ablation table declares them. `map_identity` is last because it
# is the one family fitted as a random effect rather than as design columns.
FAMILIES: tuple[str, ...] = (
    FAMILY_VENUE,
    FAMILY_STAKES,
    FAMILY_ELIMINATION,
    FAMILY_PRIZE,
    FAMILY_HOST,
    FAMILY_MAP,
)

# Families that become columns in the design. `map_identity` is pooled instead.
COLUMN_FAMILIES: tuple[str, ...] = FAMILIES[:-1]


@dataclass(frozen=True)
class ContextColumns:
    """The context block of the design, and what each column is."""

    matrix: FloatArray
    labels: tuple[str, ...]
    family_of: tuple[str, ...]

    @property
    def width(self) -> int:
        return int(self.matrix.shape[1])

    def columns_for(self, family: str) -> list[int]:
        return [i for i, name in enumerate(self.family_of) if name == family]


def _prize_centre(rows: Sequence[MapContext]) -> float:
    values = [row.log_prize for row in rows if row.log_prize is not None]
    return float(np.mean(values)) if values else 0.0


def build_context_columns(
    panel: opponent.Panel,
    context: Mapping[str, MapContext],
    families: Sequence[str],
) -> ContextColumns:
    """The context design for one panel, holding only the families asked for.

    A level with no rows in this cohort is dropped rather than carried as a zero
    column: a cohort played entirely on LAN has no venue contrast, and a column
    of zeros would report an effect of exactly nothing as though it were
    measured.
    """
    rows = [context.get(line.map_key) for line in panel.lines]
    present = [row for row in rows if row is not None]
    centre = _prize_centre(present)
    columns: list[FloatArray] = []
    labels: list[str] = []
    family_of: list[str] = []

    def add(name: str, family: str, values: list[float]) -> None:
        array = np.asarray(values, dtype=np.float64)
        if float(array.std()) <= 0.0:
            return
        columns.append(array)
        labels.append(name)
        family_of.append(family)

    if FAMILY_VENUE in families:
        add("is_lan", FAMILY_VENUE, [1.0 if r is not None and r.is_lan else 0.0 for r in rows])
        add(
            "venue_undecided",
            FAMILY_VENUE,
            [1.0 if r is None or r.is_lan is None else 0.0 for r in rows],
        )
    if FAMILY_STAKES in families:
        for level in STAKES_LEVELS[1:]:  # `regular` is the base
            add(
                f"stakes_{level}",
                FAMILY_STAKES,
                [1.0 if r is not None and r.stakes == level else 0.0 for r in rows],
            )
    if FAMILY_ELIMINATION in families:
        add(
            "elimination_facing",
            FAMILY_ELIMINATION,
            [1.0 if r is not None and r.elimination else 0.0 for r in rows],
        )
    if FAMILY_PRIZE in families:
        add(
            "log_prize",
            FAMILY_PRIZE,
            [
                (r.log_prize - centre) if r is not None and r.log_prize is not None else 0.0
                for r in rows
            ],
        )
        add(
            "prize_missing",
            FAMILY_PRIZE,
            [1.0 if r is None or r.log_prize is None else 0.0 for r in rows],
        )
    if FAMILY_HOST in families:
        add(
            "host_market",
            FAMILY_HOST,
            [
                1.0 if r is not None and line.team_id in r.host_team_ids else 0.0
                for line, r in zip(panel.lines, rows, strict=True)
            ],
        )
        add(
            "host_market_judged",
            FAMILY_HOST,
            [
                1.0
                if r is not None
                and line.team_id in r.host_team_ids
                and r.host_confidence == "judgement"
                else 0.0
                for line, r in zip(panel.lines, rows, strict=True)
            ],
        )

    matrix = (
        np.column_stack(columns) if columns else np.zeros((len(panel.lines), 0), dtype=np.float64)
    )
    return ContextColumns(matrix=matrix, labels=tuple(labels), family_of=tuple(family_of))


# -------------------------------------------------------------------- fitting

# The context block's ridge grid. Wide and geometric for the same reason the
# opponent module's is: a cohort with 48 LAN rows and one with 1,620 want
# penalties two orders of magnitude apart. The grid is searched by GCV, so the
# amount of pooling is chosen by the data rather than set here.
CONTEXT_GRID: tuple[float, ...] = tuple(float(10.0**k) for k in range(-2, 5))

# A cohort below this many lines is not fitted: the own-player and opponent
# blocks alone carry more columns than a small cohort's schedule separates, and
# adding context columns to that is fitting noise with a name on it.
MIN_COHORT_ROWS = opponent.MIN_COHORT_ROWS

# Folds for the out-of-fold error every family is judged on, cut on whole series
# so a map cannot predict its own series-mate.
FOLDS = opponent.CROSSFIT_FOLDS


def block_ridge(x: FloatArray, y: FloatArray, w: FloatArray, penalties: FloatArray) -> opponent.Wls:
    """Weighted ridge with a per-column penalty, so blocks can be pooled apart.

    `solve_wls` takes one scalar, which cannot express "leave the player block
    on the pseudo-inverse and pool the context block". The penalty vector does.
    """
    xw = x * w[:, None]
    gram = x.T @ xw
    inv = np.linalg.pinv(gram + np.diag(penalties), hermitian=True)
    beta = inv @ (xw.T @ y)
    return opponent.Wls(
        beta=beta,
        inv=inv,
        residual=y - x @ beta,
        trace_hat=float(np.trace(inv @ gram)),
        n=len(y),
    )


@dataclass(frozen=True)
class Fit:
    """One cohort-feature fitted at one set of families."""

    key: tuple[int, int]
    feature: str
    families: tuple[str, ...]
    labels: tuple[str, ...]
    family_of: tuple[str, ...]
    coefficients: FloatArray
    standard_errors: FloatArray
    ridge: float
    n_lines: int
    # Cohort standard deviation of the raw per-player season values, so a
    # coefficient in rate units can be restated in the units the leaderboard is
    # read in.
    cohort_sd: float
    residual: FloatArray
    mask: FloatArray
    oof_rmse: float
    map_effects: dict[str, float]
    map_tau2: float


def _cohort_sd(panel: opponent.Panel, feature_key: str) -> float:
    values = opponent.aggregate(panel, feature_key)
    qualified = [v.value for v in values if v.maps >= opponent.QUALIFY_MAPS]
    return float(np.std(qualified, ddof=1)) if len(qualified) > 1 else 0.0


def _penalty_vector(base_width: int, context: ContextColumns, ridge: float) -> FloatArray:
    """No penalty on the intercept or the player blocks; `ridge` on context."""
    penalties = np.zeros(base_width + context.width, dtype=np.float64)
    penalties[base_width:] = ridge
    return penalties


def _group_mean_and_variance(values: FloatArray, weights: FloatArray) -> tuple[float, float] | None:
    """A weighted mean and the variance of that mean, or None where it has none.

    The denominator is the effective sample size the weights imply, not the row
    count: five maps that ended early carry less than five full ones.

    The spread is corrected for the mean it was measured around. Dividing by the
    effective size rather than one less than it is the population variance, and
    on two observations that is half the right answer — which is exactly the
    case where a wrong variance does the most damage, because it makes the
    thinnest players look like the most precisely measured ones.
    """
    total = float(np.sum(weights))
    if total <= 0.0:
        return None
    mean = float(np.sum(weights * values) / total)
    effective = total**2 / float(np.sum(weights**2))
    if effective <= 1.0:
        return None
    spread = float(np.sum(weights * (values - mean) ** 2) / total)
    return (mean, max(spread / (effective - 1.0), 1e-12))


def _pool_by_group(
    labels: Sequence[str | None],
    residual: FloatArray,
    weight: FloatArray,
    minimum: int = 5,
) -> dict[str, float]:
    """Partially pooled per-group deviations, by empirical Bayes over the means."""
    grouped: dict[str, list[int]] = defaultdict(list)
    for i, label in enumerate(labels):
        if label is not None:
            grouped[label].append(i)
    names: list[str] = []
    observations: list[float] = []
    variances: list[float] = []
    for name in sorted(grouped):
        rows = np.asarray(grouped[name], dtype=np.int64)
        if len(rows) < minimum:
            continue
        stats = _group_mean_and_variance(residual[rows], weight[rows])
        if stats is None:
            continue
        names.append(name)
        observations.append(stats[0])
        variances.append(stats[1])
    if len(names) < 2:
        return {}
    _mu, _tau2, posterior = hierarchical.empirical_bayes(
        np.asarray(observations, dtype=np.float64), np.asarray(variances, dtype=np.float64)
    )
    return {name: float(value) for name, value in zip(names, posterior, strict=True)}


def _out_of_fold_rmse(
    x: FloatArray,
    y: FloatArray,
    w: FloatArray,
    penalties: FloatArray,
    folds: Sequence[int],
    map_names: Sequence[str | None] | None = None,
) -> float:
    """Weighted RMSE on lines held out by whole series.

    This is the number that separates a family which explains the rate from one
    which only moves the table: a family fitting its own noise raises it.

    `map_names` adds the pooled map effect to the prediction, estimated inside
    each fold's training rows and applied to its held-out ones. Without it a
    random effect cannot lower this number no matter what it is worth, because
    it never reaches the prediction — and a family judged on a number it cannot
    move is not being judged.
    """
    labels = np.asarray(folds)
    predicted = np.zeros(len(y), dtype=np.float64)
    seen = np.zeros(len(y), dtype=bool)
    for fold in sorted(set(folds)):
        holdout = labels == fold
        if not holdout.any() or holdout.all():
            continue
        trained = block_ridge(x[~holdout], y[~holdout], w[~holdout], penalties)
        predicted[holdout] = x[holdout] @ trained.beta
        if map_names is not None:
            effects = _pool_by_group(
                [map_names[i] for i in np.flatnonzero(~holdout).tolist()],
                y[~holdout] - x[~holdout] @ trained.beta,
                w[~holdout],
            )
            for position in np.flatnonzero(holdout).tolist():
                name = map_names[position]
                if name is not None:
                    predicted[position] += effects.get(name, 0.0)
        seen[holdout] = True
    if not seen.any():
        return math.nan
    error = y[seen] - predicted[seen]
    weight = w[seen]
    return float(np.sqrt(np.sum(weight * error**2) / np.sum(weight)))


# Maps a map name needs in a cohort before it gets a pooled effect of its own.
# Below it the map joins the cohort mean, which is what pooling does to a map
# nobody played anyway.
MIN_MAP_ROWS = 5


def pooled_map_effects(
    panel: opponent.Panel,
    context: Mapping[str, MapContext],
    residual: FloatArray,
    weight: FloatArray,
    mask: FloatArray,
) -> tuple[dict[str, float], float]:
    """Partially pooled per-map deviations, and the variance component behind them.

    Each map's observation is the weighted mean residual over its lines and its
    sampling variance is that mean's own. `empirical_bayes` then estimates how
    much of the spread between maps is real, which is the whole point of fitting
    map identity as a random effect: a rotation is not a fixed list, and a map
    with twenty rows should not be allowed to claim what a map with three
    hundred can.
    """
    labels = [
        entry.map_name if (entry := context.get(line.map_key)) is not None else None
        for line in panel.lines
    ]
    held = [name if mask[i] else None for i, name in enumerate(labels)]
    effects = _pool_by_group(held, residual, weight, MIN_MAP_ROWS)
    if not effects:
        return ({}, 0.0)
    return (effects, float(np.var(list(effects.values()), ddof=1)))


def fit_cohort_feature(
    panel: opponent.Panel,
    context: Mapping[str, MapContext],
    feature_key: str,
    families: Sequence[str],
    *,
    with_opponent: bool = True,
) -> Fit | None:
    """Fit one cohort-feature at one set of families, or None where it cannot be.

    With `with_opponent` false the opposing-lineup columns are left out and only
    the line's own player is held fixed. Comparing the two is what "how much of
    the effect survives opponent adjustment" means, so the comparison has to
    remove the opponent block and not some other one.
    """
    rate, weight, mask = opponent.response(panel, feature_key)
    if int(mask.sum()) < MIN_COHORT_ROWS:
        return None
    columns = opponent.build_columns(panel, teammates=False)
    base = opponent.design(panel, columns)
    if not with_opponent:
        keep = [0] + sorted(set(columns.own.values()))
        base = base[:, keep]
    column_families = [f for f in families if f in COLUMN_FAMILIES]
    ctx = build_context_columns(panel, context, column_families)
    x_full = np.column_stack([base, ctx.matrix]) if ctx.width else base
    x, y, w = x_full[mask], rate[mask], weight[mask]
    if x.shape[0] <= x.shape[1]:
        return None

    folds = opponent.fold_of([line.series_key for line in panel.lines])
    fold_subset = [f for f, keep in zip(folds, mask, strict=True) if keep]

    if ctx.width:
        best = min(
            CONTEXT_GRID,
            key=lambda ridge: opponent.gcv(
                block_ridge(x, y, w, _penalty_vector(base.shape[1], ctx, ridge)), w
            ),
        )
    else:
        best = 0.0
    penalties = _penalty_vector(base.shape[1], ctx, best)
    fitted = block_ridge(x, y, w, penalties)

    clusters = [line.team_season for line, keep in zip(panel.lines, mask, strict=True) if keep]
    covariance = opponent.cluster_cov(x, w, fitted, clusters)
    span = slice(base.shape[1], base.shape[1] + ctx.width)
    errors = np.sqrt(np.clip(np.diag(covariance)[span], 0.0, None))

    residual = np.zeros(len(rate), dtype=np.float64)
    residual[mask] = fitted.residual
    map_effects: dict[str, float] = {}
    map_tau2 = 0.0
    fold_map_names: list[str | None] | None = None
    if FAMILY_MAP in families:
        map_effects, map_tau2 = pooled_map_effects(panel, context, residual, weight, mask)
        fold_map_names = [
            entry.map_name if (entry := context.get(line.map_key)) is not None else None
            for line, keep in zip(panel.lines, mask, strict=True)
            if keep
        ]

    return Fit(
        key=panel.key,
        feature=feature_key,
        families=tuple(families),
        labels=ctx.labels,
        family_of=ctx.family_of,
        coefficients=fitted.beta[span],
        standard_errors=errors,
        ridge=best,
        n_lines=int(mask.sum()),
        cohort_sd=_cohort_sd(panel, feature_key),
        residual=residual,
        mask=mask,
        oof_rmse=_out_of_fold_rmse(x, y, w, penalties, fold_subset, fold_map_names),
        map_effects=map_effects,
        map_tau2=map_tau2,
    )


def weighted_mean(values: FloatArray, weights: FloatArray) -> float:
    total = float(np.sum(weights))
    return float(np.sum(values * weights) / total) if total > 0.0 else 0.0


def context_shift(panel: opponent.Panel, context: Mapping[str, MapContext], fit: Fit) -> FloatArray:
    """What the context block alone contributed to each line, centred on the cohort.

    Centring is what makes the shift invariant to the level: moving every
    context effect by a constant moves every line the same way and cancels out
    of the leaderboard, so only the differences between circumstances survive.
    """
    _rate, weight, _mask = opponent.response(panel, fit.feature)
    ctx = build_context_columns(panel, context, [f for f in fit.families if f in COLUMN_FAMILIES])
    contribution = (
        ctx.matrix @ fit.coefficients
        if ctx.width == len(fit.coefficients)
        else np.zeros(len(panel.lines), dtype=np.float64)
    )
    for i, line in enumerate(panel.lines):
        entry = context.get(line.map_key)
        if entry is not None and entry.map_name:
            contribution[i] += fit.map_effects.get(entry.map_name, 0.0)
    return contribution - weighted_mean(contribution, weight)


# ------------------------------------------------------------------ ablation

# The families the ablation adds one at a time, in the order declared before
# fitting. Each row is the model holding every family up to and including this
# one, measured against the row above it.
ABLATION_ORDER: tuple[str, ...] = FAMILIES

# The four rate columns every comparison in this stack is reported on, so the
# context phase and the opponent phase are read in the same units.
HEADLINE_FEATURES = opponent.HEADLINE_FEATURES


@dataclass(frozen=True)
class AblationRow:
    """One family's incremental effect on one cohort-feature."""

    key: tuple[int, int]
    feature: str
    family: str
    families: tuple[str, ...]
    mean_abs_dz: float
    p95_abs_dz: float
    top_n_churn: int
    oof_rmse: float
    oof_rmse_delta: float
    n_players: int
    ridge: float


def _leaderboard_after(
    panel: opponent.Panel, context: Mapping[str, MapContext], fit: Fit
) -> dict[int, float]:
    """The cohort's z-scores once this fit's context shift is removed."""
    shift = context_shift(panel, context, fit)
    return opponent.leaderboard(opponent.aggregate(panel, fit.feature, shift))


def ablate(
    panel: opponent.Panel,
    context: Mapping[str, MapContext],
    feature_key: str,
    order: Sequence[str] = ABLATION_ORDER,
) -> list[AblationRow]:
    """Add one family at a time, measuring each against the model below it.

    Two numbers per family, both declared before the fit ran: how far the
    leaderboard moves, in cohort standard deviations, and whether out-of-fold
    error on the rate falls. A family that moves the table without lowering the
    error is fitting its own noise, and the pair of numbers is what shows it.
    """
    baseline = fit_cohort_feature(panel, context, feature_key, [])
    if baseline is None:
        return []
    previous = opponent.leaderboard(opponent.aggregate(panel, feature_key))
    previous_rmse = baseline.oof_rmse
    rows: list[AblationRow] = []
    held: list[str] = []
    for family in order:
        held.append(family)
        fit = fit_cohort_feature(panel, context, feature_key, held)
        if fit is None:
            continue
        after = _leaderboard_after(panel, context, fit)
        moved = opponent.movement(previous, after)
        rows.append(
            AblationRow(
                key=panel.key,
                feature=feature_key,
                family=family,
                families=tuple(held),
                mean_abs_dz=moved.mean_abs_dz,
                p95_abs_dz=moved.p95_abs_dz,
                top_n_churn=moved.top_n_churn,
                oof_rmse=fit.oof_rmse,
                oof_rmse_delta=fit.oof_rmse - previous_rmse,
                n_players=moved.n_players,
                ridge=fit.ridge,
            )
        )
        previous, previous_rmse = after, fit.oof_rmse
    return rows


# ------------------------------------------------- the per-player venue effect

# Balanced rows a player needs before a venue effect is estimated for them at
# all. Below it the player has no contrast to speak of and joins the pooled
# mean, which is what shrinkage would do to them anyway — stated as a threshold
# so the population the finding covers is a stated number rather than an
# artifact of the prior.
MIN_VENUE_ROWS = 8

# And at least this many on each side. A difference of two means needs a spread
# on both of them, and two maps give a variance estimate too unstable to shrink
# against — which is how a player with two LAN maps ends up at the top of a
# table by measurement error.
MIN_VENUE_SIDE = 3


@dataclass(frozen=True)
class VenueEffect:
    """One player's LAN-minus-online deviation on one feature, after pooling."""

    player_id: int
    feature: str
    raw: float
    pooled: float
    # `pooled` minus the cohort's common venue effect, which is what the
    # interval is placed around. Shrinkage pulls every player toward that common
    # value and not toward zero, so asking whether a player's pooled effect
    # differs from zero asks whether the cohort's does — and answers yes for
    # everyone at once. The question a per-player finding can answer is whether
    # this player differs from the other players in the same cohort.
    deviation: float
    # The cohort's common venue effect: what the average eligible player in this
    # cohort did between the two venues. Published beside the deviations so the
    # level and the differences are read apart.
    common: float
    standard_error: float
    lo: float
    hi: float
    lan_maps: int
    online_maps: int
    # The same quantity before the opponent block entered the design, so the
    # share of it the opposition was carrying is published rather than assumed.
    pooled_before_opponent: float | None


def _side_means(
    panel: opponent.Panel,
    context: Mapping[str, MapContext],
    residual: FloatArray,
    weight: FloatArray,
    mask: FloatArray,
) -> dict[int, tuple[float, float, float, int, int]]:
    """Per player: (LAN mean, online mean, variance of the difference, counts)."""
    lan: dict[int, list[tuple[float, float]]] = defaultdict(list)
    online: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for i, line in enumerate(panel.lines):
        if not mask[i]:
            continue
        entry = context.get(line.map_key)
        if entry is None or entry.is_lan is None:
            continue
        (lan if entry.is_lan else online)[line.player_id].append((residual[i], weight[i]))

    out: dict[int, tuple[float, float, float, int, int]] = {}
    for player in sorted(set(lan) & set(online)):
        parts: list[tuple[float, float]] = []
        for side in (lan[player], online[player]):
            stats = _group_mean_and_variance(
                np.asarray([v for v, _ in side], dtype=np.float64),
                np.asarray([w for _, w in side], dtype=np.float64),
            )
            if stats is None:
                parts = []
                break
            parts.append(stats)
        if not parts:
            continue
        (lan_mean, lan_var), (online_mean, online_var) = parts
        out[player] = (
            lan_mean,
            online_mean,
            lan_var + online_var,
            len(lan[player]),
            len(online[player]),
        )
    return out


def venue_effects(
    panel: opponent.Panel,
    context: Mapping[str, MapContext],
    fit: Fit,
    *,
    without_opponent: Fit | None = None,
) -> list[VenueEffect]:
    """Per-player LAN-minus-online deviations, partially pooled.

    The observation is the difference of two weighted mean residuals and its
    variance is theirs summed, so a player with fourteen balanced rows carries a
    wide interval instead of a confident number. `empirical_bayes` then decides
    how much of the spread between players is real; where it decides none is,
    every player is shrunk to the common effect, and that is the finding rather
    than a failure to produce one.
    """
    _rate, weight, _ = opponent.response(panel, fit.feature)
    sides = _side_means(panel, context, fit.residual, weight, fit.mask)
    eligible = {
        player: stats
        for player, stats in sides.items()
        if min(stats[3], stats[4]) >= MIN_VENUE_SIDE and stats[3] + stats[4] >= MIN_VENUE_ROWS
    }
    if len(eligible) < 2:
        return []
    players = sorted(eligible)
    raw = np.asarray([eligible[p][0] - eligible[p][1] for p in players], dtype=np.float64)
    variances = np.asarray([eligible[p][2] for p in players], dtype=np.float64)
    common, tau2, pooled = hierarchical.empirical_bayes(raw, variances)

    prior: dict[int, float] = {}
    if without_opponent is not None:
        bare = _side_means(panel, context, without_opponent.residual, weight, without_opponent.mask)
        shared = [p for p in players if p in bare]
        if len(shared) >= 2:
            bare_raw = np.asarray([bare[p][0] - bare[p][1] for p in shared], dtype=np.float64)
            bare_var = np.asarray([bare[p][2] for p in shared], dtype=np.float64)
            _m, _t, bare_pooled = hierarchical.empirical_bayes(bare_raw, bare_var)
            prior = {p: float(v) for p, v in zip(shared, bare_pooled, strict=True)}

    # The posterior standard deviation of a normal-normal mean, from the τ² the
    # EM returned. Taking it from the spread of the shrunk values instead is the
    # circular version of the same formula and reports intervals far too narrow,
    # because those values have already had that spread taken out of them.
    out: list[VenueEffect] = []
    for index, player in enumerate(players):
        v = float(variances[index])
        se = math.sqrt(1.0 / (1.0 / v + (1.0 / tau2 if tau2 > 0.0 else 0.0)))
        value = float(pooled[index])
        deviation = value - common
        out.append(
            VenueEffect(
                player_id=player,
                feature=fit.feature,
                raw=float(raw[index]),
                pooled=value,
                deviation=deviation,
                common=common,
                standard_error=se,
                lo=deviation - 1.96 * se,
                hi=deviation + 1.96 * se,
                lan_maps=eligible[player][3],
                online_maps=eligible[player][4],
                pooled_before_opponent=prior.get(player),
            )
        )
    return out


# ----------------------------------------------------------- the host finding


@dataclass(frozen=True)
class HostEffect:
    """The home-market deviation for one cohort-feature, whichever way it lands."""

    key: tuple[int, int]
    feature: str
    coefficient: float
    standard_error: float
    lo: float
    hi: float
    in_cohort_sd: float
    host_lines: int
    # The same estimate restricted to venues in the franchise's own city, so a
    # finding does not rest on the twelve metropolitan-area judgement calls.
    clear_only: float | None


def host_effect(
    panel: opponent.Panel, context: Mapping[str, MapContext], fit: Fit
) -> HostEffect | None:
    """Read the host coefficient off a fit that carried the host family."""
    if "host_market" not in fit.labels:
        return None
    index = fit.labels.index("host_market")
    coefficient = float(fit.coefficients[index])
    error = float(fit.standard_errors[index])
    host_lines = sum(
        1
        for line in panel.lines
        if (entry := context.get(line.map_key)) is not None and line.team_id in entry.host_team_ids
    )
    # `host_market_judged` carries the extra deviation of the judgement calls, so
    # the clear-venue estimate is the base coefficient on its own.
    clear = coefficient if "host_market_judged" in fit.labels else None
    return HostEffect(
        key=fit.key,
        feature=fit.feature,
        coefficient=coefficient,
        standard_error=error,
        lo=coefficient - 1.96 * error,
        hi=coefficient + 1.96 * error,
        in_cohort_sd=coefficient / fit.cohort_sd if fit.cohort_sd > 0.0 else 0.0,
        host_lines=host_lines,
        clear_only=clear,
    )


# ------------------------------------------------------------------- coverage

# The Call of Duty League's first season. The two eras are reported apart
# because the venue contrast exists in one of them and not the other.
CDL_FIRST_SEASON = 2020


def league_of(season_year: int | None) -> str:
    return "CDL" if season_year is not None and season_year >= CDL_FIRST_SEASON else "CWL"


def coverage(
    panels: Mapping[tuple[int, int], opponent.Panel],
    context: Mapping[str, MapContext],
    seasons: Mapping[int, dict[str, Any]],
) -> dict[str, Any]:
    """What the record supports, per era, before any coefficient is read."""
    per_era: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    stakes: dict[str, int] = defaultdict(int)
    missing = 0
    for key, panel in panels.items():
        era_name = league_of(seasons[key[0]].get("year"))
        for line in panel.lines:
            entry = context.get(line.map_key)
            if entry is None:
                missing += 1
                continue
            bucket = per_era[era_name]
            bucket["lines"] += 1
            if entry.is_lan is True:
                bucket["lan"] += 1
            elif entry.is_lan is False:
                bucket["online"] += 1
            else:
                bucket["undecided"] += 1
            if entry.host_team_ids and line.team_id in entry.host_team_ids:
                bucket["host"] += 1
            stakes[entry.stakes] += 1
    return {
        "lines_without_context": missing,
        "by_era": {era: dict(counts) for era, counts in sorted(per_era.items())},
        "by_stakes": dict(sorted(stakes.items())),
    }


def _round(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def _percentiles(values: Sequence[float]) -> dict[str, float]:
    clean = [v for v in values if math.isfinite(v)]
    if not clean:
        return {}
    array = np.asarray(clean, dtype=np.float64)
    return {
        "n": len(clean),
        "median": round(float(np.median(array)), 5),
        "p90": round(float(np.percentile(array, 90)), 5),
        "max": round(float(np.max(np.abs(array))), 5),
    }


def summarize_ablation(rows: Sequence[AblationRow]) -> dict[str, dict[str, Any]]:
    """One entry per family, over every cohort-feature it was fitted on.

    A family that did nothing appears here with its zeros. It is not dropped,
    because the declaration said the nulls would be published and a table that
    only lists what worked is not the table that was declared.
    """
    by_family: dict[str, list[AblationRow]] = defaultdict(list)
    for row in rows:
        by_family[row.family].append(row)
    out: dict[str, dict[str, Any]] = {}
    for family in ABLATION_ORDER:
        group = by_family.get(family, [])
        if not group:
            out[family] = {"fits": 0, "verdict": "not fitted on any cohort"}
            continue
        deltas = [row.oof_rmse_delta for row in group]
        improved = sum(1 for d in deltas if d < 0.0)
        out[family] = {
            "fits": len(group),
            "leaderboard_move": _percentiles([row.mean_abs_dz for row in group]),
            "oof_rmse_delta": _percentiles(deltas),
            "cohorts_improved": improved,
            "cohorts_measured": len(deltas),
            "top_n_churn": sum(row.top_n_churn for row in group),
        }
    return out


# A family has to lower out-of-fold error on more than half the cohorts it was
# fitted on before its movement counts as signal. Declared with the ablation,
# before any fit ran.
KEEP_SHARE = 0.5

# Amendment, 2026-08-22. The declared rule counts cohorts and never asks how
# large the improvement was, so a family can cross `KEEP_SHARE` on a median
# improvement of 1e-5 — which `prize_pool` did, by one cohort, when the
# pre-2017 prize pools loaded. A count of wins that small is a count of
# rounding.
#
# It has to be said plainly that this floor was written after a result made the
# gap visible, because a threshold rewritten once a result is in is usually not
# a threshold. Two things keep it defensible. It is a floor on magnitude and
# not a change to the share, so it can only ever make the rule harder to pass,
# never easier. And the number is not chosen from the distribution it will be
# applied to: 0.01 cohort standard deviations is the magnitude this same
# function already used, as a bare literal, to separate "moves the table
# without predicting" from "does nothing either way". Naming that literal and
# applying it to both branches is what the amendment is. Any family that clears
# the share and moves the table less than the amount the rule already called
# negligible is reported as too small to keep.
#
# Both verdicts are published side by side for one release, the declared rule's
# and the amended rule's, with the effect size beside each. Nothing downstream
# reads either: `run_all` prints these and no box score is corrected from them.
MIN_EFFECT = 0.01
MIN_EFFECT_AMENDED_ON = "2026-08-22"


def verdicts(summary: Mapping[str, dict[str, Any]], min_effect: float = 0.0) -> dict[str, str]:
    """Kept or not, per family, against the rule declared before fitting.

    `min_effect` at 0.0 is the rule as declared. `MIN_EFFECT` is the amendment
    above. Both are computed every run and published together.
    """
    out: dict[str, str] = {}
    for family, stats in summary.items():
        measured = stats.get("cohorts_measured", 0)
        if not measured:
            out[family] = "not fitted"
            continue
        share = stats["cohorts_improved"] / measured
        moved = (stats.get("leaderboard_move") or {}).get("median", 0.0)
        if share > KEEP_SHARE and (moved or 0.0) >= min_effect:
            out[family] = "kept: lowers out-of-fold error on most cohorts"
        elif share > KEEP_SHARE:
            out[family] = (
                "dropped: clears the share on a move too small to keep "
                f"({moved:g} < {min_effect:g})"
            )
        elif moved and moved > MIN_EFFECT:
            out[family] = "dropped: moves the table without predicting"
        else:
            out[family] = "dropped: does nothing either way"
    return out


def effect_sizes(summary: Mapping[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The two numbers each verdict turns on, per family, so a reader can check
    the rule rather than take the word."""
    out: dict[str, dict[str, Any]] = {}
    for family, stats in summary.items():
        measured = stats.get("cohorts_measured", 0)
        out[family] = {
            "cohorts_improved": stats.get("cohorts_improved", 0),
            "cohorts_measured": measured,
            "share": (stats.get("cohorts_improved", 0) / measured) if measured else None,
            "median_leaderboard_move": (stats.get("leaderboard_move") or {}).get("median"),
        }
    return out


# ------------------------------------------------------------------ artifact


def _cohort_label(
    key: tuple[int, int], seasons: Mapping[int, dict[str, Any]], modes: Mapping[int, str]
) -> dict[str, Any]:
    season = seasons.get(key[0], {})
    return {
        "season": season.get("year"),
        "league": league_of(season.get("year")),
        "title": season.get("title"),
        "mode": modes.get(key[1], str(key[1])),
    }


def artifact(
    panels: Mapping[tuple[int, int], opponent.Panel],
    context: Mapping[str, MapContext],
    seasons: Mapping[int, dict[str, Any]],
    modes: Mapping[int, str],
    players: Mapping[int, str],
) -> dict[str, Any]:
    """The whole phase as one payload: the ablation, the venue finding, the host."""
    ablation: list[AblationRow] = []
    venue_rows: list[dict[str, Any]] = []
    host_rows: list[dict[str, Any]] = []
    map_rows: list[dict[str, Any]] = []
    per_cohort: list[dict[str, Any]] = []

    for key in sorted(panels, key=lambda k: (seasons[k[0]].get("year") or 0, modes.get(k[1], ""))):
        panel = panels[key]
        label = _cohort_label(key, seasons, modes)
        for feature in panel.features:
            if feature.key not in HEADLINE_FEATURES:
                continue
            rows = ablate(panel, context, feature.key)
            ablation.extend(rows)

            full = fit_cohort_feature(panel, context, feature.key, FAMILIES)
            if full is None:
                continue
            per_cohort.append(
                {
                    **label,
                    "feature": feature.key,
                    "lines": full.n_lines,
                    "ridge": full.ridge,
                    "cohort_sd": _round(full.cohort_sd, 5),
                    "map_tau2": _round(full.map_tau2, 8),
                    "maps_pooled": len(full.map_effects),
                    "coefficients": [
                        {
                            "term": name,
                            "family": family,
                            "value": _round(float(value), 5),
                            "se": _round(float(error), 5),
                            "in_cohort_sd": _round(
                                float(value) / full.cohort_sd if full.cohort_sd > 0 else 0.0, 4
                            ),
                        }
                        for name, family, value, error in zip(
                            full.labels,
                            full.family_of,
                            full.coefficients,
                            full.standard_errors,
                            strict=True,
                        )
                    ],
                }
            )

            for name, value in sorted(full.map_effects.items()):
                map_rows.append(
                    {**label, "feature": feature.key, "map": name, "effect": _round(value, 5)}
                )

            bare = fit_cohort_feature(panel, context, feature.key, FAMILIES, with_opponent=False)
            for effect in venue_effects(panel, context, full, without_opponent=bare):
                survived = (
                    effect.pooled / effect.pooled_before_opponent
                    if effect.pooled_before_opponent
                    else None
                )
                venue_rows.append(
                    {
                        **label,
                        "feature": effect.feature,
                        "player_id": effect.player_id,
                        "player": players.get(effect.player_id, str(effect.player_id)),
                        "raw": _round(effect.raw, 5),
                        "pooled": _round(effect.pooled, 5),
                        "deviation": _round(effect.deviation, 5),
                        "common": _round(effect.common, 5),
                        "se": _round(effect.standard_error, 5),
                        "lo": _round(effect.lo, 5),
                        "hi": _round(effect.hi, 5),
                        "in_cohort_sd": _round(
                            effect.pooled / full.cohort_sd if full.cohort_sd > 0 else 0.0, 4
                        ),
                        "common_in_cohort_sd": _round(
                            effect.common / full.cohort_sd if full.cohort_sd > 0 else 0.0, 4
                        ),
                        "lan_maps": effect.lan_maps,
                        "online_maps": effect.online_maps,
                        "survives_opponent": _round(survived, 4),
                        "clears_interval": bool(effect.lo > 0.0 or effect.hi < 0.0),
                    }
                )

            host = host_effect(panel, context, full)
            if host is not None:
                host_rows.append(
                    {
                        **label,
                        "feature": host.feature,
                        "coefficient": _round(host.coefficient, 5),
                        "se": _round(host.standard_error, 5),
                        "lo": _round(host.lo, 5),
                        "hi": _round(host.hi, 5),
                        "in_cohort_sd": _round(host.in_cohort_sd, 4),
                        "host_lines": host.host_lines,
                        "clears_interval": bool(host.lo > 0.0 or host.hi < 0.0),
                    }
                )

    summary = summarize_ablation(ablation)
    return {
        "version": VERSION,
        "adjusts": "box-score features, not the plus-minus",
        "claim": (
            "every term is a venue-associated or stage-associated deviation, "
            "adjusted for opposition and lineup; none of them is a cause"
        ),
        "families": list(FAMILIES),
        "ablation": {
            "declared_before_fitting": True,
            "keep_share": KEEP_SHARE,
            "min_effect": MIN_EFFECT,
            "min_effect_amended_on": MIN_EFFECT_AMENDED_ON,
            "by_family": summary,
            "effect_sizes": effect_sizes(summary),
            # The rule as declared, and the rule with the magnitude floor. Both,
            # because the floor was written after a result made the gap visible
            # and publishing only the amended table would hide that.
            "verdicts": verdicts(summary),
            "verdicts_with_min_effect": verdicts(summary, MIN_EFFECT),
            "by_cohort": [
                {
                    **_cohort_label(row.key, seasons, modes),
                    "feature": row.feature,
                    "family": row.family,
                    "mean_abs_dz": _round(row.mean_abs_dz, 5),
                    "p95_abs_dz": _round(row.p95_abs_dz, 5),
                    "top_n_churn": row.top_n_churn,
                    "oof_rmse": _round(row.oof_rmse, 5),
                    "oof_rmse_delta": _round(row.oof_rmse_delta, 6),
                    "n_players": row.n_players,
                }
                for row in ablation
            ],
        },
        "venue_effect": {
            "unit": "rate units, LAN minus online, pooled",
            "min_rows": MIN_VENUE_ROWS,
            "min_rows_per_side": MIN_VENUE_SIDE,
            "players": venue_rows,
            "n_clearing_interval": sum(1 for row in venue_rows if row["clears_interval"]),
            "n_players": len({row["player_id"] for row in venue_rows}),
        },
        "host_effect": {
            "unit": "rate units, home market minus elsewhere",
            "source": "hand-curated home markets; no source carries a team's city",
            "per_cohort": host_rows,
            "n_clearing_interval": sum(1 for row in host_rows if row["clears_interval"]),
        },
        "map_identity": {
            "pooling": "empirical Bayes over the maps in each cohort",
            "min_rows": MIN_MAP_ROWS,
            "per_cohort": map_rows,
        },
        "per_cohort": per_cohort,
        "coverage": coverage(panels, context, seasons),
        "stakes": {
            "levels": list(STAKES_LEVELS),
            "base": STAKES_REGULAR,
        },
        "params": {
            "context_grid": list(CONTEXT_GRID),
            "folds": FOLDS,
            "min_cohort_rows": MIN_COHORT_ROWS,
            "admission_maps": opponent.ADMISSION_MAPS,
            "qualify_maps": opponent.QUALIFY_MAPS,
        },
    }
