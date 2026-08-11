"""A league whose answer is known, and what the estimator does with it.

Every check the plus-minus currently ships is a negative control: shuffle the
labels and confirm nothing survives. None of them says the machinery finds
something when something is there. This module is the positive control. It
generates a league with trajectories, a team-season effect, roster churn as a
dial and mode-specific censored margins, hands it to the same estimator a
season-varying plus-minus would use, and measures four things the phase after
this one is about to depend on:

1. **Recovery.** How well the fitted β_{p,t} track the true ones, and — the
   question that actually matters — how well *teammates* are separated, which is
   measured on deviations from the team-season mean rather than on levels. Run
   across a churn dial, so "how much roster movement does this need" has a
   number rather than an opinion.
2. **Coverage.** Whether the penalized-Hessian standard errors this project
   publishes cover the truth at their nominal rate. The blend in P5 and every
   interval in PE assume they do; ridge intervals generally do not.
3. **Power.** The minimum next-season persistence gap a paired bootstrap could
   detect at the record's own sample size, stated before the test is run rather
   than read off a p-value afterwards.
4. **The smoother's leak.** A two-sided random-walk penalty pulls a season's
   coefficient toward the next season's maps, so a forward test scored against
   smoothed coefficients is scored against a target that already contains the
   answer. On synthetic data the true next-season value is known, so the size of
   that inflation is measurable rather than argued about.

**The estimator here is not P1.** It is the smallest generalized ridge that can
answer the questions above — dense, unregistered, and run on generated data
only. It writes nothing, reads no map row, and has no scope column, no migration
and no published coefficient. If the pre-flight and these curves say the design
is available, P1 writes the real one; if they do not, this is the harness that
said so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any

import numpy as np

from ..regress import FloatArray
from . import preflight
from .rapm import AdmittedMap

# The master seed. Every experiment below draws a child sequence from it, so the
# whole harness is reproducible from this one number and no experiment's stream
# depends on the order the others ran in.
SEED = 20200124  # the CDL's first match day

# Two-sided 5%, 80% power.
Z_ALPHA = 1.959964
Z_POWER = 0.841621

# How much a box-score forecaster and a plus-minus forecaster agree, for the
# dependent-correlation variance in the power calculation. The published
# rating-versus-K/D comparison sits near here; it is swept in the curve.
PREDICTOR_CORR = 0.7

# Score caps by mode, from the real record: Hardpoint plays to 250, Control to 3
# rounds, Search & Destroy to 6. A blowout truncates, which is why the response
# is censored rather than merely heteroskedastic.
MODE_CAPS: dict[str, int] = {"hardpoint": 250, "control": 3, "search-and-destroy": 6}


@dataclass(frozen=True)
class LeagueConfig:
    """The generated league's shape. Defaults track the CDL era as measured.

    `churn` is the share of a team's maps played by something other than its
    modal lineup — the dial the recovery curve is swept over. At 0.0 a team
    fields one lineup all season, which is exactly the case the pre-flight found
    in half the record and the case no penalty can rescue.
    """

    teams: int = 12
    seasons: int = 7
    roster: int = 4
    # Players beyond the starting five who can be rotated in.
    bench: int = 2
    maps_per_team_season: int = 165
    churn: float = 0.15
    # Spread of true player value, in units of the standardized response.
    player_sd: float = 0.35
    # Spread of the team-season effect, which is not a player's to keep.
    team_sd: float = 0.30
    # Within-player season-to-season drift.
    drift_sd: float = 0.12
    # Unexplained map noise.
    noise_sd: float = 1.0
    # Per-player LAN increment, unmodelled by the estimator. Zero by default so
    # the headline curve is clean; turned on to ask what an unmodelled context
    # effect costs.
    venue_sd: float = 0.0
    lan_share: float = 0.35
    # How often a player leaves for another team between seasons.
    transfer_rate: float = 0.20


@dataclass(frozen=True)
class SimGame:
    """One generated map, in the shape the design matrix reads."""

    season: int
    home_team: int
    away_team: int
    home_players: tuple[int, ...]
    away_players: tuple[int, ...]
    mode: str
    margin: float
    lan: bool


@dataclass
class League:
    games: list[SimGame]
    # The quantity the estimator is judged against: β for (player, season).
    truth: dict[tuple[int, int], float] = field(default_factory=dict)
    team_truth: dict[tuple[int, int], float] = field(default_factory=dict)
    # Which team a player was on in a season, for the deviation metric.
    team_of: dict[tuple[int, int], int] = field(default_factory=dict)


def _trajectory(rng: np.random.Generator, seasons: int, cfg: LeagueConfig) -> list[float]:
    """One career: a level, a peak, and a random walk around them.

    The shape is deliberately not the shape the estimator assumes. A random walk
    penalty expects smooth drift; a real career has a peak, so the generator
    supplies one and the recovery number says what the mismatch costs.
    """
    level = float(rng.normal(0.0, cfg.player_sd))
    peak = float(rng.uniform(0, seasons - 1))
    width = float(rng.uniform(seasons / 3.0, seasons))
    amplitude = float(abs(rng.normal(0.0, cfg.player_sd)))
    walk = np.cumsum(rng.normal(0.0, cfg.drift_sd, size=seasons))
    return [
        level + amplitude * max(0.0, 1.0 - ((t - peak) / width) ** 2) + float(walk[t])
        for t in range(seasons)
    ]


def generate(cfg: LeagueConfig, rng: np.random.Generator) -> League:
    """A league with known coefficients, played out map by map."""
    squad = cfg.roster + 1 + cfg.bench
    players_per_team = squad
    total_players = cfg.teams * players_per_team
    trajectories = [_trajectory(rng, cfg.seasons, cfg) for _ in range(total_players)]
    venue = rng.normal(0.0, cfg.venue_sd, size=total_players) if cfg.venue_sd > 0 else None
    # Who starts, decided once. See `_lineups`.
    priority = np.asarray(rng.permutation(total_players), dtype=float)

    # Season 0 rosters, then transfers between seasons: a player who moves takes
    # their trajectory with them, which is the only thing linking two teams'
    # columns within a season and the whole reason the design is identified at
    # all.
    rosters: list[list[int]] = [
        list(range(t * players_per_team, (t + 1) * players_per_team)) for t in range(cfg.teams)
    ]
    league = League(games=[])
    modes = sorted(MODE_CAPS)

    for season in range(cfg.seasons):
        if season > 0:
            rosters = _transfer(rosters, cfg, rng)
        team_effect = rng.normal(0.0, cfg.team_sd, size=cfg.teams)
        for team, members in enumerate(rosters):
            league.team_truth[(team, season)] = float(team_effect[team])
            for pid in members:
                league.truth[(pid, season)] = trajectories[pid][season]
                league.team_of[(pid, season)] = team

        lineups = [_lineups(members, cfg, priority) for members in rosters]
        maps = cfg.teams * cfg.maps_per_team_season // 2
        for _ in range(maps):
            home, away = rng.choice(cfg.teams, size=2, replace=False)
            home_line = _draw_lineup(lineups[int(home)], cfg, rng)
            away_line = _draw_lineup(lineups[int(away)], cfg, rng)
            lan = bool(rng.random() < cfg.lan_share)
            strength = sum(trajectories[p][season] for p in home_line) - sum(
                trajectories[p][season] for p in away_line
            )
            strength += float(team_effect[int(home)] - team_effect[int(away)])
            if venue is not None and lan:
                strength += float(
                    sum(venue[p] for p in home_line) - sum(venue[p] for p in away_line)
                )
            mode = modes[int(rng.integers(0, len(modes)))]
            league.games.append(
                SimGame(
                    season=season,
                    home_team=int(home),
                    away_team=int(away),
                    home_players=tuple(sorted(int(p) for p in home_line)),
                    away_players=tuple(sorted(int(p) for p in away_line)),
                    mode=mode,
                    margin=_margin(strength + float(rng.normal(0.0, cfg.noise_sd)), mode),
                    lan=lan,
                )
            )
    return league


def strengths(cfg: LeagueConfig, rng: np.random.Generator) -> FloatArray:
    """The noiseless advantage of every generated map, for calibration."""
    league = generate(replace(cfg, noise_sd=0.0), rng)
    out: list[float] = []
    for game in league.games:
        home = sum(league.truth[(p, game.season)] for p in game.home_players)
        away = sum(league.truth[(p, game.season)] for p in game.away_players)
        team = (
            league.team_truth[(game.home_team, game.season)]
            - league.team_truth[(game.away_team, game.season)]
        )
        out.append(home - away + team)
    return np.asarray(out, dtype=float)


def _accuracy(advantage: FloatArray, noise: float) -> float:
    """How often the better side wins, at this much noise.

    P(sign(s + ε) = sign(s)) = Φ(|s|/σ), averaged over the schedule. Closed form,
    so calibration needs no second simulation.
    """
    if noise <= 0.0:
        return 1.0
    return float(np.mean(0.5 * (1.0 + _erf(np.abs(advantage) / (noise * np.sqrt(2.0))))))


def calibrate_noise(
    cfg: LeagueConfig,
    target_accuracy: float,
    rng: np.random.Generator,
) -> LeagueConfig:
    """Set the map noise so the generated league is as predictable as the real one.

    Left uncalibrated, a recovery curve is a statement about whatever
    signal-to-noise the generator happened to be written with. The real record
    supplies the number — the map model's own holdout accuracy — and the noise
    term is solved to match it, so "the estimator recovers the trajectories" is a
    claim about a league as hard to predict as this one.
    """
    advantage = strengths(cfg, rng)
    low, high = 1e-3, 50.0
    for _ in range(60):
        middle = 0.5 * (low + high)
        if _accuracy(advantage, middle) > target_accuracy:
            low = middle
        else:
            high = middle
    return replace(cfg, noise_sd=0.5 * (low + high))


def _margin(latent: float, mode: str) -> float:
    """A latent advantage, realized as a score margin this mode can express.

    Hardpoint runs to 250 and Search & Destroy to 6, so the same advantage is a
    different number in each, and both truncate at the cap. The censoring is the
    point: the best players live in the tail the cap removes.
    """
    cap = MODE_CAPS[mode]
    scale = cap / 3.0
    raw = latent * scale
    signed = float(np.clip(raw, -cap, cap))
    if abs(signed) < 1.0:
        signed = 1.0 if signed >= 0 else -1.0
    return float(np.round(signed)) if cap <= 10 else float(np.round(signed, 1))


def _lineups(members: Sequence[int], cfg: LeagueConfig, priority: FloatArray) -> list[list[int]]:
    """A modal lineup plus the alternates churn will reach for.

    Who starts is fixed by a priority drawn once for each player, not redrawn
    each season. That matters more than it looks: redrawing would make every
    season a fresh lineup even with churn at zero, and the zero-churn control —
    the one case where nothing within a season can separate teammates — would
    quietly stop being a control.
    """
    pool = sorted(members, key=lambda pid: float(priority[pid]))
    modal = sorted(pool[: cfg.roster])
    alternates: list[list[int]] = []
    for extra in pool[cfg.roster :]:
        alternates.append(sorted([*modal[1:], extra]))
    return [modal, *alternates]


def _draw_lineup(
    lineups: Sequence[Sequence[int]], cfg: LeagueConfig, rng: np.random.Generator
) -> list[int]:
    if len(lineups) == 1 or rng.random() >= cfg.churn:
        return list(lineups[0])
    return list(lineups[1 + int(rng.integers(0, len(lineups) - 1))])


def _transfer(
    rosters: Sequence[list[int]], cfg: LeagueConfig, rng: np.random.Generator
) -> list[list[int]]:
    """Move a share of players between teams, keeping squad sizes fixed."""
    moved = [list(r) for r in rosters]
    for team, members in enumerate(moved):
        for slot, pid in enumerate(members):
            if rng.random() >= cfg.transfer_rate:
                continue
            other = int(rng.integers(0, len(moved)))
            if other == team:
                continue
            other_slot = int(rng.integers(0, len(moved[other])))
            members[slot], moved[other][other_slot] = moved[other][other_slot], pid
    return moved


# MARK: the estimator under test


@dataclass(frozen=True)
class Columns:
    """The season-expanded design's column index."""

    players: dict[tuple[int, int], int]
    teams: dict[tuple[int, int], int]

    @property
    def size(self) -> int:
        return len(self.players) + len(self.teams)


