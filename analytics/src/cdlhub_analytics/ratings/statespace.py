"""Plus-minus with a time axis. Spec: /methodology#season-rapm.

The published plus-minus gives a player one coefficient for their whole career,
which cannot express a peak, a decline, or a player who changed. This module
gives them one per time cell, and the pre-flight in `preflight.py` decides how
fine that cell is allowed to be — measured, per era, rather than chosen here.

**What the pre-flight settled, and it is not one answer.** Every row of the
design is one lineup against another, so the row space is spanned by lineup
differences and the rank can never exceed distinct lineups minus the components
of the lineup graph. The CDL era clears two of the three stop clauses and earns
season resolution; the CWL era trips all three — its median team-season carries
exactly one lineup, and a simulated league at that variety recovers *nothing*
about who inside it was worth more — so 2017-2019 is pooled to one coefficient
per player per era. `resolutions` reads that verdict rather than restating it,
so a record that grows into season resolution for CWL gets it without an edit.

**The team-season effect is not optional.** Without it, team quality has nowhere
to go but into the four player columns, and the minimum-norm solution divides it
equally: "this was a good team" is published as "these were four good players".
With it, a player coefficient is a *deviation from their team-season*, weakly
identified where churn is thin and honestly so. The simulation says that
deviation is recovered at r ≈ 0.29 for the CDL era and that this is noise-bound
rather than churn-bound — more roster movement than any real team-season has
tops out near 0.33. So a season coefficient is a noisy deviation from a team. It
is not a ranking of four players and nothing here presents it as one.

**Three population rules, not two.**

- *Column admission* — a (player, cell) with fewer than ADMISSION_MAPS maps does
  not get a column of its own. The map is kept and the slot joins a pooled
  replacement column for that cell, because dropping the map would discard a
  real result and bias the fit toward teams whose opponents happened to be
  established. The bucket's own coefficient is published: it is what a
  replacement-level slot is worth.
- *Fit inclusion* — every admitted cell enters, however thin. The walk penalty
  exists precisely so a thin season is informed by its neighbours.
- *Publication* — a higher floor, MIN_MAPS_PUBLISH, governs what is stored.

**Two coefficient families, and only one of them may be read forward.** The walk
penalty is two-sided: β at 2023 is pulled toward 2024 as much as toward 2022, so
a smoothed 2023 coefficient has already seen 2024's maps. That is right for a
retrospective number and disqualifying for a forward test, and the leak arrives
through the penalty rather than through a column, so no leakage test written
against the design matrix can see it. `filtered` refits through each cell and
nothing later. `require_filtered` is the mechanical enforcement: a forward test
that reaches for a smoothed coefficient raises.

The response is the map's score **margin**, rank-transformed to normal scores
within (season, mode). Margin is censored, not merely heteroskedastic — Hardpoint
runs to 250, Control to 3, Search & Destroy to 6 — so the raw number is
non-linear in value at exactly the tail where the best players live, and ranks
survive the cap. Binary win and the mode-standardized margin are fitted from the
same factorization and published as sensitivity, never averaged with it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..regress import FloatArray, matrix_hash
from . import graphs, numerics, preflight
from .preflight import CAREER_ONLY, COARSER_TIME, TEAM_ANCHORED, Season
from .rapm import MIN_MAPS, AdmittedMap

MODEL = "rapm_season"
VERSION = "1.0.0"

# How fine a time axis an era earns. `season` is one coefficient per player per
# season; `era` pools every season of that era into one, which is what a record
# that identifies nothing within a season can support.
SEASON = "season"
ERA = "era"
# One cell for the whole record: not a resolution any era earns, and the shape
# the published career fit already has. Here so the comparison in the gate can
# be made in this estimator's own design rather than across two of them.
WHOLE = "career"

# The stored coefficient families. `career` is the published global fit that
# already exists in `player_rapm` — it is retained, not recomputed here.
SMOOTHED = "smoothed"
FILTERED = "filtered"
CAREER = "career"
SCOPES = (CAREER, SMOOTHED, FILTERED)

# Column admission. Below this many maps in a cell, a player shares the cell's
# replacement bucket instead of holding a column. Eight is the qualified-cohort
# floor the era adjustment already publishes at, so "enough maps to be a season"
# means one thing across the project.
ADMISSION_MAPS = 8

# Publication. The career fit's floor, applied to a cell.
MIN_MAPS_PUBLISH = MIN_MAPS

# Where the penalty search starts and the box it may not leave, in log10 units.
# The published career fit runs at l2 = 1 on a ±1 design; the search is centred
# there and allowed three orders of magnitude either side.
LAMBDA_START = (0.0, 0.0)
LAMBDA_BOUNDS = ((-3.0, 3.0), (-3.0, 3.0))

# The sensitivity grid, as multipliers on the tuned pair. Reported rather than
# searched: nothing here selects a penalty by what it does to a leaderboard.
SENSITIVITY = (0.25, 1.0, 4.0)

# Responses fitted from the same factorization. The first is the published one.
MARGIN = "normal_score_margin"
BINARY = "binary_win"
MODE_MARGIN = "mode_standardized_margin"
RESPONSES = (MARGIN, BINARY, MODE_MARGIN)


class ScopeError(ValueError):
    """A forward test reached for a coefficient that has seen the future."""


def require_filtered(scope: str) -> None:
    """Assert a forward test is reading the one-sided family.

    The single most important line in the harness, per PE: a smoothed
    coefficient scored on next-season persistence is scored against a target
    that already contains the answer. This raises rather than warning, because a
    warning in a pipeline nobody watches is a silent pass.
    """
    if scope != FILTERED:
        raise ScopeError(
            f"a forward test may read only {FILTERED!r} coefficients, not {scope!r}: "
            "the random-walk penalty is two-sided, so a smoothed coefficient has "
            "already seen the season it is being asked to predict"
        )


def resolutions(fork: dict[str, Any]) -> dict[str, str]:
    """Per league, how fine a time cell the pre-flight's verdict allows.

    Read from the verdict rather than declared, so the resolution this module
    fits at is the one PC's stop rule chose on this run's data. Both stopping
    branches pool: an era that cannot support a season-level coefficient and an
    era that can support nothing finer than the era are the same instruction
    here, because the eras partition the record.
    """
    out: dict[str, str] = {}
    for era in fork["by_era"]:
        branch = str(era["branch"])
        out[str(era["league"])] = SEASON if branch == TEAM_ANCHORED else ERA
        if branch not in (TEAM_ANCHORED, COARSER_TIME, CAREER_ONLY):
            raise ValueError(f"unknown pre-flight branch {branch!r}")
    return out


# MARK: columns


Cell = tuple[str, int | str]


@dataclass(frozen=True)
class Columns:
    """The design's column index, and what each column is.

    `pools` is the replacement bucket per cell: every slot the admission rule
    turned away lands in the one for its cell, so the map stays in the fit and
    the unadmitted players share one coefficient rather than each absorbing a
    residual.
    """

    players: dict[tuple[int, Cell], int]
    teams: dict[tuple[int, int], int]
    pools: dict[Cell, int]
    # Cells in played order, which is the order the walk penalty differences in.
    cell_order: dict[Cell, int]
    resolutions: dict[str, str]
    pooled_players: dict[Cell, set[int]]

    @property
    def size(self) -> int:
        return len(self.players) + len(self.teams) + len(self.pools)

    def player_column(self, player_id: int, cell: Cell) -> int:
        col = self.players.get((player_id, cell))
        return self.pools[cell] if col is None else col


def cell_of(game: AdmittedMap, seasons: dict[int, Season], how: dict[str, str]) -> Cell:
    """Which time cell a map's player columns belong to."""
    season = seasons[game.season_id]
    resolution = how.get(season.league, SEASON)
    if resolution == SEASON:
        return (SEASON, game.season_id)
    if resolution == WHOLE:
        return (WHOLE, WHOLE)
    return (ERA, season.league)


