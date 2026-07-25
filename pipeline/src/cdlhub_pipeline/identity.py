"""Player and team identity normalization for the CWL archive.

Both archive tiers name players by gamertag, and the spellings drift between
events (Formal/FormaL, Abezy/aBeZy). The CSVs and the event JSONs have to land
on the same handle or they will not join, so both importers use this.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources


@dataclass
class Aliases:
    players: dict[str, str]  # archive spelling -> canonical handle
    teams: dict[str, str]  # archive name -> canonical team name
    orgs: dict[str, list[str]]  # org name -> its team names, earliest brand first

    @classmethod
    def load(cls) -> Aliases:
        raw = json.loads(resources.files("cdlhub_pipeline").joinpath("aliases.json").read_text())
        return cls(
            players=dict(raw["players"]),
            teams=dict(raw["teams"]),
            orgs={k: list(v) for k, v in raw.get("orgs", {}).items()},
        )

    def team(self, name: str) -> str:
        return self.teams.get(name, name)

    def player(self, handle: str) -> str:
        return self.players.get(handle, handle)

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
