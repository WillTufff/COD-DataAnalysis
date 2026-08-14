"""Error control over the findings: the two step-up procedures and the four nulls."""

from __future__ import annotations

import math

import pytest

from cdlhub_analytics import errorcontrol as ec


def _finding(insight_id: int, kind: str, p: float | None) -> ec.Finding:
    return ec.Finding(insight_id=insight_id, kind=kind, p_value=p)


# ------------------------------------------------------------ the procedures


def test_bh_matches_the_hand_computed_case() -> None:
    # p * n / rank is 0.05 at every rank, so every q is 0.05.
    assert ec.bh([0.01, 0.02, 0.03, 0.04, 0.05]) == pytest.approx([0.05] * 5)


def test_by_scales_bh_by_the_harmonic_sum() -> None:
    harmonic = sum(1.0 / i for i in range(1, 6))
    assert ec.by([0.01, 0.02, 0.03, 0.04, 0.05]) == pytest.approx([0.05 * harmonic] * 5)


def test_by_is_never_below_bh() -> None:
    p = [0.001, 0.01, 0.2, 0.44, 0.9]
    assert all(b >= a - 1e-12 for a, b in zip(ec.bh(p), ec.by(p), strict=True))


def test_q_values_come_back_in_the_order_the_p_values_arrived() -> None:
    assert ec.bh([0.05, 0.01, 0.03]) == pytest.approx([0.05, 0.03, 0.045])


def test_a_step_up_never_adjusts_a_small_p_above_a_larger_one() -> None:
    q = ec.bh([0.001, 0.9, 0.02])
    assert q[0] <= q[2] <= q[1]


def test_a_q_value_never_falls_below_its_own_p() -> None:
    p = [0.004, 0.02, 0.3, 0.75]
    assert all(q >= v - 1e-12 for v, q in zip(p, ec.bh(p), strict=True))


def test_an_empty_family_corrects_to_nothing() -> None:
    assert ec.bh([]).size == 0
    assert ec.by([]).size == 0


# ------------------------------------------------------------------ the nulls


def test_a_finding_sitting_exactly_on_its_threshold_carries_no_evidence() -> None:
    # The screen admitted it for reaching 2.0, so reaching 2.0 says nothing more.
    assert ec.threshold_normal(2.0, 2.0, 0.5) == pytest.approx(1.0)


def test_the_normal_null_is_conditioned_on_the_screen() -> None:
    # t = 2 one-sided is 0.02275, doubled by conditioning on the half-line.
    assert ec.threshold_normal(3.0, 2.0, 0.5) == pytest.approx(0.0455, abs=1e-4)


def test_the_normal_null_reads_the_absolute_deviation() -> None:
    assert ec.threshold_normal(-3.0, 2.0, 0.5) == ec.threshold_normal(3.0, 2.0, 0.5)


def test_a_missing_or_impossible_error_refuses_to_produce_a_p_value() -> None:
    assert ec.threshold_normal(3.0, 2.0, 0.0) is None
    assert ec.threshold_normal(3.0, 2.0, float("nan")) is None


def test_the_smallest_record_that_clears_a_binomial_screen_carries_no_evidence() -> None:
    # Six of eight is exactly 0.75, the first count reaching a 0.70 screen.
    assert ec.threshold_binomial(6, 8, 0.70, 0.70) == pytest.approx(1.0)


def test_a_perfect_binomial_record_is_the_ratio_of_two_tails() -> None:
    expected = 0.70**8 / sum(math.comb(8, k) * 0.70**k * 0.30 ** (8 - k) for k in range(6, 9))
    assert ec.threshold_binomial(8, 8, 0.70, 0.70) == pytest.approx(expected)


def test_a_longer_record_at_the_same_rate_earns_a_smaller_p() -> None:
    short = ec.threshold_binomial(15, 20, 0.70, 0.70)
    long = ec.threshold_binomial(75, 100, 0.70, 0.70)
    assert short is not None and long is not None and long < short


