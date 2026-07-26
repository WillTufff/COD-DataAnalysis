"""Fixture tests for regularized adjusted plus-minus. No database required.

The synthetic leagues here are built so the right answer is known in advance:
one player who wins every map they touch, two players who are never apart, and a
league where nobody differs. RAPM has to recover the first, refuse to separate
the second, and return nothing from the third.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from cdlhub_analytics.maprows import MapRow
from cdlhub_analytics.ratings import rapm
from cdlhub_analytics.regress import fit_logistic_l2

DAY0 = date(2018, 1, 1)


def make_map(
    game_id: int,
    winners: tuple[int, int],
    losers: tuple[int, int],
    *,
    flip: bool = False,
    season_id: int = 1,
    event_id: int = 1,
) -> list[MapRow]:
    """One map, two two-player teams, stated as who won and who lost.

    `flip` swaps which side carries the lower team id. Team ids must not track
    the roster: `build_design` orients each map by sorted team id, so if one
    lineup always held the lower id its label would never vary and the
    unpenalized intercept would absorb the whole effect.
    """
    ta, tb = (200, 100) if flip else (100, 200)
    winner = ta
    rows = []
    for team_id, members in ((ta, winners), (tb, losers)):
        for pid in members:
            rows.append(
                MapRow(
                    player_id=pid,
                    team_id=team_id,
                    game_id=game_id,
                    season_id=season_id,
                    mode_id=1,
                    mode_slug="hardpoint",
                    title="WWII",
                    event_id=event_id,
                    played_at=DAY0 + timedelta(days=game_id),
                    duration_s=600.0,
                    winner_team_id=winner,
                    values={"kills": 20.0, "deaths": 20.0},
                    team_kills=80.0,
                    team_hill_time=100.0,
                )
            )
    return rows


# ===== the design matrix =====


def test_design_is_plus_and_minus_one_per_side() -> None:
    rows = make_map(1, (1, 2), (3, 4))
    x, y, players, game_ids = rapm.build_design(rows)
    assert players == [1, 2, 3, 4]
    assert game_ids == [1]
    # The winners hold the lower team id here, so they are the +1 side.
    assert list(x[0]) == [1.0, 1.0, -1.0, -1.0]
    assert y[0] == 1.0


def test_flipping_the_team_ids_flips_the_label_not_the_result() -> None:
    x, y, _players, _g = rapm.build_design(make_map(1, (1, 2), (3, 4), flip=True))
    # Same winners, but they now sort second, so they are the -1 side and y = 0.
    assert list(x[0]) == [-1.0, -1.0, 1.0, 1.0]
    assert y[0] == 0.0


def test_a_map_without_two_teams_is_dropped() -> None:
    rows = [r for r in make_map(1, (1, 2), (3, 4)) if r.team_id == 100]
    x, _y, _players, game_ids = rapm.build_design(rows)
    assert x.shape[0] == 0
    assert game_ids == []


def test_an_undecided_map_is_dropped() -> None:
    rows = make_map(1, (1, 2), (3, 4))
    stripped = [MapRow(**{**r.__dict__, "winner_team_id": None}) for r in rows]
    x, _y, _players, _g = rapm.build_design(stripped)
    assert x.shape[0] == 0


# ===== recovery =====


def test_a_player_who_always_wins_gets_the_top_coefficient() -> None:
    """Player 1 wins every map; everyone else is shuffled through both sides.
    If RAPM cannot find that, it is not measuring anything."""
    rng = np.random.default_rng(7)
    others = list(range(2, 13))
    rows: list[MapRow] = []
    for g in range(300):
        pool = list(rng.permutation(others))
        rows.extend(
            make_map(
                g,
                (1, int(pool[0])),
                (int(pool[1]), int(pool[2])),
                flip=g % 2 == 0,
            )
        )
    fit = rapm.fit(rows, min_maps=1)
    assert fit is not None
    assert fit.players[0].player_id == 1
    assert fit.players[0].coef > 0.5


def test_a_league_of_identical_players_produces_no_separation() -> None:
    """Outcomes independent of who played. Every coefficient should sit near
    zero and the spread should be small — otherwise the leaderboard would be
    manufacturing differences out of the ridge."""
    rng = np.random.default_rng(11)
    pool = list(range(1, 13))
    rows: list[MapRow] = []
    for g in range(400):
        order = list(rng.permutation(pool))
        rows.extend(
            make_map(
                g,
                (int(order[0]), int(order[1])),
                (int(order[2]), int(order[3])),
                flip=bool(rng.integers(0, 2)),
            )
        )
    fit = rapm.fit(rows, min_maps=1)
    assert fit is not None
    coefs = [p.coef for p in fit.players]
    assert max(abs(c) for c in coefs) < 0.5
    assert float(np.std(coefs)) < 0.25


# ===== collinearity, the objection the module exists to answer =====


def test_two_players_who_never_part_get_the_same_coefficient() -> None:
    """Players 1 and 2 appear together on every map they play. Nothing in the
    data separates them, so ridge must split the credit evenly rather than
    inventing a difference — and the concentration diagnostic must say so."""
    rng = np.random.default_rng(3)
    others = list(range(3, 11))
    rows: list[MapRow] = []
    for g in range(300):
        pool = list(rng.permutation(others))
        rows.extend(make_map(g, (1, 2), (int(pool[0]), int(pool[1])), flip=g % 2 == 0))
    fit = rapm.fit(rows, min_maps=1)
    assert fit is not None
    coefs = {p.player_id: p.coef for p in fit.players}
    assert abs(coefs[1] - coefs[2]) < 1e-6
    conc = {p.player_id: p.teammate_concentration for p in fit.players}
    assert conc[1] == 1.0 and conc[2] == 1.0


def test_concentration_falls_when_partners_rotate() -> None:
    rng = np.random.default_rng(5)
    pool = list(range(1, 13))
    rows: list[MapRow] = []
    for g in range(400):
        order = list(rng.permutation(pool))
        rows.extend(
            make_map(
                g,
                (int(order[0]), int(order[1])),
                (int(order[2]), int(order[3])),
                flip=bool(rng.integers(0, 2)),
            )
        )
    fit = rapm.fit(rows, min_maps=1)
    assert fit is not None
    assert max(p.teammate_concentration for p in fit.players) < 0.5


# ===== shrinkage and errors =====


def test_standard_errors_are_published_and_positive() -> None:
    rows: list[MapRow] = []
    for g in range(200):
        rows.extend(make_map(g, (1, 2), (3, 4)) if g % 2 else make_map(g, (3, 4), (1, 2)))
    fit = rapm.fit(rows, min_maps=1)
    assert fit is not None
    assert all(p.se > 0 for p in fit.players)


def test_a_heavier_ridge_shrinks_every_coefficient() -> None:
    rng = np.random.default_rng(13)
    others = list(range(2, 13))
    rows: list[MapRow] = []
    for g in range(300):
        pool = list(rng.permutation(others))
        rows.extend(make_map(g, (1, int(pool[0])), (int(pool[1]), int(pool[2])), flip=g % 2 == 0))
    light = rapm.fit(rows, l2=0.5, min_maps=1)
    heavy = rapm.fit(rows, l2=50.0, min_maps=1)
    assert light is not None and heavy is not None
    spread = {f.l2: float(np.std([p.coef for p in f.players])) for f in (light, heavy)}
    assert spread[50.0] < spread[0.5]


def test_min_maps_keeps_a_three_map_career_off_the_board() -> None:
    rng = np.random.default_rng(17)
    others = list(range(2, 13))
    rows: list[MapRow] = []
    for g in range(200):
        pool = list(rng.permutation(others))
        rows.extend(make_map(g, (int(pool[0]), int(pool[1])), (int(pool[2]), int(pool[3]))))
    # Player 99 turns up for three maps only.
    for g in range(200, 203):
        rows.extend(make_map(g, (99, 2), (3, 4)))
    fit = rapm.fit(rows, min_maps=20)
    assert fit is not None
    assert 99 not in {p.player_id for p in fit.players}


# ===== the prior-centered variant =====


def test_an_offset_shifts_the_fit_it_is_given() -> None:
    """The mechanism the blend rests on: fitting with an offset and adding the
    centre back must reproduce shrinking toward that centre rather than zero."""
    rng = np.random.default_rng(19)
    x = rng.normal(size=(400, 3))
    y = (rng.random(400) < 1.0 / (1.0 + np.exp(-(x @ np.array([1.0, -0.5, 0.2]))))).astype(float)
    centre = np.array([1.0, -0.5, 0.2])
    plain = fit_logistic_l2(x, y, l2=50.0)
    centred = fit_logistic_l2(x, y, l2=50.0, offset=x @ centre)
    shifted = np.asarray(centred.weights) + centre
    # A heavy penalty drags the plain fit toward zero and the centred fit toward
    # the prior, so the centred one has to end up closer to the truth.
    assert np.linalg.norm(shifted - centre) < np.linalg.norm(np.asarray(plain.weights) - centre)


def test_a_prior_moves_coefficients_toward_it() -> None:
    rng = np.random.default_rng(23)
    pool = list(range(1, 13))
    rows: list[MapRow] = []
    for g in range(300):
        order = list(rng.permutation(pool))
        rows.extend(
            make_map(
                g,
                (int(order[0]), int(order[1])),
                (int(order[2]), int(order[3])),
                flip=bool(rng.integers(0, 2)),
            )
        )
    plain = rapm.fit(rows, l2=20.0, min_maps=1)
    primed = rapm.fit(rows, l2=20.0, prior={1: 2.0}, min_maps=1)
    assert plain is not None and primed is not None
    before = next(p.coef for p in plain.players if p.player_id == 1)
    after = next(p.coef for p in primed.players if p.player_id == 1)
    assert after > before


# ===== the artifact =====


def test_artifact_reports_the_two_diagnostics_that_qualify_it() -> None:
    rng = np.random.default_rng(29)
    pool = list(range(1, 13))
    rows: list[MapRow] = []
    for g in range(400):
        order = list(rng.permutation(pool))
        rows.extend(
            make_map(
                g,
                (int(order[0]), int(order[1])),
                (int(order[2]), int(order[3])),
                flip=bool(rng.integers(0, 2)),
            )
        )
    art = rapm.artifact(rows)
    assert art["available"] is True
    # Neither number may go missing: they are what a reader needs to know how
    # much of the leaderboard is real.
    assert "n_resolved" in art and "concentration_median" in art
    assert len(art["ridge_path"]) == len(rapm.RIDGE_PATH)
    # The path has to be monotone in spread, or it is not showing shrinkage.
    sds = [e["sd"] for e in art["ridge_path"]]
    assert sds == sorted(sds, reverse=True)


def test_artifact_declines_on_too_few_maps() -> None:
    rows = make_map(1, (1, 2), (3, 4))
    art = rapm.artifact(rows)
    assert art["available"] is False


# ===== the per-player rows =====


def _rotating_league(seed: int, n_games: int = 400, pool_size: int = 12) -> list[MapRow]:
    rng = np.random.default_rng(seed)
    pool = list(range(1, pool_size + 1))
    rows: list[MapRow] = []
    for g in range(n_games):
        order = list(rng.permutation(pool))
        rows.extend(
            make_map(
                g,
                (int(order[0]), int(order[1])),
                (int(order[2]), int(order[3])),
                flip=bool(rng.integers(0, 2)),
            )
        )
    return rows


def test_player_rows_cover_everyone_the_artifact_truncates() -> None:
    """The whole reason the table exists: the artifact names 80 players and the
    fit knows more than that."""
    rows = _rotating_league(31)
    emitted = rapm.player_rows(rows)
    art = rapm.artifact(rows, top_n=2)
    listed = {p["player_id"] for p in art["leaders"]} | {
        p["player_id"] for p in art["trailers"]
    }
    assert len(emitted) == art["n_players"]
    assert len(listed) < len(emitted)
    # Every truncated player is still recoverable from the rows.
    assert listed <= {r[0] for r in emitted}


def test_player_rows_agree_with_the_fit_they_come_from() -> None:
    rows = _rotating_league(37)
    fit = rapm.fit(rows)
    assert fit is not None
    emitted = {r[0]: r for r in rapm.player_rows(rows)}
    assert set(emitted) == {p.player_id for p in fit.players}
    for p in fit.players:
        _pid, maps, coef, se, conc = emitted[p.player_id]
        assert maps == p.maps
        assert coef == p.coef
        assert se == p.se
        assert conc == p.teammate_concentration


def test_player_rows_honour_the_min_maps_floor() -> None:
    rows = _rotating_league(41)
    for g in range(400, 403):
        rows.extend(make_map(g, (99, 2), (3, 4)))
    assert 99 not in {r[0] for r in rapm.player_rows(rows)}


def test_player_rows_satisfy_the_tables_constraints() -> None:
    """0013 constrains maps > 0, se > 0, coef finite and concentration in [0, 1].
    A row the writeback cannot insert is a pipeline that dies at 3am."""
    for pid, maps, coef, se, conc in rapm.player_rows(_rotating_league(43)):
        assert isinstance(pid, int)
        assert maps > 0
        assert se > 0
        assert np.isfinite(coef)
        assert 0.0 <= conc <= 1.0


def test_player_rows_are_empty_when_the_fit_declines() -> None:
    assert rapm.player_rows(make_map(1, (1, 2), (3, 4))) == []
