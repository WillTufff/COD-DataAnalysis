"""The positive control, controlled. No database required.

A simulation harness is only evidence if it can fail. These tests hold it to
both ends: a league where nothing within a season separates teammates must
recover nothing, and a league where the roster moves must recover the values
that were put in. Between them sits the determinism check — the same seed twice,
the same numbers back — without which a recovery curve is an anecdote.
"""

from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from cdlhub_analytics.ratings import simleague as sl

SMALL = sl.LeagueConfig(teams=6, seasons=4, bench=2, maps_per_team_season=90)


def test_the_error_functions_agree_with_the_standard_library() -> None:
    """They exist only because `scipy` belongs to the next phase."""
    x = np.array([-2.5, -0.7, 0.0, 0.3, 1.8], dtype=float)
    assert np.allclose(sl._erf(x), [math.erf(v) for v in x], atol=1e-6)
    quantiles = np.array([0.05, 0.25, 0.5, 0.75, 0.95], dtype=float)
    scores = np.sqrt(2.0) * sl._erfinv(2.0 * quantiles - 1.0)
    assert np.allclose(scores, [-1.6449, -0.6745, 0.0, 0.6745, 1.6449], atol=1e-3)


def test_the_normal_score_transform_is_monotone_within_a_group() -> None:
    league = sl.generate(SMALL, np.random.default_rng(1))
    scores = sl._normal_scores(league.games)
    picked = [
        (g.margin, s)
        for g, s in zip(league.games, scores, strict=True)
        if g.season == 0 and g.mode == "hardpoint"
    ]
    ordered = sorted(picked)
    assert [s for _m, s in ordered] == sorted(s for _m, s in ordered)
    assert abs(float(np.mean(scores))) < 0.05


def test_margins_are_censored_at_the_caps_each_mode_can_express() -> None:
    league = sl.generate(SMALL, np.random.default_rng(2))
    for game in league.games:
        assert 1 <= abs(game.margin) <= sl.MODE_CAPS[game.mode]
    snd = {abs(g.margin) for g in league.games if g.mode == "search-and-destroy"}
    assert snd <= {1.0, 2.0, 3.0, 4.0, 5.0, 6.0}


def test_calibration_hits_the_accuracy_it_was_given() -> None:
    rng = np.random.default_rng(3)
    tuned = sl.calibrate_noise(SMALL, 0.60, rng)
    advantage = sl.strengths(tuned, np.random.default_rng(3))
    assert abs(sl._accuracy(advantage, tuned.noise_sd) - 0.60) < 0.01


def test_a_frozen_league_recovers_nothing_about_teammates() -> None:
    """The negative control the whole verdict rests on.

    No churn and no transfers means the four columns of a team-season are one
    column wearing four names, and there is no other season to borrow a
    difference from. Anything the estimator reports here is noise, and the
    number has to say so.
    """
    frozen = sl.calibrate_noise(
        replace(SMALL, churn=0.0, transfer_rate=0.0), 0.60, np.random.default_rng(4)
    )
    result = sl.run_once(frozen, np.random.default_rng(5))
    assert abs(result.corr_within_team) < 0.2
    assert result.identified_share < 0.35


def test_a_league_that_rotates_recovers_what_was_put_into_it() -> None:
    rotating = sl.calibrate_noise(
        replace(SMALL, churn=0.5, transfer_rate=0.0), 0.60, np.random.default_rng(4)
    )
    result = sl.run_once(rotating, np.random.default_rng(5))
    assert result.corr_within_team > 0.3
    assert result.corr_level > 0.3


def test_the_penalty_borrows_across_seasons_when_players_move() -> None:
    """The same frozen season, with transfers turned back on.

    Nothing about a season changed; what changed is that other seasons now share
    players with it. A teammate difference that appears only under that
    condition was imported by the penalty, and this is the measurement that
    catches it.
    """
    base = sl.calibrate_noise(
        replace(SMALL, churn=0.0, transfer_rate=0.0), 0.60, np.random.default_rng(4)
    )
    closed = sl.run_once(base, np.random.default_rng(6))
    open_league = sl.run_once(replace(base, transfer_rate=0.35), np.random.default_rng(6))
    assert open_league.corr_within_team > closed.corr_within_team + 0.2


def test_the_smoothed_family_scores_higher_on_a_test_it_should_not_see() -> None:
    leak = sl.smoothing_inflation(SMALL, 2, np.random.SeedSequence(7))
    assert leak["inflation"] > 0.0
    assert leak["smoothed_r"] > leak["filtered_r"]


def test_the_detectable_persistence_gap_shrinks_as_the_record_grows() -> None:
    small = sl.persistence_mde(300, 0.56, 0.7)
    large = sl.persistence_mde(3000, 0.56, 0.7)
    assert small["mde80"] > large["mde80"]
    assert sl.persistence_mde(5, 0.56, 0.7)["available"] is False


def test_the_curve_is_read_at_the_lineup_variety_a_real_era_supplies() -> None:
    curve = [
        {"effective_lineups": 1.0, "corr_within_team": 0.0},
        {"effective_lineups": 3.0, "corr_within_team": 0.6},
    ]
    assert sl.recovery_at(curve, 2.0) == 0.3
    # Outside the swept range the curve is held, never extrapolated.
    assert sl.recovery_at(curve, 0.2) == 0.0
    assert sl.recovery_at(curve, 9.0) == 0.6


def test_the_same_seed_returns_the_same_numbers() -> None:
    """§4a's determinism clause, over the stage that would otherwise drift.

    Every other model in the package is seeded; this one generates its own data,
    so a drifting stream would move the published verdict with no data change
    behind it.
    """
    first = sl.artifact(0.6, 800, 0.56, cfg=SMALL, replicates=1)
    second = sl.artifact(0.6, 800, 0.56, cfg=SMALL, replicates=1)
    assert first == second
