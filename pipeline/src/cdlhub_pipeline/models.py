"""Pydantic models for normalized entities.

These are the normalized shapes the transform layer emits and load.py
upserts; raw LPDB response parsing isn't wired up yet. Field nullability
mirrors the SQL schema: absence of map-level stats is data, not an error.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, model_validator


class SeriesRow(BaseModel):
    liquipedia_match_id: str
    event_liquipedia_page: str
    team1: str | None = None
    team2: str | None = None
    team1_score: int | None = None
    team2_score: int | None = None
    best_of: int | None = None
    played_at: datetime | None = None
    round_label: str | None = None

    @model_validator(mode="after")
    def score_within_best_of(self) -> SeriesRow:
        if (
            self.best_of is not None
            and self.team1_score is not None
            and self.team2_score is not None
            and self.team1_score + self.team2_score > self.best_of
        ):
            raise ValueError(
                f"scores {self.team1_score}-{self.team2_score} exceed best_of {self.best_of}"
            )
        return self


class GamePlayerStatsRow(BaseModel):
    player_handle: str
    team: str
    kills: int | None = None
    deaths: int | None = None
    assists: int | None = None
    damage: int | None = None
    hill_time: int | None = None
    first_bloods: int | None = None
    plants: int | None = None
    defuses: int | None = None
    ticks: int | None = None

    @model_validator(mode="after")
    def non_negative(self) -> GamePlayerStatsRow:
        for field in ("kills", "deaths", "assists", "damage"):
            v = getattr(self, field)
            if v is not None and v < 0:
                raise ValueError(f"{field} must be >= 0, got {v}")
        return self


class RosterStint(BaseModel):
    player_liquipedia_page: str
    team_liquipedia_page: str
    role: str | None = None
    start_date: date
    end_date: date | None = None
