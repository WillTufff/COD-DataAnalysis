"""Is a season-varying plus-minus identified by this record? Spec: /methodology#rapm.

The published plus-minus gives each player one coefficient for their whole
career. Giving them one per season is the obvious next step and it is not
obviously possible, because a CDL team plays the same four players on nearly
every map of a season. Where a team-season has exactly one lineup, its four
player columns and its team column are **exactly** collinear: the sum of the
four minus the team is zero on every row of the design, so no penalty can
separate them from data that never distinguishes them. Ridge still returns four
numbers. They are the penalty's opinion, they look plausible, and their standard
errors are not large.

This module measures the supply of the thing that would separate them —
mid-season roster moves, substitute appearances, a rotated fifth — before
anything is fitted, and it does so in the design P1 specifies rather than the
one already published: one column per (player, season), plus an explicit
team-season column on each side.

Four statistics, and each answers a different way of being unidentified:

- **Lineup supply.** Distinct lineups per team-season, and the *effective*
  number — the perplexity of the lineup's map shares — because a substitute who
  played once makes two lineups and not two lineups' worth of evidence.
- **Rank.** The design's numerical rank against its column count, and the gap
  is the number of directions the data says nothing about at all. That gap is
  not an accident of this record: every row of a season is one lineup against
  another, so the row space is spanned by lineup *differences*, and the rank can
  never exceed **distinct lineups minus the components of the lineup graph** no
  matter how many players those lineups contain. A season with sixty-three
  player columns and thirty lineups identifies thirty directions. **Effective
  rank** — the perplexity of the eigenvalue spectrum — is the continuous version,
  and says how much is nearly unidentified rather than exactly so.
- **Penalty share.** For each column, the share of its posterior variance that
  the penalty supplies rather than the data: λ·[(XᵀX + λI)⁻¹]ⱼⱼ, which is the
  ratio of posterior variance to prior variance and runs from 0 (the data
  decided it) to 1 (the prior did). It does not reach 1: a player whose k-strong
  lineup never changes shares one identified direction with k−1 teammates and
  their team column, so their share sits near k/(k+1) — 0.80 at 4v4, 0.83 at
  5v5 — and a flat threshold of 0.9 would report no problem anywhere. What
  counts as dominated is therefore stated relative to that reference. The
  reference is not a hard ceiling: where the opponents are few as well, more
  columns share the one direction and the share goes higher still, which is why
  2017 measures above it.
  Reported with λ_w = 0 deliberately: the random-walk term links seasons, and
  the question here is what a season identifies on its own before it borrows.
- **The teammate graph**, per season, through `graphs.py`.

Nothing here fits a model to real data. The estimator is judged on synthetic
data in `simleague.py`, where the answer is known, and the two results together
decide the stop rule in `verdict()`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, cast

import numpy as np
import psycopg

from ..maprows import MapRow
from ..regress import FloatArray, matrix_hash
from . import graphs
from .rapm import L2, AdmittedMap, LineupRule, admitted_maps

# The penalty strength the shares are reported at: the same l2 = 1 the published
# plus-minus is fitted at, so the number describes the estimator this project
# actually runs rather than a hypothetical one.
PENALTY_LAMBDA = L2

# How close to its reference a column's penalty share has to run before the data
# counts as having said nothing about it. The reference is k/(k+1) — what a
# player gets when their lineup never changes — so this is "within 5% of a
# player who was never seen apart from their teammates". Declared here, before
# the numbers, because it feeds the stop rule.
PENALTY_REFERENCE_MARGIN = 0.95

# Maps below which a (player, season) column is a candidate for P1's pooled
# replacement bucket rather than a column of its own. The career fit's floor,
# applied to a season, which is the comparison P1 has to make.
THIN_COLUMN_MAPS = 20


@dataclass(frozen=True)
class Season:
    """A season as the artifact names it — never by its surrogate id."""

    season_id: int
    year: int
    league: str

    @property
    def label(self) -> str:
        return f"{self.year} {self.league}"


def load_seasons(conn: psycopg.Connection[tuple[object, ...]]) -> dict[int, Season]:
    rows = conn.execute("SELECT id, year, league FROM seasons ORDER BY id").fetchall()
    return {cast(int, r[0]): Season(cast(int, r[0]), cast(int, r[1]), str(r[2])) for r in rows}


def _perplexity(weights: Iterable[float]) -> float:
    """exp of the Shannon entropy of a distribution: its effective size.

    Four equal parts give 4.0; four parts where one holds everything give 1.0.
    The measure both the lineup counts and the eigenvalue spectrum are read
    through, so "effective" means the same thing in both places.
    """
    values = np.asarray([w for w in weights if w > 0.0], dtype=float)
    if values.size == 0:
        return 0.0
    shares = values / values.sum()
    return float(np.exp(-np.sum(shares * np.log(shares))))


@dataclass(frozen=True)
class TeamSeason:
    """How much lineup variety one team-season supplied."""

    team_id: int
    season_id: int
    maps: int
    distinct_lineups: int
    effective_lineups: float
    modal_lineup_share: float


def lineup_supply(games: Sequence[AdmittedMap]) -> list[TeamSeason]:
    """Every team-season in the admitted maps, with its lineup variety."""
    per_team: dict[tuple[int, int], Counter[tuple[int, ...]]] = defaultdict(Counter)
    for game in games:
        per_team[(game.home_team_id, game.season_id)][game.home_players] += 1
        per_team[(game.away_team_id, game.season_id)][game.away_players] += 1

    out: list[TeamSeason] = []
    for (team_id, season_id), lineups in sorted(per_team.items()):
        maps = sum(lineups.values())
        out.append(
            TeamSeason(
                team_id=team_id,
                season_id=season_id,
                maps=maps,
                distinct_lineups=len(lineups),
                effective_lineups=_perplexity(lineups.values()),
                modal_lineup_share=max(lineups.values()) / maps,
            )
        )
    return out


@dataclass(frozen=True)
class Spectrum:
    """A design block's identification, read off its Gram matrix."""

    rows: int
    columns: int
    rank: int
    effective_rank: float
    # Columns the data says nothing at all about: exactly collinear directions.
    deficiency: int
    # λ·[(XᵀX + λI)⁻¹]ⱼⱼ over the player columns only — the team columns are a
    # different question and are reported apart from them.
    penalty_share_median: float
    penalty_share_p90: float
    penalty_dominated_columns: int
    player_columns: int
    # k/(k+1) for the side size these maps were played at: what a player's share
    # sits at when their lineup never changes.
    penalty_reference: float

    def payload(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "columns": self.columns,
            "player_columns": self.player_columns,
            "rank": self.rank,
            "deficiency": self.deficiency,
            "effective_rank": round(self.effective_rank, 2),
            "effective_rank_ratio": round(self.effective_rank / self.columns, 4)
            if self.columns
            else 0.0,
            "penalty_share_median": round(self.penalty_share_median, 4),
            "penalty_share_p90": round(self.penalty_share_p90, 4),
            "penalty_share_reference": round(self.penalty_reference, 4),
            "penalty_dominated_columns": self.penalty_dominated_columns,
            "penalty_dominated_share": round(
                self.penalty_dominated_columns / self.player_columns, 4
            )
            if self.player_columns
            else 0.0,
        }


