"""Series scores come from the wiki schedule; map winners that contradict it are nulled."""

from datetime import date
from typing import Any

from cdlhub_pipeline.codwiki.transform import (
    Game,
    Series,
    _contradicts,
    _same_org,
    _Schedule,
)

DAY = date(2014, 3, 30)


def _row(sid: str, t1: str, t2: str, s1: int, s2: int, page: str = "P") -> dict[str, Any]:
    return {
        "SeriesId": sid,
        "OverviewPage": page,
        "Team1": t1,
        "Team2": t2,
        "Team1Score": str(s1),
        "Team2Score": str(s2),
        "Winner": "1" if s1 > s2 else "2",
        "DateTime UTC": f"{DAY} 18:00:00",
    }


def _schedule(*rows: dict[str, Any]) -> _Schedule:
    return _Schedule(list(rows), str.lower)


def test_the_score_is_oriented_to_the_box_score_team_order() -> None:
    found = _schedule(_row("1", "Envy", "Fariko Impact", 2, 4)).match(
        "1", "P", "Fariko Impact", "Envy", DAY
    )
    assert found is not None and found[1] == (4, 2)


def test_swapped_series_ids_are_matched_by_page_and_pair() -> None:
    schedule = _schedule(
        _row("1", "FaZe Clan", "Luminosity", 3, 1),
        _row("2", "Dream Team", "Team Kaliber", 0, 3),
    )
    found = schedule.match("1", "P", "Dream Team", "Team Kaliber", DAY)
    assert found is not None and found[1] == (0, 3) and found[2] == "page and pair"


def test_one_org_spelled_two_ways_still_matches_on_series_id() -> None:
    found = _schedule(_row("1", "OpTic Gaming", "Curse NA", 3, 2)).match(
        "1", "P", "OpTic Gaming", "Team Curse", DAY
    )
    assert found is not None and found[1] == (3, 2)


def test_a_different_opponent_under_the_same_series_id_does_not_match() -> None:
    found = _schedule(_row("1", "Rise Nation", "OpTic Gaming", 3, 0)).match(
        "1", "P", "Fnatic", "OpTic Gaming", DAY
    )
    assert found is None


def test_a_row_is_used_once() -> None:
    schedule = _schedule(_row("1", "A", "B", 3, 0))
    assert schedule.match("1", "P", "A", "B", DAY) is not None
    assert schedule.match("1", "P", "A", "B", DAY) is None


def test_org_spellings() -> None:
    assert _same_org("curse na", "team curse")
    assert _same_org("devious 13", "devious (2013 team)")
    assert not _same_org("twisted method", "tainted minds")
    assert not _same_org("team esport", "stdx esport")


def _series(score: tuple[int, int], winners: list[str | None]) -> Series:
    games = [
        Game(ordinal=i, map_name="", mode_slug="hardpoint", winner_team=w)
        for i, w in enumerate(winners, 1)
    ]
    return Series("1", "P", 2014, "Ghosts", DAY, "A", "B", games=games, score=score)


def test_missing_maps_do_not_contradict_the_score() -> None:
    assert not _contradicts(_series((3, 1), ["A", "B", "A"]))


def test_a_map_count_past_the_score_contradicts_it() -> None:
    assert _contradicts(_series((3, 1), ["A", "B", "A", "A", "A"]))


def test_map_wins_for_the_other_side_contradict_it() -> None:
    assert _contradicts(_series((3, 1), ["B", "B", "A"]))


def test_a_tied_or_self_contradicting_schedule_score_is_not_used(
    tmp_path: Any, monkeypatch: Any
) -> None:
    import json

    from cdlhub_pipeline.codwiki import transform

    rows = [
        _row("1", "Envy", "Fariko Impact", 3, 3),
        {**_row("2", "A", "B", 3, 1), "Winner": "2"},
        _row("3", "A", "B", 3, 1),
    ]
    rows[0]["Winner"] = "2"
    (tmp_path / "matchschedule.json").write_text(json.dumps(rows))
    monkeypatch.setattr(transform, "SNAPSHOT_ROOT", tmp_path)
    assert [r["SeriesId"] for r in transform.load_schedule()] == ["3"]
