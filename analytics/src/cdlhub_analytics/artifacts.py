"""Where model outputs too large for `jsonb` live, and who deletes them.

`model_artifacts` holds payloads the site renders directly. A published-surface
snapshot is neither: it is millions of key/value pairs, read only by the next
run's comparison. Those go to a declared root outside the repository, addressed
from the database by path.

Snapshots are named by the moment they were taken rather than by the run that
took one, because `model_runs` replaces a rerun with the same
`(model, version, data_through)` and the baseline a rerun needs is exactly the
snapshot the replaced run left behind. Retention is therefore over the files:
the newest `KEEP` survive and the rest are deleted.

The root is `~/.cdlhub/artifacts` unless `CDLHUB_ARTIFACT_ROOT` says otherwise.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".cdlhub" / "artifacts"
SNAPSHOT_DIRNAME = "snapshots"
SNAPSHOT_GLOB = "published-*.ndjson.gz"
# What a snapshot is called until the run that produced it commits.
PARTIAL_SUFFIX = ".partial"

# How many published-surface snapshots are kept. Two are needed to compare a
# rerun against the run before it; the rest are there to compare across a phase.
KEEP = 10


def root() -> Path:
    override = os.environ.get("CDLHUB_ARTIFACT_ROOT")
    return Path(override).expanduser() if override else DEFAULT_ROOT


def snapshot_dir() -> Path:
    path = root() / SNAPSHOT_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def snapshots() -> list[Path]:
    """Stored snapshots, oldest first."""
    return sorted(snapshot_dir().glob(SNAPSHOT_GLOB))


def latest_snapshot() -> Path | None:
    stored = snapshots()
    return stored[-1] if stored else None


def new_snapshot_path() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    return snapshot_dir() / f"published-{stamp}.ndjson.gz"


def relative(path: Path) -> str:
    """The stored form of a path: relative to the root, so a moved root still
    resolves."""
    try:
        return str(path.relative_to(root()))
    except ValueError:
        return str(path)


def resolve(stored: str) -> Path:
    path = Path(stored)
    return path if path.is_absolute() else root() / path


# What a superseded cut keeps in the new pointer's history. `size` differs per
# population (maps or players), so the caller names its own size key.
HISTORY_KEYS = ("cut", "sha256", "frozen_at", "path")


def cut_history(previous: dict[str, object] | None, size_key: str) -> list[dict[str, object]]:
    """The history a new cut carries: every earlier cut, oldest first.

    A re-cut replaces which population a version is scored against, so the
    label it replaced has to stay readable from the pointer alone. Each entry
    keeps the label, the hash, the date and the file, which is enough to score
    an old version on an old cut again.
    """
    if previous is None:
        return []
    earlier = previous.get("history")
    out: list[dict[str, object]] = list(earlier) if isinstance(earlier, list) else []
    entry = {key: previous.get(key) for key in HISTORY_KEYS}
    entry[size_key] = previous.get(size_key)
    out.append(entry)
    return out


def prune_snapshots(keep: int = KEEP) -> int:
    """Delete all but the newest `keep` snapshots, and any half-written one.

    A snapshot is renamed into place only once its run commits, so a run that
    failed or rolled back leaves a partial file that nothing will ever read.
    Returns how many files went.
    """
    stored = snapshots()
    doomed = stored[: max(0, len(stored) - keep)]
    doomed += sorted(snapshot_dir().glob(SNAPSHOT_GLOB + PARTIAL_SUFFIX))
    removed = 0
    for path in doomed:
        try:
            path.unlink()
        except OSError:
            continue
        removed += 1
    return removed
