"""Player style axes, and the archetype taxonomy that isn't there.

Spec: /methodology#player-style.

Every roster page in this sport is written in nouns. A team has an anchor, an
entry, a flex, an objective player; a signing is explained by the role it
fills. The nouns are useful shorthand and they may still be true of how teams
*play*. What this module asks is narrower and answerable: do the box scores
this archive actually holds fall into groups, or into a cloud?

**The null is that there are no groups.** Roles could be discrete — a handful
of ways to play, each with its own statistical signature, players sitting near
one of a few centres. Or style could be continuous — a few axes along which
players vary smoothly, with every intermediate position occupied and no gaps to
cut between. Those are different claims about the same cloud of points, and
clustering cannot tell them apart on its own: k-means returns k clusters for
any k you ask, on any data, including data with no structure whatsoever. A
partition is not evidence. What is published here is the comparison between the
partition this archive gives up and the partition the *same cloud with no
groups in it* gives up.

**Two things have to be removed before the question is even well posed.**

The first is quality. Cluster raw box scores and the first thing found is that
good players are good: the leading axis is "more kills, better ratio, larger
share", every metric loading the same way, and the "archetypes" come out as
tiers. That is a rating, and this site already has one. So every feature is
first residualised against the published composite rating, and what is
clustered is what is left — how a player played at their level, not what level
they played at. The rating turns out to explain 11.0% to 16.8% of the variance in
these features depending on the era, which is worth stating plainly: style and
quality are very nearly orthogonal here, and almost nothing is lost by insisting
on the distinction.

The second is the era. Metric coverage is not flat across this archive — the
kill feed exists for two titles of fourteen, Hardpoint qualification runs from
50% of Infinite Warfare's players to 86% of WWII's, and Search and Destroy's
per-10-minute metrics are unattainable in the titles whose rounds are too short
to earn a deep streak. Take every metric that appears in every season and
demand a complete row, and *no player-season in the archive qualifies*: the
feature set that looks richest describes nobody. Worse, the rows that survive a
looser cut are not a random sample — they skew to the seasons with better
coverage and to the players who played more, so a cluster fitted on them can be
an era wearing a costume. Columns are therefore admitted only if they are
attainable in every season of the era being fitted, and the published fit per
era runs on the basis that keeps essentially the whole field rather than the one
with the most columns. The numbers each basis reports live in the artifact and
on /methodology#player-style; they move on every rerun and are not repeated
here.

**The answer is that there is no taxonomy, on any basis this has been run on.**
The gap statistic prefers k=1 — a single blob beats every partition from two to
seven — and the best silhouette any k achieves sits inside the band a single
Gaussian with the same covariance and sample size scores on the same test.
Bootstrap stability at k=2 is high and means nothing on its own, which is the
trap this module exists to avoid: bisecting an elongated cloud along its long
axis is enormously reproducible, and the Gaussian null reproduces itself just as
well. The extended bases agree with the core ones.

**What is real is the axes.** Horn's parallel analysis — eigenvalues against
the same matrix with every column permuted, which destroys correlation while
preserving each metric's marginal shape — retains a handful of components of the
residual. They are recognisable and they are continuous: volume, survival, and
where the basket is rich enough to carry them, streak depth and risk. A player
is published as a position on those axes, not as a label. That is a weaker claim
than "anchor" and it is the one the data supports; it is also the more useful one
for a career page, because a position moves and a label does not.

**A null is only as good as its power.** Every verdict here is stated against
the no-cluster null it was measured on, with that null's own spread, so
"no taxonomy" is always "no taxonomy separated by more than an unclustered
cloud of this size and shape would show". At these sample sizes a
well-separated three-group structure is found easily — see
tests/test_style.py, which plants one and requires this code to recover it.
What this archive rules out is groups of that kind. It does not rule out roles
too subtle for these columns to see, and no such claim is made.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, cast

import numpy as np
import psycopg
from numpy.typing import NDArray

from .ratings.preflight import era_labels

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int_]

MODEL = "player_style"
VERSION = "1.1.0"

# The metric layer's column set changes wholesale at the archive seam — the CWL
# years carry the extras/kill-feed catalog, the CDL years the Cito box columns —
# so one league-wide basis would be reduced to the few columns both eras share.
# Each era is fitted on its own complete rectangle instead; a player-season
# belongs to exactly one era, so the published axis rows never collide.
# Bases are named for the era they were fitted on: the league where every
# season in the archive ran under it, and the span where they did not.

# A basis below this many complete-case player-seasons is not fitted at all.
MIN_BASIS_N = 50

# A column joins a basis only if it is attainable this often in *every* season.
# Below it, a metric that exists in one title and is structurally unreachable in
# another (deep streaks in Search and Destroy) would make the complete-case rows
# an era rather than a sample.
MIN_SEASON_COVERAGE = 0.40

# Cluster counts examined. Seven is past any taxonomy anybody argues for in a
# four-player-per-team sport, and the gap statistic needs the far end to show
# that its preference for k=1 is not just the near end being noisy.
K_MAX = 7

# k-means restarts. The objective is non-convex, so a single run reports the
# basin it fell into rather than the partition; twenty restarts is where the
# best-of stops improving on this data.
RESTARTS = 20
KMEANS_ITERS = 200

# Reference draws. GAP_B uniform boxes for the gap statistic, NULL_R Gaussian
# replicates for the silhouette and stability nulls, BOOT_B resamples per
# stability estimate.
GAP_B = 50
NULL_R = 25
BOOT_B = 100
HORN_R = 200

# Fixed seeds: every number on the site has to come back the same on a rerun.
SEED = 20180819  # CWL Champs 2018 grand final; any fixed seed works

# A component is retained if its eigenvalue clears this percentile of the
# permuted null. Horn's original rule is the mean; the 95th is the conservative
# form and is what a null-publishing module should use.
HORN_PCTL = 95.0

# Named for what they load on, not for a role, and named in the direction the
# score runs: a positive `survival` score is a player who dies less, not more.
# Metrics whose catalog entry says lower is better are flipped on the way in, so
# a loading has to be read back through that flip before it is named — the risk
# axis loads +0.45 on the flipped team-kill column, which is *more* team kills,
# not fewer. See the module docstring.
#
# Each name declares the column its axis loads hardest on, with aliases where
# the catalog carries one quantity under two keys, and is given to the component
# whose top loading is one of them. Naming by position instead is what broke:
# the core basis grew from 21 columns to 26, Horn's test began retaining five
# components rather than four, an assists axis moved into third place, and three
# published names slid one seat down a career's axes without a number changing.
#
# The sign is deliberately not part of the match. `horn_components` pins each
# component's largest loading positive, so a top loading is positive by
# construction and an expected sign would assert something already guaranteed.
AXIS_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("volume", ("kills",)),
    ("survival", ("deaths",)),
    ("streak depth", ("deep_streak_rate", "streak6", "streak7")),
    ("risk", ("eight_plus_streaks", "streak8plus")),
)

# Dropped from a column key before it is matched: the mode prefix, so an
# extended basis's per-mode duplicates read as the quantity they measure, and
# the denominator, so a name survives the per-10-minute/per-map fork the CDL
# box scores introduced.
DENOMINATOR_SUFFIXES = ("_p10", "_pm", "_pr", "_total")


def params() -> dict[str, Any]:
    return {
        "min_season_coverage": MIN_SEASON_COVERAGE,
        "k_max": K_MAX,
        "restarts": RESTARTS,
        "gap_b": GAP_B,
        "null_r": NULL_R,
        "boot_b": BOOT_B,
        "horn_r": HORN_R,
        "horn_pctl": HORN_PCTL,
        "seed": SEED,
        "quality": "player_rating composite, all-modes row, residualised out",
    }


# ---------------------------------------------------------------- feature grid


@dataclass(frozen=True)
class Subject:
    player_id: int
    season_id: int


@dataclass(frozen=True)
class Column:
    mode_id: int  # 0 = all modes
    metric: str

    @property
    def key(self) -> str:
        return f"{self.mode_id}:{self.metric}"


@dataclass
class Grid:
    """The raw player-season x metric matrix of era-scored z values.

    `values` carries NaN wherever a player-season did not qualify for that
    metric. Nothing is imputed: which cells are missing is itself one of the
    findings, so it is reported rather than filled.
    """

    subjects: list[Subject]
    columns: list[Column]
    values: FloatArray
    season_of: IntArray
    rating: FloatArray  # NaN where the player-season has no published rating
    league: str
    year_of: dict[int, int]  # season id -> calendar year

    @property
    def years(self) -> list[int]:
        return sorted({self.year_of[int(s)] for s in self.season_of.tolist()})

    @property
    def era(self) -> str:
        years = self.years
        return f"{years[0]}–{years[-1]}" if years else ""

    @property
    def present(self) -> NDArray[np.bool_]:
        return ~np.isnan(self.values)

    def season_coverage(self, j: int) -> dict[int, float]:
        out: dict[int, float] = {}
        for s in sorted(set(self.season_of.tolist())):
            sel = self.season_of == s
            out[int(s)] = float(self.present[sel, j].mean())
        return out

    def eligible(self) -> NDArray[np.bool_]:
        """Columns attainable in every season. See MIN_SEASON_COVERAGE."""
        return np.array(
            [
                min(self.season_coverage(j).values(), default=0.0) >= MIN_SEASON_COVERAGE
                for j in range(len(self.columns))
            ]
        )


FEATURE_SQL = """
SELECT p.player_id, p.season_id, COALESCE(p.mode_id, 0), p.metric, p.z
FROM player_metric_season p
WHERE p.run_id = %(run)s AND p.qualified AND p.z IS NOT NULL
"""

RATING_SQL = """
SELECT player_id, season_id, rating
FROM player_season_adjusted
WHERE run_id = %(run)s AND mode_id IS NULL AND rating IS NOT NULL
"""

# A season's league, and which box-score archive its maps came from. The archive
# is what decides which columns exist, so it decides the era; the league alone
# does not. `mode()` takes the archive that supplied the season, and a season
# with no box score at all gets none and is dropped by the join below.
SEASONS_SQL = """
SELECT se.id, se.year, se.league,
       mode() WITHIN GROUP (ORDER BY gps.data_source) AS source
