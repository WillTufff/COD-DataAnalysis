import pytest
from pydantic import ValidationError

from cdlhub_pipeline.models import GamePlayerStatsRow, SeriesRow


def test_series_scores_must_fit_best_of() -> None:
    with pytest.raises(ValidationError):
        SeriesRow(
            liquipedia_match_id="x",
            event_liquipedia_page="e",
            team1_score=4,
            team2_score=2,
            best_of=5,
        )


def test_series_allows_unknown_scores() -> None:
    row = SeriesRow(liquipedia_match_id="x", event_liquipedia_page="e")
    assert row.team1_score is None


def test_stats_reject_negative_kills() -> None:
    with pytest.raises(ValidationError):
        GamePlayerStatsRow(player_handle="p", team="t", kills=-1)


def test_stats_allow_missing_map_level_data() -> None:
    row = GamePlayerStatsRow(player_handle="p", team="t")
    assert row.kills is None  # pre-2018 reality: series known, map stats unknown
