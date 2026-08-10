"""The release gates, fed bad input on purpose.

A gate nothing has ever failed is indistinguishable from a gate that cannot
fail, and all three conditions here shipped green once already. So each is
exercised twice: on an artifact that meets it, and on the artifact shape the
defect actually produced — a title with no rotation, a cohort at τ² = 0, a basis
whose components moved under their names.
"""

from __future__ import annotations

from typing import Any

from cdlhub_analytics.gates import (
    PUBLISHED_BASES,
    basis_failures,
    cohort_failures,
    mode_naming_failures,
    rotation_failures,
)

# ------------------------------------------------------------------- rotation


def test_a_rotation_every_title_declares_passes() -> None:
    assert rotation_failures({"n_series_no_rotation": 0}, {"n_no_rotation": 0}) == []


def test_an_undeclared_rotation_fails_and_says_how_many_series() -> None:
    """The shape of the defect: map ratings fit all 11,623 maps while the series
    rollup covered 1,310 of 2,943 series, and the count sat in an artifact."""
    failed = rotation_failures({"n_series_no_rotation": 1633}, {"n_no_rotation": 1587})
    assert len(failed) == 2
    assert "1,633" in failed[0] and "map_elo" in failed[0]
    assert "1,587" in failed[1] and "series_dynamics" in failed[1]


# --------------------------------------------------------------------- cohort


def _cohort(**kw: Any) -> dict[str, Any]:
    base = {
        "year": 2021,
        "title": "BOCW",
        "mode": "Search & Destroy",
        "tau": 2.223,
        "collapsed": False,
        "stalled": False,
    }
    return {**base, **kw}


def test_a_cohort_that_fits_passes() -> None:
    assert cohort_failures({"cohorts": [_cohort(), _cohort(year=2022, title="VG")]}) == []


def test_a_collapsed_cohort_fails() -> None:
    """What ran green for two seasons: EM exited on iteration 1 at τ² = 0 and
    every player in the cohort published 1.00."""
    failed = cohort_failures({"cohorts": [_cohort(collapsed=True, tau=0.0)]})
    assert len(failed) == 1
    assert "2021 BOCW Search & Destroy" in failed[0]


def test_a_stalled_fit_fails_separately_from_a_collapsed_one() -> None:
    """Running out of a hundred thousand iterations is a different condition
    from landing on the boundary, and conflating them is how the collapse hid."""
    failed = cohort_failures({"cohorts": [_cohort(stalled=True)]})
    assert len(failed) == 1
    assert "ran out of iterations" in failed[0]

    both = cohort_failures({"cohorts": [_cohort(collapsed=True, stalled=True)]})
    assert len(both) == 2


# ---------------------------------------------------------------------- basis


def _published(overrides: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    """The pinned bases rendered back into the artifact shape they came from."""
    bases = []
    for name, axes in PUBLISHED_BASES.items():
        rows = (overrides or {}).get(name)
        bases.append(
            {
                "basis": name,
                "axes": rows
                or [
                    {"name": axis_name, "loadings": [{"column": f"0:{column}_pm", "loading": 0.5}]}
                    for axis_name, column in axes
                ],
            }
        )
    return {"bases": bases}


def test_the_bases_as_pinned_pass() -> None:
    assert basis_failures(_published()) == []


def test_a_component_that_moved_under_its_name_fails() -> None:
    """The defect exactly: the basis grew, an assists component moved into third
    place, and `streak depth` stayed on the third seat while describing assists.
    No number was wrong, which is why nothing caught it."""
    moved = [
        {"name": "volume", "loadings": [{"column": "0:kills_pm", "loading": 0.5}]},
        {"name": "survival", "loadings": [{"column": "0:deaths_pm", "loading": 0.5}]},
        {"name": "streak depth", "loadings": [{"column": "0:assists_pm", "loading": 0.58}]},
        {"name": "risk", "loadings": [{"column": "0:deep_streak_rate", "loading": 0.53}]},
        {"name": "axis 5", "loadings": [{"column": "0:eight_plus_streaks_total", "loading": 0.5}]},
    ]
    failed = basis_failures(_published({"core CWL": moved}))
    assert len(failed) == 3
    assert all("core CWL" in line for line in failed)
    assert "named 'streak depth', pinned 'axis 3'" in failed[0]


def test_a_basis_that_retains_a_different_number_of_components_fails() -> None:
    four = [
        {"name": name, "loadings": [{"column": f"0:{column}_pm", "loading": 0.5}]}
        for name, column in PUBLISHED_BASES["core CWL"][:4]
    ]
    failed = basis_failures(_published({"core CWL": four}))
    assert len(failed) == 1
    assert "retains 4 components, pinned at 5" in failed[0]


def test_a_basis_that_appears_or_disappears_fails() -> None:
    """A new era brings a new basis, and it reaches the site with axis names
    nobody has read. The gate is what forces that reading to happen."""
    extra = _published()
    extra["bases"].append({"basis": "core CDL2", "axes": []})
    assert basis_failures(extra) == [
        "basis 'core CDL2' is published and not pinned in gates.PUBLISHED_BASES"
    ]

    gone = _published()
    gone["bases"] = [b for b in gone["bases"] if b["basis"] != "core CDL"]
    assert basis_failures(gone) == ["basis 'core CDL' is pinned and no longer published"]


def test_a_denominator_change_alone_does_not_trip_the_gate() -> None:
    """The CDL box scores denominate per map where the CWL years denominate per
    ten minutes. A name has to survive that fork or the gate cries wolf at every
    era boundary, so the comparison is on the quantity, not the column key."""
    per10 = [
        {"name": name, "loadings": [{"column": f"1:{column}_p10", "loading": 0.5}]}
        for name, column in PUBLISHED_BASES["core CWL"]
    ]
    assert basis_failures(_published({"core CWL": per10})) == []


# ---------------------------------------------------------------- mode naming


def test_every_scored_mode_named_passes() -> None:
    named = {"hardpoint", "search-and-destroy", "overload"}
    scored = [("hardpoint", 25928), ("search-and-destroy", 37281), ("overload", 684)]
    assert mode_naming_failures(scored, named) == []


def test_a_scored_mode_with_no_name_fails_with_its_row_count() -> None:
    """The shape of the defect: a title brought a mode the site had never been
    told about, and every control that lists modes printed its slug in a column
    of names — the report builder's mode picker, the export's Mode column, and
    the per-mode cards on every player who played it."""
    failed = mode_naming_failures([("overload", 684)], {"hardpoint"})
    assert len(failed) == 1
    assert "overload" in failed[0] and "684" in failed[0]


def test_a_named_mode_nobody_played_is_not_a_failure() -> None:
    """`game_modes` carries modes no archived season used. A name with no rows
    is spare capacity, not a gap."""
    assert mode_naming_failures([], {"hardpoint", "blitz"}) == []
