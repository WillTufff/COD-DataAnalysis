"""Re-cutting an evaluation population, for both populations that have one.

The policy the two `evalpop` modules state is that a re-cut is explicit, takes
a new label, and leaves the label it replaced on record. What is exercised here
is the part that would fail silently: a second cut under an old label would
overwrite the set an earlier version was scored on, and nothing else in the
project would notice.
"""

from __future__ import annotations

from typing import Any

import pytest

from cdlhub_analytics import gates
from cdlhub_analytics.career_rank import evalpop as career_evalpop
from cdlhub_analytics.metricdiff import evalpop as map_evalpop


class FakeConn:
    """Answers the one query each `eligible` asks."""

    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self.rows = rows

    def execute(self, _sql: str, _params: Any = None) -> FakeConn:
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows


@pytest.fixture(autouse=True)
def artifact_root(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CDLHUB_ARTIFACT_ROOT", str(tmp_path))


def _map_conn(n: int) -> Any:
    return FakeConn([(f"series-{i}#1", 2020, "MW19") for i in range(n)])


def _career_conn(n: int) -> Any:
    return FakeConn([(i, 3) for i in range(n)])


def test_a_second_map_cut_records_the_one_it_replaced() -> None:
    first = map_evalpop.freeze(_map_conn(3), "pd-first")
    second = map_evalpop.freeze(_map_conn(5), "pd-second")

    assert first["supersedes"] is None
    assert second["supersedes"] == "pd-first"
    assert [e["cut"] for e in second["history"]] == ["pd-first"]
    assert second["history"][0]["sha256"] == first["sha256"]
    assert second["history"][0]["n_maps"] == 3


def test_a_third_cut_keeps_every_earlier_label() -> None:
    map_evalpop.freeze(_map_conn(3), "pd-first")
    map_evalpop.freeze(_map_conn(5), "pd-second")
    third = map_evalpop.freeze(_map_conn(7), "pd-third")

    assert [e["cut"] for e in third["history"]] == ["pd-first", "pd-second"]
    assert third["supersedes"] == "pd-second"


def test_a_superseded_map_cut_is_still_readable() -> None:
    map_evalpop.freeze(_map_conn(3), "pd-first")
    map_evalpop.freeze(_map_conn(5), "pd-second")

    assert len(list(map_evalpop.read_set("pd-first"))) == 3
    assert len(list(map_evalpop.read_set("pd-second"))) == 5


def test_a_label_already_on_record_is_refused() -> None:
    map_evalpop.freeze(_map_conn(3), "pd-first")
    map_evalpop.freeze(_map_conn(5), "pd-second")

    with pytest.raises(ValueError, match="already on record"):
        map_evalpop.freeze(_map_conn(9), "pd-first")
    assert len(list(map_evalpop.read_set("pd-first"))) == 3


def test_a_second_career_cut_records_the_one_it_replaced() -> None:
    first = career_evalpop.freeze(_career_conn(4), "cr-first")
    second = career_evalpop.freeze(_career_conn(6), "cr-second")

    assert second["supersedes"] == "cr-first"
    assert second["history"][0]["n_players"] == 4
    assert career_evalpop.read_set("cr-first") == list(range(4))
    assert first["history"] == []


def test_a_career_label_already_on_record_is_refused() -> None:
    career_evalpop.freeze(_career_conn(4), "cr-first")
    career_evalpop.freeze(_career_conn(6), "cr-second")

    with pytest.raises(ValueError, match="already on record"):
        career_evalpop.freeze(_career_conn(8), "cr-first")


# ------------------------------------------------------------------- the gate


def test_the_career_population_gate_fails_when_nothing_is_cut() -> None:
    assert gates.career_population_failures({"frozen": False}) == [
        "no career population has been cut (career_rank --freeze CUT)"
    ]


def test_the_career_population_gate_reports_drift_and_does_not_fail() -> None:
    found = gates.career_population_failures(
        {
            "frozen": True,
            "readable": True,
            "matches": False,
            "cut": "cr-first",
            "n_players": 141,
            "eligible_now": 146,
            "n_added": 6,
            "n_removed": 1,
        }
    )
    assert len(found) == 1
    assert found[0].startswith(gates.REPORTED)
    assert "141" in found[0] and "146" in found[0]


def test_the_career_population_gate_is_silent_when_the_cut_matches() -> None:
    assert gates.career_population_failures({"frozen": True, "matches": True}) == []