def _cell_start(cell: Cell, seasons: dict[int, Season]) -> tuple[int, str]:
    """When a cell begins, for ordering the within-player differences.

    An era cell starts at its earliest season, so a CWL-era coefficient sits
    before 2020 in every player's walk and the seam link runs through it.
    """
    kind, key = cell
    if kind == SEASON:
        return (seasons[int(key)].year, "")
    if kind == WHOLE:
        return (0, WHOLE)
    years = [s.year for s in seasons.values() if s.league == key]
    return (min(years) if years else 0, str(key))


def build_columns(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    how: dict[str, str],
    admission: int = ADMISSION_MAPS,
    with_teams: bool = True,
) -> Columns:
    """Index the season-expanded design, applying the column-admission rule.

    `with_teams` off drops the team-season columns, which is not a design this
    phase publishes: it exists so the gate can say how much of the difference
    from the career fit is the team term and how much is the link function,
    rather than reporting one number for two changes.
    """
    maps_in_cell: Counter[tuple[int, Cell]] = Counter()
    cells: set[Cell] = set()
    team_seasons: set[tuple[int, int]] = set()
    for game in games:
        cell = cell_of(game, seasons, how)
        cells.add(cell)
        for pid in game.players:
            maps_in_cell[(pid, cell)] += 1
        team_seasons.add((game.home_team_id, game.season_id))
        team_seasons.add((game.away_team_id, game.season_id))

    admitted = sorted(key for key, maps in maps_in_cell.items() if maps >= admission)
    pooled: dict[Cell, set[int]] = defaultdict(set)
    for (pid, cell), maps in maps_in_cell.items():
        if maps < admission:
            pooled[cell].add(pid)

    players = {key: i for i, key in enumerate(admitted)}
    offset = len(players)
    teams = {key: offset + i for i, key in enumerate(sorted(team_seasons))} if with_teams else {}
    offset += len(teams)
    ordered_cells = sorted(cells, key=lambda c: (_cell_start(c, seasons), str(c)))
    # Only where the admission rule actually turned somebody away. A bucket for
    # a cell that pooled nobody is a column of zeros, and a column of zeros in a
    # rank diagnostic is a lie about the design.
    pools = {cell: offset + i for i, cell in enumerate(c for c in ordered_cells if pooled.get(c))}
    return Columns(
        players=players,
        teams=teams,
        pools=pools,
        cell_order={cell: i for i, cell in enumerate(ordered_cells)},
        resolutions=dict(how),
        pooled_players={cell: set(members) for cell, members in pooled.items() if members},
    )


# MARK: the response


def _rank_to_normal(values: FloatArray) -> FloatArray:
    """Ranks mapped to normal scores, ties averaged.

    Ties are averaged rather than broken, because breaking them by position
    would make the response depend on the order maps arrive in — the defect
    §4a's second clause is about, one level down from the resample it was found
    in.
    """
    n = values.size
    order = np.argsort(values, kind="stable")
    ranks = np.empty(n, dtype=float)
    ranks[order] = np.arange(1, n + 1, dtype=float)
    # Average the ranks of equal values so a tie carries one score.
    unique, inverse = np.unique(values, return_inverse=True)
    sums = np.bincount(inverse, weights=ranks, minlength=unique.size)
    counts = np.bincount(inverse, minlength=unique.size)
    ranks = (sums / counts)[inverse]
    return numerics.normal_quantile(ranks / (n + 1.0))


@dataclass(frozen=True)
class Response:
    """One target the design is fitted against, and which maps carry it."""

    name: str
    y: FloatArray
    usable: FloatArray  # boolean mask over `games`
    dropped: int
    reason: str
    # The dropped maps by game id, not merely how many. A count says a handful
    # of maps disagree with themselves; the ids let somebody go and look at
    # them, which is the difference between a caveat and a defect report.
    dropped_games: tuple[int, ...] = ()


def responses(games: Sequence[AdmittedMap]) -> dict[str, Response]:
    """Every target, computed once over the same admitted maps.

    A map whose margin is zero or whose sign contradicts the recorded winner
    cannot orient a signed response, so it leaves the margin targets and is
    counted. It stays in the binary one, where the recorded winner is the whole
    of the information.
    """
    margin = np.array(
        [np.nan if g.home_margin is None else float(g.home_margin) for g in games], dtype=float
    )
    won = np.array([1.0 if g.home_won else -1.0 for g in games], dtype=float)
    usable_margin = np.isfinite(margin) & (margin != 0.0) & (np.sign(margin) == won)

    normal = np.zeros(len(games), dtype=float)
    standardized = np.zeros(len(games), dtype=float)
    groups: dict[tuple[int, str], list[int]] = defaultdict(list)
    for i, game in enumerate(games):
        if usable_margin[i]:
            groups[(game.season_id, game.mode_slug)].append(i)
    for _key, index in sorted(groups.items()):
        idx = np.array(index, dtype=int)
        values = margin[idx]
        normal[idx] = _rank_to_normal(values)
        sd = float(values.std(ddof=1)) if idx.size > 1 else 0.0
        standardized[idx] = values / sd if sd > 0 else 0.0

    all_maps = np.ones(len(games), dtype=bool)
    dropped = int(np.sum(~usable_margin))
    dropped_games = tuple(g.game_id for g, ok in zip(games, usable_margin, strict=True) if not ok)
    reason = "margin absent, zero, or contradicting the recorded winner"
    return {
        MARGIN: Response(MARGIN, normal, usable_margin, dropped, reason, dropped_games),
        BINARY: Response(BINARY, won, all_maps, 0, ""),
        MODE_MARGIN: Response(
            MODE_MARGIN, standardized, usable_margin, dropped, reason, dropped_games
        ),
    }


