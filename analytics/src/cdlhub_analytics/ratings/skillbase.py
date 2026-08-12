"""The player-level team-outcome baseline the hard gate is declared against.

Weng-Lin / Plackett-Luce online rating — the family `openskill` implements — is
the obvious thing to do with a record of four-on-four map results and no box
score at all. That makes it the right adversary for a rating built to predict
value in wins: if knowing nothing but who beat whom forecasts a player's next
season as well as the whole box-score stack does, the stack is not earning its
place. The published Glicko-2 baseline is a *team* rating and cannot answer that
question, because it never sees which four players were on the server.

**It is a filter, not a smoother.** A player's rating after their last map of a
season is a function of that map and everything before it, and of nothing after —
so it is admissible in a forward test by construction, and it is the like-for-like
comparison the plan asks for. Nothing here needs the filtered/smoothed guard the
state-space coefficients need, and the harness still routes its reads through the
same manifest so the two baselines are handled by one rule.

**It runs as a stage, not a script.** It has a `model_runs` row, an artifact and a
backtest row like every other model here, because a baseline in a hard gate that
cannot be reproduced makes the gate unenforceable.

`openskill` is confined to this module behind fully typed signatures, per the
engineering policy — the same treatment `graphs.py` gives `networkx`. It ships
`py.typed` at 6.2.0, so it needs no `ignore_missing_imports` override and the
override list stays empty; the policy predicted otherwise and was wrong in the
cheap direction for the second phase running.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from openskill.models import PlackettLuce, PlackettLuceRating

from ..backtest import Prediction
from ..maprows import MapRow

MODEL = "openskill"
VERSION = "1.0.0"

# Plackett-Luce with the library's defaults: mu 25, sigma 25/3, beta sigma/2.
# Nothing here is tuned. A baseline tuned against the test it is the baseline for
# is not a baseline, and the published Elo and Glicko-2 settings are held the same
# way — swept as sensitivity, never selected.
MU = 25.0
SIGMA = 25.0 / 3.0

# A player-season needs this many maps before its end-of-season ordinal is
# published as a season skill. The qualification floor the era adjustment and the
# persistence test already use, so "enough maps to be a season" means one thing.
MIN_MAPS_SEASON = 8


@dataclass(frozen=True)
class Fit:
    """One walk-forward pass: what it predicted, and where it ended up."""

    # (player, season) -> ordinal after that player's last map of the season.
    season_skill: dict[tuple[int, int], float]
    # (player, season) -> maps behind it.
    season_maps: dict[tuple[int, int], int]
    # Pre-update map predictions, keyed by game id.
    predictions: dict[int, Prediction]
    # (player) -> final ordinal, mu and sigma, for the artifact's leaderboard.
    final: dict[int, tuple[float, float, float]]
    n_maps: int
    n_players: int


def _sides(members: Sequence[MapRow]) -> tuple[list[MapRow], list[MapRow], bool] | None:
    """The two sides of a map and whether the first won, or None if it is unusable."""
    teams = sorted({m.team_id for m in members})
    if len(teams) != 2 or members[0].winner_team_id is None:
        return None
    a = [m for m in members if m.team_id == teams[0]]
    b = [m for m in members if m.team_id == teams[1]]
    if not a or not b:
        return None
    return (a, b, teams[0] == members[0].winner_team_id)


def _chronological(rows: Sequence[MapRow]) -> list[list[MapRow]]:
    """Maps in the order they were played, each as its player rows.

    Ordered by kickoff and then by `map_key`, the map's natural key, so the pass
    does not depend on the loader's numbering of `games.id`. Maps whose series
    carries no `source_uid` fall to the end under their own id, which is stable
    within a run and is the only thing available for them.
    """
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        per_game[r.game_id].append(r)
    return [
        per_game[g]
        for g in sorted(
            per_game,
            key=lambda g: (
                per_game[g][0].played_at,
                per_game[g][0].map_key is None,
                per_game[g][0].map_key or "",
                g,
            ),
        )
    ]


def fit_walk_forward(rows: Sequence[MapRow]) -> Fit:
    """Rate every player online over the whole record, predicting before updating."""
    model = PlackettLuce(mu=MU, sigma=SIGMA)
    ratings: dict[int, PlackettLuceRating] = {}
    season_skill: dict[tuple[int, int], float] = {}
    season_maps: dict[tuple[int, int], int] = defaultdict(int)
    predictions: dict[int, Prediction] = {}
    n_maps = 0

    def rating_of(player_id: int) -> PlackettLuceRating:
        got = ratings.get(player_id)
        if got is None:
            got = model.rating(name=str(player_id))
            ratings[player_id] = got
        return got

    for members in _chronological(rows):
        split = _sides(members)
        if split is None:
            continue
        a, b, a_won = split
        a_ids = sorted({m.player_id for m in a})
        b_ids = sorted({m.player_id for m in b})
        team_a = [rating_of(p) for p in a_ids]
        team_b = [rating_of(p) for p in b_ids]

        # Strictly pre-update: the state that predicts this map is the state
        # every earlier map produced and nothing else.
        p_a = float(model.predict_win([team_a, team_b])[0])
        predictions[members[0].game_id] = Prediction(
            p=p_a, won=a_won, when=members[0].played_at, series_id=members[0].series_id
        )

        ranks: list[float] = [1.0, 2.0] if a_won else [2.0, 1.0]
        updated = model.rate([team_a, team_b], ranks=ranks)
        for ids, side in ((a_ids, updated[0]), (b_ids, updated[1])):
            for player_id, new in zip(ids, side, strict=True):
                ratings[player_id] = new
        for m in members:
            key = (m.player_id, m.season_id)
            season_maps[key] += 1
            season_skill[key] = float(ratings[m.player_id].ordinal())
        n_maps += 1

    return Fit(
        season_skill={k: v for k, v in season_skill.items() if season_maps[k] >= MIN_MAPS_SEASON},
        season_maps=dict(season_maps),
        predictions=predictions,
        final={
            p: (float(r.ordinal()), float(r.mu), float(r.sigma)) for p, r in sorted(ratings.items())
        },
        n_maps=n_maps,
        n_players=len(ratings),
    )


def artifact(fit: Fit, names: dict[int, str], top: int = 40) -> dict[str, Any]:
    """The baseline's own record: settings, coverage, and where it ends up."""
    ranked = sorted(fit.final.items(), key=lambda kv: (-kv[1][0], kv[0]))
    return {
        "what": (
            "Plackett-Luce online player rating over four-on-four map results, at library "
            "defaults and tuned against nothing — the team-outcome baseline the persistence "
            "gate is declared against"
        ),
        "model": "plackett_luce",
        "params": {"mu": MU, "sigma": SIGMA, "min_maps_season": MIN_MAPS_SEASON},
        "n_maps": fit.n_maps,
        "n_players": fit.n_players,
        "n_player_seasons_published": len(fit.season_skill),
        "n_player_seasons_thin": len(fit.season_maps) - len(fit.season_skill),
        "filtered_by_construction": (
            "an online rating after a player's last map of a season is a function of that "
            "season and the ones before it, never of the one being predicted"
        ),
        "leaders": [
            {
                "player": names.get(player_id, str(player_id)),
                "ordinal": round(ordinal, 3),
                "mu": round(mu, 3),
                "sigma": round(sigma, 3),
            }
            for player_id, (ordinal, mu, sigma) in ranked[:top]
        ],
    }


def date_range(fit: Fit) -> tuple[date, date] | None:
    if not fit.predictions:
        return None
    when = [p.when for p in fit.predictions.values()]
    return (min(when), max(when))