def _columns(games: Sequence[SimGame]) -> Columns:
    player_seasons = sorted(
        {(p, g.season) for g in games for p in (*g.home_players, *g.away_players)}
    )
    team_seasons = sorted({(t, g.season) for g in games for t in (g.home_team, g.away_team)})
    return Columns(
        players={key: i for i, key in enumerate(player_seasons)},
        teams={key: len(player_seasons) + i for i, key in enumerate(team_seasons)},
    )


def _normal_scores(games: Sequence[SimGame]) -> FloatArray:
    """Margins ranked to normal scores within (season, mode).

    P1 names this as the cheap defensible treatment of a censored response, and
    it is the one used here: the ranks survive the cap, which the raw margin does
    not.
    """
    y = np.zeros(len(games), dtype=float)
    groups: dict[tuple[int, str], list[int]] = {}
    for i, game in enumerate(games):
        groups.setdefault((game.season, game.mode), []).append(i)
    for _key, index in sorted(groups.items()):
        values = np.array([games[i].margin for i in index], dtype=float)
        order = values.argsort(kind="stable")
        ranks = np.empty(len(index), dtype=float)
        ranks[order] = np.arange(1, len(index) + 1, dtype=float)
        quantiles = ranks / (len(index) + 1.0)
        y[index] = np.sqrt(2.0) * _erfinv(2.0 * quantiles - 1.0)
    return y


