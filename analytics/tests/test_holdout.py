"""The out-of-sample harness: correlation intervals, paired contrasts, and the
roster-strength plumbing. No database — the pieces that touch one are exercised
by the fixtures the rating engine already uses."""

import math
from datetime import date, timedelta

import numpy as np
from test_player_rating import LEAGUE_SKILL, V1_COLUMNS, coverage_for, synthetic_rows

from cdlhub_analytics.backtest import Prediction
from cdlhub_analytics.maprows import MODE_HARDPOINT, MapRow
from cdlhub_analytics.ratings import player_rating as pr
from cdlhub_analytics.ratings.holdout import (
    _brier_contrasts,
    _ci,
    _event_order,
    _kd_prefix,
    _logistic_1d,
    _map_diff,
    _pearson,
    _roster_strength,
    fit_prefix,
    fit_prefix_arms,
)

# (player, season, mode) -> rating, with mode None for the all-mode blend.
RatingTable = dict[tuple[int, int, int | None], float]


def test_pearson_matches_numpy_on_a_known_pair() -> None:
    x = [1.0, 2.0, 3.0, 4.0, 5.0]
    y = [2.0, 4.1, 5.9, 8.2, 9.8]
    assert math.isclose(_pearson(x, y), float(np.corrcoef(x, y)[0, 1]))
    assert _pearson(x, [-v for v in y]) < -0.99  # sign follows the relationship


def test_pearson_is_nan_where_undefined() -> None:
    assert math.isnan(_pearson([1.0, 2.0], [1.0, 2.0]))  # fewer than 3
    assert math.isnan(_pearson([1.0] * 5, [1.0, 2.0, 3.0, 4.0, 5.0]))  # no spread


def test_ci_ignores_nan_draws() -> None:
    """The nan draw must not poison the percentiles, which it would if it were
    passed through to numpy."""
    lo, hi = _ci([0.1, 0.2, float("nan"), 0.3])
    assert lo is not None and hi is not None
    assert 0.1 <= lo < hi <= 0.3
    assert _ci([float("nan")]) == (None, None)


# ---------- roster strength ----------


def row(
    player_id: int,
    team_id: int,
    game_id: int = 1,
    mode_id: int = 1,
    event_id: int = 1,
    played_at: date = date(2018, 1, 1),
) -> MapRow:
    return MapRow(
        player_id=player_id,
        team_id=team_id,
        game_id=game_id,
        season_id=2,
        mode_id=mode_id,
        mode_slug=MODE_HARDPOINT,
        title="WWII",
        event_id=event_id,
        played_at=played_at,
        duration_s=600.0,
        winner_team_id=1,
        values={"kills": 25.0, "deaths": 20.0},
        team_kills=0.0,
        team_hill_time=0.0,
    )


def test_roster_strength_averages_and_tolerates_one_missing_player() -> None:
    members = [row(p, 1) for p in (11, 12, 13, 14)]
    table: RatingTable = {(p, 2, 1): 1.2 for p in (11, 12, 13)}  # 14 has no history
    got = _roster_strength(members, table, mode_id=1)
    assert got is not None and math.isclose(got, 1.2)


def test_roster_strength_falls_back_to_the_all_mode_blend() -> None:
    members = [row(11, 1)]
    table: RatingTable = {(11, 2, None): 0.8}  # no Hardpoint-specific entry
    got = _roster_strength(members, table, mode_id=1)
    assert got is not None and math.isclose(got, 0.8)


def test_roster_strength_is_none_when_nobody_is_rated() -> None:
    assert _roster_strength([row(11, 1)], {}, mode_id=1) is None


def test_map_diff_orients_on_the_lower_team_id() -> None:
    members = [row(11, 1), row(21, 2)]
    table: RatingTable = {(11, 2, 1): 1.5, (21, 2, 1): 1.0}
    got = _map_diff(members, table)
    assert got is not None
    diff, a_won = got
    assert math.isclose(diff, 0.5)  # team 1 is stronger
    assert a_won is True  # winner_team_id=1 in the fixture


def test_map_diff_is_none_when_a_side_is_unrated() -> None:
    members = [row(11, 1), row(21, 2)]
    assert _map_diff(members, {(11, 2, 1): 1.5}) is None


# ---------- prefix K/D baseline ----------


def test_kd_prefix_shrinks_toward_one_and_keys_both_scopes() -> None:
    rows = [row(11, 1, game_id=g) for g in range(10)]
    table = _kd_prefix(rows)
    assert (11, 2, 1) in table and (11, 2, None) in table
    # 25/20 = 1.25 raw, pulled toward 1.0 by 10/(10+15).
    assert math.isclose(table[(11, 2, None)], 1.0 + 0.25 * 10 / 25)


def test_kd_prefix_skips_a_player_with_no_deaths() -> None:
    r = row(11, 1)
    r.values["deaths"] = 0.0
    assert _kd_prefix([r]) == {}


# ---------- event ordering ----------


def test_events_are_ordered_by_first_map_played() -> None:
    day0 = date(2018, 1, 1)
    # Event 7 happens later than event 3, so id order and time order disagree.
    rows = [
        row(11, 1, game_id=1, event_id=7, played_at=day0 + timedelta(days=5)),
        row(11, 1, game_id=2, event_id=3, played_at=day0),
    ]
    assert _event_order(rows) == [3, 7]


