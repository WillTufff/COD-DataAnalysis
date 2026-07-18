"""Plain series-level Elo. Spec: /methodology#elo.

Initial 1500, logistic expectation with scale 400, constant K. Undecided
series (archive missing the deciding map) are never rated.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

INITIAL = 1500.0
SCALE = 400.0


def expected(r_a: float, r_b: float) -> float:
    """P(A beats B)."""
    return 1.0 / (1.0 + math.pow(10.0, (r_b - r_a) / SCALE))


@dataclass
class Elo:
    k: float = 32.0
    ratings: dict[int, float] = field(default_factory=dict)

    def rating(self, team: int) -> float:
        return self.ratings.get(team, INITIAL)

    def update(self, team_a: int, team_b: int, a_won: bool) -> tuple[float, float, float]:
        """Rate one series. Returns (p_a_win, new_a, new_b) — prediction is
        walk-forward: computed before the update."""
        r_a, r_b = self.rating(team_a), self.rating(team_b)
        p = expected(r_a, r_b)
        s = 1.0 if a_won else 0.0
        self.ratings[team_a] = r_a + self.k * (s - p)
        self.ratings[team_b] = r_b + self.k * ((1.0 - s) - (1.0 - p))
        return p, self.ratings[team_a], self.ratings[team_b]