FROM seasons se
JOIN events ev ON ev.season_id = se.id
JOIN series s  ON s.event_id = ev.id
JOIN games g   ON g.series_id = s.id
JOIN game_player_stats gps ON gps.game_id = g.id
GROUP BY se.id, se.year, se.league
ORDER BY se.year
"""


@dataclass(frozen=True)
class Era:
    """One archive's seasons, inside one league. Never fitted across a seam.

    The metric layer's columns change wholesale where the archives meet — the
    CWL years carry the kill-feed and extras catalog, the CDL years the Cito box
    columns — so a single league-wide basis would be cut down to the handful of
    columns both sides happen to share.

    The league does not locate that seam, and grouping by it put one on the
    wrong side. A seam runs through the middle of the CWL league: 2016 is
    transcribed from the wiki and carries thirteen metrics, 2017 onward is the
    Activision archive and carries sixty. Grouped by league, 2016 cut the CWL
    basis down to the columns it shares with the three years after it, and the
    basis fell from five retained components to two.

    So the grouping is the archive, which is the thing that decides the columns.
    Each group takes its name from `preflight.era_labels`, the one rule this
    project names an era by: the league where every season in the archive ran
    under it, and the span where they did not.
    """

    league: str
    season_ids: frozenset[int]
    year_of: dict[int, int]
    source: str = ""


def load_eras(conn: psycopg.Connection[tuple[object, ...]]) -> list[Era]:
    rows = [
        (cast(int, sid), cast(int, year), cast(str, league), cast(str, source))
        for sid, year, league, source in conn.execute(SEASONS_SQL)
    ]
    labels = era_labels(rows)
    by_source: dict[str, dict[int, tuple[int, str]]] = {}
    for sid, year, league, source in rows:
        by_source.setdefault(source, {})[sid] = (year, league)
    out = []
    for source, seasons in sorted(
        by_source.items(), key=lambda kv: min(y for y, _ in kv[1].values())
    ):
        out.append(
            Era(
                league=labels[source],
                season_ids=frozenset(seasons),
                year_of={sid: year for sid, (year, _league) in seasons.items()},
                source=source,
            )
        )
    return out


def load_grid(
    conn: psycopg.Connection[tuple[object, ...]],
    metric_run: int,
    rating_run: int,
    orientation: dict[str, bool],
    era: Era,
) -> Grid:
    """Build the feature grid from the metric layer.

    The grid covers one era's seasons, and only metrics present in every one of
    them are eligible at all. `orientation` flips the metrics whose catalog
    entry says lower is better, so that a positive z always reads as more of
    the thing the metric names.
    """
    raw = [
        (cast(int, pid), cast(int, sid), cast(int, m), cast(str, k), cast(float, z))
        for pid, sid, m, k, z in conn.execute(FEATURE_SQL, {"run": metric_run})
        if cast(int, sid) in era.season_ids
    ]
    seasons_present = {sid for _, sid, _, _, _ in raw}
    seasons_of_metric: dict[tuple[int, str], set[int]] = {}
    for _, sid, m, k, _ in raw:
        seasons_of_metric.setdefault((m, k), set()).add(sid)
    stable = {mk for mk, seen in seasons_of_metric.items() if seen == seasons_present}
    rows = [r for r in raw if (r[2], r[3]) in stable]
    columns = sorted({Column(m, k) for _, _, m, k, _ in rows}, key=lambda c: (c.mode_id, c.metric))
    subjects = sorted(
        {Subject(p, s) for p, s, _, _, _ in rows}, key=lambda s: (s.season_id, s.player_id)
    )
    cidx = {c: i for i, c in enumerate(columns)}
    ridx = {s: i for i, s in enumerate(subjects)}
    values = np.full((len(subjects), len(columns)), np.nan)
    for pid, sid, m, k, z in rows:
        sign = 1.0 if orientation.get(k, True) else -1.0
        values[ridx[Subject(pid, sid)], cidx[Column(m, k)]] = z * sign

    ratings = {
        (cast(int, p), cast(int, s)): cast(float, r)
        for p, s, r in conn.execute(RATING_SQL, {"run": rating_run})
    }
    rating = np.array([ratings.get((s.player_id, s.season_id), np.nan) for s in subjects])
    season_of = np.array([s.season_id for s in subjects])
    return Grid(subjects, columns, values, season_of, rating, era.league, era.year_of)


@dataclass
class Basis:
    """A complete-case rectangle of the grid: which columns, which subjects."""

    name: str
    column_idx: list[int]
    row_idx: list[int]
    grid: Grid

    @property
    def columns(self) -> list[Column]:
        return [self.grid.columns[j] for j in self.column_idx]

    @property
    def subjects(self) -> list[Subject]:
        return [self.grid.subjects[i] for i in self.row_idx]

    @property
    def n(self) -> int:
        return len(self.row_idx)

    def matrix(self) -> FloatArray:
        return self.grid.values[np.ix_(self.row_idx, self.column_idx)]

    def rating(self) -> FloatArray:
        return self.grid.rating[self.row_idx]

    def coverage(self) -> list[dict[str, Any]]:
        """Per-season retention, which is how an era bias would show itself."""
        kept = set(self.row_idx)
        out = []
        for s in sorted(set(self.grid.season_of.tolist())):
            idx = [i for i in range(len(self.grid.subjects)) if self.grid.season_of[i] == s]
            n_kept = sum(1 for i in idx if i in kept)
            out.append(
                {
                    "season_id": int(s),
                    "eligible": len(idx),
                    "kept": n_kept,
                    "share": n_kept / len(idx) if idx else 0.0,
                }
            )
        return out


def build_basis(grid: Grid, name: str, modes: tuple[int, ...]) -> Basis:
    eligible = grid.eligible()
    cols = [j for j, c in enumerate(grid.columns) if c.mode_id in modes and bool(eligible[j])]
    present = grid.present
    rows = [
        i
        for i in range(len(grid.subjects))
        if bool(present[i, cols].all()) and not math.isnan(float(grid.rating[i]))
    ]
    # Ordered by what each player-season measured rather than by the id it was
    # loaded under. Three things downstream draw by row position — the parallel
    # analysis permutes columns, k-means++ seeds on rows, and the stability
    # bootstrap resamples them — so with rows in `player_id` order the retained
    # component count and the cluster stability were partly a function of the
    # loader's numbering. Subjects, matrix and rating all read through `row_idx`,
    # so they permute together and a score stays with its player.
    rows.sort(key=lambda i: (*grid.values[i, cols].tolist(), float(grid.rating[i])))
    return Basis(name, cols, rows, grid)


# ------------------------------------------------------- quality and structure


def standardize(x: FloatArray) -> FloatArray:
    sd = x.std(axis=0)
    sd = np.where(sd > 0, sd, 1.0)
    return (x - x.mean(axis=0)) / sd


@dataclass
class Residual:
    matrix: FloatArray  # standardized residual features
    quality_share: float  # variance the composite rating alone explains


def residualize_quality(x: FloatArray, rating: FloatArray) -> Residual:
    """Project the published composite rating out of every feature.

    What survives is how a player played at their own level. The share removed
    is reported because it is the answer to "are these archetypes just tiers".
    """
    z = standardize(x)
    q = standardize(rating.reshape(-1, 1))
    design = np.column_stack([np.ones(len(z)), q])
    beta = np.linalg.lstsq(design, z, rcond=None)[0]
    resid = z - design @ beta
    share = 1.0 - float(resid.var(axis=0).sum() / z.var(axis=0).sum())
    return Residual(standardize(resid), share)


@dataclass
class Components:
    scores: FloatArray  # n x k component scores
    loadings: FloatArray  # k x p
    eigenvalues: FloatArray
    null95: FloatArray
    n_retained: int
    share: list[float]  # retained share of total residual variance


def horn_components(
    x: FloatArray, rng: np.random.Generator, replicates: int = HORN_R
) -> Components:
    """PCA with Horn's parallel analysis for how many components are real.

    The null permutes each column independently. That leaves every metric's own
    distribution untouched and destroys only the correlation between them, so an
    eigenvalue that clears it is evidence of shared structure rather than of a
    heavy-tailed marginal.
    """
    centred = x - x.mean(axis=0)
    u, s, vt = np.linalg.svd(centred, full_matrices=False)
    n = x.shape[0]
    ev = s**2 / (n - 1)
    null = np.empty((replicates, len(ev)))
    for r in range(replicates):
        perm = np.column_stack([rng.permutation(x[:, j]) for j in range(x.shape[1])])
        sp = np.linalg.svd(perm - perm.mean(axis=0), compute_uv=False)
        null[r] = sp**2 / (n - 1)
    null95 = np.percentile(null, HORN_PCTL, axis=0)
    keep = int(np.sum(ev > null95))
    keep = max(keep, 1)

    # An SVD fixes each component only up to a sign, so the same data can
    # publish "high volume" as +2 today and -2 on a rerun. Pin it: the loading
    # with the largest magnitude is made positive, which is deterministic and
    # makes a score read in the direction the axis is named for.
    loadings = vt[:keep].copy()
    scores = (u[:, :keep] * s[:keep]).copy()
    for i in range(keep):
        if loadings[i, int(np.argmax(np.abs(loadings[i])))] < 0:
            loadings[i] *= -1.0
            scores[:, i] *= -1.0

    return Components(
        scores=scores,
        loadings=loadings,
        eigenvalues=ev,
        null95=null95,
        n_retained=keep,
        share=[float(e / ev.sum()) for e in ev[:keep]],
    )


# ----------------------------------------------------------------- clustering


@dataclass
class Fit:
    inertia: float
    labels: IntArray
    centres: FloatArray


def kmeans(x: FloatArray, k: int, rng: np.random.Generator, restarts: int = RESTARTS) -> Fit:
    """k-means++ with restarts. Deterministic given the generator."""
    best: Fit | None = None
    for _ in range(restarts):
        centres = [x[rng.integers(len(x))]]
        for _ in range(k - 1):
            d = np.min(((x[:, None, :] - np.array(centres)[None]) ** 2).sum(-1), axis=1)
            total = d.sum()
            p = (d / total) if total > 0 else None
            centres.append(x[rng.choice(len(x), p=p)])
        c = np.array(centres)
        labels = np.zeros(len(x), dtype=int)
        for _ in range(KMEANS_ITERS):
            labels = np.argmin(((x[:, None, :] - c[None]) ** 2).sum(-1), axis=1)
            nxt = np.array(
                [x[labels == j].mean(axis=0) if np.any(labels == j) else c[j] for j in range(k)]
            )
            if np.allclose(nxt, c):
                break
            c = nxt
        inertia = float(((x - c[labels]) ** 2).sum())
        if best is None or inertia < best.inertia:
            best = Fit(inertia, cast(IntArray, labels), cast(FloatArray, c))
    assert best is not None
    return best


def silhouette(x: FloatArray, labels: IntArray, k: int) -> float:
    """Mean silhouette width. NaN at k=1, where it is undefined rather than 0."""
    if k < 2:
        return float("nan")
    d = np.sqrt(((x[:, None, :] - x[None]) ** 2).sum(-1))
    out = np.zeros(len(x))
    for i in range(len(x)):
        own = labels == labels[i]
        own[i] = False
        a = float(d[i, own].mean()) if own.any() else 0.0
        others = [
            float(d[i, labels == j].mean())
            for j in range(k)
            if j != labels[i] and np.any(labels == j)
        ]
        if not others:
            continue
        b = min(others)
        out[i] = (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return float(out.mean())


def gap_statistic(
    x: FloatArray, k: int, rng: np.random.Generator, replicates: int = GAP_B
) -> tuple[float, float]:
    """Tibshirani's gap: log inertia against a uniform box of the same extent.

    Returns the gap and its s_k, which carries the reference spread and the
    1/B correction, so the 1-SE rule can be applied across k.
    """
    fit = kmeans(x, k, rng)
    lo, hi = x.min(axis=0), x.max(axis=0)
    refs = np.array(
        [
            math.log(kmeans(rng.uniform(lo, hi, size=x.shape), k, rng, restarts=5).inertia)
            for _ in range(replicates)
        ]
    )
    gap = float(refs.mean() - math.log(fit.inertia))
    sk = float(refs.std() * math.sqrt(1.0 + 1.0 / replicates))
    return gap, sk


def jaccard_stability(
    x: FloatArray, k: int, rng: np.random.Generator, replicates: int = BOOT_B
) -> list[float]:
    """Hennig's bootstrap cluster stability: per-cluster mean Jaccard.

    Published beside its own null, never alone. A reproducible partition of an
    unclustered cloud is still reproducible, which is exactly the failure this
    module is built to avoid asserting.
    """
    reference = kmeans(x, k, rng)
    ref = [set(np.where(reference.labels == j)[0].tolist()) for j in range(k)]
    best = np.zeros((replicates, k))
    for b in range(replicates):
        idx = rng.integers(0, len(x), len(x))
        boot = kmeans(x[idx], k, rng, restarts=5)
        assigned = np.argmin(((x[:, None, :] - boot.centres[None]) ** 2).sum(-1), axis=1)
        cand = [set(np.where(assigned == j)[0].tolist()) for j in range(k)]
        for j in range(k):
            best[b, j] = max(
                (len(ref[j] & c) / len(ref[j] | c)) if (ref[j] | c) else 0.0 for c in cand
            )
    return [float(v) for v in best.mean(axis=0)]


@dataclass
class NullBand:
    """What a cloud with no groups in it scores on the same test."""

    lo: float
    hi: float
    mean: float

    def contains(self, value: float) -> bool:
        return self.lo <= value <= self.hi

    def payload(self) -> dict[str, float]:
        return {"lo": self.lo, "hi": self.hi, "mean": self.mean}


def gaussian_null(
    x: FloatArray, k: int, rng: np.random.Generator, replicates: int = NULL_R
) -> tuple[NullBand, NullBand]:
    """Silhouette and stability of a single Gaussian matching x's covariance.

    This is the calibration the whole verdict rests on. k-means will split any
    cloud; the question is whether the split it finds here beats the split it
    finds in a cloud built to have no splits, at the same n, the same
    dimension and the same elongation.
    """
    cov = np.atleast_2d(np.cov(x, rowvar=False))
    mean = np.zeros(x.shape[1])
    sils, stabs = [], []
    for _ in range(replicates):
        y = rng.multivariate_normal(mean, cov, size=len(x))
        fit = kmeans(y, k, rng, restarts=5)
        sils.append(silhouette(y, fit.labels, k))
        stabs.append(min(jaccard_stability(y, k, rng, replicates=20)))
    return (
        NullBand(float(min(sils)), float(max(sils)), float(np.mean(sils))),
        NullBand(float(min(stabs)), float(max(stabs)), float(np.mean(stabs))),
    )


@dataclass
class KResult:
    k: int
    gap: float
    sk: float
    silhouette: float
    stability: list[float]
    silhouette_null: NullBand | None
    stability_null: NullBand | None

    @property
    def beats_null(self) -> bool:
        """A k is only a taxonomy if it clears the no-cluster null on both."""
        if self.silhouette_null is None or self.stability_null is None:
            return False
        return (
            self.silhouette > self.silhouette_null.hi
            and min(self.stability) > self.stability_null.hi
        )

    def payload(self) -> dict[str, Any]:
        return {
            "k": self.k,
            "gap": self.gap,
            "s_k": self.sk,
            "silhouette": None if math.isnan(self.silhouette) else self.silhouette,
            "stability": self.stability,
            "silhouette_null": self.silhouette_null.payload() if self.silhouette_null else None,
            "stability_null": self.stability_null.payload() if self.stability_null else None,
            "beats_null": self.beats_null,
        }


def gap_choice(results: list[KResult]) -> int:
    """Tibshirani's 1-SE rule: the smallest k whose gap is within one s_k of
    the next k's. k=1 winning is the null holding."""
    for i in range(len(results) - 1):
        if results[i].gap >= results[i + 1].gap - results[i + 1].sk:
            return results[i].k
    return results[-1].k


