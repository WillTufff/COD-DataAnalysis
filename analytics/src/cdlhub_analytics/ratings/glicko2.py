"""Glicko-2 (Glickman 2013, http://www.glicko.net/glicko/glicko2.pdf).

Spec: /methodology#glicko2. Implementation follows the paper's steps exactly;
tests pin the paper's worked example.

**Rating periods are real here.** An earlier version treated each series as its
own period and only ever advanced the two teams playing it, which meant the
volatility inflation never ran for an idle team: the rating deviation tracked
games played rather than time elapsed, and a roster returning from a layoff was
treated as exactly as well known as when it left. That is the specific failure
the volatility term exists to prevent. `Glicko2.advance` now runs a period over
a whole roster at once — every team is advanced, those with results by the
paper's update and those without by inflation alone — which is also the shape
the paper assumes (it wants 10-15 games per period, not one).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

INITIAL_R = 1500.0
INITIAL_RD = 350.0
INITIAL_SIGMA = 0.06
_GLICKO2_SCALE = 173.7178
_CONVERGENCE = 1e-6


@dataclass
class TeamState:
    r: float = INITIAL_R
    rd: float = INITIAL_RD
    sigma: float = INITIAL_SIGMA

    @property
    def mu(self) -> float:
        return (self.r - 1500.0) / _GLICKO2_SCALE

    @property
    def phi(self) -> float:
        return self.rd / _GLICKO2_SCALE


def _g(phi: float) -> float:
    return 1.0 / math.sqrt(1.0 + 3.0 * phi * phi / (math.pi * math.pi))


def _e(mu: float, mu_j: float, phi_j: float) -> float:
    return 1.0 / (1.0 + math.exp(-_g(phi_j) * (mu - mu_j)))


def _new_sigma(sigma: float, phi: float, v: float, delta: float, tau: float) -> float:
    """Illinois-method root find for the volatility update (paper step 5)."""
    a = math.log(sigma * sigma)
    phi2 = phi * phi
    d2 = delta * delta

    def f(x: float) -> float:
        ex = math.exp(x)
        return (ex * (d2 - phi2 - v - ex)) / (2.0 * (phi2 + v + ex) ** 2) - (x - a) / (tau * tau)

    big_a = a
    if d2 > phi2 + v:
        big_b = math.log(d2 - phi2 - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        big_b = a - k * tau
    fa, fb = f(big_a), f(big_b)
    while abs(big_b - big_a) > _CONVERGENCE:
        big_c = big_a + (big_a - big_b) * fa / (fb - fa)
        fc = f(big_c)
        if fc * fb <= 0:
            big_a, fa = big_b, fb
        else:
            fa /= 2.0
        big_b, fb = big_c, fc
    return math.exp(big_a / 2.0)


def rate(team: TeamState, opponents: Sequence[tuple[TeamState, float]], tau: float) -> TeamState:
    """One rating period for `team` against (opponent, score) results.

    With no results the rating is unchanged and only the deviation grows, capped
    at INITIAL_RD: 350 already means "no information", and a team idle for three
    years is not less known than a team that has never played.
    """
    if not opponents:
        phi_star = math.sqrt(team.phi**2 + team.sigma**2)
        return TeamState(team.r, min(phi_star * _GLICKO2_SCALE, INITIAL_RD), team.sigma)
    mu, phi = team.mu, team.phi
    v_inv = 0.0
    delta_sum = 0.0
    for opp, score in opponents:
        g_j = _g(opp.phi)
        e_j = _e(mu, opp.mu, opp.phi)
        v_inv += g_j * g_j * e_j * (1.0 - e_j)
        delta_sum += g_j * (score - e_j)
    v = 1.0 / v_inv
    delta = v * delta_sum
    sigma_new = _new_sigma(team.sigma, phi, v, delta, tau)
    phi_star = math.sqrt(phi * phi + sigma_new * sigma_new)
    phi_new = 1.0 / math.sqrt(1.0 / (phi_star * phi_star) + 1.0 / v)
    mu_new = mu + phi_new * phi_new * delta_sum
    return TeamState(
        r=mu_new * _GLICKO2_SCALE + 1500.0,
        rd=phi_new * _GLICKO2_SCALE,
        sigma=sigma_new,
    )


@dataclass
class Glicko2:
    tau: float = 0.5
    teams: dict[int, TeamState] = field(default_factory=dict)

    def state(self, team: int) -> TeamState:
        return self.teams.setdefault(team, TeamState())

    def predict(self, team_a: int, team_b: int) -> float:
        """P(A beats B) accounting for both teams' uncertainty."""
        a, b = self.state(team_a), self.state(team_b)
        g = _g(math.sqrt(a.phi**2 + b.phi**2))
        return 1.0 / (1.0 + math.exp(-g * (a.mu - b.mu)))

    def update(self, team_a: int, team_b: int, a_won: bool) -> float:
        """Rate one series as its own rating period; returns walk-forward P(A wins).

        A convenience for the degenerate one-series period. Note it advances only
        the two teams involved — callers wanting real periods want `advance`.
        """
        p = self.predict(team_a, team_b)
        s = 1.0 if a_won else 0.0
        self.advance({team_a: [(team_b, s)], team_b: [(team_a, 1.0 - s)]}, (team_a, team_b))
        return p

    def advance(
        self,
        results: Mapping[int, Sequence[tuple[int, float]]],
        roster: Iterable[int],
    ) -> None:
        """Close one rating period over `roster`.

        `results` maps a team to its (opponent, score) results in the period.
        Every team in `roster` is advanced whether or not it appears there, so
        an idle team gets its deviation inflated instead of being frozen.

        Opponent states are snapshotted before anything is written, so the order
        teams happen to be iterated in cannot change the outcome — within a
        period every result is scored against the ratings as they stood when the
        period opened, which is what makes a period a period.
        """
        before = {t: self.state(t) for t in {*roster, *results}}
        for played in results.values():
            for opponent, _score in played:
                before.setdefault(opponent, self.state(opponent))
        self.teams.update(
            {
                team: rate(
                    before[team],
                    [(before[o], s) for o, s in results.get(team, ())],
                    self.tau,
                )
                for team in before
            }
        )
