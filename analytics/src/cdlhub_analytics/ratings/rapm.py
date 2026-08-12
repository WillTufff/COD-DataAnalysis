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

Heavy roster churn across ten titles is close to the ideal case for this, and
rosters move enough to separate teammates from each other. Close to ideal is
not ideal, and three things have to be reported rather than assumed away.

**Lineup completeness.** A map only enters the design with two sides of equal,
complete size — four a side in the 4v4 titles, five in the two that were played
5v5 (BO4 in 2019 and MW19 in 2020). The size is read from the title rather than
declared, so a future title that changes it needs no edit here; what is declared
is that both sides must be full and equal. A 3v4 map is a map where the design
matrix would claim a team fielded three players, and a coefficient fitted on it
absorbs a missing teammate. The count dropped is published per title below,
never silently absorbed.

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
from datetime import date
from typing import Any

import numpy as np

from ..maprows import MapRow
from ..regress import FloatArray, fit_logistic_l2, matrix_hash

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
    # The design this was fitted on, fingerprinted: shape, player columns and
    # the ±1 contents.
    design_hash: str
    # What the lineup rule admitted and turned away, per title.
    lineup: LineupRule

    def by_player(self) -> dict[int, float]:
        return {p.player_id: p.coef for p in self.players}


def side_sizes(rows: Sequence[MapRow]) -> dict[str, int]:
    """The roster size each title was played at: its commonest full side.

    Measured rather than declared, because the record already contains the
    answer and a table of title-to-size is one more thing to keep in step.
    """
    slots: dict[tuple[int, int], set[int]] = defaultdict(set)
    title_of: dict[int, str] = {}
    for r in rows:
        slots[(r.game_id, r.team_id)].add(r.player_id)
        title_of[r.game_id] = r.title
    by_title: dict[str, Counter[int]] = defaultdict(Counter)
    for (game_id, _team_id), players in slots.items():
        by_title[title_of[game_id]][len(players)] += 1
    return {title: sizes.most_common(1)[0][0] for title, sizes in by_title.items()}


@dataclass(frozen=True)
class LineupRule:
    """Which maps the plus-minus design admits, and what it turned away."""

    sizes: dict[str, int]
    admitted: Counter[str]
    dropped: Counter[str]

    def payload(self) -> dict[str, Any]:
        titles = sorted(set(self.sizes) | set(self.dropped))
        return {
            "rule": "two sides of equal, complete size — the size that title was played at",
            "by_title": [
                {
                    "title": title,
                    "side_size": self.sizes.get(title),
                    "maps_admitted": self.admitted.get(title, 0),
                    "maps_dropped": self.dropped.get(title, 0),
                }
                for title in titles
            ],
            "maps_dropped": sum(self.dropped.values()),
        }


@dataclass(frozen=True)
class Identity:
    """Who the design's columns are, and how sure the record is about it.

    Every model keyed on `player_id` inherits whatever this says. A split — one
    person under two handles — hands them two columns each fitted on half a
    career; a merge hands two people one column, and ridge reports the average
    as a finding. Neither shows up as a large standard error, so the count of
    slots still in question is published next to the coefficients rather than
    left to the identity queue nobody opens.

    Substitutes stay in. They are real players whose maps really happened, and
    dropping them would quietly change which maps the fit sees; what they
    distort is the teammate graph, so they are counted here and read alongside
    the concentration diagnostic. Coaches never enter at all — the design is
    built from box scores and a coach has none — which is asserted rather than
    assumed.
    """

    # Handles still sitting in the ops identity queue as open merge candidates.
    unresolved_players: frozenset[int]
    # (game_id, player_id) slots whose player was on a substitute stint on that
    # map's date. Dated rather than career-wide: most players have stood in
    # somewhere, and a career-wide flag would mark almost the whole record.
    substitute_slots: frozenset[tuple[int, int]]
    # People whose every recorded stint is a coaching one. A box score from one
    # of them is a slot the design should never have been offered, so the count
    # is a check that should read zero rather than a diagnostic.
    coach_only_players: frozenset[int]

    def payload(self, rows: Sequence[MapRow]) -> dict[str, Any]:
        maps_of: dict[int, set[int]] = defaultdict(set)
        for r in rows:
            maps_of[r.player_id].add(r.game_id)
        unresolved_maps: set[int] = set()
        for pid in self.unresolved_players:
            unresolved_maps |= maps_of.get(pid, set())
        subbed = self.substitute_slots & {(r.game_id, r.player_id) for r in rows}
        total = len({r.game_id for r in rows}) or 1
        return {
            "rule": (
                "substitutes are fitted and flagged; coaches never enter; a slot is "
                "unresolved while its handle is still an open merge candidate"
            ),
            "unresolved_players": len(self.unresolved_players),
            "maps_with_an_unresolved_slot": len(unresolved_maps),
            "share_of_maps_unresolved": round(len(unresolved_maps) / total, 4),
            "substitute_slots": len(subbed),
            "maps_with_a_substitute": len({game_id for game_id, _pid in subbed}),
            "coach_slots": len(self.coach_only_players & set(maps_of)),
        }


