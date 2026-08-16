"""Career aggregation: the replacement baseline, both credit rules, the era rule."""

from __future__ import annotations

import pytest

from cdlhub_analytics import career
from cdlhub_analytics.ratings import statespace
from cdlhub_analytics.ratings.preflight import Season

CDL = {
    19: Season(19, 2020, "CDL"),
    20: Season(20, 2021, "CDL"),
    21: Season(21, 2022, "CDL"),
    22: Season(22, 2023, "CDL"),
}
CWL = {1: Season(1, 2017, "CWL"), 2: Season(2, 2018, "CWL"), 12: Season(12, 2019, "CWL")}
SEASONS = {**CWL, **CDL}


def value(
    player_id: int,
    season_id: int,
    v: float,
    maps: int = 20,
    sd: float | None = 0.1,
    resolution: str = statespace.SEASON,
) -> career.SeasonValue:
    return career.SeasonValue(
        player_id=player_id,
        season_id=season_id,
        maps=maps,
        value=v,
        sd=sd,
        resolution=resolution,
    )


# ------------------------------------------------------------------ replacement


def test_replacement_is_the_qualified_cohort_minimum() -> None:
    rows = [value(1, 19, 5.0), value(2, 19, 1.0), value(3, 19, 9.0)]
    assert career.replacement_by_season(rows) == {19: 1.0}


def test_an_unqualified_season_does_not_set_the_baseline() -> None:
    """A player below the map floor is not in the cohort, so they cannot be the floor."""
    rows = [value(1, 19, 5.0), value(2, 19, -99.0, maps=career.QUALIFIED_MAPS - 1)]
    assert career.replacement_by_season(rows) == {19: 5.0}


def test_a_season_with_no_qualified_player_has_no_baseline() -> None:
    """Absent, not zero: zero is a legal value on both axes and would credit a
    season that could not be measured."""
    rows = [value(1, 19, 5.0, maps=1)]
    assert career.replacement_by_season(rows) == {}


# ----------------------------------------------------------------- aggregation


def test_a_total_is_the_sum_over_replacement() -> None:
    rows = [
        value(1, 19, 5.0),
        value(1, 20, 6.0),
        value(2, 19, 1.0),
        value(2, 20, 2.0),
    ]
    out = career.aggregate(rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_ALL)
    totals = {row.player_id: row.total for row in out}
    assert totals[1] == pytest.approx(8.0)
    assert totals[2] == pytest.approx(0.0)


def test_peak_and_total_can_disagree() -> None:
    """The reason they are separate columns rather than one."""
    rows = [
        value(1, 19, 10.0),
        value(1, 20, 0.0),
        value(2, 19, 4.0),
        value(2, 20, 4.0),
        value(2, 21, 4.0),
        value(3, 19, 0.0),
        value(3, 20, 0.0),
        value(3, 21, 0.0),
    ]
    out = {
        row.player_id: row
        for row in career.aggregate(
            rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_ALL
        )
    }
    assert out[1].peak > out[2].peak
    assert out[2].total > out[1].total


def test_best_three_needs_three_consecutive_league_seasons() -> None:
    """A player who sat out a year did not have a run through it."""
    rows = [value(1, 19, 5.0), value(1, 21, 5.0), value(1, 22, 5.0), value(9, 19, 0.0)]
    for season_id in (20, 21, 22):
        rows.append(value(9, season_id, 0.0))
    out = {
        row.player_id: row
        for row in career.aggregate(
            rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_ALL
        )
    }
    # Player 1 missed 2021, so every window that covers three league seasons
    # contains a year they did not play, and it is worth two seasons rather than
    # three. The window opens on the earliest season they did play.
    assert out[1].best_three == pytest.approx(10.0)
    assert out[1].best_three_start_season_id == 19


def test_a_career_shorter_than_the_window_has_no_best_three() -> None:
    rows = [value(1, 19, 5.0), value(1, 20, 5.0), value(2, 19, 0.0), value(2, 20, 0.0)]
    out = {
        row.player_id: row
        for row in career.aggregate(
            rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_ALL
        )
    }
    assert out[1].best_three is None
    assert out[1].best_three_start_season_id is None


