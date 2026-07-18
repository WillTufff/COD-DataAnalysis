"""Era and competitive-season tagging.

A CoD competitive season straddles calendar years (title releases in the fall,
season runs into the following summer). The season *label* year is the year the
season mostly plays in — e.g. events from Nov 2024 through Aug 2025 are the
"2025" season on Black Ops 6.
"""

from __future__ import annotations

from datetime import date

Era = str  # 'early' | 'mlg' | 'cwl' | 'cdl'

# First season-label year of each league era.
_ERA_STARTS: list[tuple[int, Era]] = [
    (2020, "cdl"),
    (2016, "cwl"),
    (2013, "mlg"),
    (2008, "early"),
]

# Competitive seasons historically start after the fall title release.
_SEASON_ROLLOVER_MONTH = 10  # events in Oct-Dec belong to next year's season


def season_year(event_date: date) -> int:
    """Season label year for an event date (Oct+ rolls into next year)."""
    if event_date.month >= _SEASON_ROLLOVER_MONTH:
        return event_date.year + 1
    return event_date.year


def era_for_season(year: int) -> Era:
    """League era for a season label year."""
    for start, era in _ERA_STARTS:
        if year >= start:
            return era
    return "early"


def era_for_date(event_date: date) -> Era:
    return era_for_season(season_year(event_date))
