"""Player and team identity normalization across data sources.

Both CWL archive tiers name players by gamertag, and the spellings drift
between events (Formal/FormaL, Abezy/aBeZy). The CSVs and the event JSONs have
to land on the same handle or they will not join, so both importers use this.

Cito names teams by franchise-slot slug stamped with the slot's CURRENT brand
on every historical match ("FaZe Vegas" winning the 2020 Championship), so its
importer resolves (slug, season year) through the dated cito_teams map instead.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import resources
from typing import Any


@dataclass
class Aliases:
    players: dict[str, str]  # archive spelling -> canonical handle
    teams: dict[str, str]  # archive name -> canonical team name
    orgs: dict[str, list[str]]  # org name -> its team names, earliest brand first
    # Cito slug -> dated brand history: [{name, seasons: [first, last], status?}]
    cito_teams: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    # Cito slug -> roster-split spec for seasons where one slug hides two teams
    cito_slug_rosters: dict[str, dict[str, Any]] = field(default_factory=dict)
    # LPDB opponentname -> canonical team name where spellings differ
    lpdb_teams: dict[str, str] = field(default_factory=dict)
    # LPDB tournament pagename -> local event name (season comes from the game
    # code); null marks a page deliberately out of scope
    lpdb_events: dict[str, str | None] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._folded_teams = {k.lower(): v for k, v in self.teams.items()}
        self._folded_players = {k.lower(): v for k, v in self.players.items()}

    @classmethod
    def load(cls) -> Aliases:
        raw = json.loads(resources.files("cdlhub_pipeline").joinpath("aliases.json").read_text())
        return cls(
            players=dict(raw["players"]),
            teams=dict(raw["teams"]),
            orgs={k: list(v) for k, v in raw.get("orgs", {}).items()},
            cito_teams={k: list(v) for k, v in raw.get("cito_teams", {}).items()},
            cito_slug_rosters=dict(raw.get("cito_slug_rosters", {})),
            lpdb_teams=dict(raw.get("lpdb_teams", {})),
            lpdb_events=dict(raw.get("lpdb_events", {})),
        )

    def cito_split(self, slug: str, season_year: int) -> dict[str, set[str]] | None:
        """Roster lists (lowercased handles) when a slug hides two teams that season."""
        spec = self.cito_slug_rosters.get(slug)
        if not spec:
            return None
        first, last = spec["seasons"]
        if not first <= season_year <= last:
            return None
        return {team: {h.lower() for h in handles} for team, handles in spec["teams"].items()}

    def cito_team(self, slug: str, season_year: int) -> str | None:
        """The brand a Cito slug wore in a given season, or None if unmapped.

        A slug whose brand history doesn't cover the season falls back to its
        nearest dated entry — a mapped slug never silently becomes a new team
        because one match strays a season outside its window.
        """
        history = (self.cito_teams or {}).get(slug)
        if not history:
            return None
        for entry in history:
            first, last = entry["seasons"]
            if first <= season_year <= last:
                return str(entry["name"])

        def distance(entry: dict[str, Any]) -> int:
            first, last = entry["seasons"]
            return int(min(abs(season_year - first), abs(season_year - last)))

        nearest = min(history, key=distance)
        return str(nearest["name"])

    def team(self, name: str) -> str:
        """The canonical name, matched without regard to case.

        Sources disagree on the case of a brand: the wiki writes `compLexity
        Gaming` where the alias file writes `Complexity Gaming`. An exact match
        misses that and the loader then creates a second team row, which splits
        one roster's results across two teams.
        """
        return self._folded_teams.get(name.lower(), name)

    def player(self, handle: str) -> str:
        return self._folded_players.get(handle.lower(), handle)

    def org_of(self, team_name: str) -> str | None:
        """The organisation a canonical team name belongs to, if any.

        Teams with no entry return None and are their own lineage; the ratings
        then behave exactly as they did before any org was declared.
        """
        for org, members in self.orgs.items():
            if team_name in members:
                return org
        return None


def canonical_spellings(handles: Iterable[str]) -> dict[str, str]:
    """Pick one spelling per handle, keyed by the lowercased form.

    The most frequent spelling wins; ties break lexicographically so the result
    does not depend on input order.
    """
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for handle in handles:
        spellings[handle.lower()][handle] += 1
    return {
        low: max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
        for low, counts in spellings.items()
    }