def test_the_baseline_is_taken_inside_the_era_being_summed() -> None:
    """A CWL floor must never be subtracted from a CDL season: the two are not
    on a common scale across the league change."""
    rows = [value(1, 1, -10.0), value(1, 19, 5.0), value(2, 1, -12.0), value(2, 19, 4.0)]
    cdl = career.aggregate(rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_CDL)
    assert {row.player_id: row.replacement for row in cdl} == {1: 4.0, 2: 4.0}


def test_the_total_interval_compounds_the_season_errors() -> None:
    rows = [value(1, 19, 5.0, sd=0.3), value(1, 20, 5.0, sd=0.4), value(2, 19, 0.0, sd=0.1)]
    rows.append(value(2, 20, 0.0, sd=0.1))
    out = {
        row.player_id: row
        for row in career.aggregate(
            rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_ALL
        )
    }
    assert out[1].total_sd == pytest.approx(0.5)


def test_a_missing_season_error_withdraws_the_interval_rather_than_shrinking_it() -> None:
    rows = [value(1, 19, 5.0, sd=0.3), value(1, 20, 5.0, sd=None), value(2, 19, 0.0)]
    rows.append(value(2, 20, 0.0))
    out = {
        row.player_id: row
        for row in career.aggregate(
            rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_ALL
        )
    }
    assert out[1].total_sd is None


# ---------------------------------------------------------------- credit rules


def test_the_team_share_moves_a_total_by_a_quarter_of_the_team_term() -> None:
    rows = [value(1, 19, 1.0), value(2, 19, 0.0)]
    teams = {(1, 19): 100, (2, 19): 200}
    effects = {(100, 19): 4.0, (200, 19): 0.0}
    plain = career.aggregate(
        rows, SEASONS, career.PLUS_MINUS, career.CREDIT_DEVIATION, career.SCOPE_CDL
    )
    shared = career.aggregate(
        rows,
        SEASONS,
        career.PLUS_MINUS,
        career.CREDIT_DEVIATION_PLUS_TEAM,
        career.SCOPE_CDL,
        effects,
        teams,
    )
    assert {r.player_id: r.total for r in plain}[1] == pytest.approx(1.0)
    # Player 1 gains a quarter of 4.0 and the replacement level gains nothing,
    # because the floor player's team term is zero.
    assert {r.player_id: r.total for r in shared}[1] == pytest.approx(2.0)


def test_the_credit_rule_can_reorder_the_table() -> None:
    """The whole reason both columns are published."""
    rows = [value(1, 19, 2.0), value(2, 19, 1.0), value(3, 19, 0.0)]
    teams = {(1, 19): 100, (2, 19): 200, (3, 19): 300}
    effects = {(100, 19): 0.0, (200, 19): 8.0, (300, 19): 0.0}
    plain = {
        r.player_id: r.total
        for r in career.aggregate(
            rows, SEASONS, career.PLUS_MINUS, career.CREDIT_DEVIATION, career.SCOPE_CDL
        )
    }
    shared = {
        r.player_id: r.total
        for r in career.aggregate(
            rows,
            SEASONS,
            career.PLUS_MINUS,
            career.CREDIT_DEVIATION_PLUS_TEAM,
            career.SCOPE_CDL,
            effects,
            teams,
        )
    }
    assert plain[1] > plain[2]
    assert shared[2] > shared[1]


def test_a_player_with_no_team_column_is_dropped_from_the_shared_rule_only() -> None:
    """Silently crediting zero would publish a missing join as a bad team."""
    rows = [value(1, 19, 2.0), value(2, 19, 0.0)]
    teams = {(2, 19): 200}
    effects = {(200, 19): 1.0}
    plain = career.aggregate(
        rows, SEASONS, career.PLUS_MINUS, career.CREDIT_DEVIATION, career.SCOPE_CDL
    )
    shared = career.aggregate(
        rows,
        SEASONS,
        career.PLUS_MINUS,
        career.CREDIT_DEVIATION_PLUS_TEAM,
        career.SCOPE_CDL,
        effects,
        teams,
    )
    assert {r.player_id for r in plain} == {1, 2}
    assert {r.player_id for r in shared} == {2}


# -------------------------------------------------------------------- the eras


