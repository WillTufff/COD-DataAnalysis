"""Run pruning: a rerun must not leave superseded copies of the league behind.

DB-backed, against a scratch schema so the real model_runs is never touched.
Skips when no database is reachable, like the other DB-backed tests; the data
gate in CI runs it for real.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from cdlhub_analytics.writeback import prune_superseded

DDL = """
DROP SCHEMA IF EXISTS writeback_test CASCADE;
CREATE SCHEMA writeback_test;
SET search_path TO writeback_test;
CREATE TABLE model_runs (
  id      serial PRIMARY KEY,
  model   text NOT NULL,
  version text NOT NULL
);
CREATE TABLE outputs (
  run_id int REFERENCES model_runs(id) ON DELETE CASCADE,
  note   text
);
"""


@pytest.fixture
def conn() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("DATABASE_URL", "postgres://cdlhub:cdlhub@localhost:54329/cdlhub")
    try:
        c = psycopg.connect(dsn, connect_timeout=2)
    except Exception:  # noqa: BLE001 - any connection failure means no DB here
        pytest.skip("no database reachable")
    try:
        c.execute(DDL)
        yield c
    finally:
        c.rollback()
        c.execute("DROP SCHEMA IF EXISTS writeback_test CASCADE")
        c.commit()
        c.close()


def add(conn: Any, model: str, version: str, note: str = "row") -> int:
    run_id = conn.execute(
        "INSERT INTO model_runs (model, version) VALUES (%s, %s) RETURNING id",
        (model, version),
    ).fetchone()[0]
    conn.execute("INSERT INTO outputs (run_id, note) VALUES (%s, %s)", (run_id, note))
    return int(run_id)


def models(conn: Any) -> list[tuple[str, str]]:
    return [
        (r[0], r[1])
        for r in conn.execute("SELECT model, version FROM model_runs ORDER BY id").fetchall()
    ]


def test_prunes_superseded_versions_of_a_produced_model(conn: Any) -> None:
    add(conn, "insights", "1.0.0")
    add(conn, "insights", "1.1.0")
    keep = add(conn, "insights", "1.2.0")

    removed = prune_superseded(conn, {"insights": [keep]})

    assert removed == {"insights": 2}
    assert models(conn) == [("insights", "1.2.0")]


def test_outputs_of_a_pruned_run_go_with_it(conn: Any) -> None:
    stale = add(conn, "insights", "1.0.0", note="stale")
    keep = add(conn, "insights", "1.1.0", note="fresh")

    prune_superseded(conn, {"insights": [keep]})

    rows = conn.execute("SELECT note FROM outputs").fetchall()
    assert [r[0] for r in rows] == ["fresh"]
    assert stale not in [r[0] for r in conn.execute("SELECT id FROM model_runs").fetchall()]


def test_keeps_every_run_id_handed_over(conn: Any) -> None:
    """player_rating publishes all its feature-set versions as baselines."""
    a = add(conn, "player_rating", "1.0.0")
    b = add(conn, "player_rating", "2.0.0")
    c = add(conn, "player_rating", "2.1.0")

    assert prune_superseded(conn, {"player_rating": [a, b, c]}) == {}
    assert len(models(conn)) == 3


def test_leaves_untouched_models_alone(conn: Any) -> None:
    """The pipeline writes kill_feed_reconciliation; run_all must not prune it."""
    add(conn, "kill_feed_reconciliation", "1.0.0")
    keep = add(conn, "insights", "1.2.0")

    prune_superseded(conn, {"insights": [keep]})

    assert ("kill_feed_reconciliation", "1.0.0") in models(conn)


def test_empty_keep_list_prunes_nothing(conn: Any) -> None:
    """A model that produced no run must not be emptied out."""
    add(conn, "insights", "1.0.0")
    assert prune_superseded(conn, {"insights": []}) == {}
    assert len(models(conn)) == 1
