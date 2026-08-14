"""Every published number, flattened to sorted `key -> value` pairs.

A key names the thing, never the row that held it: `players.handle` rather than
`player_id`, a season's year/league/title rather than `season_id`, an artifact's
JSON path rather than its offset in a payload. Keys built that way survive a
refit, an identity merge and a reload, which is what makes two snapshots taken
weeks apart comparable at all.

Snapshots are written sorted so a comparison is a linear merge over two streams
and never holds either one in memory. Sorting itself is chunked to disk for the
same reason: the metric layer alone contributes over a million entries.
"""

from __future__ import annotations

import gzip
import heapq
import json
import math
import tempfile
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import psycopg

Conn = psycopg.Connection[tuple[object, ...]]

# One published number. `value` is a float, a bool, a string or None; the
# comparison treats the first as numeric and the rest as categorical.
Entry = tuple[str, Any]

# The harness's own model. Its artifacts are a description of the published
# surface, not part of it.
EXCLUDED_MODEL = "metric_diff"

FORMAT = "cdlhub-published-snapshot"
FORMAT_VERSION = 1

# Entries held in memory before a sorted chunk is spilled to disk.
CHUNK = 250_000

# Rows fetched per round trip from a server-side cursor.
ITERSIZE = 20_000

# Fields an artifact list element may be identified by, in preference order. A
# list whose elements carry one is keyed by it, so a reordered leaderboard reads
# as the moves it contains rather than as every row moving at once.
LIST_KEYS = (
    "key",
    "name",
    "slug",
    "metric",
    "basis",
    "axis",
    "model",
    "artifact",
    "spec",
    "term",
    "stat",
    "event",
    "player_id",
    "team",
    "mode",
    "title",
    "year",
    "t_s",
    "index",
)


# ---------------------------------------------------------------------------
# Keys


def escape(part: object) -> str:
    """One key component, with the separator made unambiguous."""
    text = "" if part is None else str(part)
    return text.replace("%", "%25").replace("/", "%2F").replace("\n", " ")


def key(*parts: object) -> str:
    return "/".join(escape(p) for p in parts)


def child(prefix: str, *parts: object) -> str:
    """Extend a key that is already built. `prefix` is passed through as-is;
    escaping it a second time would compound on every level of nesting."""
    return "/".join([prefix, *(escape(p) for p in parts)])


# ---------------------------------------------------------------------------
# Which runs are published


@dataclass(frozen=True)
class RunRef:
    run_id: int
    model: str
    version: str

    @property
    def label(self) -> str:
        return f"{self.model}@{self.version}"


def published_runs(conn: Conn) -> list[RunRef]:
    """The newest run of every (model, version) pair.

    Every version is included, not only the one the site defaults to: the
    rating's superseded versions are published baselines and their backtests are
    compared against the current one. The harness's own run is excluded — its
    artifacts describe the last comparison, and snapshotting them would make
    every report a report about the report before it.
    """
    rows = conn.execute(
        "SELECT DISTINCT ON (model, version) id, model, version FROM model_runs "
        "WHERE model <> %s ORDER BY model, version, created_at DESC, id DESC",
        (EXCLUDED_MODEL,),
    ).fetchall()
    return [RunRef(cast(int, r[0]), cast(str, r[1]), cast(str, r[2])) for r in rows]


# ---------------------------------------------------------------------------
# Natural-key labels


def _disambiguated(rows: Sequence[tuple[int, str, str | None]]) -> dict[int, str]:
    """Map surrogate id to a label, suffixed only where the name repeats."""
    counts: dict[str, int] = {}
    for _id, name, _alt in rows:
        counts[name] = counts.get(name, 0) + 1
    out: dict[int, str] = {}
    for row_id, name, alt in rows:
        out[row_id] = name if counts[name] == 1 else f"{name}#{alt or row_id}"
    return out


