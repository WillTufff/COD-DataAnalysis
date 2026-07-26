import math

from cdlhub_analytics.cohort import MIN_Z_COHORT, z_and_pctl
from cdlhub_analytics.era import kd_standard_error

# The golden tests pin the z arithmetic, so they pass min_z_cohort=2 to opt out
# of the publication floor. The floor itself is tested separately below.


def test_z_and_pctl_golden() -> None:
    # Cohort 1..5: mean 3, sd (ddof=1) = sqrt(2.5)
    values = {i: float(i) for i in range(1, 6)}
    stats = z_and_pctl(values, cohort_ids=[1, 2, 3, 4, 5], min_z_cohort=2)
    sd = math.sqrt(2.5)
    assert math.isclose(stats[3][0] or 0.0, 0.0)
    assert math.isclose(stats[5][0] or 0.0, 2.0 / sd)
    assert math.isclose(stats[1][1], 0.2)  # 1 of 5 at or below
    assert math.isclose(stats[5][1], 1.0)


def test_unqualified_scored_against_qualified_cohort() -> None:
    values = {1: 1.0, 2: 2.0, 3: 3.0, 99: 10.0}  # 99 not in cohort
    stats = z_and_pctl(values, cohort_ids=[1, 2, 3], min_z_cohort=2)
    z = stats[99][0]
    assert z is not None and z > 2.0  # z vs cohort mean/sd, not including itself
    assert stats[99][1] == 1.0


def test_degenerate_cohort_returns_empty() -> None:
    assert z_and_pctl({1: 5.0, 2: 5.0}, [1, 2]) == {}
    assert z_and_pctl({1: 5.0}, [1]) == {}


# ---------- the z publication floor ----------


def test_thin_cohort_publishes_a_percentile_but_no_z() -> None:
    """A rank survives a small cohort; 'N standard deviations' does not."""
    values = {i: float(i) for i in range(1, 5)}
    stats = z_and_pctl(values, cohort_ids=[1, 2, 3, 4])
    assert all(s[0] is None for s in stats.values())
    assert math.isclose(stats[4][1], 1.0)
    assert math.isclose(stats[1][1], 0.25)


def test_z_publishes_once_the_cohort_is_deep_enough() -> None:
    values = {i: float(i) for i in range(1, MIN_Z_COHORT + 1)}
    stats = z_and_pctl(values, cohort_ids=list(values))
    assert all(s[0] is not None for s in stats.values())


def test_floor_is_measured_on_the_qualified_cohort_not_the_scored_set() -> None:
    """Scoring many players against a thin qualified cohort is still thin."""
    cohort = {i: float(i) for i in range(1, 5)}
    others = {100 + i: float(i) for i in range(50)}
    stats = z_and_pctl({**cohort, **others}, cohort_ids=list(cohort))
    assert all(s[0] is None for s in stats.values())


# ---------- delta-method K/D standard error ----------


def moments(pairs: list[tuple[float, float]]) -> tuple[int, float, float, float, float, float]:
    """(maps, kills, deaths, kk, dd, kd_cross) — what the aggregate query returns."""
    return (
        len(pairs),
        sum(k for k, _ in pairs),
        sum(d for _, d in pairs),
        sum(k * k for k, _ in pairs),
        sum(d * d for _, d in pairs),
        sum(k * d for k, d in pairs),
    )


def test_kd_se_matches_a_direct_bootstrap() -> None:
    """The closed form should agree with resampling the same maps."""
    import numpy as np

    maps = [
        (24.0, 18.0),
        (31.0, 22.0),
        (19.0, 25.0),
        (28.0, 16.0),
        (22.0, 21.0),
        (35.0, 19.0),
        (17.0, 27.0),
        (26.0, 20.0),
        (30.0, 23.0),
        (21.0, 24.0),
    ]
    se = kd_standard_error(*moments(maps))
    assert se is not None

    rng = np.random.default_rng(0)
    arr = np.array(maps)
    draws = [
        (lambda s: s[0] / s[1])(arr[rng.integers(0, len(maps), len(maps))].sum(axis=0))
        for _ in range(4000)
    ]
    assert math.isclose(se, float(np.std(draws, ddof=1)), rel_tol=0.15)


def test_kd_se_is_smaller_than_ignoring_the_covariance() -> None:
    """Kills and deaths correlate across maps; pretending otherwise overstates
    the error. This is the term the old 1.96/sqrt(maps) band had no way to see."""
    maps = [(20.0 + i, 15.0 + i) for i in range(12)]  # strongly positively correlated
    m, k, d, kk, dd, cross = moments(maps)
    with_cov = kd_standard_error(m, k, d, kk, dd, cross)
    # Same data with the cross-moment set to the independence value.
    without_cov = kd_standard_error(m, k, d, kk, dd, k * d / m)
    assert with_cov is not None and without_cov is not None
    assert with_cov < without_cov


def test_kd_se_shrinks_with_more_maps() -> None:
    one = kd_standard_error(*moments([(20.0, 15.0), (30.0, 25.0)] * 5))
    more = kd_standard_error(*moments([(20.0, 15.0), (30.0, 25.0)] * 40))
    assert one is not None and more is not None
    assert more < one


def test_kd_se_is_none_where_undefined() -> None:
    assert kd_standard_error(*moments([(20.0, 15.0)])) is None  # one map
    assert kd_standard_error(*moments([(20.0, 0.0), (18.0, 0.0)])) is None  # no deaths
    assert kd_standard_error(*moments([(20.0, 15.0)] * 6)) is None  # no variance
