"""Fixture tests for the season-varying plus-minus. No database required.

The properties worth asserting here are the ones that would fail silently in
production: a resolution read from the wrong place, a thin column that drops a
map instead of pooling it, a filtered coefficient that turns out to have seen
the season after it, and an era coefficient that gets counted three times
because it is stored three times.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import numpy as np
import pytest

from cdlhub_analytics.ratings import statespace
from cdlhub_analytics.ratings.preflight import CAREER_ONLY, COARSER_TIME, TEAM_ANCHORED, Season
from cdlhub_analytics.ratings.rapm import AdmittedMap

DAY0 = date(2020, 1, 24)

# Two CDL seasons and one CWL season, which is the smallest record that can tell
# season resolution and era pooling apart.
SEASONS = {
    1: Season(1, 2018, "CWL"),
    2: Season(2, 2019, "CWL"),
    3: Season(3, 2020, "CDL"),
    4: Season(4, 2021, "CDL"),
}
BY_SEASON = {"CDL": statespace.SEASON, "CWL": statespace.SEASON}
POOLED_CWL = {"CDL": statespace.SEASON, "CWL": statespace.ERA}


def make_map(
    game_id: int,
    home: tuple[int, ...],
    away: tuple[int, ...],
    *,
    season: int = 3,
    home_team: int = 100,
    away_team: int = 200,
    home_won: bool = True,
    series_id: int | None = None,
    mode_slug: str = "hardpoint",
    home_margin: float | None = None,
) -> AdmittedMap:
    return AdmittedMap(
        game_id=game_id,
        series_id=game_id if series_id is None else series_id,
        season_id=season,
        title="SIM",
        mode_slug=mode_slug,
        played_at=DAY0 + timedelta(days=game_id),
        home_team_id=home_team,
        away_team_id=away_team,
        home_players=home,
        away_players=away,
        home_won=home_won,
        home_margin=(1.0 if home_won else -1.0) if home_margin is None else home_margin,
    )


def league(maps: int, season: int, *, first_game: int = 0) -> list[AdmittedMap]:
    """Four teams playing each other, each rotating a fifth player.

    Enough lineup variety that the design identifies something, and enough teams
    that the team-season columns are not a relabelling of the player ones.
    """
    rosters = {
        100: (1, 2, 3, 4, 5),
        200: (6, 7, 8, 9, 10),
        300: (11, 12, 13, 14, 15),
        400: (16, 17, 18, 19, 20),
    }
    pairs = [(100, 200), (300, 400), (100, 300), (200, 400), (100, 400), (200, 300)]
    lineups = ((0, 1, 2, 3), (0, 1, 2, 4))
    out: list[AdmittedMap] = []
    for i in range(maps):
        home_team, away_team = pairs[i % len(pairs)]
        # Keyed on the cycle rather than on the map, or a team that is only ever
        # home on even maps would only ever field one of its two lineups. The
        # two sides rotate on different periods so all four pairings occur.
        home = tuple(sorted(rosters[home_team][j] for j in lineups[(i // len(pairs)) % 2]))
        away = tuple(sorted(rosters[away_team][j] for j in lineups[(i // len(pairs)) % 3 == 2]))
        out.append(
            make_map(
                first_game + i,
                home,
                away,
                season=season,
                home_team=home_team,
                away_team=away_team,
                home_won=i % 3 != 0,
                series_id=first_game + i // 3,
                home_margin=float((i % 7) - 3) or 1.0,
            )
        )
    return out


def fork(cdl: str, cwl: str) -> dict[str, object]:
    return {"by_era": [{"league": "CDL", "branch": cdl}, {"league": "CWL", "branch": cwl}]}


def coef(fit: statespace.Fit, player_id: int, cell: statespace.Cell) -> float:
    """A player's coefficient, where the test has already required a column."""
    value = fit.player(player_id, cell)
    assert value is not None, f"player {player_id} holds no column in {cell}"
    return value


# ------------------------------------------------------------------ resolution


def test_the_resolution_is_read_from_the_verdict_rather_than_declared() -> None:
    assert statespace.resolutions(fork(TEAM_ANCHORED, COARSER_TIME)) == POOLED_CWL


