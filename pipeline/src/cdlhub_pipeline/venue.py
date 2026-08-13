"""Whether an event was played on LAN, and on what evidence.

`events.is_lan` used to be two assertions wearing one column. The CWL archive
importer stamped `true` on every event it created, and the LPDB loader applied
`{"Offline": True, "Online": False}` to the tournament `type` — so a tournament
Liquipedia records as `Online/Offline` became NULL, and eleven CWL events had a
flag nothing had ever checked. Anything reading the column as a covariate would
have been reading, in part, a loader default. Every event that carries a flag
now carries the evidence for it, and one that carries none says so.

The derivation, in precedence order:

1. **A curated verdict**, from `venues.json`, which wins over everything. It
   carries a reason and a `reviewed` flag, and the quality report prints every
   unreviewed entry so provisional judgements stay visible rather than settling
   in. The file is currently empty: the nine CWL open events it held were
   missing from LPDB only because the tournament pull was scoped to the premier
   circuit, and rule 2 answers them now.
2. **LPDB's tournament `type`** — `Offline` is LAN, `Online` is not.
3. **Undecided**, which is what a mixed `Online/Offline` event and an event with
   no LPDB page both get. The column stays NULL and the reason is published.

**`location` is never consulted, and that is the point.** Nine of the 2020
regular-season weeks kept their host-city branding after March 2020 moved them
online, and CDL Major 4 2022 carries Brooklyn while every match under it is
recorded `Online`. A venue string is what an event was called, not where it was
played.

An event that is genuinely mixed cannot be described by an event-level boolean
at all. LPDB carries `type` per match, so the resolution exists upstream; using
it needs a venue field on `series`, which is a schema change this module
deliberately does not make. Until then, mixed events are undecided and say so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import resources
from typing import Any

__all__ = ["MIXED_TYPES", "Verdict", "VenueRules", "derive"]

# LPDB tournament `type` values that decide the question on their own.
LPDB_TYPES: dict[str, bool] = {"Offline": True, "Online": False}

# LPDB `type` values that describe an event with both, which an event-level
# boolean cannot represent.
MIXED_TYPES = frozenset({"Online/Offline", "Offline & Online"})

SOURCE_CURATED = "curated"
SOURCE_LPDB = "lpdb_tournament_type"
SOURCE_UNDECIDED = "undecided"


@dataclass(frozen=True)
class Verdict:
    is_lan: bool | None
    source: str
    reason: str
    # False when the verdict rests on a curated entry nobody has signed off.
    reviewed: bool = True


@dataclass
class VenueRules:
    """Curated verdicts, keyed `"<season year>:<event name>"`."""

    events: dict[str, dict[str, Any]]

    @classmethod
    def load(cls) -> VenueRules:
        raw = json.loads(resources.files("cdlhub_pipeline").joinpath("venues.json").read_text())
        return cls(events=dict(raw.get("events", {})))

    def get(self, season_year: int | None, event_name: str) -> dict[str, Any] | None:
        return self.events.get(f"{season_year}:{event_name}")


def derive(
    rules: VenueRules,
    season_year: int | None,
    event_name: str,
    lpdb_type: str | None,
) -> Verdict:
    """The venue verdict for one event, and the evidence behind it."""
    curated = rules.get(season_year, event_name)
    if curated is not None:
        return Verdict(
            is_lan=curated.get("is_lan"),
            source=SOURCE_CURATED,
            reason=str(curated.get("reason") or "curated, no reason recorded"),
            reviewed=bool(curated.get("reviewed", False)),
        )

    kind = (lpdb_type or "").strip()
    if kind in LPDB_TYPES:
        return Verdict(
            is_lan=LPDB_TYPES[kind],
            source=SOURCE_LPDB,
            reason=f"LPDB tournament type is {kind}",
        )
    if kind in MIXED_TYPES:
        return Verdict(
            is_lan=None,
            source=SOURCE_UNDECIDED,
            reason=(
                f"LPDB tournament type is {kind}: the event has both, and an "
                "event-level flag cannot say which maps were which"
            ),
        )
    return Verdict(
        is_lan=None,
        source=SOURCE_UNDECIDED,
        reason="no LPDB tournament page and no curated verdict",
    )
