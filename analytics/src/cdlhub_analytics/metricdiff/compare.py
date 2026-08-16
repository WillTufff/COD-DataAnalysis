"""What moved between two snapshots.

Both streams arrive sorted by key, so the comparison is a merge: one pass, two
cursors, and no more memory than the movers it keeps. Numeric leaves are
compared against the noise floor; everything else — headlines, verdict booleans,
window dates — is compared for equality, and a change there is a flip rather
than a move.

The counts are over every difference. The named lists are bounded, and the
report says how many it left out, because a truncated list read as a complete
one is the failure this harness exists to end.
"""

from __future__ import annotations

import heapq
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from . import ATOL, RTOL, TOP_MOVERS
from .snapshot import Entry

# Added and removed keys named in the report before it starts counting only.
SAMPLE = 40

# A season as a key writes it: the year, then the league, then the title. Every
# published number is standardized inside its own season's cohort, so a season
# entering the archive moves every number in it. That makes the diff after such
# a load a change of population rather than a change of model, and the report
# has to say so or the next reader treats a baseline reset as a regression.
SEASON_IN_KEY = re.compile(r"/((?:19|20)\d{2}) [A-Za-z0-9]+ ")


@dataclass
class FamilyCounts:
    compared: int = 0
    moved: int = 0
    flipped: int = 0
    added: int = 0
    removed: int = 0
    max_abs_delta: float = 0.0
    max_rel_delta: float = 0.0


@dataclass
class Move:
    key: str
    old: float
    new: float
    delta: float
    # None where the baseline was zero: the ratio has no value there, and
    # `Infinity` is not a JSON number Postgres will accept into jsonb.
    rel: float | None

    def payload(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "family": family(self.key),
            "old": self.old,
            "new": self.new,
            "delta": self.delta,
            "rel": self.rel,
        }


@dataclass
class Flip:
    key: str
    old: Any
    new: Any

    def payload(self) -> dict[str, Any]:
        return {"key": self.key, "family": family(self.key), "old": self.old, "new": self.new}


@dataclass
class Report:
    families: dict[str, FamilyCounts] = field(default_factory=dict)
    movers: list[tuple[float, int, Move]] = field(default_factory=list)
    flips: list[Flip] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    n_flips: int = 0
    # Season years each side of the comparison names, for the baseline-reset note.
    baseline_years: set[int] = field(default_factory=set)
    current_years: set[int] = field(default_factory=set)
    _seq: int = 0

    def counts(self, key: str) -> FamilyCounts:
        return self.families.setdefault(family(key), FamilyCounts())


def family(key: str) -> str:
    return key.split("/", 1)[0]


def provenance(key: str) -> bool:
    """Whether this leaf names which run produced a number, not the number.

    A finding stores the run id of every model it read, so a refit moves all of
    them and a run where nothing changed still reports well over a hundred
    moves. That is the harness's own rule broken from the value side: a key
    names the thing rather than the row that held it, and so should what the key
    points at. Filtered here rather than at snapshot time, so the stored
    snapshot stays a faithful record of the published surface and no existing
    baseline has to be re-cut.
    """
    leaf = key.rsplit("/", 1)[-1]
    return leaf == "run_id" or leaf.endswith("_run_id")


def moved(old: float, new: float, rtol: float = RTOL, atol: float = ATOL) -> bool:
    return abs(new - old) > atol + rtol * abs(old)


def _numeric(value: Any) -> bool:
    return isinstance(value, float | int) and not isinstance(value, bool)


def merge(
    baseline: Iterator[Entry],
    current: Iterator[Entry],
    rtol: float = RTOL,
    atol: float = ATOL,
    top: int = TOP_MOVERS,
) -> Report:
    """One pass over both sorted streams."""
    report = Report()
    # Filtering preserves the sort the merge below depends on.
    baseline = (entry for entry in baseline if not provenance(entry[0]))
    current = (entry for entry in current if not provenance(entry[0]))
    left = next(baseline, None)
    right = next(current, None)

    while left is not None or right is not None:
        if left is not None and (right is None or left[0] < right[0]):
            _year(report.baseline_years, left[0])
            _removed(report, left[0])
            left = next(baseline, None)
        elif right is not None and (left is None or right[0] < left[0]):
            _year(report.current_years, right[0])
            _added(report, right[0])
            right = next(current, None)
        else:
            assert left is not None and right is not None
            _year(report.baseline_years, left[0])
            _year(report.current_years, right[0])
            _compare(report, left[0], left[1], right[1], rtol, atol, top)
            left = next(baseline, None)
            right = next(current, None)
    return report


def _year(into: set[int], key: str) -> None:
    match = SEASON_IN_KEY.search(key)
    if match is not None:
        into.add(int(match.group(1)))


def baseline_reset(report: Report) -> dict[str, Any]:
    """Whether this diff describes a larger archive rather than a changed model.

    Read from the seasons the two snapshots name, not from a date written down
    here, so the note appears exactly on the runs it is true of.
    """
    gained = sorted(report.current_years - report.baseline_years)
    lost = sorted(report.baseline_years - report.current_years)
    if not gained and not lost:
        return {"reset": False}
    return {
        "reset": True,
        "seasons_gained": gained,
        "seasons_lost": lost,
        "what": (
            "the archive changed which seasons it holds, so this diff is a baseline reset "
            "and not a regression"
        ),
        "why": (
            "every published number is standardized inside its own season's cohort, so every "
            "season that gained or lost a cohort member moved, and no model changed to move it"
        ),
    }


