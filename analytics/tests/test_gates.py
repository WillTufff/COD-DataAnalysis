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
    artifact_names_read,
    basis_failures,
    cohort_failures,
    evaluation_failures,
    mode_naming_failures,
    rotation_failures,
    season_rapm_failures,
    site_read_failures,
    skill_prior_failures,
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


# -------------------------------------------------------- the season plus-minus


def _season_payload(**over: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": True,
        "resolution_by_league": {"CDL": "season", "CWL": "era"},
        "by_cell": [
            {"cell": "CWL era", "resolution": "era"},
            {"cell": "2020 CDL", "resolution": "season"},
        ],
    }
    payload.update(over)
    return payload


def _season_rows() -> list[tuple[str, str, int, float]]:
    return [
        ("smoothed", "era", 1, 0.04),
        ("smoothed", "era", 1, 0.04),  # the same estimate, filed under two seasons
        ("filtered", "era", 1, 0.03),
        ("filtered", "era", 1, 0.03),
        ("smoothed", "season", 2, -0.01),
    ]


def test_a_season_fit_at_the_resolution_the_preflight_allowed_passes() -> None:
    assert season_rapm_failures(_season_payload(), _season_rows()) == []


def test_a_fit_that_did_not_run_is_a_failure_rather_than_a_silence() -> None:
    bad = season_rapm_failures({"available": False, "reason": "not enough admitted maps"}, [])
    assert bad and "not enough admitted maps" in bad[0]


def test_an_era_cell_in_an_era_the_verdict_did_not_pool_fails() -> None:
    """The resolution drifting from the measurement that permitted it."""
    payload = _season_payload(resolution_by_league={"CDL": "season", "CWL": "season"})
    bad = season_rapm_failures(payload, _season_rows())
    assert bad and "CWL era" in bad[0]


def test_storing_only_the_smoothed_family_fails() -> None:
    """Every forward test would be left with nothing it is allowed to read."""
    smoothed_only = [r for r in _season_rows() if r[0] == "smoothed"]
    bad = season_rapm_failures(_season_payload(), smoothed_only)
    assert bad and "forward test" in bad[0]


def test_an_era_coefficient_that_differs_between_its_seasons_fails() -> None:
    rows = _season_rows()
    rows[1] = ("smoothed", "era", 1, 0.05)
    bad = season_rapm_failures(_season_payload(), rows)
    assert bad and "separate estimates" in bad[0]


# --------------------------------------------------------- evaluation harness


def _manifest(**over: object) -> dict[str, Any]:
    return {
        "sha256": "abc",
        "pinned_sha256": "abc",
        "matches_invariants_pin": True,
        "primary": {"predictors": ["composite", "openskill", "skill"]},
        "supersedes": [
            {"version": "1.0.0", "sha256": "old", "predictors": ["composite", "openskill"]}
        ],
        **over,
    }


def _repro(**over: object) -> dict[str, Any]:
    return {
        "reproduces": True,
        "recomputed": [{"what": "cells.kd->kd.r", "matches": True}],
        "against_the_page": [
            {"what": "persistence transitions", "run": 561, "page": 561, "matches": True}
        ],
        **over,
    }


def _primary(**over: object) -> dict[str, Any]:
    return {
        "available": True,
        "power": {"by_predictor": {"composite": {"mde80_clustered": 0.09}}},
        "scored_predictors": ["composite", "openskill"],
        "not_yet_fitted": ["skill"],
        **over,
    }


def _secondary(scope: str = "filtered") -> dict[str, Any]:
    return {"season_plusminus_persistence": {"scope_read": scope}}


def _placebos(passes: bool = True) -> dict[str, Any]:
    return {"placebos": {"shuffled_sides": {"available": True, "passes": passes}}}


def test_a_harness_held_to_its_declaration_passes() -> None:
    assert evaluation_failures(_manifest(), _repro(), _primary(), _secondary(), _placebos()) == []


def test_a_manifest_edited_after_the_fact_fails() -> None:
    """A test declared after the model is not a test declared in advance."""
    bad = evaluation_failures(
        _manifest(sha256="moved"), _repro(), _primary(), _secondary(), _placebos()
    )
    assert bad and "pinned" in bad[0]


def test_a_harness_that_cannot_recover_the_published_numbers_fails() -> None:
    bad = evaluation_failures(
        _manifest(),
        _repro(reproduces=False, recomputed=[{"what": "contrasts.kd.delta_r", "matches": False}]),
        _primary(),
        _secondary(),
        _placebos(),
    )
    assert bad and "contrasts.kd.delta_r" in bad[0]


