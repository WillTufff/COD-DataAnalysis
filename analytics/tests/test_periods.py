"""Rating periods: grouping, and the idle inflation that motivated them."""

from datetime import datetime, timedelta

from cdlhub_analytics.backtest import evaluate
from cdlhub_analytics.ratings.fit import (
    PERIODS,
    SeriesRow,
    glicko2_walk_forward,
    group_periods,
)
from cdlhub_analytics.ratings.sweep import sweep

T0 = datetime(2018, 1, 1)


def s(sid: int, t1: int, t2: int, won: bool, day: int, event: int) -> SeriesRow:
    return SeriesRow(
        id=sid,
        team1=t1,
        team2=t2,
        team1_won=won,
        played_at=T0 + timedelta(days=day),
        event_id=event,
    )


def test_group_periods_cuts_on_the_key() -> None:
    rows = [s(1, 1, 2, True, 0, 10), s(2, 3, 4, True, 1, 10), s(3, 1, 3, True, 90, 11)]
    assert [len(b) for b in group_periods(rows, "event")] == [2, 1]
    assert [len(b) for b in group_periods(rows, "series")] == [1, 1, 1]
    assert [len(b) for b in group_periods(rows, "month")] == [2, 1]


def test_group_periods_keeps_periods_contiguous_in_play_order() -> None:
    """A period is a run of play, never a re-sort of the record."""
    rows = [s(1, 1, 2, True, 0, 10), s(2, 3, 4, True, 1, 11), s(3, 1, 3, True, 2, 10)]
    blocks = group_periods(rows, "event")
    assert [[r.id for r in b] for b in blocks] == [[1], [2], [3]]


def test_group_periods_covers_every_series_exactly_once() -> None:
    rows = [s(i, 1, 2, True, i, i // 3) for i in range(20)]
    for period in PERIODS:
        blocks = group_periods(rows, period)
        assert sorted(r.id for b in blocks for r in b) == list(range(20))


def test_idle_team_deviation_grows_across_events() -> None:
    """The regression this whole change exists for: a team that plays an event,
    sits out two, and comes back must be less certainly rated on its return.

    Before rating periods were real, only the two teams in a series were ever
    advanced, so a team's deviation tracked games played and never time elapsed
    — a roster back from a layoff was treated as exactly as well known as when
    it left, which is the failure the volatility term exists to prevent.
    """
    played = [s(1, 1, 2, True, 0, 10), s(2, 1, 2, True, 1, 10)]
    sat_out = [s(3, 3, 4, True, 60, 11), s(4, 3, 4, True, 120, 12)]
    returns = s(5, 1, 2, True, 180, 13)

    def return_rd(rows: list[SeriesRow]) -> float:
        _p, written = glicko2_walk_forward(rows, tau=0.5, period="event")
        return [r[4] for r in written if r[0] == 1 and r[1] == 5][0]

    with_layoff = return_rd([*played, *sat_out, returns])
    straight_back = return_rd([*played, returns])
    assert with_layoff > straight_back


def test_shorter_periods_inflate_an_idle_team_more() -> None:
    """Period length is the knob. A team sitting out one event sits out one
    period; the same absence cut per-series is ten periods of inflation, which
    is why the period is a hyperparameter and not an implementation detail."""
    rows = [
        s(0, 1, 2, True, 0, 10),
        # Ten series among other teams, all inside one event.
        *[s(i, 3, 4, i % 2 == 0, 10 + i, 11) for i in range(1, 11)],
        s(11, 1, 2, True, 100, 12),
    ]

    def on_return(rows_out: list[tuple[int, int, float, float, float]]) -> tuple[float, float]:
        """(pre-period rating, post-period deviation) for team 1's return."""
        row = [r for r in rows_out if r[0] == 1 and r[1] == 11][0]
        return row[2], row[4]

    pre_series, rd_series = on_return(glicko2_walk_forward(rows, 0.5, None, "series")[1])
    pre_event, rd_event = on_return(glicko2_walk_forward(rows, 0.5, None, "event")[1])
    # The rating itself is untouched by absence under either setting.
    assert pre_series == pre_event
    # But ten periods of inflation leave the team less certainly rated than one.
    assert rd_series > rd_event


def test_every_period_predicts_the_same_series() -> None:
    """Otherwise the sweep would compare settings on different samples."""
    rows = [s(i, 1 + i % 4, 1 + (i + 1) % 4, i % 2 == 0, i, i // 4) for i in range(40)]
    counts = {p: evaluate(glicko2_walk_forward(rows, 0.5, None, p)[0]).n for p in PERIODS}
    assert len(set(counts.values())) == 1


def test_sweep_grid_is_complete_and_does_not_pick_the_default() -> None:
    rows = [s(i, 1 + i % 4, 1 + (i + 1) % 4, i % 3 == 0, i, i // 4) for i in range(40)]
    grid = sweep(rows, None, elo_k=32.0, glicko_tau=0.5, glicko_period="event")
    assert grid["published"] == {
        "elo_k": 32.0,
        "glicko_tau": 0.5,
        "glicko_period": "event",
    }
    assert len(grid["glicko2"]) == len(PERIODS) * 5
    assert all(cell["n"] == len(rows) for cell in grid["elo"])
    # The artifact reports a best but the published settings are independent of it.
    assert "best_by_brier" in grid