def assess(x: FloatArray, seed: int = SEED, k_max: int = K_MAX) -> tuple[list[KResult], int, int]:
    """Run every validity test over k, and return the results with both verdicts.

    The two tests answer different questions and are not interchangeable. The
    gap statistic asks *how many* groups, and is the only one entitled to pick a
    k. The Gaussian null asks whether there are any groups at all, and is the
    only one entitled to say no. So the order is: let the gap statistic choose,
    then require its choice to beat a cloud with no groups in it.

    Reading them the other way around — taking the smallest k that clears the
    null — gets three well-separated groups wrong, because cutting three groups
    in two is also better separated than a Gaussian. Two is a true statement
    about that data and the wrong answer to the question asked.

    Returns (per-k results, the gap statistic's k, the published k — 1 when the
    chosen partition cannot clear the no-cluster null).
    """
    results: list[KResult] = []
    for k in range(1, k_max + 1):
        rng = np.random.default_rng(seed + k)
        gap, sk = gap_statistic(x, k, rng)
        fit = kmeans(x, k, rng)
        sil = silhouette(x, fit.labels, k)
        stab = jaccard_stability(x, k, rng) if k >= 2 else [1.0]
        sil_null, stab_null = (
            gaussian_null(x, k, np.random.default_rng(seed + 1000 + k)) if k >= 2 else (None, None)
        )
        results.append(KResult(k, gap, sk, sil, stab, sil_null, stab_null))
    gap_k = gap_choice(results)
    chosen = next((r for r in results if r.k == gap_k), None)
    published = gap_k if chosen is not None and gap_k >= 2 and chosen.beats_null else 1
    return results, gap_k, published


