"""Fixture tests for player_rating_v1: weight recovery on synthetic maps,
walk-forward hygiene, shrinkage, and rating ordering. No database required."""

from datetime import date, timedelta

import numpy as np

from cdlhub_analytics.ratings.player_rating import (
    MIN_TRAIN_GAMES,
    MapRow,
    _shrink,
    aggregate_players,
    backtest_weights,
    build_cohort_scales,
    build_game_diffs,
    compute_ratings,
    fit_mode_weights,
)

# Player skill: (mean kills per map). Team 1 = {11, 12}, team 2 = {21, 22}.
SKILL = {11: 30.0, 12: 20.0, 21: 20.0, 22: 15.0}


def synthetic_rows(n_games: int = 80, seed: int = 5) -> list[MapRow]:
    """Two teams, one season-mode cohort, two events. Kills and deaths carry
    independent signal about who wins the map; assists and objective are pure
    noise — so the regression has real structure to find, with no collinear
    degeneracy hiding it."""
    rng = np.random.default_rng(seed)
    rows: list[MapRow] = []
    day0 = date(2018, 1, 1)
    for g in range(n_games):
        kills = {p: max(0.0, rng.normal(mu, 3.0)) for p, mu in SKILL.items()}
        deaths = {p: max(0.0, rng.normal(20.0, 3.0)) for p in SKILL}
        latent = (kills[11] + kills[12] - kills[21] - kills[22]) - (
            deaths[11] + deaths[12] - deaths[21] - deaths[22]
        )
        team1_won = bool(latent + rng.normal(0.0, 2.0) > 0.0)
        event_id = 1 if g < n_games // 2 else 2
        for p in SKILL:
            team = 1 if p < 20 else 2
            rows.append(
                MapRow(
                    player_id=p,
                    team_id=team,
                    game_id=g,
                    season_id=1,
                    mode_id=1,
                    duration_s=600,
                    won=team1_won == (team == 1),
                    event_id=event_id,
                    when=day0 + timedelta(days=g),
                    raw=np.array(
                        [
                            kills[p],
                            deaths[p],
                            max(0.0, rng.normal(5.0, 1.0)),
                            max(0.0, rng.normal(10.0, 3.0)),
                        ]
                    ),
                )
            )
    return rows


def test_game_diffs_shape_and_label() -> None:
    rows = synthetic_rows()
    diffs = build_game_diffs(rows)
    assert set(diffs) == {(1, 1)}
    assert len(diffs[(1, 1)]) == 80
    # Team A is the lower id (1); its diff must correlate with winning.
    d = diffs[(1, 1)][0]
    game0 = [r for r in rows if r.game_id == 0]
    assert d.a_won == next(r.won for r in game0 if r.team_id == 1)


def test_weights_recover_structure() -> None:
    fits = fit_mode_weights(build_game_diffs(synthetic_rows()))
    w = dict(zip(("kills", "deaths", "assists", "obj"), fits[(1, 1)].weights, strict=True))
    assert w["kills"] > 0.0, "kill edge must raise win odds"
    assert w["deaths"] < 0.0, "death edge must lower win odds"
    assert w["kills"] > abs(w["assists"]), "assists are noise here"


def test_backtest_is_walk_forward_only() -> None:
    rows = synthetic_rows()
    preds = backtest_weights(build_game_diffs(rows))
    # Event 1 has no history; only event 2's 40 maps get predictions.
    assert len(preds) == 40
    assert all(0.0 < p.p < 1.0 for p in preds)
    hit = sum(1 for p in preds if (p.p >= 0.5) == p.won) / len(preds)
    assert hit > 0.6, "kills decide these maps; the model must beat coin flips"


def test_min_train_gate() -> None:
    rows = synthetic_rows(n_games=MIN_TRAIN_GAMES)  # each event below the gate
    assert backtest_weights(build_game_diffs(rows)) == []


def test_ratings_order_scale_and_uncertainty() -> None:
    rows = synthetic_rows()
    diffs = build_game_diffs(rows)
    fits = fit_mode_weights(diffs)
    aggs = aggregate_players(rows)
    scales = build_cohort_scales(aggs, fits)
    ratings = compute_ratings(aggs, fits, scales)

    blended = {r.player_id: r for r in ratings if r.mode_id is None}
    assert len(blended) == 4
    # The 30-kill player must outrate everyone, and clearly outrate the
    # 15-kill player. (Exact ranks between the two 20-kill-ish players are
    # sampling noise at 80 maps; the model shouldn't pretend otherwise.)
    ordered = sorted(blended.values(), key=lambda r: r.rating, reverse=True)
    assert ordered[0].player_id == 11
    assert blended[11].rating > blended[22].rating + 0.05
    # Normalized: cohort centers on 1.0.
    mean = float(np.mean([r.rating for r in blended.values()]))
    assert abs(mean - 1.0) < 0.05
    # Bootstrap uncertainty exists on blended rows and stays bounded.
    # (A 4-player cohort standardizes coarsely, so sds run larger here than
    # in real dozens-of-players cohorts.)
    assert all(r.rating_sd is not None and 0.0 < r.rating_sd < 0.5 for r in blended.values())
    # Per-mode rows exist too, without their own sd.
    assert sum(1 for r in ratings if r.mode_id == 1) == 4


def test_shrinkage_pulls_small_samples_to_league_mean() -> None:
    assert abs(_shrink(2.0, 8)) < abs(_shrink(2.0, 80))  # fewer maps, more pooling
    assert abs(_shrink(2.0, 10**6) - 2.0) < 1e-3  # huge samples keep their signal
    assert _shrink(-2.0, 8) > -2.0  # shrinks from both sides
