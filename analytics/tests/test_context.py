"""Match context: the taxonomy, the curated map, the pooling and the invariances."""

from __future__ import annotations

import json
from collections.abc import Sequence
from importlib import resources

import numpy as np
import pytest

from cdlhub_analytics.ratings import context as ctx
from cdlhub_analytics.ratings import opponent as op

# ------------------------------------------------------------- the taxonomy


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        # Call of Duty League prose.
        ("Major Qualifier", ctx.STAKES_REGULAR),
        ("Week 3", ctx.STAKES_REGULAR),
        ("Group Play A Winners Round 1", ctx.STAKES_GROUP),
        ("Group Stage", ctx.STAKES_GROUP),
        ("Winners Round 2", ctx.STAKES_BRACKET),
        ("Elimination Finals", ctx.STAKES_BRACKET),
        ("Grand Finals", ctx.STAKES_GRAND_FINAL),
        # Short codes.
        ("GF", ctx.STAKES_GRAND_FINAL),
        ("QF", ctx.STAKES_BRACKET),
        ("LR1", ctx.STAKES_BRACKET),
        # CWL archive slugs.
        ("champs-grand-finals-0", ctx.STAKES_GRAND_FINAL),
        ("champs-winners-1-2", ctx.STAKES_BRACKET),
        ("champs-losers-3-1", ctx.STAKES_BRACKET),
        ("pool-B-4", ctx.STAKES_GROUP),
        ("champs-pool-A-0", ctx.STAKES_GROUP),
        ("pro1-a1-7", ctx.STAKES_REGULAR),
        ("pro-w10-3", ctx.STAKES_REGULAR),
    ],
)
def test_every_vocabulary_reaches_a_class(label: str, expected: str) -> None:
    assert ctx.classify_stakes(label) == expected


def test_an_unknown_label_keeps_its_own_class() -> None:
    """A label that says it does not know is not evidence for the largest bucket."""
    assert ctx.classify_stakes("Unknown Round") == ctx.STAKES_UNCLASSIFIED
    assert ctx.classify_stakes(None) == ctx.STAKES_UNCLASSIFIED


@pytest.mark.parametrize(
    ("label", "facing"),
    [
        ("Elimination Round 2", True),
        ("champs-losers-2-1", True),
        ("plq-bracket-lr1-2", True),
        ("Group Play A Lower Round 1", True),
        ("Winners Round 1", False),
        ("champs-winners-1-2", False),
        ("Major Qualifier", False),
        # Only the lower-bracket side faces elimination in a grand final, and a
        # series-level flag cannot say which side that is.
        ("Grand Finals", False),
        ("champs-grand-finals-0", False),
    ],
)
def test_elimination_facing(label: str, facing: bool) -> None:
    assert ctx.elimination_facing(label) is facing


# ------------------------------------------------------- the curated host map


def _host_raw() -> dict[str, dict[str, object]]:
    payload = json.loads(
        resources.files("cdlhub_analytics.ratings").joinpath("host_markets.json").read_text()
    )
    return dict(payload["events"])


def test_the_shipped_host_map_states_a_reason_and_a_confidence() -> None:
    for key, entry in _host_raw().items():
        assert entry.get("reason"), key
        assert entry.get("confidence") in {"clear", "judgement"}, key
        assert isinstance(entry.get("teams"), list), key


def test_a_neutral_site_is_stated_rather_than_left_out() -> None:
    """An absent event and an event with no host are different claims."""
    neutral = [key for key, entry in _host_raw().items() if not entry["teams"]]
    assert neutral, "the map should name the venues that have no franchise"


def test_host_markets_lookup_is_keyed_by_season_as_well_as_name() -> None:
    markets = ctx.HostMarkets.load()
    assert markets.get(2024, "CDL Major 3") is not None
    assert markets.get(2019, "CDL Major 3") is None


# ---------------------------------------------------------------- the pooling


def test_the_variance_of_a_mean_corrects_for_the_mean_it_is_measured_around() -> None:
    """Two observations give half the variance under the population form."""
    values = np.array([1.0, 3.0])
    weights = np.array([1.0, 1.0])
    stats = ctx._group_mean_and_variance(values, weights)
    assert stats is not None
    mean, variance = stats
    assert mean == pytest.approx(2.0)
    # Unbiased sample variance is 2.0, and the variance of the mean is 2/2 = 1.0.
    assert variance == pytest.approx(1.0)


def test_a_group_with_no_spread_to_measure_returns_nothing() -> None:
    assert ctx._group_mean_and_variance(np.array([1.0]), np.array([1.0])) is None
    assert ctx._group_mean_and_variance(np.array([1.0, 2.0]), np.array([0.0, 0.0])) is None