def test_an_era_coefficient_is_counted_once_not_once_per_season_it_covers() -> None:
    rows = [value(1, season_id, 3.0, resolution=statespace.ERA) for season_id in sorted(CWL)]
    rows += [value(2, season_id, 1.0, resolution=statespace.ERA) for season_id in sorted(CWL)]
    out = {row.player_id: row for row in career.era_rows(rows, SEASONS, career.CREDIT_DEVIATION)}
    assert out[1].total == pytest.approx(2.0)
    assert out[1].seasons == 3
    assert out[1].era_scope == career.SCOPE_CWL


def test_an_era_row_claims_no_peak_season_and_no_run() -> None:
    """One estimate over three years cannot name which of them was the peak."""
    rows = [value(1, season_id, 3.0, resolution=statespace.ERA) for season_id in sorted(CWL)]
    rows += [value(2, season_id, 1.0, resolution=statespace.ERA) for season_id in sorted(CWL)]
    out = {row.player_id: row for row in career.era_rows(rows, SEASONS, career.CREDIT_DEVIATION)}
    assert out[1].peak_season_id is None
    assert out[1].best_three is None


def test_season_resolution_rows_do_not_leak_into_the_era_rule() -> None:
    rows = [value(1, 19, 5.0), value(2, 19, 0.0)]
    assert career.era_rows(rows, SEASONS, career.CREDIT_DEVIATION) == []


# ------------------------------------------------------------------- artifact


def test_the_artifact_reports_what_the_two_rules_disagree_about() -> None:
    rows = [value(p, 19, float(p)) for p in range(1, 8)]
    rows += [value(p, 20, float(p) * 2.0) for p in range(1, 8)]
    teams = {(p, s): 100 + p for p in range(1, 8) for s in (19, 20)}
    effects = {(100 + p, s): float(p % 3) for p in range(1, 8) for s in (19, 20)}
    built = career.aggregate(
        rows, SEASONS, career.PLUS_MINUS, career.CREDIT_DEVIATION, career.SCOPE_CDL
    ) + career.aggregate(
        rows,
        SEASONS,
        career.PLUS_MINUS,
        career.CREDIT_DEVIATION_PLUS_TEAM,
        career.SCOPE_CDL,
        effects,
        teams,
    )
    payload = career.artifact(built, rows, rows, set(), SEASONS)
    assert payload["available"]
    assert payload["credit_rules"]["n_players"] == 7
    assert payload["credit_rules"]["rank_correlation"] is not None
    assert payload["team_share"] == career.TEAM_SHARE


def test_separation_counts_totals_that_clear_two_standard_deviations() -> None:
    rows = [value(1, 19, 10.0, sd=0.1), value(2, 19, 0.0, sd=0.1)]
    built = career.aggregate(rows, SEASONS, career.COMPOSITE, career.CREDIT_NONE, career.SCOPE_ALL)
    payload = career.artifact(built, rows, [], set(), SEASONS)
    block = payload["separation"]["composite.none.all"]
    assert block["n_clear_of_zero"] == 1
    assert block["n_with_interval"] == 2


# ------------------------------------------------------- the MLG era, unscored

MLG = {31: Season(31, 2013, "MLG"), 32: Season(32, 2014, "MLG"), 33: Season(33, 2015, "MLG")}
WITH_MLG = {**SEASONS, **MLG}


def test_a_league_with_no_scale_of_its_own_is_out_of_scope_not_cwl() -> None:
    """The two-way test read every non-CDL season as a CWL one."""
    assert career._scope_of(MLG[31]) == career.SCOPE_OUT
    assert career._scope_of(CWL[1]) == career.SCOPE_CWL
    assert career._scope_of(CDL[19]) == career.SCOPE_CDL


def test_an_out_of_scope_season_is_summed_under_no_era() -> None:
    rows = [value(1, 31, 3.0), value(1, 32, 3.0), value(1, 19, 2.0), value(2, 19, 1.0)]
    for scope in (career.SCOPE_CWL, career.SCOPE_CDL, career.SCOPE_ALL):
        out = career.aggregate(rows, WITH_MLG, career.PLUS_MINUS, career.CREDIT_NONE, scope)
        assert all(sid not in MLG for row in out for sid in [row.peak_season_id] if sid), scope


def test_the_all_scope_does_not_sweep_an_out_of_scope_season_back_in() -> None:
    order = career._season_order(WITH_MLG, career.SCOPE_ALL)
    assert set(order) == set(SEASONS)
