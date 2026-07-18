from datetime import UTC, datetime
from pathlib import Path

from cdlhub_pipeline.cwl_archive.manifest import EVENTS
from cdlhub_pipeline.cwl_archive.parse import (
    Aliases,
    ArchiveStatLine,
    ParsedEvent,
    canonical_handles,
    parse_row,
)

ARCHIVE_DIR = Path(__file__).resolve().parents[1] / "snapshots" / "cwl-archive"


def _row_2019(**over: str) -> dict[str, str]:
    row = {
        "match id": "m1",
        "series id": "pool-A-1",
        "end time": "2019-08-14 17:17:25 UTC",
        "duration (s)": "621",
        "mode": "Hardpoint",
        "map": "Seaside",
        "team": "Team EnVyUs",
        "player": "Abezy",
        "win?": "W",
        "score": "250",
        "kills": "19",
        "deaths": "20",
        "+/-": "-1",
        "k/d": "0.95",
        "assists": "10",
        "damage dealt": "4394",
        "ekia": "28",
        "hill time (s)": "88",
        "hill captures": "4",
        "snd firstbloods": "",
        "bomb plants": "",
        "accuracy (%)": "21.0%",
        "avg kill dist (m)": "23.1m",
        "fave weapon": "Saug 9mm",
    }
    row.update(over)
    return row


def test_parse_row_maps_basics_and_extras() -> None:
    line = parse_row(_row_2019())
    assert line.kills == 19 and line.deaths == 20 and line.assists == 10
    assert line.damage == 4394
    assert line.hill_time == 88
    assert line.first_bloods is None and line.plants is None  # empty stays absent
    assert line.won and line.team_score == 250
    assert line.ended_at == datetime(2019, 8, 14, 17, 17, 25, tzinfo=UTC)
    # measured stats land in extras with normalized keys
    assert line.extras["ekia"] == 28
    assert line.extras["hill_captures"] == 4
    assert line.extras["avg_kill_dist_m"] == 23.1
    assert line.extras["fave_weapon"] == "Saug 9mm"  # trailing 'm' preserved on strings
    # derived stats are dropped
    for k in ("k_d", "plus_/_", "accuracy_pct"):
        assert k not in line.extras


def test_aliases_apply() -> None:
    aliases = Aliases.load()
    assert aliases.player("Abezy") == "aBeZy"
    assert aliases.team("Team EnVyUs") == "Team Envy"
    assert aliases.player("Scump") == "Scump"  # untouched passthrough


def test_canonical_handles_majority_casing() -> None:
    def line(player: str) -> ArchiveStatLine:
        return parse_row(_row_2019(player=player))

    pe = ParsedEvent(event=EVENTS[0], lines=[line("SlasheR"), line("SlasheR"), line("Slasher")])
    assert canonical_handles([pe])["slasher"] == "SlasheR"


def test_manifest_matches_files_on_disk() -> None:
    files = {p.name for p in ARCHIVE_DIR.glob("data-*.csv")}
    assert {e.filename for e in EVENTS} == files
    assert len({e.slug for e in EVENTS}) == len(EVENTS)
    assert len({(e.season_year, e.name) for e in EVENTS}) == len(EVENTS)