# MARK: the normal equations


def gram(
    games: Sequence[AdmittedMap],
    columns: Columns,
    seasons: dict[int, Season],
    mask: FloatArray | None = None,
) -> tuple[FloatArray, list[list[tuple[int, float]]]]:
    """XᵀX and, per map, the (column, sign) pairs its row is made of.

    Accumulated a map at a time for the reason the pre-flight gives: the design
    is ten non-zeros in a row of a thousand columns, and forming it densely
    would spend a hundred megabytes multiplying by zero. The row index is
    returned alongside because every response reuses it.
    """
    size = columns.size
    out = np.zeros((size, size), dtype=float)
    rows: list[list[tuple[int, float]]] = []
    for i, game in enumerate(games):
        cell = cell_of(game, seasons, columns.resolutions)
        entries: Counter[int] = Counter()
        for pid in game.home_players:
            entries[columns.player_column(pid, cell)] += 1
        for pid in game.away_players:
            entries[columns.player_column(pid, cell)] -= 1
        if columns.teams:
            entries[columns.teams[(game.home_team_id, game.season_id)]] += 1
            entries[columns.teams[(game.away_team_id, game.season_id)]] -= 1
        row = [(col, float(sign)) for col, sign in sorted(entries.items()) if sign != 0]
        rows.append(row)
        if mask is not None and not mask[i]:
            continue
        for col_i, sign_i in row:
            for col_j, sign_j in row:
                out[col_i, col_j] += sign_i * sign_j
    return out, rows


def rhs(
    rows: Sequence[Sequence[tuple[int, float]]],
    y: FloatArray,
    size: int,
    mask: FloatArray | None = None,
) -> FloatArray:
    """Xᵀy, from the same row index."""
    out = np.zeros(size, dtype=float)
    for i, row in enumerate(rows):
        if mask is not None and not mask[i]:
            continue
        value = y[i]
        for col, sign in row:
            out[col] += sign * value
    return out


def walk_penalty(columns: Columns, seasons: dict[int, Season]) -> FloatArray:
    """DᵀD for the within-player first difference over consecutive cells.

    Player columns only. A team-season is a different object each year and
    smoothing it would launder team quality across seasons, which is the one
    thing the team column was added to stop. The replacement buckets are not
    walked either: the bucket is a different set of people every cell.
    """
    size = columns.size
    out = np.zeros((size, size), dtype=float)
    by_player: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for (pid, cell), col in columns.players.items():
        by_player[pid].append((columns.cell_order[cell], col))
    for entries in by_player.values():
        entries.sort()
        for (_a, left), (_b, right) in zip(entries, entries[1:], strict=False):
            out[left, left] += 1.0
            out[right, right] += 1.0
            out[left, right] -= 1.0
            out[right, left] -= 1.0
    return out


@dataclass
class Fit:
    """A solved generalized ridge, with everything read off one factorization."""

    columns: Columns
    beta: FloatArray
    se: FloatArray
    # Share of each column's prior variance that survives into the posterior:
    # 1 means the data said nothing about it. Generalizes the pre-flight's
    # λ·[(XᵀX+λI)⁻¹]ⱼⱼ to a penalty with a walk term in it.
    penalty_share: FloatArray
    lambda0: float
    lambda_walk: float
    effective_df: float
    sigma2: float
    n_maps: int
    gcv: float

    def player(self, player_id: int, cell: Cell) -> float | None:
        col = self.columns.players.get((player_id, cell))
        return None if col is None else float(self.beta[col])


def solve(
    gram_matrix: FloatArray,
    xty: FloatArray,
    walk: FloatArray,
    y: FloatArray,
    n_rows: int,
    lambda0: float,
    lambda_walk: float,
    columns: Columns,
    shares: bool = True,
) -> Fit:
    """Minimize ‖y − Xβ‖² + λ₀‖β‖² + λ_w Σ(β_{p,t} − β_{p,t−1})².

    Everything below comes from one Cholesky of A = XᵀX + λ₀I + λ_wDᵀD: the
    coefficients, the effective degrees of freedom, the residual variance, the
    penalized-Hessian standard errors this project has always published, and the
    GCV score the penalties are tuned on.

    `shares` off skips the second factorization the penalty share needs. The
    tuning loop calls this a hundred times and reads only the GCV score, so the
    diagnostic is computed once at the end rather than a hundred times over.
    """
    size = gram_matrix.shape[0]
    prior = lambda0 * np.eye(size) + lambda_walk * walk
    factor = numerics.cholesky(gram_matrix + prior)
    beta = factor.solve(xty)
    inverse = factor.inverse()
    # RSS without forming X: ‖y‖² − 2βᵀXᵀy + βᵀ(XᵀX)β.
    rss = float(y @ y) - 2.0 * float(beta @ xty) + float(beta @ (gram_matrix @ beta))
    effective_df = float(np.trace(inverse @ gram_matrix))
    dof = max(n_rows - effective_df, 1.0)
    sigma2 = rss / dof
    se = np.sqrt(np.maximum(sigma2 * np.diag(inverse), 0.0))
    if shares:
        prior_var = np.diag(numerics.cholesky(prior).inverse())
        share = np.where(prior_var > 0, np.diag(inverse) / np.maximum(prior_var, 1e-300), 1.0)
        share = np.clip(share, 0.0, 1.0)
    else:
        share = np.zeros(size, dtype=float)
    gcv = n_rows * rss / max((n_rows - effective_df) ** 2, 1e-9)
    return Fit(
        columns=columns,
        beta=beta,
        se=se,
        penalty_share=share,
        lambda0=lambda0,
        lambda_walk=lambda_walk,
        effective_df=effective_df,
        sigma2=sigma2,
        n_maps=n_rows,
        gcv=gcv,
    )


