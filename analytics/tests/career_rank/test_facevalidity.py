"""What each face-validity test must refuse to do.

The danger in a face-validity suite is not that it fails. It is that it passes
for the wrong reason: a test that reads a missing record as a fact, or one that
looks for a defect in one direction and calls the other direction healthy.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdlhub_analytics.career_rank import anchors, facevalidity


def _board(rows: list[tuple[int, str, float, int]]) -> facevalidity.Board:
    return facevalidity.Board(
        [
            {
                "player_id": pid,
                "handle": handle,
                "total": total,
                "peak": total,
                "peak_season_id": season_id,
                "n_seasons": 3,
            }
            for pid, handle, total, season_id in rows
        ]
    )


def _anchor_set(players: list[dict[str, Any]]) -> dict[str, Any]:
    tiers: dict[str, int] = {}
    for player in players:
        tiers[str(player["tier"])] = tiers.get(str(player["tier"]), 0) + 1
    return {"players": players, "tier_counts": tiers}


class SeasonStub:
    def __init__(self, era: str) -> None:
        self.era_key = era


class FakeConn:
    def __init__(self, rows: dict[str, list[tuple[Any, ...]]], one: tuple[Any, ...] | None) -> None:
        self.rows = rows
        self.one = one
        self._result: list[tuple[Any, ...]] = []

    def execute(self, sql: str, _params: Any = None) -> FakeConn:
        for key, value in self.rows.items():
            if key in sql:
                self._result = value
                return self
        self._result = []
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result

    def fetchone(self) -> tuple[Any, ...] | None:
        return self.one


def test_absent_legend_counts_a_missing_player_as_absent() -> None:
    """A tier A anchor the board never ranks fails, the same as one ranked 90th.

    Falling out of the population is the more severe version of the defect, and
    an absent row must not read as a pass.
    """
    board = _board([(1, "Filler", 10.0, 1)])
    anchor_set = _anchor_set([{"handle": "Legend", "player_id": 2, "tier": "A"}])
    result = facevalidity.absent_legend(board, anchor_set)
    assert result.verdict == facevalidity.FAIL
    assert result.detail["absent"] == [{"handle": "Legend", "rank": None}]


def test_unearned_top_ten_is_inconclusive_while_rings_are_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A zero from an unloaded roster is missing data, never an acquittal."""
    monkeypatch.setattr(anchors, "rings_are_complete", lambda _conn: False)
    conn = FakeConn({}, one=(2016,))
    result = facevalidity.unearned_top_ten(
        conn,  # type: ignore[arg-type]
        _board([(1, "Nobody", 99.0, 1)]),
        _anchor_set([]),
    )
    assert result.verdict == facevalidity.INCONCLUSIVE
    assert result.verdict != facevalidity.PASS
    assert result.detail["rings_covered_to"] == 2016


def test_unearned_top_ten_clears_a_player_with_a_ring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One championship is enough; the test polices résumés, not taste."""
    monkeypatch.setattr(anchors, "rings_are_complete", lambda _conn: True)
    monkeypatch.setattr(
        anchors,
        "resume",
        lambda _conn, ids: {pid: {"event_wins": 1 if pid == 1 else 0} for pid in ids},
    )
    conn = FakeConn({"player_awards": []}, one=None)
    result = facevalidity.unearned_top_ten(
        conn,  # type: ignore[arg-type]
        _board([(1, "Winner", 99.0, 1), (2, "Unknown", 98.0, 1)]),
        _anchor_set([]),
    )
    assert result.verdict == facevalidity.FAIL
    assert [row["handle"] for row in result.detail["unearned"]] == ["Unknown"]


def test_era_balance_fails_an_era_with_no_peaks(monkeypatch: pytest.MonkeyPatch) -> None:
    """An era that holds a third of the archive and no peak is the worst case.

    Reading the ratio one way only would call this healthy, because a share of
    zero can never exceed a limit.
    """
    monkeypatch.setattr(
        facevalidity,
        "load_seasons",
        lambda _conn: {1: SeasonStub("old"), 2: SeasonStub("new")},
    )
    conn = FakeConn({"player_season_adjusted": [(1, 100), (2, 100)]}, one=None)
    result = facevalidity.era_balance(
        conn,  # type: ignore[arg-type]
        _board([(i, f"P{i}", 100.0 - i, 2) for i in range(1, 11)]),
    )
    assert result.verdict == facevalidity.FAIL
    assert result.detail["unrepresented"] == ["old"]


def test_era_balance_passes_a_proportionate_board(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        facevalidity,
        "load_seasons",
        lambda _conn: {1: SeasonStub("old"), 2: SeasonStub("new")},
    )
    conn = FakeConn({"player_season_adjusted": [(1, 100), (2, 100)]}, one=None)
    rows = [(i, f"P{i}", 100.0 - i, 1 if i % 2 else 2) for i in range(1, 11)]
    result = facevalidity.era_balance(conn, _board(rows))  # type: ignore[arg-type]
    assert result.verdict == facevalidity.PASS


def test_rank_correlation_never_gates() -> None:
    """Reported, so nobody is tempted to fit the formula to the lists."""
    anchor_set = _anchor_set(
        [
            {"handle": "A", "player_id": 1, "tier": "A", "mean_all_time_rank": 1.0},
            {"handle": "B", "player_id": 2, "tier": "A", "mean_all_time_rank": 2.0},
            {"handle": "C", "player_id": 3, "tier": "A", "mean_all_time_rank": 3.0},
        ]
    )
    board = _board([(3, "C", 99.0, 1), (2, "B", 98.0, 1), (1, "A", 97.0, 1)])
    result = facevalidity.rank_correlation(board, anchor_set)
    assert result.verdict == facevalidity.REPORT
    assert result.detail["rho"] == pytest.approx(-1.0)