def test_a_published_figure_that_drifted_from_the_page_fails() -> None:
    """The defect this gate was written for: the page said 541, the run said 561."""
    bad = evaluation_failures(
        _manifest(),
        _repro(
            against_the_page=[
                {"what": "persistence transitions", "run": 561, "page": 541, "matches": False}
            ]
        ),
        _primary(),
        _secondary(),
        _placebos(),
    )
    assert bad and "541" in bad[0]


def test_a_primary_test_with_no_declared_threshold_fails() -> None:
    bad = evaluation_failures(
        _manifest(),
        _repro(),
        _primary(power={"by_predictor": {"composite": {"mde80_clustered": None}}}),
        _secondary(),
        _placebos(),
    )
    assert bad and "minimum detectable effect" in bad[0]


def test_a_forward_test_reading_the_smoothed_family_fails() -> None:
    bad = evaluation_failures(
        _manifest(), _repro(), _primary(), _secondary(scope="smoothed"), _placebos()
    )
    assert bad and "smoothed" in bad[0]


def test_a_placebo_finding_structure_in_shuffled_data_fails() -> None:
    bad = evaluation_failures(
        _manifest(), _repro(), _primary(), _secondary(), _placebos(passes=False)
    )
    assert bad and "shuffled_sides" in bad[0]


def test_the_fixed_half_of_the_declaration_moving_fails() -> None:
    """Extending a declaration is allowed; changing what the test is, is not."""
    bad = evaluation_failures(
        _manifest(matches_invariants_pin=False), _repro(), _primary(), _secondary(), _placebos()
    )
    assert bad and "different evaluation" in bad[0]


def test_a_predictor_dropped_from_a_superseded_version_fails() -> None:
    bad = evaluation_failures(
        _manifest(primary={"predictors": ["composite", "skill"]}),
        _repro(),
        _primary(scored_predictors=["composite"], not_yet_fitted=["skill"]),
        _secondary(),
        _placebos(),
    )
    assert bad and "never trimmed" in bad[0]


def test_a_current_declaration_listed_as_superseded_fails() -> None:
    """The history has to say which version is in force."""
    bad = evaluation_failures(
        _manifest(
            supersedes=[
                {"version": "1.0.0", "sha256": "abc", "predictors": ["composite", "openskill"]}
            ]
        ),
        _repro(),
        _primary(),
        _secondary(),
        _placebos(),
    )
    assert bad and "which version is in force" in bad[0]


def test_a_declared_predictor_that_is_neither_scored_nor_reported_unfitted_fails() -> None:
    bad = evaluation_failures(
        _manifest(), _repro(), _primary(not_yet_fitted=[]), _secondary(), _placebos()
    )
    assert bad and "neither scored nor reported as unfitted" in bad[0]


def test_a_predictor_declared_ahead_of_its_model_passes_while_it_is_named() -> None:
    """The whole point of declaring `skill` before P5 fits it."""
    assert evaluation_failures(_manifest(), _repro(), _primary(), _secondary(), _placebos()) == []


# ------------------------------------------------------------------ the floor


def _power(**over: object) -> dict[str, Any]:
    return {
        "available": True,
        "floors": {"composite_measured": {"mde80_clustered": 0.16}},
        **over,
    }


def _fitted(**over: object) -> dict[str, Any]:
    """A prior that passes every condition, so each test breaks exactly one."""
    return {
        "exposure_loading": {"available": True, "passes": True, "ratio": 0.88},
        "ladder": {
            "arms": {
                "ridge": {"available": True},
                "random_forest": {"available": True, "vs_ridge": {"ships": False}},
                "lightgbm": {"available": True, "vs_ridge": {"ships": False}},
            },
            "published_arm": "ridge",
        },
        "ladder_history": [],
        **over,
    }


PINNED = {"n": 267, "clusters": 90, "mde80_clustered": 0.16, "distance_to_clear": 0.433}


def test_a_floor_that_still_matches_its_pin_passes() -> None:
    assert skill_prior_failures(_power(), _fitted(), PINNED) == []


def test_a_floor_with_no_prior_yet_passes() -> None:
    """The floor ships first and waits. That is the whole ordering."""
    assert skill_prior_failures(_power(), {}, PINNED) == []


def test_a_floor_that_moved_once_a_prior_existed_fails() -> None:
    """The failure the run-id ordering was reaching for, stated so it can happen."""
    bad = skill_prior_failures(
        _power(floors={"composite_measured": {"mde80_clustered": 0.09}}), _fitted(), PINNED
    )
    assert bad and "declared in advance" in bad[0]