def test_both_stopping_branches_pool_because_the_eras_partition_the_record() -> None:
    """`career_level_only` and `coarser_time_resolution` are one instruction here."""
    assert statespace.resolutions(fork(CAREER_ONLY, CAREER_ONLY)) == {
        "CDL": statespace.ERA,
        "CWL": statespace.ERA,
    }


def test_an_era_that_grows_into_season_resolution_gets_it_without_an_edit() -> None:
    assert statespace.resolutions(fork(TEAM_ANCHORED, TEAM_ANCHORED)) == BY_SEASON


def test_an_unknown_branch_is_not_quietly_pooled() -> None:
    with pytest.raises(ValueError, match="unknown pre-flight branch"):
        statespace.resolutions(fork(TEAM_ANCHORED, "some_new_fallback"))


def test_a_pooled_era_puts_both_its_seasons_in_one_cell() -> None:
    cwl = league(12, season=1) + league(12, season=2, first_game=100)
    cells = {statespace.cell_of(g, SEASONS, POOLED_CWL) for g in cwl}
    assert cells == {(statespace.ERA, "CWL")}
    # The same maps at season resolution are two cells, so the pooling is the
    # verdict's doing and not the fixture's.
    assert len({statespace.cell_of(g, SEASONS, BY_SEASON) for g in cwl}) == 2


def test_an_era_cell_sorts_before_the_seasons_that_follow_it() -> None:
    """The seam link runs through the era cell, so it has to precede 2020."""
    games = league(12, season=1) + league(12, season=3, first_game=100)
    columns = statespace.build_columns(games, SEASONS, POOLED_CWL, admission=1)
    order = columns.cell_order
    assert order[(statespace.ERA, "CWL")] < order[(statespace.SEASON, 3)]


# ------------------------------------------------------------ column admission


def test_a_thin_column_joins_its_cell_bucket_and_the_map_is_not_dropped() -> None:
    games = league(60, season=3)
    # One player appears twice and nowhere else: below any admission floor.
    games += [make_map(900, (1, 2, 3, 99), (6, 7, 8, 9), season=3) for _ in range(2)]
    columns = statespace.build_columns(
        games, SEASONS, BY_SEASON, admission=statespace.ADMISSION_MAPS
    )
    cell = (statespace.SEASON, 3)
    assert (99, cell) not in columns.players
    assert 99 in columns.pooled_players[cell]
    # The slot resolves to the bucket, so the row is still eight players wide and
    # the map stays in the fit.
    assert columns.player_column(99, cell) == columns.pools[cell]
    _gram, rows = statespace.gram(games, columns, SEASONS)
    assert len(rows) == len(games)


def test_a_cell_that_pooled_nobody_gets_no_bucket_column() -> None:
    """A column of zeros in a rank diagnostic is a lie about the design."""
    games = league(60, season=3)
    columns = statespace.build_columns(games, SEASONS, BY_SEASON, admission=1)
    assert columns.pools == {}
    assert columns.size == len(columns.players) + len(columns.teams)


def test_dropping_the_team_columns_is_available_but_is_not_the_published_design() -> None:
    games = league(60, season=3)
    with_teams = statespace.build_columns(games, SEASONS, BY_SEASON, admission=1)
    without = statespace.build_columns(games, SEASONS, BY_SEASON, admission=1, with_teams=False)
    assert with_teams.teams and without.teams == {}
    assert without.size == with_teams.size - len(with_teams.teams)


# ----------------------------------------------------------------- the penalty


def test_the_walk_differences_consecutive_cells_of_one_player_only() -> None:
    games = league(30, season=3) + league(30, season=4, first_game=100)
    columns = statespace.build_columns(games, SEASONS, BY_SEASON, admission=1)
    walk = statespace.walk_penalty(columns, SEASONS)
    a = columns.players[(1, (statespace.SEASON, 3))]
    b = columns.players[(1, (statespace.SEASON, 4))]
    assert walk[a, b] == -1.0 and walk[b, a] == -1.0
    assert walk[a, a] == 1.0 and walk[b, b] == 1.0
    # A different player's column in the same season is not a neighbour.
    other = columns.players[(2, (statespace.SEASON, 4))]
    assert walk[a, other] == 0.0