def _erfinv(x: FloatArray) -> FloatArray:
    """Inverse error function by Newton refinement of a rational start.

    `scipy` owns this in P1; the phase that decides whether P1 happens should not
    be the phase that adds its dependency.
    """
    a = 0.147
    ln = np.log(1.0 - x**2)
    term = 2.0 / (np.pi * a) + ln / 2.0
    guess = np.sign(x) * np.sqrt(np.sqrt(term**2 - ln / a) - term)
    for _ in range(3):
        error = _erf(guess) - x
        guess = guess - error / (2.0 / np.sqrt(np.pi) * np.exp(-(guess**2)))
    return np.asarray(guess, dtype=float)


def _erf(x: FloatArray) -> FloatArray:
    """Abramowitz-Stegun 7.1.26, enough for a rank transform."""
    sign = np.sign(x)
    z = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (
        0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))
    )
    return np.asarray(sign * (1.0 - poly * np.exp(-(z**2))), dtype=float)


def _design(games: Sequence[SimGame], columns: Columns) -> FloatArray:
    x = np.zeros((len(games), columns.size), dtype=float)
    for i, game in enumerate(games):
        for pid in game.home_players:
            x[i, columns.players[(pid, game.season)]] = 1.0
        for pid in game.away_players:
            x[i, columns.players[(pid, game.season)]] = -1.0
        x[i, columns.teams[(game.home_team, game.season)]] = 1.0
        x[i, columns.teams[(game.away_team, game.season)]] = -1.0
    return x


