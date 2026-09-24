"""Infinite Warfare overlap pages load; every other overlap row stays out."""

import json
from pathlib import Path

import pytest

from cdlhub_pipeline.codwiki import transform


def _row(page: str, title: str = "Infinite Warfare") -> dict[str, str]:
    return {"TournamentPage": page, "GameTitle": title}


def test_only_listed_infinite_warfare_pages_join_the_load_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "playerstats-playerstats.json").write_text(
        json.dumps([_row("CWL/2016 Season/Stage 1", "Black Ops 3")])
    )
    (tmp_path / "playerstats-overlap.json").write_text(
        json.dumps(
            [
                _row("CWL/2017 Season/Dallas Open"),
                _row("Call of Duty World League Championship 2017"),
                _row("CWL/2018 Season/Pro League/Stage 1", "World War II"),
            ]
        )
    )
    monkeypatch.setattr(transform, "SNAPSHOT_ROOT", tmp_path)
    pages = [row["TournamentPage"] for row in transform.load_rows()]
    assert pages == ["CWL/2016 Season/Stage 1", "CWL/2017 Season/Dallas Open"]


def test_the_championship_is_left_to_the_activision_archive() -> None:
    assert not any("Championship" in page for page in transform.IW_OVERLAP_EVENTS)
