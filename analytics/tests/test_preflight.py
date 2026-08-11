"""Fixture tests for the identification pre-flight. No database required.

The statements this module makes about the record are structural, so they can be
checked against schedules small enough to reason about by hand: a league where
two teams never change their four players identifies one direction and no more,
and a league where they swap identifies exactly as many as there are lineups.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from cdlhub_analytics.ratings import graphs, preflight
from cdlhub_analytics.ratings.rapm import AdmittedMap

DAY0 = date(2020, 1, 24)


def make_map(
    game_id: int,
    home: tuple[int, ...],
    away: tuple[int, ...],
    *,
    season: int = 1,
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


def fixed_lineups(maps: int = 60) -> list[AdmittedMap]:
    """Two teams, four players each, never changed."""
    return [make_map(i, (1, 2, 3, 4), (5, 6, 7, 8), home_won=i % 2 == 0) for i in range(maps)]


def swapped_lineups(maps: int = 60) -> list[AdmittedMap]:
    """The same two teams, each rotating a fifth player in half the time.

    The two rotations vary independently, so all four pairings occur. That
    matters: if each home lineup only ever met one away lineup the lineup graph
    would fall into two pieces, and two disconnected pools identify one fewer
    direction than one connected pool of the same size.
    """
    out: list[AdmittedMap] = []
    for i in range(maps):
        home = (1, 2, 3, 4) if i % 2 else (1, 2, 3, 9)
        away = (5, 6, 7, 8) if (i // 2) % 2 else (5, 6, 7, 10)
        out.append(make_map(i, home, away, home_won=i % 3 == 0))
    return out


def test_perplexity_counts_effective_parts_not_distinct_ones() -> None:
    assert preflight._perplexity([1, 1, 1, 1]) == 4.0
    assert preflight._perplexity([100, 0, 0]) == 1.0
    # Ninety-nine maps on one lineup and one on another is not two lineups.
    assert preflight._perplexity([99, 1]) < 1.1


def test_lineup_supply_reports_one_lineup_per_team_when_nothing_changes() -> None:
    supply = preflight.lineup_supply(fixed_lineups())
    assert len(supply) == 2
    assert all(ts.distinct_lineups == 1 for ts in supply)
    assert all(ts.effective_lineups == 1.0 for ts in supply)
    assert all(ts.modal_lineup_share == 1.0 for ts in supply)


def test_a_never_changing_lineup_identifies_exactly_one_direction() -> None:
    """Eight players, one column of information: who is on which side.

    The four home columns, the four away columns and both team columns move
    together on every row, so the design has rank 1 however many maps are
    played. This is the case the whole phase is about.
    """
    spectrum = preflight.season_spectrum(fixed_lineups())
    assert spectrum.columns == 10
    assert spectrum.rank == 1


def test_rank_is_capped_by_the_lineup_graph_not_by_the_player_count() -> None:
    games = swapped_lineups()
    spectrum = preflight.season_spectrum(games)
    lineups = preflight.lineup_graph(games)
    assert spectrum.rank <= lineups.nodes - lineups.components
    # Four lineups in one pool: three directions, against ten player columns.
    assert lineups.nodes == 4
    assert lineups.components == 1
    assert spectrum.rank == 3
    assert spectrum.player_columns == 10


def test_the_penalty_share_of_a_frozen_lineup_passes_its_reference() -> None:
    """And passes it, rather than stopping there — which is the honest reading.

    k/(k+1) is what a frozen lineup carries when it meets a varied field. A
    two-team fixture is not a varied field: both teams' columns move together
    too, so ten columns share one direction and the share goes to 0.9.
    """
    spectrum = preflight.season_spectrum(fixed_lineups(maps=200))
    reference = preflight.penalty_reference(4)
    assert reference == 0.8
    assert spectrum.penalty_share_median >= reference
    assert spectrum.penalty_dominated_columns == spectrum.player_columns


def test_churn_pulls_the_penalty_share_off_the_ceiling() -> None:
    frozen = preflight.season_spectrum(fixed_lineups(maps=200))
    rotated = preflight.season_spectrum(swapped_lineups(maps=200))
    assert rotated.penalty_share_median < frozen.penalty_share_median


def test_the_design_fingerprint_moves_with_the_maps_and_not_with_the_order() -> None:
    games = swapped_lineups()
    assert preflight.design_fingerprint(games) == preflight.design_fingerprint(list(games))
    changed = [*games[:-1], make_map(999, (1, 2, 3, 4), (5, 6, 7, 11))]
    assert preflight.design_fingerprint(changed) != preflight.design_fingerprint(games)


def test_the_teammate_graph_names_its_pieces() -> None:
    stats = graphs.graph_stats([(1, 2, 5), (3, 4, 2)], [1, 2, 3, 4, 5])
    assert stats.components == 3
    assert stats.isolated_nodes == 1
    assert stats.bridges == 2
    assert stats.bridge_median_maps == 3.5


def _measured(games: list[AdmittedMap], league: str) -> preflight.Preflight:
    seasons = {1: preflight.Season(1, 2021, league)}
    return preflight.measure_admitted(games, seasons)


def _curve(recovery: float) -> list[dict[str, object]]:
    return [
        {"effective_lineups": 1.0, "corr_within_team": recovery},
        {"effective_lineups": 4.0, "corr_within_team": recovery},
    ]


def test_the_stop_rule_stops_when_all_three_clauses_trip() -> None:
    fork = preflight.verdict(_measured(fixed_lineups(), "CWL"), _curve(0.05))
    assert fork["stops_p1_as_specified"] is True
    assert fork["branch"] == preflight.CAREER_ONLY


def test_a_rank_deficient_design_that_still_recovers_does_not_stop() -> None:
    """Two of three clauses is not a stop, and the reason is the penalty.

    A design can be short of full rank and still return the trajectories that
    were put into it, which is what a penalty is for. The rule is a conjunction
    precisely so that case ships.
    """
    fork = preflight.verdict(_measured(fixed_lineups(), "CWL"), _curve(0.8))
    assert fork["stops_p1_as_specified"] is False
    assert fork["branch"] == preflight.TEAM_ANCHORED


def test_weak_but_present_recovery_pools_the_time_axis() -> None:
    fork = preflight.verdict(_measured(fixed_lineups(), "CWL"), _curve(0.4))
    assert fork["branch"] == preflight.COARSER_TIME


def test_the_verdict_answers_per_era_rather_than_once() -> None:
    games = [
        *fixed_lineups(),
        *[
            make_map(1000 + i, h, a, season=2, home_team=300, away_team=400)
            for i, (h, a) in enumerate(
                [
                    ((11, 12, 13, 14), (15, 16, 17, 18)),
                    ((11, 12, 13, 19), (15, 16, 17, 18)),
                    ((11, 12, 13, 14), (15, 16, 17, 20)),
                    ((11, 12, 13, 19), (15, 16, 17, 20)),
                ]
                * 15
            )
        ],
    ]
    seasons = {
        1: preflight.Season(1, 2018, "CWL"),
        2: preflight.Season(2, 2021, "CDL"),
    }
    measured = preflight.measure_admitted(games, seasons)
    fork = preflight.verdict(measured, _curve(0.05))
    leagues = {era["league"]: era for era in fork["by_era"]}
    assert leagues["CWL"]["median_effective_lineups"] == 1.0
    assert leagues["CDL"]["median_effective_lineups"] > 1.5
    # The frozen era identifies one direction over its eight columns; the era
    # that rotates identifies more, and neither number is the other's.
    assert np.isclose(float(leagues["CWL"]["rank_share"]), 1 / 8)
    assert float(leagues["CDL"]["rank_share"]) > float(leagues["CWL"]["rank_share"])