def _removed(report: Report, key: str) -> None:
    report.counts(key).removed += 1
    if len(report.removed) < SAMPLE:
        report.removed.append(key)


def _added(report: Report, key: str) -> None:
    report.counts(key).added += 1
    if len(report.added) < SAMPLE:
        report.added.append(key)


def _compare(
    report: Report, key: str, old: Any, new: Any, rtol: float, atol: float, top: int
) -> None:
    counts = report.counts(key)
    counts.compared += 1

    if _numeric(old) and _numeric(new):
        if not moved(float(old), float(new), rtol, atol):
            return
        delta = float(new) - float(old)
        rel = abs(delta) / abs(float(old)) if old else None
        counts.moved += 1
        counts.max_abs_delta = max(counts.max_abs_delta, abs(delta))
        if rel is not None:
            counts.max_rel_delta = max(counts.max_rel_delta, rel)
        _keep(report, Move(key, float(old), float(new), delta, rel), top)
        return

    if old == new:
        return
    counts.flipped += 1
    report.n_flips += 1
    if len(report.flips) < SAMPLE:
        report.flips.append(Flip(key, old, new))


def _keep(report: Report, move: Move, top: int) -> None:
    """The `top` largest relative moves, as a bounded min-heap.

    Ties break on insertion order rather than on the move, which keeps the heap
    total-ordered without comparing dataclasses.
    """
    report._seq += 1
    # A move off zero has no ratio, so it ranks by its size instead — above any
    # ordinary relative move, which is what a number appearing from nothing is.
    rank = move.rel if move.rel is not None else abs(move.delta) * 1e9
    item = (rank, report._seq, move)
    if len(report.movers) < top:
        heapq.heappush(report.movers, item)
    else:
        heapq.heappushpop(report.movers, item)


def payload(
    report: Report,
    baseline_header: dict[str, Any],
    current_header: dict[str, Any],
    baseline_path: str,
    current_path: str,
    rtol: float = RTOL,
    atol: float = ATOL,
) -> dict[str, Any]:
    """The stored report: counts first, names second, omissions stated."""
    totals = FamilyCounts()
    for counts in report.families.values():
        totals.compared += counts.compared
        totals.moved += counts.moved
        totals.flipped += counts.flipped
        totals.added += counts.added
        totals.removed += counts.removed
        totals.max_abs_delta = max(totals.max_abs_delta, counts.max_abs_delta)
        totals.max_rel_delta = max(totals.max_rel_delta, counts.max_rel_delta)

    movers = [move for _rank, _seq, move in sorted(report.movers, reverse=True)]
    return {
        "available": True,
        "threshold": {"rtol": rtol, "atol": atol},
        "baseline": _side(baseline_header, baseline_path),
        "current": _side(current_header, current_path),
        "totals": {
            "compared": totals.compared,
            "moved": totals.moved,
            "flipped": totals.flipped,
            "added": totals.added,
            "removed": totals.removed,
            "max_abs_delta": round(totals.max_abs_delta, 8),
            "max_rel_delta": round(totals.max_rel_delta, 8),
        },
        "families": [
            {
                "family": name,
                "compared": counts.compared,
                "moved": counts.moved,
                "flipped": counts.flipped,
                "added": counts.added,
                "removed": counts.removed,
                "max_abs_delta": round(counts.max_abs_delta, 8),
                "max_rel_delta": round(counts.max_rel_delta, 8),
            }
            for name, counts in sorted(report.families.items())
        ],
        "movers": [m.payload() for m in movers],
        "movers_omitted": max(0, totals.moved - len(movers)),
        "flips": [f.payload() for f in report.flips],
        "flips_omitted": max(0, report.n_flips - len(report.flips)),
        "added_keys": report.added,
        "added_omitted": max(0, totals.added - len(report.added)),
        "removed_keys": report.removed,
        "removed_omitted": max(0, totals.removed - len(report.removed)),
        "baseline_reset": baseline_reset(report),
    }


def _side(header: dict[str, Any], path: str) -> dict[str, Any]:
    return {
        "snapshot_path": path,
        "generated_at": header.get("generated_at"),
        "n_entries": header.get("n_entries"),
        "runs": header.get("runs", []),
    }


def unavailable(reason: str, current_header: dict[str, Any], current_path: str) -> dict[str, Any]:
    """The first run's report: a snapshot was taken, nothing to compare it to."""
    return {
        "available": False,
        "reason": reason,
        "threshold": {"rtol": RTOL, "atol": ATOL},
        "baseline": None,
        "current": _side(current_header, current_path),
        "totals": {
            "compared": 0,
            "moved": 0,
            "flipped": 0,
            "added": 0,
            "removed": 0,
            "max_abs_delta": 0.0,
            "max_rel_delta": 0.0,
        },
        "families": [],
        "movers": [],
        "movers_omitted": 0,
        "flips": [],
        "flips_omitted": 0,
        "added_keys": [],
        "added_omitted": 0,
        "removed_keys": [],
        "removed_omitted": 0,
    }