def _gram(
    games: Sequence[AdmittedMap],
    player_col: dict[int, int],
    team_col: dict[int, int],
) -> FloatArray:
    """XᵀX, accumulated a map at a time.

    The design is ten non-zeros in a row of a few thousand columns; forming it
    densely would cost a third of a gigabyte to multiply by zero. Accumulating
    the Gram matrix directly is exact and costs a hundred updates per map.

    An empty `team_col` builds the design without team columns, which is the
    published career design rather than the one P1 proposes.
    """
    size = len(player_col) + len(team_col)
    gram = np.zeros((size, size), dtype=float)
    offset = len(player_col)
    for game in games:
        index = [player_col[p] for p in game.home_players]
        sign = [1.0] * len(index)
        index += [player_col[p] for p in game.away_players]
        sign += [-1.0] * len(game.away_players)
        if team_col:
            index.append(offset + team_col[game.home_team_id])
            sign.append(1.0)
            index.append(offset + team_col[game.away_team_id])
            sign.append(-1.0)
        for i, si in zip(index, sign, strict=True):
            for j, sj in zip(index, sign, strict=True):
                gram[i, j] += si * sj
    return gram


def penalty_reference(side_size: int) -> float:
    """What a never-separated player column carries: k/(k+1).

    A lineup that never changes gives its k players and their team column one
    shared identified direction and nothing else, so each of the k+1 columns
    keeps all but 1/(k+1) of its prior variance. Any flat threshold above this
    would declare a perfectly collinear design to be fine.

    It is a reference and not a bound. The derivation assumes the lineup meets a
    varied field; where the opponents are few, their columns join the same
    direction and the share climbs past k/(k+1) — which is what 2017 does.
    """
    return side_size / (side_size + 1.0)