class Labels:
    """Every surrogate id the published tables carry, resolved to a name."""

    def __init__(self, conn: Conn) -> None:
        self.players = _disambiguated(
            [
                (cast(int, r[0]), cast(str, r[1]), cast("str | None", r[2]))
                for r in conn.execute(
                    "SELECT id, handle, liquipedia_page FROM players ORDER BY id"
                ).fetchall()
            ]
        )
        self.teams = _disambiguated(
            [
                (cast(int, r[0]), cast(str, r[1]), cast("str | None", r[2]))
                for r in conn.execute(
                    "SELECT id, name, liquipedia_page FROM teams ORDER BY id"
                ).fetchall()
            ]
        )
        self.seasons = {
            cast(int, r[0]): f"{r[1]} {r[2]} {r[3]}"
            for r in conn.execute(
                "SELECT s.id, s.year, s.league, t.short_name FROM seasons s "
                "JOIN titles t ON t.id = s.title_id ORDER BY s.id"
            ).fetchall()
        }
        self.modes = {
            cast(int, r[0]): cast(str, r[1])
            for r in conn.execute("SELECT id, slug FROM game_modes ORDER BY id").fetchall()
        }
        self.series = {
            cast(int, r[0]): cast(str, r[1])
            for r in conn.execute(
                "SELECT s.id, coalesce(s.liquipedia_match_id, s.source_uid, "
                "  e.name || '|' || coalesce(s.played_at::text, '?') || '|' || s.id::text) "
                "FROM series s JOIN events e ON e.id = s.event_id ORDER BY s.id"
            ).fetchall()
        }
        self.events = {
            cast(int, r[0]): cast(str, r[1])
            for r in conn.execute("SELECT id, name FROM events ORDER BY id").fetchall()
        }

    def player(self, pid: object) -> str:
        return self.players.get(cast(int, pid), f"player#{pid}")

    def team(self, tid: object) -> str:
        return self.teams.get(cast(int, tid), f"team#{tid}")

    def season(self, sid: object) -> str:
        # A career-scope plus-minus row carries no season, and "career" is what
        # that row is: naming it so keeps the key readable rather than
        # "season#None".
        if sid is None:
            return "career"
        return self.seasons.get(cast(int, sid), f"season#{sid}")

    def mode(self, mid: object) -> str:
        return "all" if mid is None else self.modes.get(cast(int, mid), f"mode#{mid}")

    def subject(self, subject_type: object, subject_id: object) -> str:
        kind = str(subject_type)
        if kind == "player":
            return self.player(subject_id)
        if kind == "team":
            return self.team(subject_id)
        if kind == "season":
            return self.season(subject_id)
        if kind == "event":
            return self.events.get(cast(int, subject_id), f"event#{subject_id}")
        return f"{kind}#{subject_id}"


# ---------------------------------------------------------------------------
# Artifact payload flattening


def _element_key(element: object, index: int) -> str:
    """A list element's identity: its own key fields, else its position."""
    if not isinstance(element, dict):
        return str(index)
    found = [f"{name}={element[name]}" for name in LIST_KEYS if name in element]
    return "+".join(found) if found else str(index)


def flatten(payload: object, prefix: str = "") -> Iterator[Entry]:
    """Every leaf of a JSON payload, as `path -> value`."""
    if isinstance(payload, dict):
        items = cast(dict[str, Any], payload)
        for name in sorted(items):
            yield from flatten(items[name], child(prefix, name) if prefix else escape(name))
        return
    if isinstance(payload, list):
        elements: list[Any] = payload
        yield (child(prefix, "len"), float(len(elements)))
        for index, element in enumerate(elements):
            yield from flatten(element, child(prefix, _element_key(element, index)))
        return
    if isinstance(payload, bool) or payload is None or isinstance(payload, str):
        yield (prefix, payload)
        return
    if isinstance(payload, int | float):
        yield (prefix, float(payload))
        return
    yield (prefix, str(payload))


# ---------------------------------------------------------------------------
# Per-table extraction


# Every table keyed to a model run whose rows are published numbers, with the
# columns that identify a row and the columns that carry a value.
@dataclass(frozen=True)
class Table:
    name: str
    family: str
    id_columns: tuple[str, ...]
    value_columns: tuple[str, ...]


