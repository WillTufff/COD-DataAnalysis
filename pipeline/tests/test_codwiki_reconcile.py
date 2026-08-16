from datetime import date

from cdlhub_pipeline.codwiki.reconcile import (
    Line,
    Map,
    _line_pairs,
    _map_agreement,
    _map_key,
    _pair_maps,
    _team_key,
)


def _map(
    day: str, lobby: dict[int, tuple[int, int]], name: str = "Raid", mode: str = "hardpoint"
) -> Map:
    entry = Map(day=date.fromisoformat(day), map_name=_map_key(name), mode=mode, ref={"event": "e"})
    for pid, (kills, deaths) in lobby.items():
        entry.lines[pid] = Line(player_id=pid, kills=kills, deaths=deaths, won=kills > deaths)
    return entry


LOBBY = {1: (20, 15), 2: (18, 17), 3: (22, 14), 4: (16, 19)}


def test_pairs_the_same_lobby_across_a_one_day_gap() -> None:
    wiki = [_map("2020-02-08", LOBBY)]
    ours = [_map("2020-02-09", LOBBY)]
    pairs, wiki_only, ours_only = _pair_maps(wiki, ours)
    assert len(pairs) == 1
    assert not pairs[0].ambiguous
    assert not wiki_only and not ours_only


def test_will_not_pair_across_a_two_day_gap() -> None:
    pairs, wiki_only, ours_only = _pair_maps(
        [_map("2020-02-08", LOBBY)], [_map("2020-02-10", LOBBY)]
    )
    assert not pairs
    assert len(wiki_only) == 1 and len(ours_only) == 1


def test_will_not_pair_a_lobby_that_shares_too_few_players() -> None:
    ours = [_map("2020-02-08", {1: (20, 15), 2: (18, 17), 9: (1, 1), 8: (2, 2)})]
    pairs, _wiki_only, _ours_only = _pair_maps([_map("2020-02-08", LOBBY)], ours)
    assert not pairs


def test_two_games_with_one_lobby_on_one_day_are_ambiguous() -> None:
    """The case that would otherwise report two real games as a transcription error."""
    wiki = [_map("2020-02-08", LOBBY)]
    ours = [
        _map("2020-02-08", LOBBY),
        _map("2020-02-08", {k: (v[0] + 5, v[1]) for k, v in LOBBY.items()}),
    ]
    pairs, _wiki_only, ours_only = _pair_maps(wiki, ours)
    assert len(pairs) == 1
    assert pairs[0].ambiguous
    assert len(ours_only) == 1
    assert not _line_pairs(pairs)  # an ambiguous pair contributes no line to the rate


def test_a_better_lobby_overlap_wins_and_stays_confident() -> None:
    wiki = [_map("2020-02-08", LOBBY)]
    weaker = _map("2020-02-08", {1: (5, 5), 2: (5, 5), 3: (5, 5), 7: (5, 5)})
    ours = [weaker, _map("2020-02-08", LOBBY)]
    pairs, _wiki_only, _ours_only = _pair_maps(wiki, ours)
    assert len(pairs) == 1
    assert not pairs[0].ambiguous
    assert len(_line_pairs(pairs)) == 4


def test_map_agreement_separates_one_bad_line_from_a_whole_lobby() -> None:
    one_line = {**LOBBY, 1: (21, 15)}
    every_line = {k: (v[0] + 7, v[1] + 3) for k, v in LOBBY.items()}
    pairs, _w, _o = _pair_maps(
        [_map("2020-02-08", LOBBY), _map("2020-03-08", LOBBY, name="Hijacked")],
        [_map("2020-02-08", one_line), _map("2020-03-08", every_line, name="Hijacked")],
    )
    result = _map_agreement(pairs)
    assert result["one_or_more_lines"] == 1
    assert result["whole_lobby"] == 1
    assert result["whole_lobby_lines"] == 4


def test_team_key_folds_the_words_two_sources_spell_differently() -> None:
    assert _team_key("FaZe Clan") == _team_key("Faze  Clan Esports")
    assert _team_key("Team Envy") == _team_key("EnVy")
