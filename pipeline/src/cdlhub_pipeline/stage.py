"""Where in a competition a series was played, written to `series.stage`.

A round label says where a series sat inside its event, and nothing about the
event. Read alone it gets three eras wrong:

- The 2020 CDL home series were league play, but each weekend was labelled as
  a small tournament ("Group Play A Winners Round 1", "Grand Finals").
- The 2017 Global Pro League is labelled "Group A" and "Group B".
- 2013-2015 had no league, so there is no league play to find.

So the stage is decided at the event first, from a curated list of league
events (`leagues.json`), and by the round label second:

- A **league** event's maps are league play. Where the entry says the league
  ran its own playoffs inside the event (the 2017 and 2018 stages), a bracket
  or final label keeps its class.
- Any other event's maps are **group**, **bracket** or **final** by label. A
  round-robin day at an event is group play.
- A label that decides nothing leaves the stage NULL.

`series.round_label` carries three vocabularies: CWL archive slugs
(`champs-winners-1-2`, `pool-B-4`, `pro1-a1-7`), Call of Duty League prose
(`Winners Round 1`, `Major Qualifier`) and short codes (`GF`, `LR1`, `OWR4`,
`Group B`), the last shared by Cito and the CoD wiki's match schedule.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib import resources
from typing import Any, cast

import psycopg

__all__ = [
    "BRACKET",
    "FINAL",
    "GROUP",
    "LEAGUE",
    "REGULAR",
    "STAGES",
    "Leagues",
    "apply",
    "classify_label",
    "derive",
]

LEAGUE = "league"
GROUP = "group"
BRACKET = "bracket"
FINAL = "final"
STAGES: tuple[str, ...] = (LEAGUE, GROUP, BRACKET, FINAL)

# A label that reads as league play ("Week 3", "Major Qualifier"). Only a
# label class: at an event that is not a league it is group play.
REGULAR = "regular"

_SLUG_FINAL = re.compile(r"^(champs?|pro\d?)-.*grand-finals-\d+$")
_SLUG_BRACKET = re.compile(
    r"^(champs?|pro\d?|rel|plq|playin)\b.*-(winners|losers|bracket|lr\d|wr\d|\d)"
)
_SLUG_POOL = re.compile(r"^(champs-)?pool-[a-z](-tie)?-\d+$")
_SLUG_LEAGUE = re.compile(r"^pro\d?-([ab]\d|w\d+)-\d+$")

_PROSE_FINAL = frozenset({"grand finals", "grand final", "finals", "gf", "gf1", "gf2"})
_PROSE_GROUP = frozenset({"group stage", "play-in"})
_PROSE_BRACKET = frozenset(
    {"semifinals", "quarterfinals", "relegation", "qf", "sf", "wf", "lf", "tb", "r16"}
)
# WR1, LR11, OWR4, OLR10, R2: winners, losers and open-bracket rounds.
_CODE_BRACKET = re.compile(r"^(o?[wl]r|r)\d+$")
# 3RD, 5TH, 13TH: placement matches, played off the bracket.
_CODE_PLACEMENT = re.compile(r"^\d+(st|nd|rd|th)$")
_GROUP_NAME = re.compile(r"^(group|pool) [a-z]$")


def classify_label(label: str | None) -> str | None:
    """REGULAR, GROUP, BRACKET or FINAL for a round label; None when it says nothing."""
    low = (label or "").strip().lower()
    if not low:
        return None
    if low in _PROSE_FINAL or _SLUG_FINAL.match(low):
        return FINAL
    if low.startswith(("major qualifier", "week ", "day ")) or _SLUG_LEAGUE.match(low):
        return REGULAR
    if (
        _SLUG_POOL.match(low)
        or _GROUP_NAME.match(low)
        or low.startswith("group play")
        or low in _PROSE_GROUP
    ):
        return GROUP
    if _SLUG_BRACKET.match(low) or _CODE_BRACKET.match(low) or _CODE_PLACEMENT.match(low):
        return BRACKET
    if low in _PROSE_BRACKET or low.startswith(("winners ", "elimination ", "round ")):
        return BRACKET
    return None


@dataclass(frozen=True)
class League:
    # True where the league's own playoffs sit inside the event, so a bracket
    # or final label keeps its class rather than reading as league play.
    playoffs: bool
    reason: str


@dataclass
class Leagues:
    """The curated league events, keyed by event name."""

    events: dict[str, League]

    @classmethod
    def load(cls) -> Leagues:
        raw = json.loads(resources.files("cdlhub_pipeline").joinpath("leagues.json").read_text())
        return cls(
            events={
                name: League(
                    playoffs=bool(entry.get("playoffs", False)), reason=str(entry["reason"])
                )
                for name, entry in raw["events"].items()
            }
        )

    def get(self, event_name: str) -> League | None:
        return self.events.get(event_name)


def derive(league: League | None, label: str | None) -> str | None:
    """The stage of one series, from its event's league entry and its round label."""
    cls = classify_label(label)
    if league is not None:
        if league.playoffs and cls in (BRACKET, FINAL):
            return cls
        return LEAGUE
    if cls == REGULAR:
        return GROUP
    return cls


STAGE_SQL = """
SELECT s.id, e.name, s.round_label, s.stage
FROM series s JOIN events e ON e.id = s.event_id
"""


def apply(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, Any]:
    """Write `series.stage` for every series; returns counts and the unlabelled events.

    Runs over every series, so a label or list change reaches rows no loader
    touched on this run, and a series that stops being decidable loses its stage.
    """
    leagues = Leagues.load()
    counts: dict[str, int] = {}
    changed = 0
    undecided: dict[str, int] = {}
    for sid, event_name, label, held in conn.execute(STAGE_SQL).fetchall():
        name = cast(str, event_name)
        stage = derive(leagues.get(name), cast("str | None", label))
        counts[stage or "none"] = counts.get(stage or "none", 0) + 1
        if stage is None:
            undecided[name] = undecided.get(name, 0) + 1
        if stage != held:
            conn.execute("UPDATE series SET stage = %s WHERE id = %s", (stage, sid))
            changed += 1
    names = {cast(str, r[0]) for r in conn.execute("SELECT DISTINCT name FROM events").fetchall()}
    return {
        "series": dict(sorted(counts.items())),
        "changed": changed,
        "undecided_by_event": dict(sorted(undecided.items())),
        "listed_but_absent": sorted(set(leagues.events) - names),
    }