def _spectrum(
    gram: FloatArray,
    rows: int,
    player_columns: int,
    side_size: int,
    penalty: float = PENALTY_LAMBDA,
) -> Spectrum:
    reference = penalty_reference(side_size)
    size = gram.shape[0]
    if size == 0:
        return Spectrum(rows, 0, 0, 0.0, 0, 1.0, 1.0, 0, 0, reference)
    eigenvalues, vectors = np.linalg.eigh(gram)
    eigenvalues = np.maximum(eigenvalues, 0.0)
    tolerance = float(eigenvalues.max()) * size * np.finfo(float).eps
    rank = int(np.sum(eigenvalues > tolerance))
    # diag((XᵀX + λI)⁻¹) without inverting: Σ_k v_jk² / (w_k + λ).
    inverse_diagonal = (vectors**2) @ (1.0 / (eigenvalues + penalty))
    share = penalty * inverse_diagonal[:player_columns]
    return Spectrum(
        rows=rows,
        columns=size,
        rank=rank,
        effective_rank=_perplexity(eigenvalues),
        deficiency=size - rank,
        penalty_share_median=float(np.median(share)) if share.size else 1.0,
        penalty_share_p90=float(np.quantile(share, 0.9)) if share.size else 1.0,
        penalty_dominated_columns=int(np.sum(share >= PENALTY_REFERENCE_MARGIN * reference)),
        player_columns=player_columns,
        penalty_reference=reference,
    )


def side_size(games: Sequence[AdmittedMap]) -> int:
    """The side size these maps were played at, from the maps themselves."""
    sizes = Counter(len(game.home_players) for game in games)
    return sizes.most_common(1)[0][0] if sizes else 4


def season_spectrum(games: Sequence[AdmittedMap], penalty: float = PENALTY_LAMBDA) -> Spectrum:
    """The season-expanded design for one season: players and both team columns.

    Every column of the season-expanded design belongs to exactly one season, so
    XᵀX is block diagonal by season and a season can be measured on its own.
    That is not a convenience — it is the statement that nothing but the
    random-walk penalty crosses a season boundary.
    """
    players = sorted({p for game in games for p in game.players})
    teams = sorted({t for game in games for t in (game.home_team_id, game.away_team_id)})
    player_col = {pid: i for i, pid in enumerate(players)}
    team_col = {tid: i for i, tid in enumerate(teams)}
    gram = _gram(games, player_col, team_col)
    return _spectrum(gram, len(games), len(players), side_size(games), penalty)


def career_spectrum(games: Sequence[AdmittedMap], penalty: float = PENALTY_LAMBDA) -> Spectrum:
    """The published design, measured the same way: one column per player, no season.

    Here as the comparison the whole phase turns on. A season-expanded design
    that is far worse identified than this one is the cost of the time axis,
    stated in the same units.
    """
    players = sorted({p for game in games for p in game.players})
    player_col = {pid: i for i, pid in enumerate(players)}
    gram = _gram(games, player_col, {})
    return _spectrum(gram, len(games), len(players), side_size(games), penalty)