def tune(
    gram_matrix: FloatArray,
    xty: FloatArray,
    walk: FloatArray,
    y: FloatArray,
    n_rows: int,
    columns: Columns,
) -> tuple[float, float, bool]:
    """(λ₀, λ_w) by generalized cross-validation. (lambda0, lambda_walk, converged).

    GCV rather than a search over a held-out set, for the project's standing
    reason: tuning against the maps a model is scored on turns a test into a
    selection statistic. The criterion is closed-form given the trace of the hat
    matrix, and at this column count that trace is computed exactly rather than
    by the stochastic estimator a larger design would need — exact is both
    cheaper to defend and free of a seed.
    """

    def objective(log_lambdas: FloatArray) -> float:
        lambda0, lambda_walk = (10.0 ** float(v) for v in log_lambdas)
        fit = solve(gram_matrix, xty, walk, y, n_rows, lambda0, lambda_walk, columns, shares=False)
        return fit.gcv

    best, _value, converged = numerics.minimize_scalar_pair(objective, LAMBDA_START, LAMBDA_BOUNDS)
    return 10.0 ** float(best[0]), 10.0 ** float(best[1]), converged


def fit_smoothed(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    how: dict[str, str],
    response: Response,
    lambdas: tuple[float, float] | None = None,
    with_teams: bool = True,
) -> Fit:
    """The two-sided family: every cell informed by the cells on both sides."""
    columns = build_columns(games, seasons, how, with_teams=with_teams)
    mask = response.usable.astype(float)
    gram_matrix, rows = gram(games, columns, seasons, mask)
    xty = rhs(rows, response.y, columns.size, mask)
    y = response.y[response.usable]
    n_rows = int(response.usable.sum())
    walk = walk_penalty(columns, seasons)
    if lambdas is None:
        lambda0, lambda_walk, _ok = tune(gram_matrix, xty, walk, y, n_rows, columns)
    else:
        lambda0, lambda_walk = lambdas
    return solve(gram_matrix, xty, walk, y, n_rows, lambda0, lambda_walk, columns)


@dataclass(frozen=True)
class FilteredFit:
    """The one-sided family, both column blocks of it.

    A player coefficient here is a deviation from their team-season, and the
    quantity it deviates *from* is a fitted column of the same solve. Career
    aggregation has to say whether it credits the deviation alone or the
    deviation plus a share of the team term, and it cannot say either without
    the team column. Returning only the player block threw that away and made
    the question unanswerable downstream without refitting.
    """

    players: dict[tuple[int, Cell], tuple[float, float, float]]
    teams: dict[tuple[int, int], tuple[float, str]]


def fit_filtered(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    how: dict[str, str],
    response: Response,
    lambdas: tuple[float, float],
) -> FilteredFit:
    """The one-sided family: β at cell *t* from maps through *t* only.

    One solve per cell rather than one solve total. Its whole purpose is that it
    cannot have seen cell t+1, which is what makes a forward test a test. The
    penalties are handed in rather than retuned per fold, because a penalty
    chosen on a different amount of data would make the folds incomparable.

    Players carry (coefficient, standard error, penalty share) per (player,
    cell). Teams carry the coefficient per (team, season), taken from the solve
    whose cell covers that season, so a team term is one-sided in exactly the
    sense its players are.
    """
    order = sorted(
        {cell_of(g, seasons, how) for g in games},
        key=lambda c: (_cell_start(c, seasons), str(c)),
    )
    position_of = {cell: i for i, cell in enumerate(order)}
    at = [position_of[cell_of(g, seasons, how)] for g in games]
    covered = cell_seasons(games, seasons, how)
    out: dict[tuple[int, Cell], tuple[float, float, float]] = {}
    teams: dict[tuple[int, int], tuple[float, str]] = {}
    for position, cell in enumerate(order):
        through = [g for g, p in zip(games, at, strict=True) if p <= position]
        if not through:
            continue
        sub = responses(through)[response.name]
        fit = fit_smoothed(through, seasons, how, sub, lambdas)
        for (pid, key), col in fit.columns.players.items():
            if key == cell:
                out[(pid, cell)] = (
                    float(fit.beta[col]),
                    float(fit.se[col]),
                    float(fit.penalty_share[col]),
                )
        # A team-season column belongs to whichever cell covers that season, so
        # an era-resolution cell claims all three of its seasons at once and a
        # season cell claims one. Later cells see more maps, so a season is
        # written by the earliest solve that covers it and not overwritten.
        for (team_id, season_id), col in fit.columns.teams.items():
            if season_id in covered[cell] and (team_id, season_id) not in teams:
                teams[(team_id, season_id)] = (float(fit.beta[col]), cell_resolution(cell))
    return FilteredFit(players=out, teams=teams)


def fit_logistic(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    how: dict[str, str],
    lambda0: float,
    lambda_walk: float,
    with_teams: bool = True,
    max_iter: int = 25,
    tol: float = 1e-8,
) -> Fit:
    """The same penalty, on the map-win link the published fit uses.

    Here for one reason: the λ_w → 0 limit of a Gaussian fit on transformed
    margin is *not* the published logistic fit on binary win, so a rank
    correlation between them confounds the link function with everything else.
    A logistic variant of this design is a like-for-like check, and it is cheap
    — Newton on the same Gram machinery, ten iterations of a solve that costs
    a twentieth of a second.

    The Hessian is XᵀWX + λ₀I + λ_wDᵀD, so the weighted Gram matrix has to be
    re-accumulated each step; everything else is the linear solve above.
    """
    columns = build_columns(games, seasons, how, with_teams=with_teams)
    _unweighted, rows = gram(games, columns, seasons)
    won = np.array([1.0 if g.home_won else 0.0 for g in games], dtype=float)
    size = columns.size
    prior = lambda0 * np.eye(size) + lambda_walk * walk_penalty(columns, seasons)
    beta = np.zeros(size, dtype=float)
    inverse = np.eye(size)
    for _step in range(max_iter):
        eta = np.array([sum(sign * beta[col] for col, sign in row) for row in rows])
        p = 1.0 / (1.0 + np.exp(-np.clip(eta, -35.0, 35.0)))
        weights = np.maximum(p * (1.0 - p), 1e-9)
        weighted = np.zeros((size, size), dtype=float)
        score = np.zeros(size, dtype=float)
        for i, row in enumerate(rows):
            residual = won[i] - p[i]
            for col_i, sign_i in row:
                score[col_i] += sign_i * residual
                for col_j, sign_j in row:
                    weighted[col_i, col_j] += weights[i] * sign_i * sign_j
        gradient = score - prior @ beta
        factor = numerics.cholesky(weighted + prior)
        step = factor.solve(gradient)
        beta = beta + step
        inverse = factor.inverse()
        if float(np.linalg.norm(gradient)) < tol * len(games):
            break
    se = np.sqrt(np.maximum(np.diag(inverse), 0.0))
    prior_var = np.diag(numerics.cholesky(prior).inverse())
    share = np.clip(np.diag(inverse) / np.maximum(prior_var, 1e-300), 0.0, 1.0)
    return Fit(
        columns=columns,
        beta=beta,
        se=se,
        penalty_share=share,
        lambda0=lambda0,
        lambda_walk=lambda_walk,
        effective_df=float(np.trace(inverse @ (weighted))),
        sigma2=1.0,
        n_maps=len(games),
        gcv=float("nan"),
    )