TABLES: tuple[Table, ...] = (
    Table(
        "player_metric_season",
        "metric.player",
        ("player_id", "season_id", "mode_id", "metric"),
        ("value", "denom", "z", "pctl", "qualified"),
    ),
    Table(
        "team_metric_season",
        "metric.team",
        ("team_id", "season_id", "mode_id", "metric"),
        ("value", "denom", "z", "pctl", "qualified"),
    ),
    Table(
        "player_season_adjusted",
        "era.player",
        ("player_id", "season_id", "mode_id"),
        (
            "maps_played",
            "kd_raw",
            "kd_z",
            "kd_pctl",
            "engagement_z",
            "obj_z",
            "rating",
            "rating_sd",
            "completeness",
        ),
    ),
    # Keyed by scope and season as well as player since migration 0017: one
    # player now holds a career row, a smoothed row per season and a filtered
    # one, and a key that named only the player would compare a season against
    # a career and call the difference a move.
    Table(
        "player_rapm",
        "rapm.player",
        ("scope", "player_id", "season_id"),
        ("maps", "coef", "se", "teammate_concentration", "penalty_share", "resolution"),
    ),
    Table(
        "player_style_season",
        "style.player",
        ("player_id", "season_id", "axis"),
        ("score", "pctl"),
    ),
    Table(
        "player_role_season",
        "role.player",
        ("player_id", "season_id"),
        (
            "maps",
            "contact_rate",
            "contact_win_rate",
            "contact_pctl",
            "kd_raw",
            "kd_adjustment",
            "kd_adjusted",
        ),
    ),
    Table(
        "team_ratings",
        "rating.team",
        ("team_id", "series_id"),
        ("rating_pre", "rating_post", "rating_sd"),
    ),
    # SKILL was published by P5 and never entered this snapshot, so a change to
    # the rating the site leads with reported nothing moved. Added here rather
    # than left for its own phase: an instrument blind to the flagship number is
    # worse than no instrument, because it reads as a clean bill of health.
    Table(
        "player_skill",
        "skill.player",
        ("player_id", "season_id", "scope"),
        (
            "prior_mean",
            "prior_sd",
            "coef",
            "se",
            "skill",
            "skill_sd",
            "weight_prior",
            "model",
        ),
    ),
    # Keyed by every discriminator migration 0019 added, not only the player and
    # the x value: one player now holds a curve per population, per fit and per
    # component, and a key that named fewer of them would compare a delta fit
    # against a naive one and report the difference as a move.
    Table(
        "career_curves",
        "curve.player",
        ("player_id", "population", "fit", "component", "x_is_age", "age_or_seq"),
        ("fitted", "lo95", "hi95"),
    ),
    # Same rule one table over: axis and credit are different ways of counting
    # the same career and their orderings differ by design.
    Table(
        "player_career",
        "career.player",
        ("player_id", "axis", "credit", "era_scope"),
        (
            "seasons",
            "maps",
            "replacement",
            "total",
            "total_sd",
            "peak",
            "peak_season_id",
            "best_three",
            "best_three_start_season_id",
        ),
    ),
    Table(
        "team_season_effect",
        "rapm.team",
        ("team_id", "season_id", "scope"),
        ("coef", "resolution"),
    ),
)


def _identity(table: Table, labels: Labels, row: tuple[Any, ...]) -> list[str]:
    out: list[str] = []
    for column, value in zip(table.id_columns, row, strict=True):
        if column == "player_id":
            out.append(labels.player(value))
        elif column == "team_id":
            out.append(labels.team(value))
        elif column == "season_id":
            out.append(labels.season(value))
        elif column == "mode_id":
            out.append(labels.mode(value))
        elif column == "series_id":
            out.append(labels.series.get(cast(int, value), f"series#{value}"))
        else:
            out.append(str(value))
    return out


def _value(raw: object) -> Any:
    """One stored number, as a comparable value.

    Only the metric-layer tables forbid NaN and Infinity by constraint, so a
    non-finite value can reach here from a rating or a backtest column. It is
    carried as text rather than as a float: `jsonb` will not accept either
    literal, and a coefficient that has become infinite is a change of kind
    rather than a difference of degree.
    """
    if raw is None or isinstance(raw, bool | str):
        return raw
    if isinstance(raw, int | float):
        number = float(raw)
        return number if math.isfinite(number) else repr(number)
    return str(raw)


def table_entries(conn: Conn, table: Table, run: RunRef, labels: Labels) -> Iterator[Entry]:
    columns = ", ".join(table.id_columns + table.value_columns)
    sql = f"SELECT {columns} FROM {table.name} WHERE run_id = %s"  # noqa: S608
    with conn.cursor(name=f"snap_{table.name}") as cur:
        cur.itersize = ITERSIZE
        cur.execute(sql, (run.run_id,))
        n_ids = len(table.id_columns)
        for row in cur:
            identity = _identity(table, labels, row[:n_ids])
            head = key(table.family, run.label, *identity)
            for column, raw in zip(table.value_columns, row[n_ids:], strict=True):
                yield (child(head, column), _value(raw))


def backtest_entries(conn: Conn, run: RunRef) -> Iterator[Entry]:
    row = conn.execute(
        "SELECT window_from, window_to, n_predictions, brier, log_loss, accuracy, calibration "
        "FROM backtests WHERE run_id = %s",
        (run.run_id,),
    ).fetchone()
    if row is None:
        return
    head = key("backtest", run.label)
    yield (child(head, "window_from"), str(row[0]))
    yield (child(head, "window_to"), str(row[1]))
    for name, raw in zip(("n_predictions", "brier", "log_loss", "accuracy"), row[2:6], strict=True):
        yield (child(head, name), _value(raw))
    yield from flatten(row[6], child(head, "calibration"))