def teammate_edges(games: Sequence[AdmittedMap]) -> tuple[list[tuple[int, int, int]], set[int]]:
    """Pairs who played a map on the same side, with how many, and every player."""
    together: Counter[tuple[int, int]] = Counter()
    nodes: set[int] = set()
    for game in games:
        for side in (game.home_players, game.away_players):
            nodes.update(side)
            for i, left in enumerate(side):
                for right in side[i + 1 :]:
                    together[(left, right)] += 1
    return [(left, right, maps) for (left, right), maps in sorted(together.items())], nodes


def lineup_graph(games: Sequence[AdmittedMap]) -> graphs.GraphStats:
    """The graph whose components cap the rank: lineups joined by having met.

    Every row of the design is one lineup against another, so the row space is
    spanned by lineup differences and the rank is at most `nodes − components`.
    Measuring it here turns the design's rank from a number into a statement
    about the schedule: what a season can identify is not how many players it
    had but how many lineups faced each other, and in how many separate pools.
    """
    seen: dict[tuple[int, tuple[int, ...]], int] = {}

    def node(team_id: int, players: tuple[int, ...]) -> int:
        return seen.setdefault((team_id, players), len(seen))

    met: Counter[tuple[int, int]] = Counter()
    for game in games:
        home = node(game.home_team_id, game.home_players)
        away = node(game.away_team_id, game.away_players)
        met[(min(home, away), max(home, away))] += 1
    edges = [(left, right, maps) for (left, right), maps in sorted(met.items())]
    return graphs.graph_stats(edges, seen.values())


@dataclass(frozen=True)
class SeasonPreflight:
    """One season's answer to whether it identifies its own coefficients."""

    season: Season
    maps: int
    team_seasons: list[TeamSeason]
    spectrum: Spectrum
    graph: graphs.GraphStats
    lineups: graphs.GraphStats
    thin_columns: int

    @property
    def rank_ceiling(self) -> int:
        """What the schedule allows: distinct lineups minus their components."""
        return self.lineups.nodes - self.lineups.components

    @property
    def single_lineup_team_seasons(self) -> int:
        return sum(1 for ts in self.team_seasons if ts.distinct_lineups == 1)

    @property
    def median_effective_lineups(self) -> float:
        return float(np.median([ts.effective_lineups for ts in self.team_seasons]))

    def payload(self) -> dict[str, Any]:
        return {
            "year": self.season.year,
            "league": self.season.league,
            "maps": self.maps,
            "team_seasons": len(self.team_seasons),
            "median_distinct_lineups": float(
                np.median([ts.distinct_lineups for ts in self.team_seasons])
            ),
            "median_effective_lineups": round(self.median_effective_lineups, 2),
            "median_modal_lineup_share": round(
                float(np.median([ts.modal_lineup_share for ts in self.team_seasons])), 4
            ),
            "single_lineup_team_seasons": self.single_lineup_team_seasons,
            "median_maps_per_effective_lineup": round(
                float(np.median([ts.maps / ts.effective_lineups for ts in self.team_seasons])), 1
            ),
            "thin_player_columns": self.thin_columns,
            "distinct_lineups": self.lineups.nodes,
            "lineup_pools": self.lineups.components,
            "rank_ceiling": self.rank_ceiling,
            "spectrum": self.spectrum.payload(),
            "graph": self.graph.payload(),
            "lineup_graph": self.lineups.payload(),
        }


