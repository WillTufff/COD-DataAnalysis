"""Event manifest for the CWL 2017-2019 archive CSVs.

Dates/locations come from the archive's DATA-README.md (Activision cwl-data,
BSD-3). Season assignment follows cdlhub_pipeline.eras: CWL Dallas (Dec 2017)
belongs to the 2018 WWII season. Every CWL event was played on LAN.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class ArchiveEvent:
    slug: str  # stable key, also used in synthetic series ids
    filename: str
    name: str
    season_year: int
    title_short: str  # titles.short_name
    start_date: date
    end_date: date
    location: str
    tier: str  # 'S' for Champs, 'A' otherwise (Liquipedia-style)


EVENTS: tuple[ArchiveEvent, ...] = (
    ArchiveEvent(
        "2017-champs",
        "data-2017-08-13-champs.csv",
        "CWL Championship 2017",
        2017,
        "IW",
        date(2017, 8, 9),
        date(2017, 8, 13),
        "Orlando, FL",
        "S",
    ),
    ArchiveEvent(
        "2018-dallas",
        "data-2017-12-10-dallas.csv",
        "CWL Dallas 2017",
        2018,
        "WWII",
        date(2017, 12, 8),
        date(2017, 12, 10),
        "Dallas, TX",
        "A",
    ),
    ArchiveEvent(
        "2018-neworleans",
        "data-2018-01-14-neworleans.csv",
        "CWL New Orleans 2018",
        2018,
        "WWII",
        date(2018, 1, 12),
        date(2018, 1, 14),
        "New Orleans, LA",
        "A",
    ),
    ArchiveEvent(
        "2018-proleague1",
        "data-2018-04-08-proleague1.csv",
        "CWL Pro League 2018 Stage 1",
        2018,
        "WWII",
        date(2018, 1, 23),
        date(2018, 4, 8),
        "Columbus, OH",
        "A",
    ),
    ArchiveEvent(
        "2018-atlanta",
        "data-2018-03-11-atlanta.csv",
        "CWL Atlanta 2018",
        2018,
        "WWII",
        date(2018, 3, 9),
        date(2018, 3, 11),
        "Atlanta, GA",
        "A",
    ),
    ArchiveEvent(
        "2018-birmingham",
        "data-2018-04-01-birmingham.csv",
        "CWL Birmingham 2018",
        2018,
        "WWII",
        date(2018, 3, 30),
        date(2018, 4, 1),
        "Birmingham, UK",
        "A",
    ),
    ArchiveEvent(
        "2018-relegation",
        "data-2018-04-19-relegation.csv",
        "CWL Pro League 2018 Relegation",
        2018,
        "WWII",
        date(2018, 4, 19),
        date(2018, 4, 19),
        "Seattle, WA",
        "B",
    ),
    ArchiveEvent(
        "2018-seattle",
        "data-2018-04-22-seattle.csv",
        "CWL Seattle 2018",
        2018,
        "WWII",
        date(2018, 4, 20),
        date(2018, 4, 22),
        "Seattle, WA",
        "A",
    ),
    ArchiveEvent(
        "2018-anaheim",
        "data-2018-06-17-anaheim.csv",
        "CWL Anaheim 2018",
        2018,
        "WWII",
        date(2018, 6, 15),
        date(2018, 6, 17),
        "Anaheim, CA",
        "A",
    ),
    ArchiveEvent(
        "2018-proleague2",
        "data-2018-07-29-proleague2.csv",
        "CWL Pro League 2018 Stage 2",
        2018,
        "WWII",
        date(2018, 5, 15),
        date(2018, 7, 29),
        "Columbus, OH",
        "A",
    ),
    ArchiveEvent(
        "2018-champs",
        "data-2018-08-19-champs.csv",
        "CWL Championship 2018",
        2018,
        "WWII",
        date(2018, 8, 15),
        date(2018, 8, 19),
        "Columbus, OH",
        "S",
    ),
    ArchiveEvent(
        "2019-proleague-qual",
        "data-2019-01-20-proleague-qual.csv",
        "CWL Pro League 2019 Qualifier",
        2019,
        "BO4",
        date(2019, 1, 16),
        date(2019, 1, 20),
        "Columbus, OH",
        "A",
    ),
    ArchiveEvent(
        "2019-proleague",
        "data-2019-07-05-proleague.csv",
        "CWL Pro League 2019",
        2019,
        "BO4",
        date(2019, 2, 4),
        date(2019, 7, 5),
        "Columbus, OH",
        "A",
    ),
    ArchiveEvent(
        "2019-fortworth",
        "data-2019-03-17-fortworth.csv",
        "CWL Fort Worth 2019",
        2019,
        "BO4",
        date(2019, 3, 15),
        date(2019, 3, 17),
        "Fort Worth, TX",
        "A",
    ),
    ArchiveEvent(
        "2019-london",
        "data-2019-05-05-london.csv",
        "CWL London 2019",
        2019,
        "BO4",
        date(2019, 5, 3),
        date(2019, 5, 5),
        "London, UK",
        "A",
    ),
    ArchiveEvent(
        "2019-anaheim",
        "data-2019-06-16-anaheim.csv",
        "CWL Anaheim 2019",
        2019,
        "BO4",
        date(2019, 6, 14),
        date(2019, 6, 16),
        "Anaheim, CA",
        "A",
    ),
    ArchiveEvent(
        "2019-proleague-finals",
        "data-2019-07-21-proleague-finals.csv",
        "CWL Pro League 2019 Finals",
        2019,
        "BO4",
        date(2019, 7, 19),
        date(2019, 7, 21),
        "Miami, FL",
        "A",
    ),
    ArchiveEvent(
        "2019-champs",
        "data-2019-08-18-champs.csv",
        "CWL Championship 2019",
        2019,
        "BO4",
        date(2019, 8, 14),
        date(2019, 8, 18),
        "Los Angeles, CA",
        "S",
    ),
)

# titles.short_name -> season league label; all archive events are CWL.
LEAGUE = "CWL"

MODE_SLUGS: dict[str, str] = {
    "Hardpoint": "hardpoint",
    "Search & Destroy": "search-and-destroy",
    "Capture The Flag": "capture-the-flag",
    "Uplink": "uplink",
    "Control": "control",
}