def test_neither_a_team_season_nor_a_replacement_bucket_is_walked() -> None:
    """Smoothing a team column would launder team quality across seasons."""
    games = league(30, season=3) + league(30, season=4, first_game=100)
    games += [make_map(900 + i, (1, 2, 3, 99), (6, 7, 8, 9), season=3) for i in range(2)]
    columns = statespace.build_columns(games, SEASONS, BY_SEASON, admission=8)
    walk = statespace.walk_penalty(columns, SEASONS)
    for col in list(columns.teams.values()) + list(columns.pools.values()):
        assert not np.any(walk[col, :])
        assert not np.any(walk[:, col])


def test_the_walk_is_a_difference_operator_so_a_flat_trajectory_costs_nothing() -> None:
    games = league(30, season=3) + league(30, season=4, first_game=100)
    columns = statespace.build_columns(games, SEASONS, BY_SEASON, admission=1)
    walk = statespace.walk_penalty(columns, SEASONS)
    flat = np.ones(columns.size, dtype=float)
    assert float(flat @ walk @ flat) == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------------- the response


def test_ties_average_rather_than_break_so_the_response_ignores_arrival_order() -> None:
    values = np.array([3.0, 1.0, 1.0, 2.0], dtype=float)
    scores = statespace._rank_to_normal(values)
    assert scores[1] == pytest.approx(scores[2])
    shuffled = statespace._rank_to_normal(values[[2, 0, 3, 1]])
    assert sorted(np.round(scores, 12)) == sorted(np.round(shuffled, 12))


def test_a_margin_contradicting_the_recorded_winner_leaves_the_margin_targets() -> None:
    games = [
        make_map(1, (1, 2, 3, 4), (5, 6, 7, 8), home_won=True, home_margin=1.0),
        make_map(2, (1, 2, 3, 4), (5, 6, 7, 8), home_won=True, home_margin=-1.0),
        make_map(3, (1, 2, 3, 4), (5, 6, 7, 8), home_won=True, home_margin=0.0),
        # A map whose score the record never carried. `make_map` fills a missing
        # margin in from the winner, so this one is built without it.
        replace(make_map(4, (1, 2, 3, 4), (5, 6, 7, 8), home_won=True), home_margin=None),
    ]
    out = statespace.responses(games)
    assert out[statespace.MARGIN].dropped == 3
    assert list(out[statespace.MARGIN].usable) == [True, False, False, False]
    # Named, not merely counted: a reader can go and look at these three.
    assert out[statespace.MARGIN].dropped_games == (2, 3, 4)
    # The binary target keeps every map: the recorded winner is all it reads.
    assert out[statespace.BINARY].dropped == 0
    assert out[statespace.BINARY].usable.all()


def test_the_margin_is_normal_scored_within_a_season_and_mode() -> None:
    """Two modes on different scales come out on one scale."""
    games = [
        make_map(i, (1, 2, 3, 4), (5, 6, 7, 8), mode_slug="hardpoint", home_margin=float(i + 1))
        for i in range(8)
    ]
    games += [
        make_map(
            100 + i, (1, 2, 3, 4), (5, 6, 7, 8), mode_slug="control", home_margin=float(i + 1) / 100
        )
        for i in range(8)
    ]
    y = statespace.responses(games)[statespace.MARGIN].y
    assert np.allclose(np.sort(y[:8]), np.sort(y[8:]))


# --------------------------------------------------------------------- the fit


def test_the_scope_guard_raises_rather_than_warning() -> None:
    statespace.require_filtered(statespace.FILTERED)
    for scope in (statespace.SMOOTHED, statespace.CAREER, "anything"):
        with pytest.raises(statespace.ScopeError, match="already seen"):
            statespace.require_filtered(scope)


