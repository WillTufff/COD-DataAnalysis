"""The player row's career-level display figures, built without a database."""

from __future__ import annotations

from typing import Any

import pytest

from cdlhub_analytics.career_rank import blend, engine
from cdlhub_analytics.ratings.preflight import Season

SEASONS = {19: Season(19, 2020, "CDL"), 20: Season(20, 2021, "CDL"), 21: Season(21, 2022, "CDL")}


def _career(player_id: int) -> blend.CareerRank:
    scores = [
        blend.SeasonScore(player_id, sid, {blend.PERFORMANCE: 50.0}, 2.0) for sid in (19, 20, 21)
    ]
    return next(r for r in blend.build(scores, SEASONS) if r.player_id == player_id)


def _row(player_id: int, net_of_teammates: dict[int, float], **over: Any) -> engine.PlayerRow:
    defaults: dict[str, Any] = dict(
        player_id=player_id,
        career=_career(player_id),
        seasons={},
        season_sd={},
        season_breadth={},
        season_value={},
        season_families={},
        net_of_teammates=net_of_teammates,
        opponent_strength={},
        resume={},
        resume_credit={},
        accolade={},
        accolade_credit={},
        season_awards={},
        components={},
        chips=0,
        rings=0,
    )
    defaults.update(over)
    return engine.PlayerRow(**defaults)


def _published(*season_ids: int) -> dict[int, tuple[str, ...]]:
    return {sid: (blend.PERFORMANCE,) for sid in season_ids}


def test_net_of_teammates_mean_is_the_plain_mean_over_seasons() -> None:
    row = _row(1, {19: 1.0, 20: -0.5, 21: 0.5}, components=_published(19, 20, 21))
    assert row.net_of_teammates_mean == pytest.approx(1.0 / 3.0)


def test_net_of_teammates_mean_is_none_when_the_career_carries_no_season() -> None:
    row = _row(1, {}, components=_published(19, 20, 21))
    assert row.net_of_teammates_mean is None


def test_net_of_teammates_mean_is_not_a_map_weighted_or_peak_figure() -> None:
    """The plain mean, not best-3 or a weighted one: two seasons at
    opposite signs cancel exactly."""
    row = _row(1, {19: 2.0, 20: -2.0}, components=_published(19, 20))
    assert row.net_of_teammates_mean == pytest.approx(0.0)


def test_net_of_teammates_mean_skips_seasons_the_run_does_not_publish() -> None:
    """Roster strength reaches seasons `player_season_rank` never receives.
    Averaging those in puts a figure on the page that the published season
    rows do not add up to."""
    row = _row(1, {19: 1.0, 20: -0.5, 21: 9.0}, components=_published(19, 20))
    assert row.net_of_teammates_mean == pytest.approx(0.25)


def test_net_of_teammates_mean_is_none_when_no_published_season_carries_one() -> None:
    row = _row(1, {21: 9.0}, components=_published(19, 20))
    assert row.net_of_teammates_mean is None
