"""Net-of-teammates and opponent-strength, over a modal-team roster."""

from __future__ import annotations

import pytest

from cdlhub_analytics.career_rank import roster_strength as roster_strength

from .conftest import FakeConn, as_conn


def _patch_modal_teams(monkeypatch: pytest.MonkeyPatch, teams: dict[tuple[int, int], int]) -> None:
    monkeypatch.setattr(roster_strength, "modal_teams", lambda conn: teams)


# ------------------------------------------------------------ net_of_teammates


def test_net_is_own_value_minus_teammate_mean(monkeypatch: pytest.MonkeyPatch) -> None:
    teams = {(1, 19): 100, (2, 19): 100, (3, 19): 100}
    _patch_modal_teams(monkeypatch, teams)
    season_value = {(1, 19): 10.0, (2, 19): 4.0, (3, 19): 2.0}
    out = {
        r.player_id: r for r in roster_strength.net_of_teammates(as_conn(FakeConn()), season_value)
    }
    # Player 1's teammates are 2 and 3, mean 3.0.
    assert out[1].net == pytest.approx(7.0)
    assert out[1].n_teammates == 2


def test_a_player_with_no_value_row_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    teams = {(1, 19): 100, (2, 19): 100}
    _patch_modal_teams(monkeypatch, teams)
    season_value = {(2, 19): 4.0}  # player 1 has no VALUE
    out = roster_strength.net_of_teammates(as_conn(FakeConn()), season_value)
    assert {r.player_id for r in out} == set()


def test_an_all_rookie_roster_with_no_valued_teammates_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teams = {(1, 19): 100, (2, 19): 100}
    _patch_modal_teams(monkeypatch, teams)
    season_value = {(1, 19): 5.0}  # teammate 2 has no VALUE row
    out = roster_strength.net_of_teammates(as_conn(FakeConn()), season_value)
    assert out == []


# -------------------------------------------------------- team-value proxy


def test_team_season_value_is_the_mean_of_its_modal_players(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    teams = {(1, 19): 100, (2, 19): 100, (3, 19): 200}
    _patch_modal_teams(monkeypatch, teams)
    season_value = {(1, 19): 10.0, (2, 19): 6.0, (3, 19): 1.0}
    out = roster_strength.build_team_season_value(as_conn(FakeConn()), season_value)
    assert out[(100, 19)] == pytest.approx(8.0)
    assert out[(200, 19)] == pytest.approx(1.0)


# ------------------------------------------------------------- opponent strength


def test_opponent_strength_is_weighted_by_maps_faced() -> None:
    team_season_value = {(200, 19): 10.0, (300, 19): 0.0}
    # Player 1 faced team 200 on 3 maps and team 300 on 1 map.
    rows = [(1, 19, 200, 3), (1, 19, 300, 1)]
    result = roster_strength.opponent_strength(as_conn(FakeConn(rows)), team_season_value)
    out = {r.player_id: r for r in result}
    assert out[1].mean_opponent_value == pytest.approx(7.5)
    assert out[1].n_opponent_maps == 4


def test_opponent_strength_skips_an_opponent_with_no_team_value() -> None:
    team_season_value = {(200, 19): 10.0}  # team 300 has no proxy
    rows = [(1, 19, 200, 2), (1, 19, 300, 5)]
    result = roster_strength.opponent_strength(as_conn(FakeConn(rows)), team_season_value)
    out = {r.player_id: r for r in result}
    assert out[1].mean_opponent_value == pytest.approx(10.0)
    assert out[1].n_opponent_maps == 2
