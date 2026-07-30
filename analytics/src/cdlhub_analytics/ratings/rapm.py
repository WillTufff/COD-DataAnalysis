"""Regularized adjusted plus-minus. Spec: /methodology#rapm.

Every other player number on this site starts from the box score: kills, deaths,
hill time, and a learned weighting over them. That makes all of them vulnerable
to the same objection, and the holdout tests have already shown it bites — the
composite rating is a decomposition of the scoreboard, and it does not forecast.

RAPM asks the question from the opposite end. Forget what a player did; look only
at whether their team won the map, and at who else was on the server. One row per
map, one column per player, +1 for the players on one side and −1 for the other,
ridge-regressed on the map result. A coefficient is then that player's estimated
contribution to the log-odds of winning a map, holding the other seven players
constant. Nothing from the box score enters at any point.

4v4 with heavy roster churn across three titles and eighteen events is close to
the ideal case for this: 5,087 decided maps over 273 players, and rosters that
move enough to separate teammates from each other. Close to ideal is not ideal,
and two things have to be reported rather than assumed away.

**Collinearity.** Four players who never play apart are one column wearing four
names, and no amount of data separates them; ridge responds by splitting the
credit evenly. That is the correct behaviour and it is also indistinguishable
from a finding, so the artifact reports each player's *teammate concentration* —
the share of their maps spent alongside their most frequent teammate — next to
their coefficient. A player at 0.95 has a number that is mostly their team's.

**Shrinkage.** With ~270 players against a few thousand maps, most coefficients
are dominated by the penalty. Per-player standard errors come from the penalized
Hessian and are published with every coefficient. A leaderboard of point
estimates with short careers at the top would be an artifact of the method.

Two variants are fit. Plain RAPM shrinks toward zero: the prior is that a player
is average until the results say otherwise. Prior-centered RAPM shrinks toward
the box-score rating instead, with the rating's exchange rate into map-win logits
learned on the training maps rather than assumed — the "composite defensible from
two independent directions" this was built for. Both are handed to the same
walk-forward roster forecast that scores the composite rating and Glicko-2, so
the question "does measuring value in wins beat measuring it in box scores"
is answered on identical maps with paired intervals.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..maprows import MapRow
from ..regress import FloatArray, fit_logistic_l2

# The ridge strength. Deliberately not tuned against the held-out maps — that
# would make the forecast below a selection statistic rather than a test — so it
# is the same l2 = 1 the rest of the package uses, on a design matrix of ±1
# columns that needs no standardization. `ridge_path` reports what other values
# would have done.
L2 = 1.0

# Below this many maps a player is not given a published coefficient. Ridge will
# happily return a number for someone with three maps; it is the prior, not the
# player.
MIN_MAPS = 20

# Ridge strengths reported alongside the fit, to show how much of any coefficient
# is the data and how much is the penalty.
RIDGE_PATH = (0.25, 1.0, 4.0, 16.0, 64.0)


@dataclass(frozen=True)
class PlayerRapm:
    player_id: int
    maps: int
    coef: float
    se: float
    # Share of this player's maps played alongside their most frequent teammate.
    # 1.0 means the column is inseparable from that teammate's.
    teammate_concentration: float


@dataclass
class RapmFit:
    players: list[PlayerRapm]
    intercept: float
    n_maps: int
    l2: float
    converged: bool

    def by_player(self) -> dict[int, float]:
        return {p.player_id: p.coef for p in self.players}


def build_design(
    rows: Sequence[MapRow],
) -> tuple[FloatArray, FloatArray, list[int], list[int]]:
    """(X, y, player ids by column, game ids by row).

    One row per decided map with two distinguishable teams. The side that sorts
    first by team id is +1 and is the side `y` refers to, matching how the map
    holdout orients every other predictor, so a coefficient's sign means the same
    thing here as it does there.
    """
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        if r.winner_team_id is not None:
            per_game[r.game_id].append(r)

    usable: list[tuple[int, list[MapRow], int, int, bool]] = []
    seen: set[int] = set()
    for game_id in sorted(per_game, key=lambda g: (per_game[g][0].played_at, g)):
        members = per_game[game_id]
        teams = sorted({m.team_id for m in members})
        if len(teams) != 2:
            continue
        a, b = teams
        a_won = next((m.won is True for m in members if m.team_id == a), None)
        if a_won is None:
            continue
        usable.append((game_id, members, a, b, a_won))
        seen.update(m.player_id for m in members)

    players = sorted(seen)
    col = {pid: i for i, pid in enumerate(players)}
    x = np.zeros((len(usable), len(players)), dtype=float)
    y = np.zeros(len(usable), dtype=float)
    game_ids: list[int] = []
    for i, (game_id, members, a, _b, a_won) in enumerate(usable):
        for m in members:
            x[i, col[m.player_id]] = 1.0 if m.team_id == a else -1.0
        y[i] = 1.0 if a_won else 0.0
        game_ids.append(game_id)
    return x, y, players, game_ids


def _teammate_concentration(rows: Sequence[MapRow]) -> dict[int, float]:
    """Per player, the share of their maps spent with their commonest teammate.

    The diagnostic that says whether a coefficient belongs to a player or to a
    lineup. Computed from the same rows the design matrix is built from.
    """
    per_game_team: dict[tuple[int, int], list[int]] = defaultdict(list)
    for r in rows:
        per_game_team[(r.game_id, r.team_id)].append(r.player_id)
    maps_with: dict[int, Counter[int]] = defaultdict(Counter)
    maps_total: Counter[int] = Counter()
    for members in per_game_team.values():
        for pid in members:
            maps_total[pid] += 1
            for other in members:
                if other != pid:
                    maps_with[pid][other] += 1
    out: dict[int, float] = {}
    for pid, total in maps_total.items():
        if total <= 0:
            continue
        top = maps_with[pid].most_common(1)
        out[pid] = (top[0][1] / total) if top else 0.0
    return out


def _standard_errors(x: FloatArray, p: FloatArray, l2: float) -> FloatArray:
    """Per-coefficient SE from the penalized Hessian.

    The covariance of a ridge fit is (X'WX + λI)^-1, and its diagonal is what a
    published coefficient needs beside it. These are the *penalized* errors: they
    describe the estimator actually being reported, shrinkage included, rather
    than the unpenalized fit nobody could compute here.
    """
    n = x.shape[0]
    xd = np.hstack([np.ones((n, 1)), x])
    w = np.maximum(p * (1.0 - p), 1e-9)
    penalty = np.full(xd.shape[1], l2)
    penalty[0] = 0.0
    hess = (xd * w[:, None]).T @ xd + np.diag(penalty)
    cov = np.linalg.pinv(hess)
    se: FloatArray = np.sqrt(np.maximum(np.diag(cov)[1:], 0.0))
    return se


def fit(
    rows: Sequence[MapRow],
    l2: float = L2,
    prior: dict[int, float] | None = None,
    min_maps: int = MIN_MAPS,
) -> RapmFit | None:
    """Fit RAPM over the maps given.

    `prior` supplies a per-player centre in logit units; the penalty then pulls
    toward it instead of toward zero, and players it does not mention keep the
    usual zero centre. Passing None is plain RAPM.
    """
    x, y, players, _game_ids = build_design(rows)
    if x.shape[0] < 50 or not players:
        return None

    centre = np.zeros(len(players), dtype=float)
    if prior:
        for i, pid in enumerate(players):
            centre[i] = prior.get(pid, 0.0)
    offset = x @ centre if prior else None

    result = fit_logistic_l2(x, y, l2=l2, offset=offset)
    coefs = np.asarray(result.weights) + centre
    # Fitted probabilities at the solution, for the Hessian the SEs come from.
    logit = result.intercept + x @ coefs
    p = 1.0 / (1.0 + np.exp(-np.clip(logit, -35.0, 35.0)))
    ses = _standard_errors(x, p, l2)

    maps_played = Counter(r.player_id for r in rows if r.winner_team_id is not None)
    concentration = _teammate_concentration(rows)

    out = [
        PlayerRapm(
            player_id=pid,
            maps=maps_played[pid],
            coef=float(coefs[i]),
            se=float(ses[i]),
            teammate_concentration=concentration.get(pid, 0.0),
        )
        for i, pid in enumerate(players)
        if maps_played[pid] >= min_maps
    ]
    return RapmFit(
        players=sorted(out, key=lambda r: r.coef, reverse=True),
        intercept=result.intercept,
        n_maps=x.shape[0],
        l2=l2,
        converged=result.converged,
    )


def prefix(
    rows: Sequence[MapRow],
    l2: float = L2,
    prior: dict[int, float] | None = None,
) -> dict[tuple[int, int, int | None], float]:
    """RAPM in the (player, season, mode) shape the roster forecast consumes.

    Fitted per season over the maps handed in, and stored under mode `None`:
    RAPM as built here is not mode-specific, so the forecast's per-mode lookup
    falls through to the all-mode entry, which is the honest thing for it to
    find. `min_maps` is dropped to 1 because the forecast is scoring rosters,
    not publishing a leaderboard — a heavily shrunk coefficient is still the
    model's actual opinion, and censoring it would quietly turn missing players
    into skipped maps and change which maps get scored.
    """
    by_season: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        by_season[r.season_id].append(r)
    out: dict[tuple[int, int, int | None], float] = {}
    for season_id, season_rows in by_season.items():
        f = fit(season_rows, l2=l2, prior=prior, min_maps=1)
        if f is None:
            continue
        for p in f.players:
            out[(p.player_id, season_id, None)] = p.coef
    return out


def rating_prior(
    rows: Sequence[MapRow],
    ratings: dict[tuple[int, int, int | None], float],
) -> dict[int, float]:
    """Turn box-score ratings into a prior centre in map-win logit units.

    The rating lives on its own scale, so the exchange rate into logits is
    estimated rather than assumed: regress the map result on the roster rating
    differential and read off the slope. That single learned number is what
    converts each player's rating into the centre their RAPM coefficient is
    shrunk toward, which keeps the blend honest — if the rating carries no
    information about map results on these maps, the slope comes back near zero
    and the prior collapses to plain RAPM on its own.
    """
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        if r.winner_team_id is not None:
            per_game[r.game_id].append(r)

    diffs: list[float] = []
    wins: list[float] = []
    for members in per_game.values():
        teams = sorted({m.team_id for m in members})
        if len(teams) != 2:
            continue
        a, b = teams
        sides: list[float | None] = []
        for team in (a, b):
            vals: list[float] = []
            for m in members:
                if m.team_id != team:
                    continue
                v = ratings.get((m.player_id, m.season_id, m.mode_id))
                if v is None:
                    v = ratings.get((m.player_id, m.season_id, None))
                if v is not None:
                    vals.append(v)
            sides.append(float(np.mean(vals)) if vals else None)
        if sides[0] is None or sides[1] is None:
            continue
        a_won = next((m.won is True for m in members if m.team_id == a), None)
        if a_won is None:
            continue
        diffs.append(sides[0] - sides[1])
        wins.append(1.0 if a_won else 0.0)

    if len(diffs) < 50 or len(set(wins)) < 2:
        return {}
    arr = np.asarray(diffs, dtype=float).reshape(-1, 1)
    sd = float(arr.std(ddof=1))
    if sd == 0.0:
        return {}
    f = fit_logistic_l2(arr / sd, np.asarray(wins, dtype=float), l2=1.0)
    slope = float(f.weights[0]) / sd

    # A roster differential is a difference of team means, so the per-player
    # logit centre is the slope applied to that player's own rating, divided by
    # the roster size the mean was taken over.
    sizes = [len({m.player_id for m in v if m.team_id == v[0].team_id}) for v in per_game.values()]
    team_size = float(np.median(sizes)) if sizes else 4.0
    centre: dict[int, float] = {}
    seen_players = {(r.player_id, r.season_id, r.mode_id) for r in rows}
    for player_id, season_id, mode_id in seen_players:
        v = ratings.get((player_id, season_id, mode_id)) or ratings.get(
            (player_id, season_id, None)
        )
        if v is not None:
            centre[player_id] = slope * v / max(team_size, 1.0)
    return centre


def ridge_path(
    rows: Sequence[MapRow], strengths: Sequence[float] = RIDGE_PATH
) -> list[dict[str, Any]]:
    """How much of a coefficient is the data and how much is the penalty.

    Reported rather than swept for a winner: nothing here selects l2. If the
    spread of coefficients collapses smoothly as the penalty rises and the
    ordering does not survive, the leaderboard is a ridge artifact and should be
    read as one.
    """
    out: list[dict[str, Any]] = []
    base: list[float] | None = None
    base_ids: list[int] = []
    for l2 in strengths:
        f = fit(rows, l2=l2)
        if f is None:
            continue
        ordered = sorted(f.players, key=lambda p: p.player_id)
        coefs = [p.coef for p in ordered]
        ids = [p.player_id for p in ordered]
        if base is None:
            base, base_ids = coefs, ids
        entry: dict[str, Any] = {
            "l2": l2,
            "n_players": len(coefs),
            "sd": round(float(np.std(coefs)), 4),
            "max_abs": round(float(np.max(np.abs(coefs))), 4),
        }
        if base is not None and ids == base_ids and len(coefs) > 2:
            entry["corr_with_l2_min"] = round(float(np.corrcoef(base, coefs)[0, 1]), 4)
        out.append(entry)
    return out


def player_rows(
    rows: Sequence[MapRow],
) -> list[tuple[int, int, float, float, float]]:
    """Every fitted player as (player_id, maps, coef, se, concentration).

    The artifact below publishes the top and bottom `top_n` because a
    methodology page wants the shape of the distribution, not 196 rows. A player
    page wants exactly one row and has no way to ask a leaderboard for it, so
    the same fit is emitted whole here and written to player_rapm.

    No filtering beyond the `min_maps` floor `fit` already applies, and no
    ordering: a caller reading one player's row must not depend on rank, and a
    caller that wants rank has the artifact.
    """
    full = fit(rows)
    if full is None:
        return []
    return [(p.player_id, p.maps, p.coef, p.se, p.teammate_concentration) for p in full.players]


def artifact(
    rows: Sequence[MapRow],
    ratings: dict[tuple[int, int, int | None], float] | None = None,
    top_n: int = 40,
) -> dict[str, Any]:
    """The published RAPM artifact: coefficients, their errors, and the two
    diagnostics that decide how much they mean."""
    full = fit(rows)
    if full is None:
        return {"available": False, "reason": "not enough decided maps"}

    prior = rating_prior(rows, ratings) if ratings else {}
    blended = fit(rows, prior=prior) if prior else None

    def row(p: PlayerRapm) -> dict[str, Any]:
        return {
            "player_id": p.player_id,
            "maps": p.maps,
            "coef": round(p.coef, 4),
            "se": round(p.se, 4),
            "z": round(p.coef / p.se, 2) if p.se > 0 else None,
            "teammate_concentration": round(p.teammate_concentration, 3),
        }

    ordered = full.players
    resolved = [p for p in ordered if p.se > 0 and abs(p.coef) > 1.96 * p.se]
    concentrated = [p for p in ordered if p.teammate_concentration >= 0.9]

    out: dict[str, Any] = {
        "available": True,
        "l2": L2,
        "min_maps": MIN_MAPS,
        "n_maps": full.n_maps,
        "n_players": len(ordered),
        "converged": full.converged,
        "leaders": [row(p) for p in ordered[:top_n]],
        "trailers": [row(p) for p in ordered[-top_n:]],
        # The two numbers that say how to read the leaderboard.
        "n_resolved": len(resolved),
        "n_concentrated": len(concentrated),
        "concentration_median": round(
            float(np.median([p.teammate_concentration for p in ordered])), 3
        ),
        "coef_sd": round(float(np.std([p.coef for p in ordered])), 4),
        "se_median": round(float(np.median([p.se for p in ordered])), 4),
        "ridge_path": ridge_path(rows),
    }

    if blended is not None:
        both = {p.player_id: p.coef for p in blended.players}
        common = [p for p in ordered if p.player_id in both]
        if len(common) > 2:
            out["blend"] = {
                "available": True,
                "what": "RAPM shrunk toward the box-score rating instead of toward zero,"
                " with the rating's exchange rate into map-win logits learned on the"
                " same maps",
                "n_players": len(common),
                "corr_with_plain": round(
                    float(
                        np.corrcoef(
                            [p.coef for p in common],
                            [both[p.player_id] for p in common],
                        )[0, 1]
                    ),
                    4,
                ),
                "prior_sd": round(float(np.std(list(prior.values()))), 4),
            }
    else:
        out["blend"] = {"available": False, "reason": "no box-score ratings supplied"}
    return out
