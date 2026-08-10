"""Conditions a published run is not allowed to satisfy quietly.

    uv run python -m cdlhub_analytics.gates [--dsn DSN]

Every check here started as a counter in an artifact. A model met a degraded
condition, wrote the count, and carried on; the run reported success, the test
suite stayed green, and the site published the result. Three of those shipped at
once — a title whose series rollup never ran, a cohort that rated every player
1.00, and an axis wearing the name of the axis behind it — and none of them
could be found by reading the code, only by reading the numbers afterwards.

So each is stated here as a condition on the newest run of its model, checked
against the database rather than against a fixture, and failed loudly. The rule
these follow: a model may decline to publish something, but the decision has to
be visible from outside the model.

Exit codes are three-way on purpose. 0 is every gate met, 1 is a gate failed,
and 3 is "these gates did not run" — no database, or a model that has never been
fitted here. `checks.sh` reports 3 as a skip rather than a pass, because a gate
that cannot run is not a gate that passed.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, cast

import psycopg

from . import metrics, seriesdyn, style
from .db import connect
from .ratings import maplevel, player_rating
from .style import marker_column
from .writeback import latest_run_id

# The rating fits several feature-set versions under one model name, so its runs
# are found by version rather than by recency. The other models name themselves.
RATING_MODEL = "player_rating"
STYLE_MODEL = style.MODEL
DYNAMICS_MODEL = seriesdyn.MODEL

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CANNOT_RUN = 3

# The published bases, pinned: how many components each retains and the column
# each of those loads hardest on, under the same normalization that assigns the
# names. A basis moves when the metric layer gains or loses a column, which has
# happened once already — the CWL core grew from 21 columns to 26, began
# retaining a fifth component, and slid three published names one seat down
# without anything failing. Drift here is not necessarily wrong, but it is
# always a decision: re-read the loadings, rename what needs renaming, and
# update this table in the same commit.
PUBLISHED_BASES: dict[str, tuple[tuple[str, str], ...]] = {
    "core CWL": (
        ("volume", "kills"),
        ("survival", "deaths"),
        ("axis 3", "assists"),
        ("streak depth", "deep_streak_rate"),
        ("risk", "eight_plus_streaks"),
    ),
    "extended CWL": (
        ("volume", "kills"),
        ("survival", "deaths"),
        ("axis 3", "assists"),
        ("axis 4", "hill_time"),
        ("streak depth", "deep_streak_rate"),
        ("axis 6", "streak6"),
        ("axis 7", "snd_pickups"),
        ("axis 8", "four_piece"),
        ("axis 9", "streak7"),
        ("axis 10", "suicides"),
    ),
    "core CDL": (
        ("volume", "kills"),
        ("survival", "deaths"),
    ),
    "extended CDL": (
        ("volume", "kills"),
        ("survival", "deaths"),
        ("axis 3", "kd"),
        ("axis 4", "damage"),
        ("axis 5", "damage"),
    ),
}


class CannotRun(Exception):
    """The gate has nothing to read, which is neither a pass nor a failure."""


def artifact(conn: psycopg.Connection[Any], model: str, name: str) -> dict[str, Any]:
    run_id = latest_run_id(conn, model)
    if run_id is None:
        raise CannotRun(f"no {model} run in this database")
    row = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = %s",
        (run_id, name),
    ).fetchone()
    if row is None:
        raise CannotRun(f"{model} run {run_id} has no {name} artifact")
    payload = row[0]
    return cast(dict[str, Any], payload if isinstance(payload, dict) else json.loads(payload))


def rating_artifact(conn: psycopg.Connection[Any], name: str) -> dict[str, Any]:
    """The published feature-set version's artifact, not whichever ran last.

    Every run fits several versions under one model name, so the newest run id
    belongs to the last version fitted rather than the one the site reads.
    """
    row = conn.execute(
        "SELECT id FROM model_runs WHERE model = %s AND version = %s"
        " ORDER BY created_at DESC, id DESC LIMIT 1",
        (RATING_MODEL, player_rating.PUBLISHED_VERSION),
    ).fetchone()
    if row is None:
        raise CannotRun(f"no {RATING_MODEL} {player_rating.PUBLISHED_VERSION} run here")
    payload = conn.execute(
        "SELECT payload FROM model_artifacts WHERE run_id = %s AND name = %s",
        (row[0], name),
    ).fetchone()
    if payload is None:
        raise CannotRun(f"{RATING_MODEL} run {row[0]} has no {name} artifact")
    got = payload[0]
    return cast(dict[str, Any], got if isinstance(got, dict) else json.loads(got))


def rotation_failures(rollup: dict[str, Any], dynamics: dict[str, Any]) -> list[str]:
    """A title with no declared rotation is rated map by map and then dropped
    from every series rollup — silently, at the scale of an entire era. The
    counter read 1,633 series for two years before anybody read it."""
    bad = []
    n = int(rollup["n_series_no_rotation"])
    if n:
        bad.append(f"map_elo: {n:,} series whose title declares no rotation (maplevel.THIRD_MAP)")
    n = int(dynamics["n_no_rotation"])
    if n:
        bad.append(f"series_dynamics: {n:,} series whose title declares no rotation")
    return bad


def cohort_failures(posterior: dict[str, Any]) -> list[str]:
    """τ² at zero means the cohort's players are indistinguishable, and the
    ratings that follow are 1.00 for everyone. The model now withholds those
    rather than publishing them, but a collapse is still a fit that failed and
    the release should stop on it rather than quietly lose a season."""
    bad = []
    for c in posterior["cohorts"]:
        if c["collapsed"]:
            bad.append(
                f"collapsed cohort: {c['year']} {c['title']} {c['mode']} (tau={c['tau']:.4f})"
            )
        if c["stalled"]:
            bad.append(f"EM ran out of iterations: {c['year']} {c['title']} {c['mode']}")
    return bad


def basis_failures(payload: dict[str, Any]) -> list[str]:
    """Axis names are assigned by what a component loads on, so a basis that
    moves renames axes on every player page that reads them. That is allowed to
    happen and not allowed to happen unnoticed."""
    bad = []
    fitted = {b["basis"]: b for b in payload["bases"]}
    for name in sorted(set(fitted) | set(PUBLISHED_BASES)):
        pinned = PUBLISHED_BASES.get(name)
        basis = fitted.get(name)
        if pinned is None:
            bad.append(f"basis '{name}' is published and not pinned in gates.PUBLISHED_BASES")
            continue
        if basis is None:
            bad.append(f"basis '{name}' is pinned and no longer published")
            continue
        axes = basis["axes"]
        if len(axes) != len(pinned):
            bad.append(f"basis '{name}': retains {len(axes)} components, pinned at {len(pinned)}")
            continue
        for i, (axis, (axis_name, column)) in enumerate(zip(axes, pinned, strict=True), start=1):
            top = marker_column(axis["loadings"][0]["column"])
            if top != column:
                bad.append(f"basis '{name}' axis {i}: loads hardest on {top}, pinned to {column}")
            elif axis["name"] != axis_name:
                bad.append(f"basis '{name}' axis {i}: named '{axis['name']}', pinned '{axis_name}'")
    return bad


def mode_naming_failures(scored: list[tuple[str, int]], named: set[str]) -> list[str]:
    """A mode the metric layer scores and `game_modes` does not name reaches the
    site as its raw slug, in a list of properly named ones. The site derives
    every mode label from that table, so an unnamed mode is not a missing label
    on one page — it is a missing label everywhere, and nothing else reports it."""
    return [
        f"{slug}: {rows:,} scored rows and no name in game_modes"
        for slug, rows in scored
        if slug not in named
    ]


def scored_modes(conn: psycopg.Connection[Any]) -> tuple[list[tuple[str, int]], set[str]]:
    """Modes carrying rows in the newest metric layer run, and the named set."""
    run_id = latest_run_id(conn, metrics.MODEL)
    if run_id is None:
        raise CannotRun(f"no {metrics.MODEL} run in this database")
    rows = conn.execute(
        "SELECT gm.slug, count(*) FROM ("
        "  SELECT mode_id FROM player_metric_season WHERE run_id = %s AND mode_id IS NOT NULL"
        "  UNION ALL"
        "  SELECT mode_id FROM team_metric_season WHERE run_id = %s AND mode_id IS NOT NULL"
        ") s JOIN game_modes gm ON gm.id = s.mode_id GROUP BY gm.slug",
        (run_id, run_id),
    ).fetchall()
    named = conn.execute(
        "SELECT slug FROM game_modes WHERE name IS NOT NULL AND name <> ''"
    ).fetchall()
    return [(str(r[0]), int(r[1])) for r in rows], {str(r[0]) for r in named}


def run_gates(conn: psycopg.Connection[Any]) -> list[tuple[str, list[str]]]:
    """Every gate against the newest run of its model. The predicates above take
    payloads rather than a connection, so the same conditions are exercised on
    fabricated artifacts in the test suite — a gate nothing has ever failed is
    indistinguishable from a gate that cannot fail."""
    return [
        (
            "rotation",
            rotation_failures(
                artifact(conn, maplevel.MODEL, "series_rollup"),
                artifact(conn, DYNAMICS_MODEL, "series_dynamics"),
            ),
        ),
        ("cohort", cohort_failures(rating_artifact(conn, "rating_posterior"))),
        ("basis", basis_failures(artifact(conn, STYLE_MODEL, "player_style"))),
        ("mode naming", mode_naming_failures(*scored_modes(conn))),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cdlhub_analytics.gates", description=__doc__)
    parser.add_argument("--dsn")
    args = parser.parse_args(argv)

    try:
        conn = connect(args.dsn)
    except psycopg.Error as exc:
        print(f"gates did not run: no database ({exc.__class__.__name__})", file=sys.stderr)
        return EXIT_CANNOT_RUN

    with conn:
        try:
            results = run_gates(conn)
        except CannotRun as exc:
            print(f"gates did not run: {exc}", file=sys.stderr)
            return EXIT_CANNOT_RUN

    failures: list[str] = []
    for name, found in results:
        for line in found:
            print(f"{name}: {line}", file=sys.stderr)
        failures.extend(found)
        if not found:
            print(f"{name}: ok")

    return EXIT_FAILED if failures else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