# ------------------------------------------------------------------ artifacts


def marker_column(key: str) -> str:
    """A loading's column reduced to the quantity it measures."""
    rest = key.rpartition(":")[2]
    for suffix in DENOMINATOR_SUFFIXES:
        if rest.endswith(suffix):
            return rest[: -len(suffix)]
    return rest


def axis_names(loadings: FloatArray, columns: Sequence[Column]) -> list[str]:
    """Name each retained component by the column it loads hardest on.

    Names belong to a basis's components, not to a position: the extended basis
    retains ten and they are not the core's five with five more behind them —
    its fourth is objective play, which the core cannot see at all. Assignment
    is one-to-one and greedy down the components, so the strongest component
    with a claim on a name gets it; a component whose top loading matches no
    marker, or whose marker is already taken, stays numbered. That is the honest
    outcome and it is why the CDL bases, which no one has read loadings off,
    can be run through the same function rather than left nameless.
    """
    taken: set[str] = set()
    names: list[str] = []
    for i in range(loadings.shape[0]):
        top = marker_column(columns[int(np.argmax(np.abs(loadings[i])))].key)
        name = next(
            (n for n, markers in AXIS_MARKERS if n not in taken and top in markers),
            None,
        )
        if name is not None:
            taken.add(name)
        names.append(name or f"axis {i + 1}")
    return names


