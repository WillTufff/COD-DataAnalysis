from datetime import date

from cdlhub_pipeline.eras import era_for_date, era_for_season, season_year


def test_fall_events_roll_into_next_season() -> None:
    assert season_year(date(2024, 11, 15)) == 2025  # BO6 launch events -> 2025 season
    assert season_year(date(2025, 2, 1)) == 2025
    assert season_year(date(2025, 8, 30)) == 2025  # Champs
    assert season_year(date(2025, 10, 5)) == 2026


def test_era_boundaries() -> None:
    assert era_for_season(2026) == "cdl"
    assert era_for_season(2020) == "cdl"
    assert era_for_season(2019) == "cwl"
    assert era_for_season(2016) == "cwl"
    assert era_for_season(2015) == "mlg"
    assert era_for_season(2013) == "mlg"
    assert era_for_season(2012) == "early"


def test_era_for_date_uses_season_year() -> None:
    # Dec 2019 is the launch window of the 2020 (first CDL) season
    assert era_for_date(date(2019, 12, 1)) == "cdl"