def test_the_ordering_null_is_two_of_n_factorial() -> None:
    assert ec.ordering(3) == pytest.approx(1 / 3)
    assert ec.ordering(4) == pytest.approx(1 / 12)
    assert ec.ordering(5) == pytest.approx(1 / 60)


def test_two_seasons_cannot_carry_a_trend() -> None:
    assert ec.ordering(2) is None


# ------------------------------------------------------------- the partition


def test_every_declared_class_is_one_of_the_four() -> None:
    assert set(ec.CLASS_OF.values()) <= {
        ec.TESTABLE,
        ec.UNCORRECTED,
        ec.DESCRIPTIVE,
        ec.SELF_TESTED,
    }


def test_an_unnamed_kind_is_reported_rather_than_silently_classified() -> None:
    assert ec.unclassified(["outlier", "brand_new_kind"]) == ["brand_new_kind"]


def test_only_testable_findings_are_corrected() -> None:
    findings = [
        _finding(1, "outlier", 0.01),
        _finding(2, "milestone", None),
        _finding(3, "profile_extreme", None),
        _finding(4, "model_null", None),
    ]
    assert [c.insight_id for c in ec.correct(findings)] == [1]


def test_a_testable_finding_with_no_p_value_is_left_uncorrected() -> None:
    assert ec.correct([_finding(1, "outlier", None)]) == []


def test_families_are_corrected_separately() -> None:
    # Two kinds of one finding each correct to their own p, not to a pooled two.
    findings = [_finding(1, "outlier", 0.04), _finding(2, "trend", 0.04)]
    assert [c.q_bh for c in ec.correct(findings)] == pytest.approx([0.04, 0.04])


def test_the_threshold_decides_retraction_on_the_bh_column() -> None:
    findings = [_finding(i, "outlier", p) for i, p in enumerate([0.001, 0.5], start=1)]
    verdicts = {c.insight_id: c.retracted for c in ec.correct(findings)}
    assert verdicts == {1: False, 2: True}


def test_renumbering_the_findings_does_not_move_any_q_value() -> None:
    ps = [0.002, 0.31, 0.31, 0.9]
    first = {
        i: (c.q_bh, c.q_by)
        for i, c in enumerate(ec.correct([_finding(n, "outlier", p) for n, p in enumerate(ps)]))
    }
    shifted = {
        i: (c.q_bh, c.q_by)
        for i, c in enumerate(
            ec.correct([_finding(n + 5000, "outlier", p) for n, p in enumerate(ps)])
        )
    }
    assert first == shifted


# -------------------------------------------------------------- the artifact


def test_the_artifact_counts_every_finding_into_exactly_one_class() -> None:
    findings = [
        _finding(1, "outlier", 0.01),
        _finding(2, "trend", 0.4),
        _finding(3, "milestone", None),
        _finding(4, "team_style", None),
        _finding(5, "mode_null", None),
    ]
    art = ec.artifact(findings, ec.correct(findings))
    assert sum(art["by_class"].values()) == len(findings)
    assert art["by_class"] == {
        ec.TESTABLE: 2,
        ec.DESCRIPTIVE: 1,
        ec.UNCORRECTED: 1,
        ec.SELF_TESTED: 1,
    }


def test_an_uncorrected_family_publishes_the_reason_it_is_uncorrected() -> None:
    findings = [_finding(1, "team_style", None)]
    art = ec.artifact(findings, [])
    block = next(f for f in art["families"] if f["kind"] == "team_style")
    assert block["reason"] == ec.UNCORRECTED_REASON
    assert "q_bh" not in block


def test_the_artifact_names_the_failures_before_the_threshold_is_applied() -> None:
    findings = [_finding(i, "outlier", p) for i, p in enumerate([0.001, 0.6, 0.9], start=1)]
    art = ec.artifact(findings, ec.correct(findings))
    block = next(f for f in art["families"] if f["kind"] == "outlier")
    assert block["tested"] == 3
    assert block["fails_threshold"] == 2
    assert art["n_retracted"] == 2