# MARK: what a cell contains


def cell_label(cell: Cell, seasons: dict[int, Season]) -> str:
    """A cell as the artifact names it — never by a surrogate id."""
    kind, key = cell
    if kind == SEASON:
        return seasons[int(key)].label
    if kind == WHOLE:
        return "career"
    return f"{key} era"


def cell_resolution(cell: Cell) -> str:
    """What a stored row's coefficient covers: one season, an era, or a career."""
    return str(cell[0])


def cell_maps(
    games: Sequence[AdmittedMap], seasons: dict[int, Season], how: dict[str, str]
) -> Counter[tuple[int, Cell]]:
    """Maps played per (player, cell): the effective sample size of a column."""
    out: Counter[tuple[int, Cell]] = Counter()
    for game in games:
        cell = cell_of(game, seasons, how)
        for pid in game.players:
            out[(pid, cell)] += 1
    return out


def cell_seasons(
    games: Sequence[AdmittedMap], seasons: dict[int, Season], how: dict[str, str]
) -> dict[Cell, list[int]]:
    """The seasons a cell covers, which is what a stored row is keyed by.

    An era cell covers three of them. Its coefficient is stored against each,
    marked `era` so nobody reads three rows as three estimates — the join stays
    uniform and the row says what it is.
    """
    out: dict[Cell, set[int]] = defaultdict(set)
    for game in games:
        out[cell_of(game, seasons, how)].add(game.season_id)
    return {cell: sorted(members) for cell, members in out.items()}


def cell_concentration(
    games: Sequence[AdmittedMap], seasons: dict[int, Season], how: dict[str, str]
) -> dict[tuple[int, Cell], float]:
    """Teammate concentration inside a cell, on the cell's own maps.

    The career figure answers a different question: a player can have played
    with everyone over ten years and with exactly three people this season, and
    it is this season's coefficient the number has to be read against.
    """
    together: dict[tuple[int, Cell], Counter[int]] = defaultdict(Counter)
    total: Counter[tuple[int, Cell]] = Counter()
    for game in games:
        cell = cell_of(game, seasons, how)
        for side in (game.home_players, game.away_players):
            for pid in side:
                total[(pid, cell)] += 1
                for other in side:
                    if other != pid:
                        together[(pid, cell)][other] += 1
    out: dict[tuple[int, Cell], float] = {}
    for key, maps in total.items():
        top = together[key].most_common(1)
        out[key] = (top[0][1] / maps) if top and maps else 0.0
    return out


def design_hash(games: Sequence[AdmittedMap], columns: Columns, seasons: dict[int, Season]) -> str:
    """The fitted design, fingerprinted in the sparse form it is built in."""
    if not games:
        return matrix_hash(np.zeros((0, 0)))
    width = max(len(g.players) for g in games) + 2
    rows = np.full((len(games), width + 1), -1.0, dtype=float)
    for i, game in enumerate(games):
        cell = cell_of(game, seasons, columns.resolutions)
        entries = [columns.player_column(p, cell) for p in game.home_players]
        entries += [columns.player_column(p, cell) for p in game.away_players]
        if columns.teams:
            entries += [
                columns.teams[(game.home_team_id, game.season_id)],
                columns.teams[(game.away_team_id, game.season_id)],
            ]
        rows[i, 0] = float(len(game.home_players))
        rows[i, 1 : 1 + len(entries)] = entries
    return matrix_hash(rows, ["side_size", *(f"col{j}" for j in range(width))])


# MARK: diagnostics


# Bootstrap draws for the reliability interval. Matches the rest of the package.
BOOTSTRAP_B = 2000
BOOTSTRAP_SEED = 20200124  # the CDL's first match day, as elsewhere in this phase


def _spearman(left: Sequence[float] | FloatArray, right: Sequence[float] | FloatArray) -> float:
    """Rank correlation. Ties averaged, which is what makes it a rank."""
    if len(left) < 3:
        return float("nan")

    def ranks(values: Sequence[float] | FloatArray) -> FloatArray:
        arr = np.asarray(values, dtype=float)
        order = arr.argsort(kind="stable")
        out = np.empty(arr.size, dtype=float)
        out[order] = np.arange(1, arr.size + 1, dtype=float)
        unique, inverse = np.unique(arr, return_inverse=True)
        sums = np.bincount(inverse, weights=out, minlength=unique.size)
        counts = np.bincount(inverse, minlength=unique.size)
        return (sums / counts)[inverse]

    a, b = ranks(left), ranks(right)
    if a.std() == 0.0 or b.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _pearson(left: Sequence[float] | FloatArray, right: Sequence[float] | FloatArray) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.size < 3 or a.std() == 0.0 or b.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def series_halves(games: Sequence[AdmittedMap]) -> tuple[list[AdmittedMap], list[AdmittedMap]]:
    """The maps split into two halves by whole series, alternating in played order.

    Series rather than maps, because maps inside one share a lineup, a day, a
    patch and an opponent — splitting them apart would put the same evidence on
    both sides of a reliability check and report the correlation of a series with
    itself. Ordered by the date the series was played, which is a natural key; a
    surrogate id breaks ties only so the sort is reproducible.
    """
    per_series: dict[int, list[AdmittedMap]] = defaultdict(list)
    for game in games:
        per_series[game.series_id].append(game)
    ordered = sorted(
        per_series.values(),
        key=lambda maps: (min(m.played_at for m in maps), len(maps), str(maps[0].series_id)),
    )
    odd: list[AdmittedMap] = []
    even: list[AdmittedMap] = []
    for i, maps in enumerate(ordered):
        (odd if i % 2 == 0 else even).extend(maps)
    return odd, even