def insight_entries(conn: Conn, run: RunRef, labels: Labels) -> Iterator[Entry]:
    """Findings, keyed by what they are about rather than by their serial id.

    The generator caps findings per subject per kind, so the ordinal below is
    small and stable; a rewritten headline in the same slot reads as the
    categorical change it is.
    """
    rows = conn.execute(
        "SELECT subject_type, subject_id, kind, headline, score, detail, valid_through, "
        "finding_class, p_value, q_bh, q_by, retracted "
        "FROM insights WHERE run_id = %s ORDER BY subject_type, subject_id, kind, score DESC, id",
        (run.run_id,),
    ).fetchall()
    ordinals: dict[tuple[str, str, str], int] = {}
    for row in rows:
        subject = labels.subject(row[0], row[1])
        slot = (str(row[0]), subject, str(row[2]))
        ordinals[slot] = ordinals.get(slot, 0) + 1
        head = key("finding", run.label, row[0], subject, row[2], ordinals[slot])
        yield (child(head, "headline"), str(row[3]))
        yield (child(head, "score"), _value(row[4]))
        yield (child(head, "valid_through"), None if row[6] is None else str(row[6]))
        # The error-control verdict is published beside the claim, so a finding
        # that changes class or gets retracted has to read as a move here.
        yield (child(head, "finding_class"), str(row[7]))
        yield (child(head, "p_value"), _value(row[8]))
        yield (child(head, "q_bh"), _value(row[9]))
        yield (child(head, "q_by"), _value(row[10]))
        yield (child(head, "retracted"), bool(row[11]))
        yield from flatten(row[5], child(head, "detail"))


def artifact_entries(conn: Conn, run: RunRef) -> Iterator[Entry]:
    rows = conn.execute(
        "SELECT name, payload FROM model_artifacts WHERE run_id = %s ORDER BY name",
        (run.run_id,),
    ).fetchall()
    for name, payload in rows:
        yield from flatten(payload, key("artifact", run.label, name))


def entries(conn: Conn) -> Iterator[Entry]:
    """Every published number in the database, in no particular order."""
    labels = Labels(conn)
    for run in published_runs(conn):
        for table in TABLES:
            yield from table_entries(conn, table, run, labels)
        yield from backtest_entries(conn, run)
        yield from insight_entries(conn, run, labels)
        yield from artifact_entries(conn, run)


# ---------------------------------------------------------------------------
# Sorting, writing and reading


def sorted_entries(source: Iterable[Entry], chunk: int = CHUNK) -> Iterator[Entry]:
    """`source`, ordered by key, without holding it all in memory.

    Chunks are sorted in memory and spilled to disk; the merge reads one line
    from each at a time.
    """
    with tempfile.TemporaryDirectory(prefix="cdlhub-snapshot-") as tmp:
        spill: list[Path] = []
        buffer: list[Entry] = []

        def flush() -> None:
            buffer.sort(key=lambda e: e[0])
            path = Path(tmp) / f"chunk-{len(spill):04d}.ndjson.gz"
            with gzip.open(path, "wt", encoding="utf-8") as handle:
                for entry in buffer:
                    handle.write(json.dumps(entry) + "\n")
            spill.append(path)
            buffer.clear()

        for entry in source:
            buffer.append(entry)
            if len(buffer) >= chunk:
                flush()

        if not spill:
            buffer.sort(key=lambda e: e[0])
            yield from buffer
            return

        if buffer:
            flush()

        streams = [_read_lines(path) for path in spill]
        yield from heapq.merge(*streams, key=lambda e: e[0])


def _read_lines(path: Path) -> Iterator[Entry]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            pair = json.loads(line)
            yield (cast(str, pair[0]), pair[1])


def write(path: Path, conn: Conn) -> dict[str, Any]:
    """Build a snapshot at `path` and return its header, entry count included."""
    runs = published_runs(conn)
    header: dict[str, Any] = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "runs": [{"run_id": r.run_id, "model": r.model, "version": r.version} for r in runs],
    }
    count = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        handle.write(json.dumps(header) + "\n")
        for entry in sorted_entries(entries(conn)):
            handle.write(json.dumps(entry) + "\n")
            count += 1
    header["n_entries"] = count
    return header


def header_of(path: Path) -> dict[str, Any]:
    """The snapshot's first line: format, when it was taken, which runs it covers."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        first = handle.readline()
    if not first:
        raise ValueError(f"{path} is empty")
    header = cast(dict[str, Any], json.loads(first))
    if header.get("format") != FORMAT:
        raise ValueError(f"{path} is not a {FORMAT}")
    return header


def read(path: Path) -> Iterator[Entry]:
    """The snapshot's entries, still sorted, one line at a time."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        handle.readline()
        for line in handle:
            pair = json.loads(line)
            yield (cast(str, pair[0]), pair[1])