def test_pooling_shrinks_a_thin_group_further_than_a_thick_one() -> None:
    """Partial pooling, which is the whole reason map identity is a random effect."""
    rng = np.random.default_rng(3)
    labels: Sequence[str | None] = ["thick"] * 200 + ["thin"] * 6
    residual = np.concatenate([rng.normal(1.0, 1.0, 200), rng.normal(1.0, 1.0, 6)])
    weight = np.ones(len(labels))
    effects = ctx._pool_by_group(labels, residual, weight)
    assert set(effects) == {"thick", "thin"}
    centre = float(np.mean(list(effects.values())))
    thick_raw = float(residual[:200].mean())
    thin_raw = float(residual[200:].mean())
    thick_pull = abs(thick_raw - effects["thick"]) / max(abs(thick_raw - centre), 1e-9)
    thin_pull = abs(thin_raw - effects["thin"]) / max(abs(thin_raw - centre), 1e-9)
    assert thin_pull >= thick_pull


def test_a_single_group_is_not_pooled_against_itself() -> None:
    effects = ctx._pool_by_group(["only"] * 20, np.ones(20), np.ones(20))
    assert effects == {}


# ----------------------------------------------------------------- the solver


def _toy(n: int = 80, p: int = 4, seed: int = 5) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = np.column_stack([np.ones(n), rng.standard_normal((n, p - 1))])
    y = x @ rng.standard_normal(p) + 0.2 * rng.standard_normal(n)
    return x, y, 1.0 + rng.random(n)


def test_block_ridge_with_no_penalty_is_the_weighted_least_squares_fit() -> None:
    x, y, w = _toy()
    fit = ctx.block_ridge(x, y, w, np.zeros(x.shape[1]))
    expected = op.solve_wls(x, y, w, 0.0)
    assert np.allclose(fit.beta, expected.beta, atol=1e-8)


def test_block_ridge_penalizes_only_the_columns_it_is_told_to() -> None:
    x, y, w = _toy()
    penalties = np.zeros(x.shape[1])
    penalties[-1] = 1e9
    fit = ctx.block_ridge(x, y, w, penalties)
    assert abs(fit.beta[-1]) < 1e-6
    # The unpenalized columns still fit: the intercept is not crushed with it.
    assert abs(fit.beta[0]) > 1e-6


# -------------------------------------------------------------- the ablation


def _summary(improved: int, measured: int, move: float) -> dict[str, dict[str, object]]:
    return {
        "venue": {
            "cohorts_improved": improved,
            "cohorts_measured": measured,
            "leaderboard_move": {"median": move},
        }
    }


def test_a_family_that_moves_the_table_without_predicting_is_dropped() -> None:
    verdict = ctx.verdicts(_summary(improved=2, measured=20, move=0.4))
    assert verdict["venue"].startswith("dropped: moves the table without predicting")


def test_a_family_that_lowers_out_of_fold_error_is_kept() -> None:
    verdict = ctx.verdicts(_summary(improved=18, measured=20, move=0.4))
    assert verdict["venue"].startswith("kept")


def test_a_family_that_does_nothing_says_so_rather_than_disappearing() -> None:
    """The declaration promised the nulls would be published."""
    summary = ctx.summarize_ablation([])
    assert set(summary) == set(ctx.ABLATION_ORDER)
    assert all(entry["fits"] == 0 for entry in summary.values())


# -------------------------------------------------------------- the era split


def test_the_two_leagues_are_split_at_the_first_call_of_duty_league_season() -> None:
    assert ctx.league_of(2019) == "CWL"
    assert ctx.league_of(2020) == "CDL"
    assert ctx.league_of(None) == "CWL"


def test_min_effect_drops_a_family_that_clears_the_share_on_a_rounding_move() -> None:
    """The amendment is a floor on magnitude and never on the share.

    `prize_pool` crossed KEEP_SHARE by one cohort on a median move of 1e-5,
    which is a count of rounding rather than a count of wins. The declared rule
    keeps it and has to go on saying so; the amended rule does not.
    """
    summary = _summary(improved=18, measured=20, move=1e-5)
    assert ctx.verdicts(summary)["venue"].startswith("kept")
    amended = ctx.verdicts(summary, ctx.MIN_EFFECT)["venue"]
    assert amended.startswith("dropped: clears the share on a move too small")


def test_min_effect_can_only_make_the_rule_harder() -> None:
    """Nothing the declared rule dropped may be kept by the amendment."""
    for improved, move in ((2, 0.4), (18, 0.4), (18, 1e-5), (10, 0.0)):
        summary = _summary(improved=improved, measured=20, move=move)
        declared = ctx.verdicts(summary)["venue"]
        amended = ctx.verdicts(summary, ctx.MIN_EFFECT)["venue"]
        if declared.startswith("dropped"):
            assert amended.startswith("dropped")
