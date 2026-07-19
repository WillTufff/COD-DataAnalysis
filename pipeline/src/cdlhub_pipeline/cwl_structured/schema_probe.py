"""Report what is actually inside the event tarballs.

The tier's README documents one death-event shape, but IW uses a different one
and the 2019 files carry no events at all. Everything the importer relies on is
measured here first rather than assumed.

    python -m cdlhub_pipeline.cwl_structured.schema_probe
"""

from __future__ import annotations

import json
import tarfile
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Death events come in two shapes. WWII and BO4 nest the attacker in an object;
# IW spreads the same fields across the death data with an attacker_ prefix and
# adds a z coordinate.
SCHEMA_NESTED = "nested"
SCHEMA_FLAT = "flat"
SCHEMA_NONE = "none"


@dataclass
class EventCoverage:
    """What one event's tarball holds."""

    slug: str
    games: int = 0
    games_with_events: int = 0
    unreadable: int = 0
    game_ids: set[str] = field(default_factory=set)
    titles: set[str] = field(default_factory=set)
    modes: Counter[str] = field(default_factory=Counter)
    death_schema: Counter[str] = field(default_factory=Counter)
    event_types: Counter[str] = field(default_factory=Counter)
    death_fields: Counter[str] = field(default_factory=Counter)
    game_fields: Counter[str] = field(default_factory=Counter)


def iter_games(tarball: Path) -> Iterator[tuple[str, dict[str, Any] | None]]:
    """Yield (member name, parsed game) for each JSON in a tarball.

    Yields None for members that are empty or fail to parse, so callers can
    count them instead of crashing.
    """
    with tarfile.open(tarball) as tar:
        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".json"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                yield member.name, None
                continue
            raw = handle.read()
            if not raw.strip():
                yield member.name, None
                continue
            try:
                yield member.name, json.loads(raw)
            except json.JSONDecodeError:
                yield member.name, None


def death_schema(death: dict[str, Any]) -> str:
    """Which of the two death shapes this event uses."""
    data = death.get("data") or {}
    if isinstance(data.get("attacker"), dict):
        return SCHEMA_NESTED
    if "attacker_id" in data:
        return SCHEMA_FLAT
    return SCHEMA_NONE


def probe_event(tarball: Path) -> EventCoverage:
    slug = tarball.name[len("structured-") : -len(".tar.gz")]
    cov = EventCoverage(slug=slug)
    for _, game in iter_games(tarball):
        if game is None:
            cov.unreadable += 1
            continue
        cov.games += 1
        cov.game_ids.add(game["id"])
        cov.titles.add(game.get("title") or "?")
        cov.modes[game.get("mode") or "?"] += 1
        for key in game:
            cov.game_fields[key] += 1

        events = game.get("events") or []
        deaths = [e for e in events if e.get("type") == "death"]
        if deaths:
            cov.games_with_events += 1
        for e in events:
            cov.event_types[e.get("type") or "?"] += 1
        for d in deaths:
            cov.death_schema[death_schema(d)] += 1
            data = d.get("data") or {}
            for key in data:
                cov.death_fields[key] += 1
            attacker = data.get("attacker")
            if isinstance(attacker, dict):
                for key in attacker:
                    cov.death_fields[f"attacker.{key}"] += 1
    return cov


def probe_all(structured_dir: Path) -> list[EventCoverage]:
    return [probe_event(tb) for tb in sorted(structured_dir.glob("structured-*.tar.gz"))]


def csv_match_ids(archive_dir: Path) -> dict[str, set[str]]:
    """Match ids per CSV, for checking which games the event tier covers."""
    import csv as _csv

    out: dict[str, set[str]] = {}
    for path in sorted(archive_dir.glob("*.csv")):
        with path.open(newline="", encoding="utf-8-sig") as fh:
            out[path.stem.removeprefix("data-")] = {r["match id"] for r in _csv.DictReader(fh)}
    return out


def report(structured_dir: Path, archive_dir: Path) -> str:
    covs = probe_all(structured_dir)
    by_csv = csv_match_ids(archive_dir)
    lines: list[str] = []

    lines.append("per-event coverage")
    lines.append(
        f"{'event':28} {'games':>6} {'events':>7} {'bad':>4} {'csv':>6} {'joined':>7} {'cover':>7}"
    )
    tot: Counter[str] = Counter()
    for cov in covs:
        csv_ids = by_csv.get(cov.slug, set())
        joined = len(cov.game_ids & csv_ids)
        pct = joined / len(csv_ids) * 100 if csv_ids else 0.0
        lines.append(
            f"{cov.slug:28} {cov.games:6} {cov.games_with_events:7} {cov.unreadable:4} "
            f"{len(csv_ids):6} {joined:7} {pct:6.1f}%"
        )
        tot["games"] += cov.games
        tot["with_events"] += cov.games_with_events
        tot["bad"] += cov.unreadable
        tot["joined"] += joined
    csv_total = sum(len(v) for v in by_csv.values())
    lines.append(
        f"{'TOTAL':28} {tot['games']:6} {tot['with_events']:7} {tot['bad']:4} "
        f"{csv_total:6} {tot['joined']:7} {tot['joined'] / csv_total * 100:6.1f}%"
    )

    lines.append("")
    lines.append("death schema and event presence by title and mode")
    # Schema varies per file, so it has to be counted next to the mode it came
    # from rather than rolled up per event.
    detail: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    empties: Counter[tuple[str, str]] = Counter()
    totals: Counter[tuple[str, str]] = Counter()
    for tb in sorted(structured_dir.glob("structured-*.tar.gz")):
        for _, game in iter_games(tb):
            if game is None:
                continue
            key = (game.get("title") or "?", game.get("mode") or "?")
            totals[key] += 1
            deaths = [e for e in (game.get("events") or []) if e.get("type") == "death"]
            if not deaths:
                empties[key] += 1
                continue
            detail[key][death_schema(deaths[0])] += 1
    lines.append(
        f"{'title':6} {'mode':20} {'games':>6} {'no events':>10} {'nested':>7} {'flat':>6}"
    )
    for key in sorted(totals):
        d = detail[key]
        lines.append(
            f"{key[0]:6} {key[1]:20} {totals[key]:6} {empties[key]:10} "
            f"{d[SCHEMA_NESTED]:7} {d[SCHEMA_FLAT]:6}"
        )

    lines.append("")
    lines.append("death data fields seen")
    fields: Counter[str] = Counter()
    for cov in covs:
        fields.update(cov.death_fields)
    for name, n in fields.most_common():
        lines.append(f"  {name:24} {n}")
    return "\n".join(lines)


def main() -> int:
    root = Path(__file__).resolve().parents[3]
    print(report(root / "snapshots" / "cwl-structured", root / "snapshots" / "cwl-archive"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