def test_a_solve_recovers_a_planted_signal_in_the_right_order() -> None:
    """The design's own arithmetic, before any of the diagnostics read it."""
    games = league(120, season=3)
    columns = statespace.build_columns(games, SEASONS, BY_SEASON, admission=1)
    _gram, rows = statespace.gram(games, columns, SEASONS)
    truth = np.zeros(columns.size, dtype=float)
    cell = (statespace.SEASON, 3)
    truth[columns.players[(1, cell)]] = 1.0
    truth[columns.players[(6, cell)]] = -1.0
    y = np.array([sum(sign * truth[col] for col, sign in row) for row in rows], dtype=float)
    gram_matrix, _rows = statespace.gram(games, columns, SEASONS)
    fit = statespace.solve(
        gram_matrix,
        statespace.rhs(rows, y, columns.size),
        statespace.walk_penalty(columns, SEASONS),
        y,
        len(games),
        0.01,
        0.01,
        columns,
    )
    assert coef(fit, 1, cell) > coef(fit, 11, cell) > coef(fit, 6, cell)


def test_the_penalty_share_is_a_share() -> None:
    games = league(90, season=3)
    response = statespace.responses(games)[statespace.MARGIN]
    fit = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response, (1.0, 1.0))
    assert np.all(fit.penalty_share >= 0.0) and np.all(fit.penalty_share <= 1.0)


def test_a_heavier_ridge_shrinks_the_coefficients_and_spends_less_of_the_design() -> None:
    games = league(90, season=3)
    response = statespace.responses(games)[statespace.MARGIN]
    light = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response, (0.1, 0.1))
    heavy = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response, (100.0, 0.1))
    assert float(np.linalg.norm(heavy.beta)) < float(np.linalg.norm(light.beta))
    assert heavy.effective_df < light.effective_df
    assert float(np.median(heavy.penalty_share)) > float(np.median(light.penalty_share))


def test_a_heavy_walk_penalty_pulls_a_player_toward_one_number_across_seasons() -> None:
    games = league(60, season=3) + league(60, season=4, first_game=200)
    response = statespace.responses(games)[statespace.MARGIN]
    loose = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response, (1.0, 0.001))
    tight = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response, (1.0, 1000.0))

    def spread(fit: statespace.Fit) -> float:
        gaps = [
            abs(coef(fit, pid, (statespace.SEASON, 3)) - coef(fit, pid, (statespace.SEASON, 4)))
            for pid in range(1, 21)
            if fit.player(pid, (statespace.SEASON, 3)) is not None
            and fit.player(pid, (statespace.SEASON, 4)) is not None
        ]
        return float(np.mean(gaps))

    assert spread(tight) < spread(loose)


def test_tuning_picks_the_penalties_inside_the_declared_box() -> None:
    games = league(90, season=3)
    response = statespace.responses(games)[statespace.MARGIN]
    columns = statespace.build_columns(games, SEASONS, BY_SEASON)
    mask = response.usable.astype(float)
    gram_matrix, rows = statespace.gram(games, columns, SEASONS, mask)
    xty = statespace.rhs(rows, response.y, columns.size, mask)
    lambda0, lambda_walk, _ok = statespace.tune(
        gram_matrix,
        xty,
        statespace.walk_penalty(columns, SEASONS),
        response.y[response.usable],
        int(response.usable.sum()),
        columns,
    )
    lo, hi = statespace.LAMBDA_BOUNDS[0]
    assert 10.0**lo <= lambda0 <= 10.0**hi
    assert 10.0**lo <= lambda_walk <= 10.0**hi


def test_the_same_maps_fit_the_same_numbers_twice() -> None:
    games = league(90, season=3)
    response = statespace.responses(games)[statespace.MARGIN]
    first = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response)
    second = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response)
    assert np.array_equal(first.beta, second.beta)
    assert (first.lambda0, first.lambda_walk) == (second.lambda0, second.lambda_walk)


# ---------------------------------------------------------- the filtered family