def reliability(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    how: dict[str, str],
    lambda0: float,
) -> dict[str, Any]:
    """Split-half reliability within a cell: does this metric say the same thing twice?

    VALUE's headline diagnostic, and the one directly comparable to the
    persistence *r* the methodology already reports for the composite. It
    separates "this metric is noisy" from "this player changed", which is the
    distinction a season-varying rating lives or dies on.

    Each cell is fitted twice on its own maps — no walk term, because a single
    cell has no neighbour to borrow from, which is exactly the point: this is
    what the season identifies on its own. The Spearman-Brown correction is
    reported beside the raw correlation because each half is half the record.
    """
    per_cell: dict[Cell, list[AdmittedMap]] = defaultdict(list)
    for game in games:
        per_cell[cell_of(game, seasons, how)].append(game)

    pairs: list[tuple[float, float]] = []
    by_cell: list[dict[str, Any]] = []
    for cell, cell_games in sorted(per_cell.items(), key=lambda kv: str(kv[0])):
        odd, even = series_halves(cell_games)
        halves: list[Fit] = []
        for half in (odd, even):
            if len(half) < 50:
                break
            response = responses(half)[MARGIN]
            halves.append(fit_smoothed(half, seasons, how, response, (lambda0, 0.0)))
        if len(halves) != 2:
            continue
        shared = sorted(
            {p for p, c in halves[0].columns.players if c == cell}
            & {p for p, c in halves[1].columns.players if c == cell}
        )
        cell_pairs = [(halves[0].player(pid, cell), halves[1].player(pid, cell)) for pid in shared]
        usable = [(a, b) for a, b in cell_pairs if a is not None and b is not None]
        if len(usable) < 5:
            continue
        pairs.extend(usable)
        r = _pearson([a for a, _ in usable], [b for _, b in usable])
        by_cell.append(
            {
                "cell": cell_label(cell, seasons),
                "n_players": len(usable),
                "maps": len(cell_games),
                "r": round(r, 4) if np.isfinite(r) else None,
                "spearman_brown": round(2.0 * r / (1.0 + r), 4)
                if np.isfinite(r) and r > -1.0
                else None,
            }
        )

    if len(pairs) < 10:
        return {"available": False, "reason": "too few players fitted in both halves"}

    # Ordered by contents, not by the player id they arrived under: the draw
    # below picks positions, and positions keyed on a surrogate move when a
    # reload renumbers rows.
    ordered = sorted(pairs)
    left = np.array([a for a, _ in ordered], dtype=float)
    right = np.array([b for _, b in ordered], dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    idx = rng.integers(0, left.size, size=(BOOTSTRAP_B, left.size))
    draws = [_pearson(left[row], right[row]) for row in idx]
    finite = [d for d in draws if np.isfinite(d)]
    lo, hi = np.percentile(finite, [2.5, 97.5]) if finite else (float("nan"), float("nan"))
    pooled = _pearson(left, right)
    return {
        "available": True,
        "what": (
            "each cell fitted twice, on alternating whole series, and the two sets of "
            "coefficients correlated — the number that separates a noisy metric from a "
            "player who changed"
        ),
        "unit": "series",
        "n_player_cells": int(left.size),
        "r": round(pooled, 4),
        "lo": round(float(lo), 4),
        "hi": round(float(hi), 4),
        "spearman_brown": round(2.0 * pooled / (1.0 + pooled), 4) if pooled > -1.0 else None,
        "bootstrap_b": BOOTSTRAP_B,
        "by_cell": by_cell,
    }


def sensitivity(
    gram_matrix: FloatArray,
    xty: FloatArray,
    walk: FloatArray,
    y: FloatArray,
    n_rows: int,
    columns: Columns,
    lambda0: float,
    lambda_walk: float,
    grid: Sequence[float] = SENSITIVITY,
) -> list[dict[str, Any]]:
    """What other penalties would have said, reported rather than selected.

    Two dials, so the grid moves them one at a time: how much of a coefficient
    is the ridge, and how much of it is the neighbouring season. The λ_w = 0 row
    is the one worth reading — it is the fit with no time-borrowing at all.
    """
    player_columns = sorted(columns.players.values())

    def at(scale_0: float, scale_walk: float) -> tuple[Fit, list[float]]:
        fit = solve(
            gram_matrix,
            xty,
            walk,
            y,
            n_rows,
            lambda0 * scale_0,
            lambda_walk * scale_walk,
            columns,
            shares=False,
        )
        return fit, [float(fit.beta[c]) for c in player_columns]

    _chosen_fit, chosen = at(1.0, 1.0)
    out: list[dict[str, Any]] = []
    for scale_walk in (0.0, *grid):
        for scale_0 in grid:
            fit, coefs = at(scale_0, scale_walk)
            rho = _spearman(chosen, coefs)
            out.append(
                {
                    "lambda0": round(lambda0 * scale_0, 4),
                    "lambda_walk": round(lambda_walk * scale_walk, 4),
                    "effective_df": round(fit.effective_df, 1),
                    "coef_sd": round(float(np.std(coefs)), 4),
                    "gcv": round(fit.gcv, 6),
                    "rank_corr_with_chosen": round(rho, 4) if np.isfinite(rho) else None,
                }
            )
    return out


def against_published(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    published: Sequence[tuple[int, float]],
    lambda0: float,
    top_n: int = 10,
) -> dict[str, Any]:
    """This estimator at career resolution, against the fit that ships today.

    An earlier draft of the gate asked for a reproduction, and that was wrong:
    the λ_w → 0 limit of a Gaussian fit on transformed margin is not a logistic
    fit on binary win, and no amount of care makes it one. So the honest version
    is a rank correlation with the disagreements named, and the two changes are
    separated rather than reported as one number — the team-season columns come
    out in one arm, the link function changes in another.
    """
    if len(published) < 10:
        return {"available": False, "reason": "no published career fit to compare against"}
    whole = dict.fromkeys(("CDL", "CWL"), WHOLE)
    for season in seasons.values():
        whole[season.league] = WHOLE
    cell: Cell = (WHOLE, WHOLE)
    response = responses(games)[MARGIN]
    arms = {
        "gaussian_margin_with_team_effect": fit_smoothed(
            games, seasons, whole, response, (lambda0, 0.0)
        ),
        "gaussian_margin_no_team_effect": fit_smoothed(
            games, seasons, whole, response, (lambda0, 0.0), with_teams=False
        ),
        "logistic_win_with_team_effect": fit_logistic(games, seasons, whole, lambda0, 0.0),
    }
    out: dict[str, Any] = {
        "available": True,
        "what": (
            "the published career plus-minus against this estimator collapsed to one cell, "
            "with the link function and the team-season effect varied one at a time"
        ),
        "n_published": len(published),
        "arms": [],
    }
    for name, fit in arms.items():
        common = [(pid, coef) for pid, coef in published if fit.player(pid, cell) is not None]
        if len(common) < 10:
            continue
        mine = [fit.player(pid, cell) or 0.0 for pid, _ in common]
        theirs = [coef for _, coef in common]
        rho = _spearman(theirs, mine)
        # The disagreements are the point: rank distance, not coefficient
        # distance, because the two arms are in different units.
        order_theirs = np.argsort(np.argsort(theirs))
        order_mine = np.argsort(np.argsort(mine))
        gaps = sorted(
            (
                {
                    "player_id": int(common[i][0]),
                    "published_rank": int(order_theirs[i]) + 1,
                    "state_space_rank": int(order_mine[i]) + 1,
                    "rank_shift": int(order_mine[i] - order_theirs[i]),
                }
                for i in range(len(common))
            ),
            key=lambda row: -abs(int(row["rank_shift"])),
        )
        out["arms"].append(
            {
                "arm": name,
                "n_players": len(common),
                "rank_corr": round(rho, 4) if np.isfinite(rho) else None,
                "largest_disagreements": gaps[:top_n],
            }
        )
    return out


def cell_graphs(
    games: Sequence[AdmittedMap], seasons: dict[int, Season], how: dict[str, str]
) -> list[dict[str, Any]]:
    """Per cell, what the schedule allowed and what the fit got.

    The scalar teammate concentration says how entangled one player is; the
    graph says whether the cell could have separated anybody. Both are published
    because they fail differently — a player can sit at a low concentration
    inside a lineup pool that never met the rest of the league.
    """
    per_cell: dict[Cell, list[AdmittedMap]] = defaultdict(list)
    for game in games:
        per_cell[cell_of(game, seasons, how)].append(game)
    out: list[dict[str, Any]] = []
    for cell, cell_games in sorted(per_cell.items(), key=lambda kv: str(kv[0])):
        edges, nodes = preflight.teammate_edges(cell_games)
        lineups = preflight.lineup_graph(cell_games)
        out.append(
            {
                "cell": cell_label(cell, seasons),
                "resolution": cell_resolution(cell),
                "maps": len(cell_games),
                "distinct_lineups": lineups.nodes,
                "lineup_pools": lineups.components,
                "rank_ceiling": lineups.nodes - lineups.components,
                "teammate_graph": graphs.graph_stats(edges, nodes).payload(),
            }
        )
    return out


# MARK: the published objects


@dataclass(frozen=True)
class TeamEffect:
    """One stored team-season effect, in the shape `team_season_effect` takes it.

    Same era rule as `Coefficient`: an era cell writes one estimate against each
    season it covers, and `resolution` says which case the row is.
    """

    team_id: int
    season_id: int
    resolution: str
    coef: float


@dataclass(frozen=True)
class Coefficient:
    """One stored coefficient, in the shape `player_rapm` takes it.

    An era-resolution coefficient is stored against every season it covers, with
    `resolution` saying so. Three rows carrying the same number is the honest
    shape for a quantity that was estimated once over three seasons: the join
    stays uniform and the row cannot be mistaken for three estimates.
    """

    scope: str
    player_id: int
    season_id: int
    resolution: str
    maps: int
    coef: float
    se: float
    teammate_concentration: float
    penalty_share: float


def coefficients(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    how: dict[str, str],
    smoothed: Fit,
    filtered: dict[tuple[int, Cell], tuple[float, float, float]],
    min_maps: int = MIN_MAPS_PUBLISH,
) -> list[Coefficient]:
    """Both families, expanded to one row per (scope, player, season)."""
    maps = cell_maps(games, seasons, how)
    concentration = cell_concentration(games, seasons, how)
    covered = cell_seasons(games, seasons, how)
    out: list[Coefficient] = []
    for (player_id, cell), column in sorted(smoothed.columns.players.items(), key=str):
        played = maps[(player_id, cell)]
        if played < min_maps:
            continue
        rows = [
            (
                SMOOTHED,
                float(smoothed.beta[column]),
                float(smoothed.se[column]),
                float(smoothed.penalty_share[column]),
            ),
        ]
        one_sided = filtered.get((player_id, cell))
        if one_sided is not None:
            rows.append((FILTERED, *one_sided))
        for scope, coef, se, share in rows:
            for season_id in covered[cell]:
                out.append(
                    Coefficient(
                        scope=scope,
                        player_id=player_id,
                        season_id=season_id,
                        resolution=cell_resolution(cell),
                        maps=played,
                        coef=coef,
                        se=se,
                        teammate_concentration=concentration.get((player_id, cell), 0.0),
                        penalty_share=share,
                    )
                )
    return out


def team_effects(filtered: FilteredFit) -> list[TeamEffect]:
    """The team block of the one-sided fit, in stored order.

    No publication floor applies. A team-season column exists because that team
    played that season, so there is no thin-cell case to filter: the floor on
    `player_rapm` is about how many maps one *player* contributed to a column
    they share with three teammates, and a team column has no such division.
    """
    return [
        TeamEffect(team_id=team_id, season_id=season_id, resolution=resolution, coef=coef)
        for (team_id, season_id), (coef, resolution) in sorted(filtered.teams.items())
    ]


def _distribution(values: Sequence[float]) -> dict[str, Any]:
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "median": round(float(np.median(arr)), 4),
        "p90": round(float(np.quantile(arr, 0.9)), 4),
        "sd": round(float(arr.std()), 4),
    }