def _walk_penalty(columns: Columns) -> FloatArray:
    """DᵀD for the within-player first difference, over player columns only.

    Team-season effects are not walked: a roster is a different object each year
    and smoothing it would launder team quality across seasons, which is the very
    thing the team column was added to stop.
    """
    size = columns.size
    penalty = np.zeros((size, size), dtype=float)
    by_player: dict[int, list[tuple[int, int]]] = {}
    for (pid, season), col in columns.players.items():
        by_player.setdefault(pid, []).append((season, col))
    for entries in by_player.values():
        entries.sort()
        for (_s0, left), (_s1, right) in zip(entries, entries[1:], strict=False):
            penalty[left, left] += 1.0
            penalty[right, right] += 1.0
            penalty[left, right] -= 1.0
            penalty[right, left] -= 1.0
    return penalty


@dataclass
class StateSpaceFit:
    columns: Columns
    beta: FloatArray
    se: FloatArray
    effective_df: float

    def player(self, key: tuple[int, int]) -> float | None:
        col = self.columns.players.get(key)
        return None if col is None else float(self.beta[col])

    def player_se(self, key: tuple[int, int]) -> float | None:
        col = self.columns.players.get(key)
        return None if col is None else float(self.se[col])


def fit_state_space(
    games: Sequence[SimGame],
    lambda0: float = 1.0,
    lambda_walk: float = 1.0,
    columns: Columns | None = None,
) -> StateSpaceFit:
    """‖y − Xβ‖² + λ₀‖β‖² + λ_w Σ(β_{p,t} − β_{p,t−1})², solved directly.

    Standard errors come from the penalized Hessian, the same convention the
    published plus-minus uses, because the point is to test *those* intervals
    rather than a better set nobody would ship.
    """
    index = columns or _columns(games)
    x = _design(games, index)
    y = _normal_scores(games)
    gram = x.T @ x
    amat = gram + lambda0 * np.eye(index.size) + lambda_walk * _walk_penalty(index)
    inverse = np.linalg.inv(amat)
    beta = inverse @ (x.T @ y)
    residual = y - x @ beta
    effective_df = float(np.trace(inverse @ gram))
    dof = max(len(games) - effective_df, 1.0)
    sigma2 = float(residual @ residual) / dof
    se = np.sqrt(np.maximum(sigma2 * np.diag(inverse), 0.0))
    return StateSpaceFit(columns=index, beta=beta, se=se, effective_df=effective_df)