def _percentiles(v: FloatArray) -> FloatArray:
    order = np.argsort(np.argsort(v))
    return (order + 0.5) / len(v)


@dataclass
class BasisFit:
    basis: Basis
    residual: Residual
    components: Components
    results: list[KResult]
    gap_k: int
    surviving_k: int

    def payload(self) -> dict[str, Any]:
        names = axis_names(self.components.loadings, self.basis.columns)
        return {
            "basis": self.basis.name,
            "league": self.basis.grid.league,
            "era": self.basis.grid.era,
            "years": self.basis.grid.years,
            "n": self.basis.n,
            "n_columns": len(self.basis.column_idx),
            "columns": [c.key for c in self.basis.columns],
            "coverage": self.basis.coverage(),
            "quality_share": self.residual.quality_share,
            "n_components": self.components.n_retained,
            "component_share": self.components.share,
            "eigenvalues": [float(v) for v in self.components.eigenvalues[:12]],
            "null95": [float(v) for v in self.components.null95[:12]],
            "axes": [
                {
                    "index": i + 1,
                    "name": names[i],
                    "share": self.components.share[i],
                    "loadings": sorted(
                        (
                            {"column": c.key, "loading": float(self.components.loadings[i, j])}
                            for j, c in enumerate(self.basis.columns)
                        ),
                        key=lambda d: -abs(cast(float, d["loading"])),
                    )[:10],
                }
                for i in range(self.components.n_retained)
            ],
            "clustering": [r.payload() for r in self.results],
            "gap_k": self.gap_k,
            "surviving_k": self.surviving_k,
            "taxonomy": self.surviving_k >= 2,
        }