@dataclass(frozen=True)
class AdmittedMap:
    """One map the lineup rule let through, with its two sides named.

    The side that sorts first by team id is the `+1` side everywhere below, and
    `home_won` refers to it — matching how the map holdout orients every other
    predictor, so a coefficient's sign means the same thing here as it does
    there.
    """

    game_id: int
    series_id: int
    season_id: int
    title: str
    mode_slug: str
    played_at: date
    home_team_id: int
    away_team_id: int
    home_players: tuple[int, ...]
    away_players: tuple[int, ...]
    home_won: bool
    # Score margin from the `+1` side, where the record carries both scores.
    # Kept beside the binary outcome rather than replacing it: the season-varying
    # fit reads the margin, this fit reads the win, and both read one admission
    # rule.
    home_margin: float | None

    @property
    def players(self) -> tuple[int, ...]:
        return self.home_players + self.away_players


def admitted_maps(rows: Sequence[MapRow]) -> tuple[list[AdmittedMap], LineupRule]:
    """The maps the plus-minus design is built from, in played order.

    Separated from the design matrix because the identification pre-flight asks
    the same question of the same maps in a different shape, and a second copy
    of the lineup rule is a second rule.
    """
    per_game: dict[int, list[MapRow]] = defaultdict(list)
    for r in rows:
        if r.winner_team_id is not None:
            per_game[r.game_id].append(r)

    sizes = side_sizes(rows)
    admitted: Counter[str] = Counter()
    dropped: Counter[str] = Counter()
    usable: list[AdmittedMap] = []
    for game_id in sorted(per_game, key=lambda g: (per_game[g][0].played_at, g)):
        members = per_game[game_id]
        title = members[0].title
        teams = sorted({m.team_id for m in members})
        if len(teams) != 2:
            dropped[title] += 1
            continue
        a, b = teams
        # Both sides full and equal, at the size this title was played at. The
        # slot count is the number of distinct players, so a duplicated stat
        # line cannot pass for a fifth man.
        expected = sizes.get(title)
        sides = {t: sorted({m.player_id for m in members if m.team_id == t}) for t in (a, b)}
        if expected is None or len(sides[a]) != expected or len(sides[b]) != expected:
            dropped[title] += 1
            continue
        a_won = next((m.won is True for m in members if m.team_id == a), None)
        if a_won is None:
            dropped[title] += 1
            continue
        admitted[title] += 1
        home_margin = next((m.margin for m in members if m.team_id == a), None)
        usable.append(
            AdmittedMap(
                game_id=game_id,
                series_id=members[0].series_id,
                season_id=members[0].season_id,
                title=title,
                mode_slug=members[0].mode_slug,
                played_at=members[0].played_at,
                home_margin=home_margin,
                home_team_id=a,
                away_team_id=b,
                home_players=tuple(sides[a]),
                away_players=tuple(sides[b]),
                home_won=a_won,
            )
        )
    return usable, LineupRule(sizes=sizes, admitted=admitted, dropped=dropped)


def build_design(
    rows: Sequence[MapRow],
) -> tuple[FloatArray, FloatArray, list[int], list[int], LineupRule]:
    """(X, y, player ids by column, game ids by row, the lineup rule's tally).

    One row per admitted map, `+1` for the first side's players and `−1` for the
    other's.
    """
    usable, lineup = admitted_maps(rows)
    players = sorted({pid for game in usable for pid in game.players})
    col = {pid: i for i, pid in enumerate(players)}
    x = np.zeros((len(usable), len(players)), dtype=float)
    y = np.zeros(len(usable), dtype=float)
    game_ids: list[int] = []
    for i, game in enumerate(usable):
        for pid in game.home_players:
            x[i, col[pid]] = 1.0
        for pid in game.away_players:
            x[i, col[pid]] = -1.0
        y[i] = 1.0 if game.home_won else 0.0
        game_ids.append(game.game_id)
    return x, y, players, game_ids, lineup


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


def standard_errors(x: FloatArray, p: FloatArray, l2: float) -> FloatArray:
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
    x, y, players, _game_ids, lineup = build_design(rows)
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
    ses = standard_errors(x, p, l2)

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
        design_hash=matrix_hash(x, [str(pid) for pid in players]),
        lineup=lineup,
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
    identity: Identity | None = None,
) -> dict[str, Any]:
    """The published RAPM artifact: coefficients, their errors, and the three
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
        "design_hash": full.design_hash,
        "lineup": full.lineup.payload(),
        "identity": identity.payload(rows) if identity else None,
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