def fit_filtered(
    games: Sequence[SimGame],
    lambda0: float = 1.0,
    lambda_walk: float = 1.0,
) -> dict[tuple[int, int], float]:
    """β at season t fitted on maps through t only — one solve per season.

    The one-sided family. Its whole purpose is that it cannot have seen season
    t+1, which is what makes a forward test a test.
    """
    out: dict[tuple[int, int], float] = {}
    for season in sorted({g.season for g in games}):
        through = [g for g in games if g.season <= season]
        fit = fit_state_space(through, lambda0, lambda_walk)
        for (pid, s), _col in fit.columns.players.items():
            if s == season:
                value = fit.player((pid, s))
                if value is not None:
                    out[(pid, s)] = value
    return out


# MARK: experiments


def as_admitted(league: League) -> list[AdmittedMap]:
    """The generated maps in the shape the pre-flight measures.

    So the churn dial reports in the same units the real record does: a recovery
    curve indexed by "effective lineups per team-season" can be read against the
    measured record, which a curve indexed by an invented probability cannot.
    """
    base = date(2020, 1, 24)
    out: list[AdmittedMap] = []
    for i, game in enumerate(league.games):
        out.append(
            AdmittedMap(
                game_id=i,
                # A generated league has no series: each map is its own, which
                # is the conservative reading for anything that clusters.
                series_id=i,
                season_id=game.season,
                title=f"SIM{game.season}",
                mode_slug=game.mode,
                played_at=base + timedelta(days=game.season * 365 + (i % 300)),
                home_team_id=game.home_team,
                away_team_id=game.away_team,
                home_players=game.home_players,
                away_players=game.away_players,
                home_won=game.margin > 0,
                home_margin=game.margin,
            )
        )
    return out


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    a = np.asarray(left, dtype=float)
    b = np.asarray(right, dtype=float)
    if a.size < 3 or a.std() == 0.0 or b.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def _deviations(
    values: dict[tuple[int, int], float], team_of: dict[tuple[int, int], int]
) -> dict[tuple[int, int], float]:
    """Each player-season minus the mean of its team-season.

    The level of a team-season is the part every estimator gets roughly right;
    what a season-varying player rating claims to know is who *within* it was
    worth more, so that is what the recovery number is computed on.
    """
    grouped: dict[tuple[int, int], list[float]] = {}
    for key, value in values.items():
        team = team_of.get(key)
        if team is None:
            continue
        grouped.setdefault((team, key[1]), []).append(value)
    means = {k: float(np.mean(v)) for k, v in grouped.items()}
    out: dict[tuple[int, int], float] = {}
    for key, value in values.items():
        team = team_of.get(key)
        if team is None:
            continue
        out[key] = value - means[(team, key[1])]
    return out