def artifact(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    how: dict[str, str],
    published: Sequence[tuple[int, float]] = (),
) -> tuple[dict[str, Any], list[Coefficient], list[TeamEffect]]:
    """The gate's payload, and the two row sets it is a summary of.

    Everything P1's gate asks for lands here: coefficients with standard errors
    and teammate concentration, the share of each one the penalty supplied, the
    ridge-path sensitivity, split-half reliability within a cell, the graph
    diagnostics beside the scalar, and the comparison against the fit that ships
    today. The one thing it deliberately does not carry is a ranking: the
    leaders below are printed with their standard error and their penalty share,
    and the recovery number from the pre-flight is repeated next to them.
    """
    if len(games) < 50:
        return {"available": False, "reason": "not enough admitted maps"}, [], []

    all_responses = responses(games)
    primary = all_responses[MARGIN]
    columns = build_columns(games, seasons, how)
    mask = primary.usable.astype(float)
    gram_matrix, rows = gram(games, columns, seasons, mask)
    walk = walk_penalty(columns, seasons)
    xty = rhs(rows, primary.y, columns.size, mask)
    y = primary.y[primary.usable]
    n_rows = int(primary.usable.sum())
    lambda0, lambda_walk, tuned = tune(gram_matrix, xty, walk, y, n_rows, columns)
    smoothed = solve(gram_matrix, xty, walk, y, n_rows, lambda0, lambda_walk, columns)
    filtered = fit_filtered(games, seasons, how, primary, (lambda0, lambda_walk))

    player_columns = sorted(columns.players.values())
    coefs = [float(smoothed.beta[c]) for c in player_columns]
    shares = [float(smoothed.penalty_share[c]) for c in player_columns]
    side = int(np.median([len(g.home_players) for g in games]))
    reference = preflight.penalty_reference(side)
    dominated = sum(1 for s in shares if s >= preflight.PENALTY_REFERENCE_MARGIN * reference)

    # The other two responses, from the same design and the same penalties: a
    # different y is a different right-hand side and nothing else.
    others: list[dict[str, Any]] = []
    for name in (BINARY, MODE_MARGIN):
        alt = all_responses[name]
        alt_mask = alt.usable.astype(float)
        alt_gram, _rows = gram(games, columns, seasons, alt_mask)
        alt_fit = solve(
            alt_gram,
            rhs(rows, alt.y, columns.size, alt_mask),
            walk,
            alt.y[alt.usable],
            int(alt.usable.sum()),
            lambda0,
            lambda_walk,
            columns,
            shares=False,
        )
        rho = _spearman(coefs, [float(alt_fit.beta[c]) for c in player_columns])
        others.append(
            {
                "response": name,
                "n_maps": int(alt.usable.sum()),
                "rank_corr_with_published_response": round(rho, 4) if np.isfinite(rho) else None,
                "coef_sd": round(float(np.std([alt_fit.beta[c] for c in player_columns])), 4),
            }
        )

    stored = coefficients(games, seasons, how, smoothed, filtered.players)
    shared = [
        (float(smoothed.beta[col]), filtered.players[key][0])
        for key, col in smoothed.columns.players.items()
        if key in filtered.players
    ]
    smoothing_gap = _pearson([a for a, _ in shared], [b for _, b in shared]) if shared else None

    def cell_summary(cell: Cell) -> dict[str, Any]:
        cols = [col for (_player, c), col in columns.players.items() if c == cell]
        cell_shares = [float(smoothed.penalty_share[c]) for c in cols]
        return {
            "cell": cell_label(cell, seasons),
            "resolution": cell_resolution(cell),
            "player_columns": len(cols),
            "coef_sd": round(float(np.std([smoothed.beta[c] for c in cols])), 4) if cols else None,
            "se_median": round(float(np.median([smoothed.se[c] for c in cols])), 4)
            if cols
            else None,
            "penalty_dominated_share": round(
                sum(1 for s in cell_shares if s >= preflight.PENALTY_REFERENCE_MARGIN * reference)
                / len(cell_shares),
                4,
            )
            if cell_shares
            else None,
            "pooled_players": len(columns.pooled_players.get(cell, set())),
            "replacement_coef": round(float(smoothed.beta[columns.pools[cell]]), 4)
            if cell in columns.pools
            else None,
            "replacement_se": round(float(smoothed.se[columns.pools[cell]]), 4)
            if cell in columns.pools
            else None,
        }

    ordered_cells = sorted(columns.cell_order, key=lambda c: columns.cell_order[c])
    concentration = cell_concentration(games, seasons, how)
    payload: dict[str, Any] = {
        "available": True,
        "what": (
            "one plus-minus coefficient per player per time cell, as a deviation from an "
            "explicit team-season effect — the time axis the identification pre-flight "
            "found this record can carry, at the resolution it carries it per era"
        ),
        "resolution_by_league": dict(sorted(how.items())),
        "design_hash": design_hash(games, columns, seasons),
        "n_maps": len(games),
        "n_maps_fitted": n_rows,
        "response": {
            "published": MARGIN,
            "dropped_maps": primary.dropped,
            "dropped_reason": primary.reason,
            "dropped_game_ids": list(primary.dropped_games),
            "sensitivity": others,
        },
        "penalties": {
            "lambda0": round(lambda0, 4),
            "lambda_walk": round(lambda_walk, 4),
            "chosen_by": "generalized cross-validation, exact hat-matrix trace",
            "converged": tuned,
            "effective_df": round(smoothed.effective_df, 1),
            "sigma2": round(smoothed.sigma2, 4),
        },
        "columns": {
            "total": columns.size,
            "player_cells": len(columns.players),
            "team_seasons": len(columns.teams),
            "replacement_buckets": len(columns.pools),
        },
        "admission": {
            "min_maps": ADMISSION_MAPS,
            "rule": (
                "below the floor a player shares their cell's replacement bucket rather "
                "than holding a column; the map stays in the fit either way"
            ),
            "pooled_players": sum(len(v) for v in columns.pooled_players.values()),
        },
        "publication": {
            "min_maps": MIN_MAPS_PUBLISH,
            "rows": len(stored),
            "player_cells_published": len({(c.player_id, c.season_id) for c in stored})
            if stored
            else 0,
        },
        "scopes": {
            "stored": [SMOOTHED, FILTERED],
            "career_retained_in": "player_rapm, under the published rating run",
            "smoothed_vs_filtered_r": round(smoothing_gap, 4)
            if smoothing_gap is not None and np.isfinite(smoothing_gap)
            else None,
            "rule": (
                "a forward test may read only the filtered family; require_filtered "
                "raises rather than warning"
            ),
        },
        "penalty_share": {
            "reference": round(reference, 4),
            "dominated_at": f"{preflight.PENALTY_REFERENCE_MARGIN} of k/(k+1)",
            "dominated_share": round(dominated / len(shares), 4) if shares else None,
            **_distribution(shares),
        },
        "teammate_concentration": _distribution(
            [v for (pid, cell), v in concentration.items() if (pid, cell) in columns.players]
        ),
        "coefficients": _distribution(coefs),
        "by_cell": [cell_summary(cell) for cell in ordered_cells],
        "graphs": cell_graphs(games, seasons, how),
        "sensitivity": sensitivity(
            gram_matrix, xty, walk, y, n_rows, columns, lambda0, lambda_walk
        ),
        "reliability": reliability(games, seasons, how, lambda0),
        "against_published": against_published(games, seasons, published, lambda0),
        "how_to_read": (
            "a coefficient here is a deviation from that team-season, recovered at r ≈ 0.29 "
            "in simulation at the CDL era's lineup variety and at nothing at all at the "
            "CWL era's — read it against its standard error and its penalty share, never "
            "as a ranking of the four players on a roster"
        ),
    }
    return payload, stored, team_effects(filtered)