def by_season(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    penalty: float = PENALTY_LAMBDA,
) -> list[SeasonPreflight]:
    grouped: dict[int, list[AdmittedMap]] = defaultdict(list)
    for game in games:
        grouped[game.season_id].append(game)

    out: list[SeasonPreflight] = []
    for season_id, season_games in sorted(grouped.items()):
        season = seasons.get(season_id) or Season(season_id, season_id, "unknown")
        edges, nodes = teammate_edges(season_games)
        maps_played: Counter[int] = Counter()
        for game in season_games:
            for pid in game.players:
                maps_played[pid] += 1
        out.append(
            SeasonPreflight(
                season=season,
                maps=len(season_games),
                team_seasons=lineup_supply(season_games),
                spectrum=season_spectrum(season_games, penalty),
                graph=graphs.graph_stats(edges, nodes),
                lineups=lineup_graph(season_games),
                thin_columns=sum(1 for n in maps_played.values() if n < THIN_COLUMN_MAPS),
            )
        )
    return out


def design_fingerprint(games: Sequence[AdmittedMap]) -> str:
    """The season-expanded design, hashed in the sparse form it is measured in.

    Same purpose as the plus-minus design hash: a rerun that reports different
    identification numbers can be told apart from one that was handed different
    maps.
    """
    if not games:
        return matrix_hash(np.zeros((0, 0)))
    width = max(len(game.players) for game in games)
    rows = np.full((len(games), 3 + width), -1.0, dtype=float)
    for i, game in enumerate(games):
        rows[i, 0] = game.season_id
        rows[i, 1] = game.home_team_id
        rows[i, 2] = game.away_team_id
        for j, pid in enumerate(game.players):
            rows[i, 3 + j] = pid
    columns = ["season", "home_team", "away_team", *(f"slot{j}" for j in range(width))]
    return matrix_hash(rows, columns)