@dataclass(frozen=True)
class Recovery:
    """One league, fitted and scored against its own truth."""

    churn: float
    effective_lineups: float
    identified_share: float
    corr_level: float
    corr_within_team: float
    rmse: float
    coverage: float
    coverage_within_team: float

    def payload(self) -> dict[str, Any]:
        return {
            "churn": round(self.churn, 3),
            "effective_lineups": round(self.effective_lineups, 2),
            "identified_share": round(self.identified_share, 4),
            "corr_level": round(self.corr_level, 4),
            "corr_within_team": round(self.corr_within_team, 4),
            "rmse": round(self.rmse, 4),
            "coverage": round(self.coverage, 4),
            "coverage_within_team": round(self.coverage_within_team, 4),
        }


def run_once(cfg: LeagueConfig, rng: np.random.Generator, lambda_walk: float = 1.0) -> Recovery:
    """Generate a league, fit it, and score the fit against what was put there."""
    league = generate(cfg, rng)
    fit = fit_state_space(league.games, lambda_walk=lambda_walk)

    keys = sorted(k for k in league.truth if fit.player(k) is not None)
    fitted = {k: fit.player(k) or 0.0 for k in keys}
    truth = {k: league.truth[k] for k in keys}
    fitted_dev = _deviations(fitted, league.team_of)
    truth_dev = _deviations(truth, league.team_of)

    ses = {k: fit.player_se(k) or 0.0 for k in keys}
    covered = [abs(fitted[k] - truth[k]) <= Z_ALPHA * ses[k] for k in keys]
    covered_dev = [
        abs(fitted_dev[k] - truth_dev[k]) <= Z_ALPHA * ses[k] for k in keys if k in fitted_dev
    ]

    admitted = as_admitted(league)
    supply = preflight.lineup_supply(admitted)
    spectra = [
        preflight.season_spectrum([g for g in admitted if g.season_id == s])
        for s in sorted({g.season_id for g in admitted})
    ]
    identified = sum(s.rank for s in spectra) / max(sum(s.player_columns for s in spectra), 1)

    return Recovery(
        churn=cfg.churn,
        effective_lineups=float(np.median([ts.effective_lineups for ts in supply])),
        identified_share=identified,
        corr_level=_correlation([fitted[k] for k in keys], [truth[k] for k in keys]),
        corr_within_team=_correlation(
            [fitted_dev[k] for k in sorted(fitted_dev)],
            [truth_dev[k] for k in sorted(truth_dev)],
        ),
        rmse=float(np.sqrt(np.mean([(fitted[k] - truth[k]) ** 2 for k in keys]))),
        coverage=float(np.mean(covered)),
        coverage_within_team=float(np.mean(covered_dev)) if covered_dev else float("nan"),
    )


def recovery_curve(
    churn_levels: Sequence[float],
    replicates: int,
    cfg: LeagueConfig,
    seed: np.random.SeedSequence,
) -> list[dict[str, Any]]:
    """Recovery against the churn dial, averaged over replicate leagues.

    Run twice by `artifact` below, on an **open** league where players transfer
    between seasons and a **closed** one where nobody moves. The difference is
    the whole question: in an open league the random-walk penalty can import a
    teammate difference from another season, so recovery at zero churn measures
    what was borrowed rather than what this season identified. The closed league
    is the same estimator with nothing to borrow.
    """
    out: list[dict[str, Any]] = []
    for level, child in zip(churn_levels, seed.spawn(len(churn_levels)), strict=True):
        rng = np.random.default_rng(child)
        runs = [run_once(replace(cfg, churn=level), rng) for _ in range(replicates)]
        out.append(
            {
                "churn": round(level, 3),
                "replicates": replicates,
                "effective_lineups": round(float(np.mean([r.effective_lineups for r in runs])), 2),
                "identified_share": round(float(np.mean([r.identified_share for r in runs])), 4),
                "corr_level": round(float(np.mean([r.corr_level for r in runs])), 4),
                "corr_within_team": round(float(np.mean([r.corr_within_team for r in runs])), 4),
                "corr_within_team_sd": round(float(np.std([r.corr_within_team for r in runs])), 4),
                "rmse": round(float(np.mean([r.rmse for r in runs])), 4),
                "coverage": round(float(np.mean([r.coverage for r in runs])), 4),
                "coverage_within_team": round(
                    float(np.nanmean([r.coverage_within_team for r in runs])), 4
                ),
            }
        )
    return out