def test_a_floor_that_moved_inside_rounding_passes() -> None:
    assert (
        skill_prior_failures(
            _power(floors={"composite_measured": {"mde80_clustered": 0.1602}}), _fitted(), PINNED
        )
        == []
    )


def test_a_prior_published_with_no_floor_at_all_fails() -> None:
    bad = skill_prior_failures(
        {"available": False, "reason": "too few transitions"}, _fitted(), PINNED
    )
    assert bad and "never computed" in bad[0]


def test_a_panel_too_thin_to_score_is_not_a_failure_until_a_prior_exists() -> None:
    thin = {"available": False, "reason": "too few transitions"}
    assert skill_prior_failures(thin, {}, PINNED) == []


def test_a_floor_that_published_no_threshold_fails() -> None:
    bad = skill_prior_failures(
        _power(floors={"composite_measured": {"mde80_clustered": None}}), _fitted(), PINNED
    )
    assert bad and "no detectable-effect floor" in bad[0]


def test_a_prior_that_loads_on_exposure_harder_than_its_target_fails() -> None:
    """The diagnostic's whole job: a shrinkage map published as a rating."""
    bad = skill_prior_failures(
        _power(),
        _fitted(
            exposure_loading={
                "available": True,
                "passes": False,
                "prior_r2": 0.41,
                "target_r2": 0.30,
                "ratio": 1.37,
                "ratio_max": 1.0,
            }
        ),
        PINNED,
    )
    assert bad and "reporting shrinkage as skill" in bad[0]


def test_an_arm_that_is_neither_fitted_nor_recorded_fails() -> None:
    """A ladder stops being one the moment an arm can go unmentioned."""
    arms = {"ridge": {"available": True}, "random_forest": {"available": True, "vs_ridge": {}}}
    bad = skill_prior_failures(
        _power(),
        _fitted(ladder={"arms": arms, "published_arm": "ridge"}),
        PINNED,
    )
    assert bad and "lightgbm" in bad[0]


def test_a_dropped_arm_passes_on_the_verdict_it_was_dropped_for() -> None:
    """The dependency goes; the measurement that removed it does not."""
    arms = {
        "ridge": {"available": True},
        "random_forest": {"available": True, "vs_ridge": {"ships": False}},
        "lightgbm": {"available": False, "reason": "ModuleNotFoundError"},
    }
    assert (
        skill_prior_failures(
            _power(),
            _fitted(
                ladder={"arms": arms, "published_arm": "ridge"},
                ladder_history=[{"arm": "lightgbm", "verdict": "did not beat the ridge"}],
            ),
            PINNED,
        )
        == []
    )


# ------------------------------------------------------------------ site reads

SITE_SHAPES = """
  const rows = await db.execute(sql`
    SELECT payload FROM model_artifacts
    WHERE run_id = ${ratingRunId} AND name = 'rating_posterior'
  `);
  const more = await db.execute(sql`
    SELECT name, payload FROM model_artifacts
    WHERE run_id = ${run.id} AND name IN ('series_dynamics', 'series_momentum')
  `);
  return artifactPayload<SeasonRapm>(runId, "rapm_season");
  const mapBacktest = byName.get("map_backtest") as MapElo["mapBacktest"];
const META_ARTIFACT_NAMES = [
  "meta_weapons",
  "meta_rigs",
] as const;
"""


def test_every_shape_the_site_reads_an_artifact_by_is_found() -> None:
    """Four shapes reach model_artifacts, and a parser that misses one would
    report a clean subset while the missing read stays unchecked."""
    assert artifact_names_read(SITE_SHAPES) == {
        "rating_posterior",
        "series_dynamics",
        "series_momentum",
        "rapm_season",
        "map_backtest",
        "meta_weapons",
        "meta_rigs",
    }


def test_a_page_reading_an_artifact_no_run_writes_fails() -> None:
    assert site_read_failures({"rapm_season", "skill_ghost"}, {"rapm_season"}) == [
        "the site reads 'skill_ghost', which no run has written"
    ]


def test_an_artifact_no_page_reads_yet_is_not_a_failure() -> None:
    """The gate is one-directional on purpose: writing ahead of the site is how
    every phase here shipped, and reading ahead of the models is the defect."""
    assert site_read_failures({"rapm_season"}, {"rapm_season", "rapm_recovery"}) == []