# ---------- the 1-D calibration ----------


def test_logistic_recovers_a_positive_slope_and_undoes_standardization() -> None:
    rng = np.random.default_rng(1)
    xs, ys = [], []
    for _ in range(400):
        d = float(rng.normal(0.0, 1.0))
        xs.append(d + 10.0)  # offset, to check the intercept is folded back
        ys.append(bool(d + rng.normal(0.0, 0.5) > 0.0))
    fit = _logistic_1d(xs, ys)
    assert fit is not None
    a, b = fit
    assert b > 0.0
    # A diff of exactly the offset must land near 0.5.
    p = 1.0 / (1.0 + math.exp(-(a + b * 10.0)))
    assert 0.4 < p < 0.6


def test_logistic_declines_degenerate_input() -> None:
    assert _logistic_1d([1.0] * 30, [True] * 15 + [False] * 15) is None  # no spread
    assert _logistic_1d([float(i) for i in range(30)], [True] * 30) is None  # one class
    assert _logistic_1d([1.0, 2.0], [True, False]) is None  # too few


# ---------- paired contrasts ----------


def pred(p: float, won: bool) -> Prediction:
    return Prediction(p=p, won=won, when=date(2018, 1, 1))


def test_brier_contrast_finds_a_real_gap() -> None:
    """A confident-and-right predictor must beat a coin flip decisively."""
    good = {g: pred(0.9 if g % 2 == 0 else 0.1, g % 2 == 0) for g in range(400)}
    flat = {g: pred(0.5, g % 2 == 0) for g in range(400)}
    out = _brier_contrasts({"rating": good, "kd": flat})
    assert out["available"] is True
    assert out["brier"]["kd"]["delta"] < 0  # rating better
    assert out["brier"]["kd"]["excludes_zero"] is True
    assert out["brier"]["coin_flip"]["excludes_zero"] is True
    assert out["accuracy"]["rating"]["beats_coin_flip"] is True


def test_brier_contrast_reports_a_null_as_a_null() -> None:
    """Two identical predictors must not separate, and a predictor sitting at 0.5
    must not be credited with beating the coin flip."""
    rng = np.random.default_rng(0)
    a = {g: pred(0.5, bool(rng.integers(0, 2))) for g in range(600)}
    out = _brier_contrasts({"rating": a, "kd": dict(a)})
    assert out["brier"]["kd"]["delta"] == 0.0
    assert out["brier"]["kd"]["excludes_zero"] is False
    assert out["brier"]["coin_flip"]["excludes_zero"] is False
    assert out["accuracy"]["rating"]["beats_coin_flip"] is False


def test_contrast_uses_only_commonly_scored_maps() -> None:
    a = {g: pred(0.6, True) for g in range(300)}
    b = {g: pred(0.6, True) for g in range(150)}  # covers half
    out = _brier_contrasts({"rating": a, "kd": b})
    assert out["n_maps"] == 150


def test_contrast_declines_when_there_is_nothing_to_compare() -> None:
    a = {g: pred(0.6, True) for g in range(300)}
    assert _brier_contrasts({"rating": a})["available"] is False
    assert _brier_contrasts({"kd": a})["available"] is False


# ---------- both estimators, one fit ----------


def test_fit_prefix_arms_rates_the_same_seasons_two_ways() -> None:
    """The comparison in Test B is only a comparison of estimators if both arms
    see identical cohorts, weights and aggregates. So the two tables must cover
    exactly the same keys and differ only in the values."""
    rows = synthetic_rows(n_games=150, skills=LEAGUE_SKILL)
    coverage = coverage_for("WWII", V1_COLUMNS)
    arms = fit_prefix_arms(rows, coverage, "1.0.0")
    assert set(arms) == set(pr.ESTIMATORS)
    hier, legacy = arms["hierarchical"], arms["z_shrink"]
    assert hier and set(hier) == set(legacy)
    assert hier != legacy, "two estimators that agree exactly are one estimator"
    # The default arm is what the site publishes, not whichever ran last.
    assert fit_prefix(rows, coverage, "1.0.0") == arms[pr.PUBLISHED_ESTIMATOR]
    # Both keep the ordering the synthetic skills imply: 11 > 12 in the blend.
    for table in (hier, legacy):
        assert table[(11, 1, None)] > table[(12, 1, None)]


def test_a_cohort_the_variance_fit_cannot_support_is_rated_by_one_arm_only() -> None:
    """Four players, and the spread of their season scores is smaller than the
    noise on any one of them. The hierarchical arm says so by publishing nothing
    for them; the fixed-constant arm has no way to notice and rates them anyway.
    The prefix therefore has to tolerate arms of different sizes — a missing
    rating is a rating withheld, not a key the two estimators disagree about."""
    arms = fit_prefix_arms(synthetic_rows(), coverage_for("WWII", V1_COLUMNS), "1.0.0")
    assert not arms["hierarchical"]
    assert arms["z_shrink"]


def test_fit_prefix_arms_is_empty_when_the_prefix_cannot_be_fitted() -> None:
    short = [r for r in synthetic_rows() if r.game_id < 5]  # under MIN_TRAIN_GAMES
    assert all(
        not v for v in fit_prefix_arms(short, coverage_for("WWII", V1_COLUMNS), "1.0.0").values()
    )