def persistence_mde(n_pairs: int, baseline_r: float, predictor_corr: float) -> dict[str, Any]:
    """The smallest persistence gap the record could detect at 80% power.

    Two predictors of the same next-season target share their observations, so
    their correlations are dependent and the variance of the difference is not
    the sum of the two variances. Olkin's formula gives it in closed form —

        var(r) = (1 − r²)² / n
        cov(r_ay, r_by) = [ (r_ab − ½ r_ay r_by)(1 − r_ay² − r_by² − r_ab²) + r_ab³ ] / n

    — so the detectable gap is (z_.975 + z_.80)·sqrt(var + var − 2cov), swept
    until the assumed gap clears it. No simulation, and nothing to seed.

    **This is a floor, not the number PE will get.** The formula assumes
    independent observations; the plan's resampling unit is the series, and
    clustering only widens the interval. A gate declared at this value would be
    optimistic by whatever the intra-cluster correlation is worth.
    """
    if n_pairs < 10:
        return {"available": False, "reason": "too few paired seasons"}
    curve: list[dict[str, Any]] = []
    detectable: dict[str, Any] | None = None
    for step in range(1, 41):
        gap = round(0.01 * step, 3)
        skill_r = min(baseline_r + gap, 0.99)
        var_a = (1.0 - skill_r**2) ** 2 / n_pairs
        var_b = (1.0 - baseline_r**2) ** 2 / n_pairs
        cov = (
            (predictor_corr - 0.5 * skill_r * baseline_r)
            * (1.0 - skill_r**2 - baseline_r**2 - predictor_corr**2)
            + predictor_corr**3
        ) / n_pairs
        se = float(np.sqrt(max(var_a + var_b - 2.0 * cov, 1e-12)))
        point = {
            "gap": gap,
            "se": round(se, 5),
            "mde80": round((Z_ALPHA + Z_POWER) * se, 4),
            "detectable": bool(gap >= (Z_ALPHA + Z_POWER) * se),
        }
        curve.append(point)
        if detectable is None and point["detectable"]:
            detectable = point
    return {
        "available": True,
        "n_pairs": n_pairs,
        "baseline_r": baseline_r,
        "predictor_corr": predictor_corr,
        "criterion": (
            "smallest gap in next-season persistence r whose dependent-correlation "
            "standard error clears 80% power at a two-sided 5% level"
        ),
        "caveat": ("independent observations; the plan resamples clusters, which widens this"),
        "mde80": detectable["gap"] if detectable else None,
        "curve": curve,
    }


def smoothing_inflation(
    cfg: LeagueConfig,
    replicates: int,
    seed: np.random.SeedSequence,
    lambda_walk: float = 1.0,
) -> dict[str, Any]:
    """What a two-sided penalty adds to a forward test that should not have it.

    Both families predict the same thing — the *true* β of the following season —
    and only one of them was allowed to see it. The gap between their persistence
    correlations is the leak, measured rather than asserted.
    """
    smoothed: list[float] = []
    filtered: list[float] = []
    for child in seed.spawn(replicates):
        rng = np.random.default_rng(child)
        league = generate(cfg, rng)
        full = fit_state_space(league.games, lambda_walk=lambda_walk)
        one_sided = fit_filtered(league.games, lambda_walk=lambda_walk)
        pairs = [k for k in league.truth if (k[0], k[1] + 1) in league.truth]
        target = [league.truth[(pid, season + 1)] for pid, season in pairs]
        smoothed.append(_correlation([full.player(k) or 0.0 for k in pairs], target))
        filtered.append(_correlation([one_sided.get(k, 0.0) for k in pairs], target))
    return {
        "replicates": replicates,
        "what": (
            "correlation with the following season's true coefficient, from a two-sided "
            "penalty that has already seen it and from a one-sided one that has not"
        ),
        "smoothed_r": round(float(np.mean(smoothed)), 4),
        "filtered_r": round(float(np.mean(filtered)), 4),
        "inflation": round(float(np.mean(smoothed) - np.mean(filtered)), 4),
    }


