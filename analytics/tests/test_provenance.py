"""The reproducibility record, and the property it exists to defend.

A stored backtest that cannot be reproduced exactly is a claim, not a record.
Two things have to hold for it to be a record: every stochastic stage draws from
a seed the run wrote down, and refitting the same data twice gives the same
numbers back — bit for bit, not to within a tolerance.

The seed inventory is checked by walking the package rather than by listing the
modules here, so a new stochastic stage that forgets to declare its seed fails
this file instead of silently publishing an unreproducible number.
"""

from __future__ import annotations

import ast
import re
from datetime import date
from pathlib import Path

import numpy as np

from cdlhub_analytics import provenance
from cdlhub_analytics.maprows import MapRow
from cdlhub_analytics.ratings import rapm
from cdlhub_analytics.regress import fit_logistic_l2, matrix_hash

PACKAGE = Path(provenance.__file__).parent

# A module-level constant naming a seed. Anything matching this is expected in
# `provenance.SEEDS`.
SEED_NAME = re.compile(r"^[A-Z_]*SEED$")


def _seed_constants() -> dict[str, set[str]]:
    """Every module-level seed constant in the package, by module name."""
    found: dict[str, set[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {
            target.id
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name) and SEED_NAME.match(target.id)
        }
        if names:
            found[path.stem] = names
    return found


def test_every_module_that_owns_a_seed_declares_it() -> None:
    owners = set(_seed_constants())
    # `hierarchical` reuses the rating's seed rather than owning one, and the
    # provenance block is keyed by owner.
    undeclared = owners - set(provenance.SEEDS) - {"hierarchical"}
    assert undeclared == set(), f"seeds not recorded in provenance.SEEDS: {sorted(undeclared)}"


def test_the_provenance_block_carries_seeds_an_environment_and_a_lock_hash() -> None:
    block = provenance.block()
    assert block["seeds"] == provenance.SEEDS
    assert set(block["environment"]) >= {"python", "numpy", "platform"}
    # The lockfile is committed, so its hash is not allowed to be missing.
    assert isinstance(block["lock_sha256"], str)
    assert len(block["lock_sha256"]) == 64


def test_an_unchanged_lockfile_hashes_the_same_twice() -> None:
    assert provenance.lock_sha256() == provenance.lock_sha256()


# --------------------------------------------------------------- matrix hashes


def test_the_same_matrix_hashes_the_same_and_a_changed_cell_does_not() -> None:
    x = np.arange(12, dtype=float).reshape(4, 3)
    assert matrix_hash(x) == matrix_hash(x.copy())
    y = x.copy()
    y[2, 1] += 1e-9
    assert matrix_hash(x) != matrix_hash(y)


def test_the_hash_ignores_dtype_and_layout_but_not_column_names() -> None:
    x = np.arange(12, dtype=np.float32).reshape(4, 3)
    assert matrix_hash(x) == matrix_hash(np.asfortranarray(x.astype(np.float64)))
    assert matrix_hash(x, ["a", "b", "c"]) != matrix_hash(x, ["a", "b", "d"])


# ---------------------------------------------------------------- determinism


def _maps(n_games: int = 60) -> list[MapRow]:
    """A small synthetic league: four teams, four players each, alternating."""
    rows: list[MapRow] = []
    for game in range(n_games):
        left, right = (game % 4) + 1, ((game + 1) % 4) + 1
        if left == right:
            continue
        won = game % 3 != 0
        for side, team in ((0, left), (1, right)):
            for slot in range(4):
                rows.append(
                    MapRow(
                        player_id=team * 10 + slot,
                        team_id=team,
                        game_id=game,
                        series_id=game,
                        season_id=1,
                        mode_id=1,
                        mode_slug="hp",
                        title="MWIII",
                        event_id=1,
                        played_at=date(2024, 1, 1 + (game % 27)),
                        duration_s=600.0,
                        winner_team_id=left if won else right,
                        values={"kills": 20.0 + slot + side, "deaths": 20.0},
                        team_kills=80.0,
                        team_hill_time=120.0,
                    )
                )
    return rows


def test_the_same_fit_run_twice_returns_the_same_numbers() -> None:
    rows = _maps()
    first, second = rapm.fit(rows), rapm.fit(rows)
    assert first is not None and second is not None
    assert first.design_hash == second.design_hash
    assert [(p.player_id, p.coef, p.se) for p in first.players] == [
        (p.player_id, p.coef, p.se) for p in second.players
    ]


def test_a_fit_on_reordered_rows_sees_the_same_design() -> None:
    """Row order is an accident of the query, never of the data."""
    rows = _maps()
    assert rapm.fit(rows) is not None
    shuffled = list(reversed(rows))
    first, second = rapm.fit(rows), rapm.fit(shuffled)
    assert first is not None and second is not None
    assert first.design_hash == second.design_hash


def test_the_solver_itself_is_deterministic() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 5))
    y = (x[:, 0] + rng.normal(size=200) > 0).astype(float)
    a, b = fit_logistic_l2(x, y, l2=1.0), fit_logistic_l2(x, y, l2=1.0)
    assert a.intercept == b.intercept
    assert np.array_equal(a.weights, b.weights)
