import math

from cdlhub_analytics.cohort import z_and_pctl


def test_z_and_pctl_golden() -> None:
    # Cohort 1..5: mean 3, sd (ddof=1) = sqrt(2.5)
    values = {i: float(i) for i in range(1, 6)}
    stats = z_and_pctl(values, cohort_ids=[1, 2, 3, 4, 5])
    sd = math.sqrt(2.5)
    assert math.isclose(stats[3][0], 0.0)
    assert math.isclose(stats[5][0], 2.0 / sd)
    assert math.isclose(stats[1][1], 0.2)  # 1 of 5 at or below
    assert math.isclose(stats[5][1], 1.0)


def test_unqualified_scored_against_qualified_cohort() -> None:
    values = {1: 1.0, 2: 2.0, 3: 3.0, 99: 10.0}  # 99 not in cohort
    stats = z_and_pctl(values, cohort_ids=[1, 2, 3])
    assert stats[99][0] > 2.0  # z vs cohort mean/sd, not including itself
    assert stats[99][1] == 1.0


def test_degenerate_cohort_returns_empty() -> None:
    assert z_and_pctl({1: 5.0, 2: 5.0}, [1, 2]) == {}
    assert z_and_pctl({1: 5.0}, [1]) == {}