# The churn levels the curves are swept over. Zero is the case the pre-flight
# found in half the record; the top of the range is more movement than any real
# team-season has.
CHURN_LEVELS: tuple[float, ...] = (0.0, 0.05, 0.15, 0.30, 0.50)

# Replicate leagues per churn level. Small deliberately: the standard deviation
# across replicates is published beside the mean, so a reader can see whether
# more would have changed anything.
REPLICATES = 4


def recovery_at(curve: Sequence[dict[str, Any]], effective_lineups: float) -> float | None:
    """Read a curve at the lineup variety a real era actually supplies.

    The churn dial is an invented probability; effective lineups per team-season
    is a measured quantity, and both the simulation and the record report it. So
    the curve is indexed by the measured one and interpolated at the record's own
    value, which is what makes "recovery fails at the observed churn level" a
    statement about this record rather than about the generator.
    """
    points = sorted(
        ((float(p["effective_lineups"]), float(p["corr_within_team"])) for p in curve),
        key=lambda pair: pair[0],
    )
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    if effective_lineups <= xs[0]:
        return ys[0]
    if effective_lineups >= xs[-1]:
        return ys[-1]
    return float(np.interp(effective_lineups, xs, ys))


def artifact(
    map_accuracy: float,
    n_persistence_pairs: int,
    baseline_r: float,
    cfg: LeagueConfig | None = None,
    replicates: int = REPLICATES,
    seed: int = SEED,
) -> dict[str, Any]:
    """The whole harness: calibrate, sweep, cover, power, and the smoother's leak.

    `map_accuracy` is the real record's own map-model holdout accuracy and
    `n_persistence_pairs` its count of consecutive player-seasons, both passed in
    rather than assumed, so the synthetic league is as hard as the real one and
    the power statement is about the sample size that exists.
    """
    base = cfg or LeagueConfig()
    sequence = np.random.SeedSequence(seed)
    calibration, open_seed, closed_seed, leak_seed = sequence.spawn(4)
    tuned = calibrate_noise(base, map_accuracy, np.random.default_rng(calibration))
    closed = replace(tuned, transfer_rate=0.0)

    open_curve = recovery_curve(CHURN_LEVELS, replicates, tuned, open_seed)
    closed_curve = recovery_curve(CHURN_LEVELS, replicates, closed, closed_seed)
    return {
        "what": (
            "a league whose coefficients are known, fitted by the estimator P1 would use, "
            "to say how much roster movement season-varying value needs before it can be "
            "recovered at all"
        ),
        "seed": seed,
        "replicates": replicates,
        "config": {
            "teams": tuned.teams,
            "seasons": tuned.seasons,
            "roster": tuned.roster,
            "bench": tuned.bench,
            "maps_per_team_season": tuned.maps_per_team_season,
            "player_sd": tuned.player_sd,
            "team_sd": tuned.team_sd,
            "drift_sd": tuned.drift_sd,
            "transfer_rate": tuned.transfer_rate,
            "mode_caps": MODE_CAPS,
        },
        "calibration": {
            "target_map_accuracy": round(map_accuracy, 4),
            "noise_sd": round(tuned.noise_sd, 4),
            "what": "map noise solved so the generated league is as predictable as the record",
        },
        "recovery_open": open_curve,
        "recovery_closed": closed_curve,
        "borrowing": {
            "what": (
                "within-team recovery at zero churn, with players transferring between "
                "seasons and without. The gap is what the random-walk penalty imports from "
                "other seasons rather than reads from this one"
            ),
            "open": open_curve[0]["corr_within_team"],
            "closed": closed_curve[0]["corr_within_team"],
        },
        "power": persistence_mde(n_persistence_pairs, baseline_r, PREDICTOR_CORR),
        "smoothing": smoothing_inflation(tuned, max(2, replicates // 2), leak_seed),
    }