def test_a_filtered_coefficient_cannot_have_seen_the_season_after_it() -> None:
    """The property the whole scope split exists for, asserted directly.

    The 2020 coefficient is fitted twice: once on the record as it stands, once
    with 2021 replaced by maps that plant a large opposite signal. A filtered
    number that moves has read the future.
    """
    first = league(60, season=3)
    second = league(60, season=4, first_game=200)
    louder = [
        make_map(
            400 + i,
            (1, 2, 3, 4),
            (6, 7, 8, 9),
            season=4,
            home_won=True,
            series_id=400 + i // 3,
            home_margin=50.0,
        )
        for i in range(60)
    ]
    lambdas = (1.0, 1.0)
    cell = (statespace.SEASON, 3)
    base = statespace.fit_filtered(
        first + second,
        SEASONS,
        BY_SEASON,
        statespace.responses(first + second)[statespace.MARGIN],
        lambdas,
    )
    moved = statespace.fit_filtered(
        first + louder,
        SEASONS,
        BY_SEASON,
        statespace.responses(first + louder)[statespace.MARGIN],
        lambdas,
    )
    shared = sorted(
        {p for p, c in base.players if c == cell} & {p for p, c in moved.players if c == cell}
    )
    assert shared
    for pid in shared:
        assert base.players[(pid, cell)] == pytest.approx(moved.players[(pid, cell)], abs=1e-9)


def test_the_smoothed_family_does_move_when_the_next_season_changes() -> None:
    """The contrast that makes the test above mean something."""
    first = league(60, season=3)
    second = league(60, season=4, first_game=200)
    louder = [
        make_map(
            400 + i,
            (1, 2, 3, 4),
            (6, 7, 8, 9),
            season=4,
            home_won=True,
            series_id=400 + i // 3,
            home_margin=50.0,
        )
        for i in range(60)
    ]
    cell = (statespace.SEASON, 3)
    base = statespace.fit_smoothed(
        first + second,
        SEASONS,
        BY_SEASON,
        statespace.responses(first + second)[statespace.MARGIN],
        (1.0, 10.0),
    )
    moved = statespace.fit_smoothed(
        first + louder,
        SEASONS,
        BY_SEASON,
        statespace.responses(first + louder)[statespace.MARGIN],
        (1.0, 10.0),
    )
    assert base.player(1, cell) != pytest.approx(moved.player(1, cell), abs=1e-6)


# ------------------------------------------------------------- what is stored


def test_an_era_coefficient_is_stored_against_every_season_it_covers_and_says_so() -> None:
    games = league(60, season=1) + league(60, season=2, first_game=200)
    response = statespace.responses(games)[statespace.MARGIN]
    fit = statespace.fit_smoothed(games, SEASONS, POOLED_CWL, response, (1.0, 1.0))
    stored = statespace.coefficients(games, SEASONS, POOLED_CWL, fit, {}, min_maps=1)
    era_rows = [c for c in stored if c.player_id == 1 and c.scope == statespace.SMOOTHED]
    assert {c.season_id for c in era_rows} == {1, 2}
    assert {c.resolution for c in era_rows} == {statespace.ERA}
    # One estimate wearing two season labels, not two estimates.
    assert len({c.coef for c in era_rows}) == 1


def test_publication_is_a_higher_floor_than_fit_inclusion() -> None:
    games = league(60, season=3)
    thin = [
        make_map(900 + i, (1, 2, 3, 90), (6, 7, 8, 9), season=3, series_id=900 + i)
        for i in range(statespace.ADMISSION_MAPS + 1)
    ]
    games += thin
    response = statespace.responses(games)[statespace.MARGIN]
    fit = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response, (1.0, 1.0))
    cell = (statespace.SEASON, 3)
    # Admitted — it cleared the column floor — but not published.
    assert (90, cell) in fit.columns.players
    stored = statespace.coefficients(games, SEASONS, BY_SEASON, fit, {})
    assert 90 not in {c.player_id for c in stored}


