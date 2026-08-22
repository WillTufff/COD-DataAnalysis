"""The anchor set's two guards: what a tier means, and what freezing protects.

The set is the only outside referent the all-time board has, so the failure
that matters is a quiet one — a tier that moved because a list was edited, or a
re-cut that overwrote the membership an earlier version was judged against.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdlhub_analytics.career_rank import PUBLISH_FROM_YEAR, anchors


class FakeConn:
    """Answers each query `anchors` asks, keyed on a fragment of its text."""

    def __init__(
        self,
        players: list[tuple[int, str]],
        resume: list[tuple[Any, ...]] | None = None,
        roster_years: tuple[int, int] | None = None,
        last_season: int = 2026,
        coverage: list[tuple[Any, ...]] | None = None,
    ) -> None:
        self.players = players
        # (player_id, chips, rings, events, first_year, last_year)
        self.resume = resume or []
        self.roster_years = roster_years
        self.last_season = last_season
        # one row per year: (year, wins, attributable, unattributable event names)
        self.coverage = coverage if coverage is not None else _covered(last_season)
        self._result: list[tuple[Any, ...]] = []
        self._one: tuple[Any, ...] | None = None

    def execute(self, sql: str, _params: Any = None) -> FakeConn:
        if "WITH wins AS" in sql:
            self._result, self._one = self.coverage, None
        elif "FROM event_rosters" in sql and "placement_min" in sql:
            self._result, self._one = self.resume, None
        elif "min(s.year)" in sql or "max(s.year)" in sql:
            self._result, self._one = [], self.roster_years
        elif "max(year) FROM seasons" in sql:
            self._result, self._one = [], (self.last_season,)
        else:
            self._result, self._one = self.players, None
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._result

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._one


def _covered(last_season: int) -> list[tuple[Any, ...]]:
    """Every published year carrying one title that a roster answers for."""
    return [(year, 1, 1, None) for year in range(PUBLISH_FROM_YEAR, last_season + 1)]


@pytest.fixture(autouse=True)
def artifact_root(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDLHUB_ARTIFACT_ROOT", str(tmp_path))


def _lists(monkeypatch: pytest.MonkeyPatch, lists: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(anchors, "load_lists", lambda: {"lists": lists})


def test_tier_counts_lists_not_places(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tier is how many lists name a player, never how high they are placed.

    The player ranked last on three lists outranks the one ranked first on two.
    """
    _lists(
        monkeypatch,
        [
            {"id": "a", "kind": "all_time", "order": ["Top", "Deep"]},
            {"id": "b", "kind": "all_time", "order": ["Other", "Deep"]},
            {"id": "c", "kind": "all_time", "order": ["Third", "Deep"]},
            {"id": "d", "kind": "all_time", "order": ["Top"]},
        ],
    )
    conn = FakeConn([(1, "Top"), (2, "Deep"), (3, "Other"), (4, "Third")])
    built = anchors.build(conn)  # type: ignore[arg-type]
    tiers = {p["handle"]: p["tier"] for p in built["players"]}
    assert tiers["Deep"] == "A"
    assert tiers["Top"] == "B"


def test_current_form_list_cannot_lift_a_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three current-form lists are still tier C: they rank the season, not a career."""
    _lists(
        monkeypatch,
        [
            {"id": "a", "kind": "current_form", "order": ["Now"]},
            {"id": "b", "kind": "current_form", "order": ["Now"]},
            {"id": "c", "kind": "current_form", "order": ["Now"]},
        ],
    )
    conn = FakeConn([(1, "Now")])
    built = anchors.build(conn)  # type: ignore[arg-type]
    assert built["players"][0]["tier"] == "C"
    assert built["players"][0]["mean_all_time_rank"] is None


def test_case_collision_is_unresolved_not_guessed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two players separated only by case leave the handle unresolved.

    Liquipedia keeps people apart by letter case, so folding is safe only while
    it lands on one row.
    """
    _lists(monkeypatch, [{"id": "a", "kind": "all_time", "order": ["ace"]}])
    conn = FakeConn([(1, "ace"), (2, "Ace")])
    built = anchors.build(conn)  # type: ignore[arg-type]
    assert built["unresolved"] == ["ace"]
    with pytest.raises(ValueError, match="unresolved"):
        anchors.freeze(conn, "cut-1")  # type: ignore[arg-type]


