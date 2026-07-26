"""The sign-rule baseline: does a single column already know the winner?

No database. The rule itself is tiny, so these tests pin its two properties
that matter — it reads direction off the data rather than assuming it, and it
declines to score maps where the two teams tied on the column.
"""

from datetime import date

import numpy as np

from cdlhub_analytics.ratings.leakage import _sign_rule
from cdlhub_analytics.ratings.player_rating import GameDiff


def diff(values: list[float], a_won: bool, game_id: int = 0) -> GameDiff:
    return GameDiff(
        game_id=game_id,
        event_id=1,
        when=date(2018, 1, 1),
        diff=np.array(values),
        a_won=a_won,
    )


def test_a_column_that_is_the_outcome_scores_perfectly() -> None:
    """The Capture the Flag case: the differential's sign is never wrong."""
    diffs = [diff([1.0 if won else -1.0], won, g) for g, won in enumerate([True, False] * 10)]
    assert _sign_rule(diffs, 0) == (20, 20, 1)


def test_an_inverted_column_is_reported_at_its_real_direction() -> None:
    """Deaths point the other way. A column that is wrong 100% of the time is
    a perfect predictor of the opposite, and reporting 0% would hide that."""
    diffs = [diff([-1.0 if won else 1.0], won, g) for g, won in enumerate([True, False] * 10)]
    correct, n, direction = _sign_rule(diffs, 0)
    assert (correct, n, direction) == (20, 20, -1)


def test_a_noise_column_lands_near_a_coin_flip() -> None:
    rng = np.random.default_rng(0)
    diffs = [diff([float(rng.normal())], bool(rng.integers(0, 2)), g) for g in range(2000)]
    correct, n, _ = _sign_rule(diffs, 0)
    assert n == 2000
    assert 0.5 <= correct / n < 0.56  # never below half, by construction


def test_ties_are_skipped_rather_than_guessed() -> None:
    diffs = [
        diff([0.0], True, 0),
        diff([0.0], False, 1),
        diff([2.0], True, 2),
    ]
    assert _sign_rule(diffs, 0) == (1, 1, 1)


def test_no_scoreable_maps_returns_a_neutral_result() -> None:
    assert _sign_rule([diff([0.0], True)], 0) == (0, 0, 1)


def test_the_rule_reads_the_requested_column_only() -> None:
    """Column 0 is the outcome, column 1 is inverted noise-free garbage."""
    diffs = [diff([1.0 if won else -1.0, 5.0], won, g) for g, won in enumerate([True, False] * 6)]
    assert _sign_rule(diffs, 0)[0] == 12
    # Column 1 is constant, so its sign says "A won" every time: 6 of 12.
    assert _sign_rule(diffs, 1) == (6, 12, 1)
