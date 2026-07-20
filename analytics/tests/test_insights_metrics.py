"""Tests for the metric-layer insight kinds.

The bug these are written against is a real one that shipped in the first draft:
half the intangibles are lower-is-better, so comparing raw percentiles against
K/D reported players who were *elite at both* as contradictions, with a headline
saying the opposite of the truth. Direction handling is therefore the thing
under test, not the SQL."""

from cdlhub_analytics.insights_metrics import (
    _fmt,
    _label,
    _ordinal,
    _quality_pctl,
)

CATALOG = {
    "snd_fb_rate": {
        "key": "snd_fb_rate",
        "label": "First-blood rate",
        "higher_is_better": True,
        "unit": "per round",
        "tier": "gold",
    },
    "untraded_death_rate": {
        "key": "untraded_death_rate",
        "label": "Untraded-death rate",
        "higher_is_better": False,
        "unit": "rate",
        "tier": "gold",
    },
}


def test_quality_pctl_leaves_higher_is_better_alone() -> None:
    assert _quality_pctl(CATALOG, "snd_fb_rate", 0.95) == 0.95


def test_quality_pctl_flips_lower_is_better() -> None:
    """A 3rd-percentile untraded-death rate is an excellent season, not a poor
    one — this is the flip that the first version was missing."""
    assert abs(_quality_pctl(CATALOG, "untraded_death_rate", 0.03) - 0.97) < 1e-9


def test_elite_at_both_is_not_a_contradiction() -> None:
    """The regression case: elite K/D and an elite (low) untraded-death rate
    must agree, so no outlier is reported."""
    kd_pctl = 0.99
    quality = _quality_pctl(CATALOG, "untraded_death_rate", 0.01)
    undersold = quality >= 0.9 and kd_pctl <= 0.5
    oversold = quality <= 0.1 and kd_pctl >= 0.9
    assert not undersold and not oversold


def test_genuine_contradiction_is_still_caught() -> None:
    kd_pctl = 0.95
    quality = _quality_pctl(CATALOG, "untraded_death_rate", 0.97)  # dies alone constantly
    assert quality <= 0.1 and kd_pctl >= 0.9


def test_unknown_metric_defaults_to_higher_is_better() -> None:
    assert _quality_pctl({}, "mystery", 0.8) == 0.8


def test_ordinal_reads_as_a_rank() -> None:
    assert _ordinal(0.93) == "93rd percentile"
    assert _ordinal(0.01) == "1st percentile"
    assert _ordinal(0.02) == "2nd percentile"
    assert _ordinal(0.11) == "11th percentile"
    assert _ordinal(1.0) == "100th percentile"


def test_ordinal_never_claims_a_zeroth_percentile() -> None:
    assert _ordinal(0.0) == "1st percentile"


def test_rate_metrics_format_as_percentages() -> None:
    assert _fmt(CATALOG, "untraded_death_rate", 0.615) == "62%"


def test_label_falls_back_to_the_key() -> None:
    assert _label(CATALOG, "snd_fb_rate") == "First-blood rate"
    assert _label(CATALOG, "not_in_catalog") == "not_in_catalog"