def test_reusing_a_label_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-cut takes a new label; the old membership stays readable."""
    _lists(monkeypatch, [{"id": "a", "kind": "all_time", "order": ["One"]}])
    conn = FakeConn([(1, "One"), (2, "Two")])
    first = anchors.freeze(conn, "cut-1")  # type: ignore[arg-type]

    _lists(monkeypatch, [{"id": "a", "kind": "all_time", "order": ["One", "Two"]}])
    second = anchors.freeze(conn, "cut-2")  # type: ignore[arg-type]
    assert second["supersedes"] == "cut-1"
    assert [entry["cut"] for entry in second["history"]] == ["cut-1"]
    assert second["sha256"] != first["sha256"]
    assert anchors.read_set("cut-1")["players"][0]["handle"] == "One"

    with pytest.raises(ValueError, match="already on record"):
        anchors.freeze(conn, "cut-1")  # type: ignore[arg-type]


def test_digest_ignores_resume_but_not_membership(monkeypatch: pytest.MonkeyPatch) -> None:
    """Loading more championships is not a re-cut; adding a player is.

    The résumé counts move with every import, so hashing them would make the
    frozen set look changed on a load that changed nobody's membership.
    """
    _lists(monkeypatch, [{"id": "a", "kind": "all_time", "order": ["One"]}])
    bare = FakeConn([(1, "One")])
    with_rings = FakeConn([(1, "One")], resume=[(1, 3, 1, 9, 2015, 2016)])
    assert anchors.digest(anchors.build(bare)["players"]) == anchors.digest(  # type: ignore[arg-type]
        anchors.build(with_rings)["players"]  # type: ignore[arg-type]
    )

    _lists(monkeypatch, [{"id": "a", "kind": "all_time", "order": ["One", "Two"]}])
    wider = FakeConn([(1, "One"), (2, "Two")])
    assert anchors.digest(anchors.build(wider)["players"]) != anchors.digest(  # type: ignore[arg-type]
        anchors.build(bare)["players"]  # type: ignore[arg-type]
    )


def test_chips_are_incomplete_while_one_year_cannot_attribute_one() -> None:
    """A win count is a career total only when every published year is covered.

    The year that fails is named, because the reason a zero is unsafe is a
    particular year's missing rosters and not the record as a whole.
    """
    first = PUBLISH_FROM_YEAR
    short = FakeConn(
        [(1, "One")],
        coverage=[
            (year, 2, 1 if year == first else 2, ["CWL Atlanta Open 2017"])
            for year in range(first, first + 2)
        ],
        last_season=first + 1,
    )
    assert anchors.chips_are_complete(short) is False  # type: ignore[arg-type]
    assert anchors.chip_coverage(short)["years_short"] == [first]  # type: ignore[arg-type]

    covered = FakeConn([(1, "One")], last_season=2026)
    assert anchors.chips_are_complete(covered) is True  # type: ignore[arg-type]


def test_a_published_year_with_no_chip_at_all_is_incomplete() -> None:
    """The hole a coverage count cannot see is a year whose events never loaded.

    Reading the highest roster year called that complete, which is how a 2017
    with one event of thirteen passed while 2026 was being loaded.
    """
    first = PUBLISH_FROM_YEAR
    missing = FakeConn(
        [(1, "One")],
        coverage=[(year, 1, 1, None) for year in range(first + 1, first + 3)],
        last_season=first + 2,
    )
    assert anchors.chip_coverage(missing)["years_without_a_chip"] == [first]  # type: ignore[arg-type]
    assert anchors.chips_are_complete(missing) is False  # type: ignore[arg-type]


def test_load_reresolves_after_a_merge(monkeypatch: pytest.MonkeyPatch) -> None:
    """An identity merge retires a player_id; the handles still resolve.

    The frozen file stores both, and the handle is what the list published, so
    the set survives a merge and the recomputed digest says it moved.
    """
    _lists(monkeypatch, [{"id": "a", "kind": "all_time", "order": ["One"]}])
    before = FakeConn([(1, "One")])
    pointer = anchors.freeze(before, "cut-1")  # type: ignore[arg-type]

    after = FakeConn([(7, "One")])
    loaded = anchors.load(after)  # type: ignore[arg-type]
    assert loaded["players"][0]["player_id"] == 7
    assert loaded["unresolved"] == []
    assert loaded["frozen_sha256"] == pointer["sha256"]
    assert loaded["sha256"] != pointer["sha256"]
