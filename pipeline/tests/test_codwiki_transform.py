"""A game's box score covers both teams or it is not loaded."""

from cdlhub_pipeline.codwiki.transform import Game, StatLine, _strip_one_sided


def _line(team: str, player_id: int) -> StatLine:
    return StatLine(
        player_link=str(player_id),
        player_id=player_id,
        team_name=team,
        kills=10,
        deaths=10,
        hill_time=None,
        plants=None,
        defuses=None,
        first_bloods=None,
        first_deaths=None,
        captures=None,
        extras={},
    )


def _game(*teams: str) -> Game:
    game = Game(ordinal=1, map_name="Hijacked", mode_slug="hardpoint", winner_team=teams[0])
    game.lines = [_line(team, i) for i, team in enumerate(teams)]
    return game


def test_a_game_with_one_team_loses_its_lines_and_keeps_its_result() -> None:
    game = _game("OpTic Gaming", "OpTic Gaming", "OpTic Gaming", "OpTic Gaming")
    assert _strip_one_sided([game]) == 4
    assert game.lines == []
    assert game.winner_team == "OpTic Gaming"


def test_a_game_with_both_teams_is_untouched() -> None:
    game = _game("Complexity", "Complexity", "SoaR Gaming", "SoaR Gaming")
    assert _strip_one_sided([game]) == 0
    assert len(game.lines) == 4


def test_a_game_with_no_lines_is_not_counted() -> None:
    assert _strip_one_sided([Game(ordinal=2, map_name="", mode_slug="ctf", winner_team=None)]) == 0