def fit_basis(basis: Basis, seed: int = SEED) -> BasisFit:
    resid = residualize_quality(basis.matrix(), basis.rating())
    comps = horn_components(resid.matrix, np.random.default_rng(seed))
    results, gap_k, surviving = assess(comps.scores, seed=seed)
    return BasisFit(basis, resid, comps, results, gap_k, surviving)


def axis_rows(fit: BasisFit) -> list[tuple[int, int, int, float, float]]:
    """(player_id, season_id, axis, score, pctl) for the published basis."""
    rows: list[tuple[int, int, int, float, float]] = []
    scores = fit.components.scores
    for a in range(fit.components.n_retained):
        pctl = _percentiles(scores[:, a])
        for i, subject in enumerate(fit.basis.subjects):
            rows.append(
                (subject.player_id, subject.season_id, a + 1, float(scores[i, a]), float(pctl[i]))
            )
    return rows


def build_artifacts(
    conn: psycopg.Connection[tuple[object, ...]],
    metric_run: int,
    rating_run: int,
    orientation: dict[str, bool],
    seed: int = SEED,
) -> tuple[dict[str, Any], list[BasisFit]]:
    """Fit every era's bases and return (artifacts, the published fits).

    Within an era the core basis is published because it keeps the league; the
    extended basis is fitted and reported as the robustness arm, since it is the
    one carrying objective play and also the one whose coverage is season-skewed.
    """
    published: list[BasisFit] = []
    all_fits: list[BasisFit] = []
    eras: list[dict[str, Any]] = []
    for era in load_eras(conn):
        grid = load_grid(conn, metric_run, rating_run, orientation, era)
        core = build_basis(grid, f"core {era.league}", (0,))
        if core.n < MIN_BASIS_N:
            continue
        core_fit = fit_basis(core, seed)
        published.append(core_fit)
        all_fits.append(core_fit)
        extended = build_basis(grid, f"extended {era.league}", (0, 1, 2))
        if extended.n >= MIN_BASIS_N:
            all_fits.append(fit_basis(extended, seed))
        eras.append(
            {
                "league": era.league,
                "era": grid.era,
                "years": grid.years,
                "published_basis": core.name,
                "n_subjects_total": len(grid.subjects),
                "ineligible_columns": [
                    {"column": c.key, "coverage": grid.season_coverage(j)}
                    for j, c in enumerate(grid.columns)
                    if not grid.eligible()[j]
                ],
            }
        )
    if not published:
        return {}, []
    artifacts = {
        "player_style": {
            "min_season_coverage": MIN_SEASON_COVERAGE,
            "published_bases": [f.basis.name for f in published],
            "eras": eras,
            "bases": [f.payload() for f in all_fits],
        }
    }
    return artifacts, published


def headline(fit: BasisFit) -> str:
    era = fit.basis.grid.era
    if fit.surviving_k >= 2:
        return (
            f"{era}: {fit.surviving_k} archetypes survive the no-cluster null "
            f"on {fit.basis.n} player-seasons"
        )
    best = max((r for r in fit.results if r.k >= 2), key=lambda r: r.silhouette)
    assert best.silhouette_null is not None
    return (
        f"{era}: no archetypes — the gap statistic prefers k={fit.gap_k}, and the best "
        f"partition (k={best.k}, silhouette {best.silhouette:.3f}) sits inside what an "
        f"unclustered cloud scores ({best.silhouette_null.lo:.3f}-{best.silhouette_null.hi:.3f}). "
        f"{fit.components.n_retained} continuous style axes instead, on "
        f"{fit.basis.n} player-seasons"
    )


def data_through(conn: psycopg.Connection[tuple[object, ...]]) -> date:
    row = conn.execute("SELECT max(played_at)::date FROM series").fetchone()
    assert row is not None and row[0] is not None
    return cast(date, row[0])