@dataclass(frozen=True)
class Preflight:
    """Everything the identification question can be answered from."""

    games: int
    lineup: LineupRule
    seasons: list[SeasonPreflight]
    career: Spectrum
    career_graph: graphs.GraphStats
    design_hash: str

    def eras(self) -> dict[str, list[SeasonPreflight]]:
        grouped: dict[str, list[SeasonPreflight]] = defaultdict(list)
        for season in self.seasons:
            grouped[season.season.league].append(season)
        return dict(grouped)

    def era_payload(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for league, seasons in sorted(self.eras().items()):
            team_seasons = [ts for s in seasons for ts in s.team_seasons]
            columns = sum(s.spectrum.columns for s in seasons)
            rank = sum(s.spectrum.rank for s in seasons)
            dominated = sum(s.spectrum.penalty_dominated_columns for s in seasons)
            player_columns = sum(s.spectrum.player_columns for s in seasons)
            out.append(
                {
                    "league": league,
                    "seasons": len(seasons),
                    "maps": sum(s.maps for s in seasons),
                    "team_seasons": len(team_seasons),
                    "median_effective_lineups": round(
                        float(np.median([ts.effective_lineups for ts in team_seasons])), 2
                    ),
                    "single_lineup_share": round(
                        sum(1 for ts in team_seasons if ts.distinct_lineups == 1)
                        / len(team_seasons),
                        4,
                    )
                    if team_seasons
                    else 0.0,
                    "columns": columns,
                    "rank": rank,
                    "deficiency": columns - rank,
                    "distinct_lineups": sum(s.lineups.nodes for s in seasons),
                    "rank_ceiling": sum(s.rank_ceiling for s in seasons),
                    "penalty_dominated_share": round(dominated / player_columns, 4)
                    if player_columns
                    else 0.0,
                    "median_algebraic_connectivity": round(
                        float(np.median([s.graph.algebraic_connectivity for s in seasons])), 4
                    ),
                }
            )
        return out

    def payload(self) -> dict[str, Any]:
        team_seasons = [ts for s in self.seasons for ts in s.team_seasons]
        columns = sum(s.spectrum.columns for s in self.seasons)
        rank = sum(s.spectrum.rank for s in self.seasons)
        return {
            "what": (
                "what a season-varying plus-minus would be identified by, measured on the "
                "design P1 specifies — one column per (player, season) plus a team-season "
                "column on each side — before anything is fitted"
            ),
            "design_hash": self.design_hash,
            "penalty_lambda": PENALTY_LAMBDA,
            "penalty_dominated_at": (
                f"{PENALTY_REFERENCE_MARGIN} of k/(k+1), the share a never-changing lineup gives"
            ),
            "thin_column_maps": THIN_COLUMN_MAPS,
            "maps": self.games,
            "lineup": self.lineup.payload(),
            "team_seasons": len(team_seasons),
            "single_lineup_team_seasons": sum(1 for ts in team_seasons if ts.distinct_lineups == 1),
            "median_effective_lineups": round(
                float(np.median([ts.effective_lineups for ts in team_seasons])), 2
            ),
            "season_expanded": {
                "columns": columns,
                "rank": rank,
                "deficiency": columns - rank,
                "distinct_lineups": sum(s.lineups.nodes for s in self.seasons),
                "rank_ceiling": sum(s.rank_ceiling for s in self.seasons),
                # Every column belongs to one season, so the blocks add.
                "block_diagonal_by_season": True,
            },
            "career": self.career.payload(),
            "career_graph": self.career_graph.payload(),
            "by_era": self.era_payload(),
            "by_season": [s.payload() for s in self.seasons],
        }


def measure(
    rows: Sequence[MapRow],
    seasons: dict[int, Season],
    penalty: float = PENALTY_LAMBDA,
) -> Preflight:
    """Run the pre-flight over the maps the plus-minus design admits."""
    games, lineup = admitted_maps(rows)
    return measure_admitted(games, seasons, lineup, penalty)


def measure_admitted(
    games: Sequence[AdmittedMap],
    seasons: dict[int, Season],
    lineup: LineupRule | None = None,
    penalty: float = PENALTY_LAMBDA,
) -> Preflight:
    """The same measurement over maps that have already cleared the lineup rule.

    Split out so a fixture can hand in a schedule directly. `measure` is the
    door the pipeline uses; nothing here reapplies the admission rule, because
    there is one of those and it lives in `rapm`.
    """
    if lineup is None:
        admitted: Counter[str] = Counter()
        for game in games:
            admitted[game.title] += 1
        sizes = {game.title: len(game.home_players) for game in games}
        lineup = LineupRule(sizes=sizes, admitted=admitted, dropped=Counter())
    edges, nodes = teammate_edges(games)
    return Preflight(
        games=len(games),
        lineup=lineup,
        seasons=by_season(games, seasons, penalty),
        career=career_spectrum(games, penalty),
        career_graph=graphs.graph_stats(edges, nodes),
        design_hash=design_fingerprint(games),
    )


# MARK: the stop rule


# The three clauses, declared before the numbers were seen. The phase is allowed
# to stop P1, and a stop rule written after the measurement is not a stop rule.
#
# 1. Lineup supply: the median team-season carries fewer than this many
#    effective lineups.
STOP_EFFECTIVE_LINEUPS = 1.5
# 2. Rank: the season-expanded design identifies fewer directions than this
#    share of its own player columns.
STOP_RANK_SHARE = 0.5
# 3. Recovery: teammates are separated below this correlation, in a simulated
#    league with the same lineup variety and nothing to borrow from other
#    seasons.
STOP_RECOVERY = 0.5
# Below this, recovery is not merely weak but absent, and the fallback drops
# from a coarser time axis to none at all.
STOP_RECOVERY_FLOOR = 0.3

# The three fallbacks, named as the plan names them.
TEAM_ANCHORED = "team_anchored_deviations"
COARSER_TIME = "coarser_time_resolution"
CAREER_ONLY = "career_level_only"


@dataclass(frozen=True)
class EraVerdict:
    league: str
    effective_lineups: float
    rank_share: float
    recovery: float | None
    thin: bool
    rank_poor: bool
    recovery_fails: bool

    @property
    def stops(self) -> bool:
        return self.thin and self.rank_poor and self.recovery_fails

    @property
    def branch(self) -> str:
        """Which of the three named fallbacks this era earns."""
        if not self.stops:
            return TEAM_ANCHORED
        if self.recovery is not None and self.recovery < STOP_RECOVERY_FLOOR:
            return CAREER_ONLY
        return COARSER_TIME

    def payload(self) -> dict[str, Any]:
        return {
            "league": self.league,
            "median_effective_lineups": round(self.effective_lineups, 2),
            "rank_share": round(self.rank_share, 4),
            "recovery_within_team": round(self.recovery, 4) if self.recovery is not None else None,
            "thin_lineups": self.thin,
            "rank_below_threshold": self.rank_poor,
            "recovery_below_threshold": self.recovery_fails,
            "stops": self.stops,
            "branch": self.branch,
        }


def verdict(measured: Preflight, recovery_curve: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Which branch of PC's fork this record chooses, and the numbers that chose it.

    The plan's stop rule is a conjunction: thin lineups **and** rank far below
    column count **and** a simulation that cannot recover the trajectories at
    that level of churn. Two of three is not a stop — a design can be rank
    deficient and still recover what it claims to, which is exactly what a
    penalty is for — so all three are evaluated and all three are published,
    per era, because the two eras in this record are not the same league.
    """
    from .simleague import recovery_at

    eras: list[EraVerdict] = []
    for era in measured.era_payload():
        league = str(era["league"])
        seasons = measured.eras()[league]
        team_seasons = [ts for s in seasons for ts in s.team_seasons]
        effective = float(np.median([ts.effective_lineups for ts in team_seasons]))
        player_columns = sum(s.spectrum.player_columns for s in seasons)
        rank_share = int(era["rank"]) / player_columns if player_columns else 0.0
        recovered = recovery_at(recovery_curve, effective)
        eras.append(
            EraVerdict(
                league=league,
                effective_lineups=effective,
                rank_share=rank_share,
                recovery=recovered,
                thin=effective < STOP_EFFECTIVE_LINEUPS,
                rank_poor=rank_share < STOP_RANK_SHARE,
                recovery_fails=recovered is not None and recovered < STOP_RECOVERY,
            )
        )

    stopped = [e for e in eras if e.stops]
    branches = {e.branch for e in eras}
    if branches == {TEAM_ANCHORED}:
        branch = TEAM_ANCHORED
        reason = (
            "no era trips all three clauses, so the design is available in the form P1 "
            "already adopts by default: the team-season effect is estimated explicitly and "
            "player coefficients are deviations from it, weakly identified where churn is "
            "thin and published as such"
        )
    elif branches == {CAREER_ONLY}:
        branch = CAREER_ONLY
        reason = (
            "the simulation cannot separate teammates at the lineup variety any era of this "
            "record supplies, so a season-level plus-minus is not available and the finding "
            "is the null: career-level RAPM stands, with the lineup and rank numbers as the "
            "evidence"
        )
    else:
        branch = COARSER_TIME
        kept = sorted(e.league for e in eras if e.branch == TEAM_ANCHORED)
        pooled = sorted(e.league for e in eras if e.branch != TEAM_ANCHORED)
        reason = (
            "the eras do not answer the same way, so the time axis is as fine as each one "
            f"can carry: season resolution for {', '.join(kept) or 'no era'}, as team-anchored "
            f"deviations, and pooled to era level for {', '.join(pooled)}, where within-season "
            "lineup variety identifies nothing and every apparent difference between "
            "teammates would be imported by the penalty"
        )

    return {
        "what": "PC's fork, resolved by the three clauses declared before the measurement",
        "thresholds": {
            "effective_lineups": STOP_EFFECTIVE_LINEUPS,
            "rank_share": STOP_RANK_SHARE,
            "recovery_within_team": STOP_RECOVERY,
            "recovery_floor": STOP_RECOVERY_FLOOR,
        },
        "by_era": [e.payload() for e in eras],
        "stops_p1_as_specified": bool(stopped),
        "eras_stopped": [e.league for e in stopped],
        "branch": branch,
        "reason": reason,
    }