def test_both_families_are_stored_under_their_own_scope() -> None:
    games = league(90, season=3)
    response = statespace.responses(games)[statespace.MARGIN]
    fit = statespace.fit_smoothed(games, SEASONS, BY_SEASON, response, (1.0, 1.0))
    filtered = statespace.fit_filtered(games, SEASONS, BY_SEASON, response, (1.0, 1.0))
    stored = statespace.coefficients(games, SEASONS, BY_SEASON, fit, filtered.players, min_maps=1)
    scopes = {c.scope for c in stored}
    assert scopes == {statespace.SMOOTHED, statespace.FILTERED}
    # And they are keyed apart, which is what migration 0017's index requires.
    keys = [(c.scope, c.player_id, c.season_id) for c in stored]
    assert len(keys) == len(set(keys))


# -------------------------------------------------------------- the diagnostics


def test_the_reliability_split_keeps_a_series_whole() -> None:
    games = league(60, season=3)
    odd, even = statespace.series_halves(games)
    assert len(odd) + len(even) == len(games)
    left = {g.series_id for g in odd}
    right = {g.series_id for g in even}
    assert left & right == set()


def test_the_split_is_the_same_whatever_order_the_maps_arrive_in() -> None:
    games = league(60, season=3)
    forward = statespace.series_halves(games)
    backward = statespace.series_halves(list(reversed(games)))
    assert {g.game_id for g in forward[0]} == {g.game_id for g in backward[0]}


def test_a_cell_names_itself_by_its_season_label_never_by_a_surrogate() -> None:
    assert statespace.cell_label((statespace.SEASON, 3), SEASONS) == "2020 CDL"
    assert statespace.cell_label((statespace.ERA, "CWL"), SEASONS) == "CWL era"
    assert statespace.cell_label((statespace.WHOLE, statespace.WHOLE), SEASONS) == "career"


def test_the_design_hash_moves_when_the_design_does_and_not_otherwise() -> None:
    games = league(60, season=3)
    columns = statespace.build_columns(games, SEASONS, BY_SEASON, admission=1)
    first = statespace.design_hash(games, columns, SEASONS)
    assert first == statespace.design_hash(games, columns, SEASONS)
    swapped = [*games[:-1], make_map(999, (1, 2, 3, 5), (16, 17, 18, 19), season=3)]
    assert statespace.design_hash(swapped, columns, SEASONS) != first


def test_the_artifact_declines_rather_than_fitting_a_handful_of_maps() -> None:
    payload, stored, _teams = statespace.artifact(league(10, season=3), SEASONS, BY_SEASON)
    assert payload["available"] is False
    assert stored == []


def test_the_artifact_carries_the_gate_and_never_a_bare_ranking() -> None:
    games = league(90, season=3) + league(90, season=4, first_game=300)
    payload, stored, _teams = statespace.artifact(games, SEASONS, BY_SEASON)
    assert payload["available"] is True
    for key in (
        "resolution_by_league",
        "design_hash",
        "penalties",
        "columns",
        "admission",
        "publication",
        "scopes",
        "penalty_share",
        "teammate_concentration",
        "by_cell",
        "graphs",
        "sensitivity",
        "reliability",
        "against_published",
        "how_to_read",
    ):
        assert key in payload
    # The penalty share is read against k/(k+1), not against a flat threshold.
    assert payload["penalty_share"]["reference"] == pytest.approx(0.8)
    assert "0.95 of k/(k+1)" in payload["penalty_share"]["dominated_at"]
    assert "never" in payload["how_to_read"]
    assert stored and {c.scope for c in stored} <= {statespace.SMOOTHED, statespace.FILTERED}


def test_the_sensitivity_grid_reports_the_no_time_borrowing_fit() -> None:
    games = league(90, season=3) + league(90, season=4, first_game=300)
    payload, _stored, _teams = statespace.artifact(games, SEASONS, BY_SEASON)
    assert any(row["lambda_walk"] == 0.0 for row in payload["sensitivity"])
    chosen = [
        row for row in payload["sensitivity"] if row["rank_corr_with_chosen"] == pytest.approx(1.0)
    ]
    assert chosen, "the chosen penalty has to appear on its own grid"


def test_the_comparison_declines_when_there_is_no_published_fit_to_compare_against() -> None:
    games = league(90, season=3)
    out = statespace.against_published(games, SEASONS, [], 1.0)
    assert out["available"] is False
