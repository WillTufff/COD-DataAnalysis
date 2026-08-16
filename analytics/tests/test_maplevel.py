"""Map-level and per-mode team ratings.

The tests that matter here are the leakage ones. A map-level rating updates
mid-series, so the series rollup is one careless line away from predicting a
series from its own first map, and the permutation null is one careless line
away from shuffling nothing.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from cdlhub_analytics.ratings import maplevel as ml
from cdlhub_analytics.ratings.elo import INITIAL

START = datetime(2018, 1, 20, tzinfo=UTC)


def _map(
    game_id: int,
    series_id: int,
    team1: int,
    team2: int,
    team1_won: bool,
    mode: str = "hardpoint",
    ordinal: int = 1,
    event_id: int = 1,
    day: int = 0,
    title: str = "WWII",
) -> ml.MapResult:
    return ml.MapResult(
        game_id=game_id,
        series_id=series_id,
        team1=team1,
        team2=team2,
        team1_won=team1_won,
        mode=mode,
        title=title,
        played_at=START + timedelta(days=day),
        event_id=event_id,
        ordinal=ordinal,
    )


def _series(
    series_id: int, team1: int, team2: int, results: list[bool], **kw: object
) -> list[ml.MapResult]:
    """One series as a run of maps, modes taken from the WWII rotation.

    Past the fifth map the rotation repeats, which is close enough to how the
    long formats extend and keeps a seven-map fixture from needing its own
    declaration — the rollup skips those series either way."""
    rotation = ml.ROTATION["WWII"]
    return [
        _map(
            game_id=series_id * 10 + i,
            series_id=series_id,
            team1=team1,
            team2=team2,
            team1_won=won,
            mode=rotation[i % len(rotation)],
            ordinal=i + 1,
            **kw,  # type: ignore[arg-type]
        )
        for i, won in enumerate(results)
    ]


# ----------------------------------------------------------------- the state


def test_unrated_teams_are_a_coin_flip_in_every_arm() -> None:
    state = ml.State()
    for arm in ml.ARMS:
        assert state.predict(arm, 1, 2, "hardpoint") == 0.5


def test_blend_starts_at_global_and_moves_toward_the_mode_rating() -> None:
    """w = n/(n+blend_k): with no mode history the blend *is* the global rating."""
    state = ml.State(blend_k=40.0)
    assert state.blend_weight(1, "hardpoint") == 0.0
    assert state.blend_rating(1, "hardpoint") == state.global_rating(1)

    state.n_maps[(1, "hardpoint")] = 40
    assert state.blend_weight(1, "hardpoint") == pytest.approx(0.5)
    state.glob[1] = 1600.0
    state.per_mode[(1, "hardpoint")] = 1400.0
    assert state.blend_rating(1, "hardpoint") == pytest.approx(1500.0)

    state.n_maps[(1, "hardpoint")] = 3960  # w = 0.99
    assert state.blend_rating(1, "hardpoint") == pytest.approx(1402.0)


def test_mode_state_is_kept_apart() -> None:
    """A Hardpoint result must not move the Search rating."""
    state = ml.State(k=16.0)
    state.update(1, 2, "hardpoint", a_won=True)
    assert state.mode_rating(1, "hardpoint") > INITIAL
    assert state.mode_rating(1, "search-and-destroy") == INITIAL
    assert state.global_rating(1) > INITIAL


def test_global_arm_ignores_the_mode_label() -> None:
    a, b = ml.State(k=16.0), ml.State(k=16.0)
    a.update(1, 2, "hardpoint", a_won=True)
    b.update(1, 2, "control", a_won=True)
    assert a.global_rating(1) == b.global_rating(1)


def test_unknown_arm_is_an_error() -> None:
    with pytest.raises(ValueError, match="unknown arm"):
        ml.State().rating("mode-ish", 1, "hardpoint")


# ----------------------------------------------------------------- the rollup


def test_bo5_of_fair_maps_is_a_coin_flip() -> None:
    assert ml._bo5([0.5] * 5) == pytest.approx(0.5)


def test_bo5_amplifies_an_edge() -> None:
    """A best-of-five is a majority vote, so it is sharper than one map."""
    p = ml._bo5([0.6] * 5)
    assert p > 0.6
    assert p == pytest.approx(0.68256, abs=1e-5)


def test_bo5_uses_each_maps_own_probability() -> None:
    """A Hardpoint specialist against a Search specialist: 3 of the 5 rotation
    maps are Hardpoint or its repeat, so the mode split is not symmetric."""
    lopsided = ml._bo5([0.9, 0.1, 0.5, 0.9, 0.1])
    assert lopsided == pytest.approx(ml._bo5([0.9, 0.9, 0.5, 0.1, 0.1]))
    assert lopsided > 0.5  # two of the three non-Search maps are winnable


def test_series_rollup_never_reads_a_map_inside_its_own_series() -> None:
    """The rollup must be identical whichever way the series actually went.

    Same two teams, same history, same series id — only the results inside the
    series differ. A rollup that changed would be reading the thing it predicts.
    """
    prior = _series(1, 1, 2, [True, True, True], day=0)
    sweep_win = prior + _series(2, 1, 2, [True, True, True], day=1)
    long_win = prior + _series(2, 1, 2, [False, True, False, True, True], day=1)

    a = ml.walk_forward(sweep_win, k=16.0)
    b = ml.walk_forward(long_win, k=16.0)
    for arm in ml.ARMS:
        assert a.series_preds[arm][2].p == pytest.approx(b.series_preds[arm][2].p)
        assert a.series_preds[arm][2].won is True


def test_undecided_series_are_not_rolled_up_but_their_maps_still_rate() -> None:
    maps = _series(1, 1, 2, [True, False], day=0)  # 1-1, no winner
    walk = ml.walk_forward(maps, k=16.0)
    assert walk.series_preds["global"] == {}
    assert len(walk.map_preds["global"]) == 2
    assert walk.n_series_rolled == 0
    assert walk.n_series_other_format == 1


def test_a_race_to_four_is_counted_not_rolled_up_as_a_best_of_five() -> None:
    """A handful of series are best-of-seven or longer, and their recorded
    `best_of` cannot be used to find them — the column says seven on five-map
    scorelines. The winner's map count can, and a race _bo5 did not model is
    skipped and counted rather than scored against the wrong question."""
    maps = _series(1, 1, 2, [True, False, True, False, True, False, True], day=0)  # 4-3
    walk = ml.walk_forward(maps, k=16.0)
    assert walk.n_series_rolled == 0
    assert walk.n_series_other_format == 1
    assert len(walk.map_preds["global"]) == 7, "every map still rates"


def test_a_title_with_no_declared_rotation_is_counted_not_guessed() -> None:
    maps = [_map(1, 1, 1, 2, True, title="MW2"), _map(2, 1, 1, 2, True, title="MW2")]
    walk = ml.walk_forward(maps, k=16.0)
    assert walk.n_series_no_rotation == 1
    assert walk.series_preds["global"] == {}


def test_every_title_the_rollup_can_meet_declares_a_rotation() -> None:
    """The counter above exists for a title nobody has declared yet, not as a
    standing state for half the archive: every title in ROTATION is one the
    walk-forward pass will actually encounter, and the data-backed check below
    holds each declaration to what the league played."""
    assert set(ml.ROTATION) == set(ml.THIRD_MAP) | set(ml.WRITTEN_OUT)
    assert not set(ml.ROTATION) & ml.NO_FIXED_ROTATION
    for title, rotation in ml.ROTATION.items():
        assert len(rotation) == 2 * ml.SERIES_WINS_NEEDED - 1, title
    # The shorthand only says which map is third, so it can only describe a
    # title whose last two maps repeat its first two. A title that breaks that
    # is written out in full instead, which is what WRITTEN_OUT is for.
    for title in ml.THIRD_MAP:
        rotation = ml.ROTATION[title]
        assert rotation[3:] == rotation[:2], f"{title}: maps 4 and 5 repeat maps 1 and 2"


def test_rollup_predictions_carry_their_series_id() -> None:
    """`significance.model_gaps` pairs on series_id; without it the rollup could
    not be compared against the published series table at all."""
    walk = ml.walk_forward(_series(7, 1, 2, [True, True, True]), k=16.0)
    pred = walk.series_preds["blend"][7]
    assert pred.series_id == 7


# ------------------------------------------------------------ walk-forward


def test_map_predictions_precede_their_own_update() -> None:
    maps = [_map(i, i, 1, 2, True, day=i) for i in range(1, 5)]
    walk = ml.walk_forward(maps, k=16.0)
    ps = [walk.map_preds["global"][i].p for i in range(1, 5)]
    assert ps[0] == 0.5  # nothing known yet
    assert ps == sorted(ps)  # each win informs the next prediction, never its own


def test_lineage_carries_the_rating_across_a_rebrand() -> None:
    """Team 3 is team 1 under a new name: its curve must continue, not restart.

    Both fits favour team 3 in map 2, because team 2 lost map 1 either way. The
    lineage is what adds team 1's *win* on top of team 2's loss, so the merged
    fit has to be the more confident of the two.
    """
    maps = [_map(1, 1, 1, 2, True, day=0), _map(2, 2, 3, 2, True, day=1)]
    plain = ml.walk_forward(maps, k=16.0)
    merged = ml.walk_forward(maps, k=16.0, lineage={3: 1})
    assert merged.map_preds["global"][2].p > plain.map_preds["global"][2].p > 0.5


def test_modes_override_replaces_the_label_positionally() -> None:
    """What the permutation null relies on: same maps, relabelled."""
    maps = _series(1, 1, 2, [True, True, True])
    walk = ml.walk_forward(maps, k=16.0, modes=["control"] * 3)
    assert walk.state.n_maps.get((1, "control")) == 3
    assert walk.state.n_maps.get((1, "hardpoint")) is None


# ------------------------------------------------------------- specialization


def _league(n_events: int = 12) -> list[ml.MapResult]:
    """Four teams, round robin, every event, WWII rotation.

    Twelve events by default so the once-per-series Capture the Flag cell clears
    MIN_MODE_MAPS too — a league where only two of three modes qualify would
    test the cell floor rather than the null.
    """
    maps: list[ml.MapResult] = []
    gid = 0
    sid = 0
    for e in range(n_events):
        for a, b in ((1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)):
            sid += 1
            for i, mode in enumerate(ml.ROTATION["WWII"]):
                gid += 1
                # Team 1 wins Hardpoint, loses Search: a real specialist.
                if mode == "hardpoint":
                    won = a == 1
                elif mode == "search-and-destroy":
                    won = b == 1
                else:
                    won = (gid % 2) == 0
                maps.append(
                    _map(gid, sid, a, b, won, mode=mode, ordinal=i + 1, event_id=e, day=e * 7)
                )
    return maps


def test_specialization_finds_a_planted_specialist() -> None:
    art = ml.specialization(_league(), n_permutations=40)
    assert art["available"]
    assert art["observed_sd"] > art["null_hi"]
    assert art["exceeds_null"]
    assert art["p_value"] < 0.05


def test_specialization_reports_the_null_when_mode_carries_nothing() -> None:
    """Same league, results independent of mode: the spread must not clear the
    null it is measured against.

    The winner is decided by the series, so every map inside a series goes the
    same way and no mode carries information. Deciding it off the map id instead
    would leave a residual mode signal — map ids cycle with the rotation.
    """
    maps = [
        _map(
            m.game_id,
            m.series_id,
            m.team1,
            m.team2,
            m.series_id % 2 == 0,
            mode=m.mode,
            ordinal=m.ordinal,
            event_id=m.event_id,
            day=m.event_id * 7,
        )
        for m in _league()
    ]
    art = ml.specialization(maps, n_permutations=40)
    assert art["available"]
    assert not art["exceeds_null"]
    assert art["p_value"] > 0.05


def test_specialization_p_value_can_never_be_zero() -> None:
    """Add-one, so a spread no permutation reaches reports 1/(B+1), not 0."""
    art = ml.specialization(_league(), n_permutations=20)
    assert art["p_value"] == pytest.approx(1.0 / (art["null_permutations"] + 1), abs=1e-4)


def test_specialization_needs_cells_to_say_anything() -> None:
    art = ml.specialization(_series(1, 1, 2, [True, True, True]), n_permutations=5)
    assert not art["available"]


def test_mode_table_reports_the_gap_from_the_teams_own_global_rating() -> None:
    walk = ml.walk_forward(_league())
    rows = ml.mode_table(walk.state, {1: "Alpha", 2: "Bravo", 3: "Charlie", 4: "Delta"})
    assert rows
    assert all(r["n_maps"] >= ml.MIN_MODE_MAPS for r in rows)
    # Sorted by the size of the gap, largest first.
    gaps = [abs(r["delta"]) for r in rows]
    assert gaps == sorted(gaps, reverse=True)
    top = rows[0]
    assert top["delta"] == pytest.approx(top["rating"] - top["global_rating"], abs=0.11)
    alpha_hp = next(r for r in rows if r["team"] == "Alpha" and r["mode"] == "hardpoint")
    alpha_snd = next(r for r in rows if r["team"] == "Alpha" and r["mode"] == "search-and-destroy")
    assert alpha_hp["delta"] > 0 > alpha_snd["delta"]


def test_cells_below_the_floor_are_left_out() -> None:
    walk = ml.walk_forward(_league())
    assert ml.mode_table(walk.state, {}, min_maps=10_000) == []


# -------------------------------------------------------------------- sweep


def test_sweep_scores_every_arm_at_every_k() -> None:
    grid = ml.sweep(_league(4))
    assert len(grid["k_grid"]) == len(ml.SWEEP_KS)
    assert all(arm in cell for cell in grid["k_grid"] for arm in ml.ARMS)
    assert grid["published"] == {"k": ml.K, "blend_k": ml.BLEND_K}
    # The grid is sensitivity, so it must state its own verdict rather than
    # leave a reader to eyeball twenty-four Brier scores.
    assert isinstance(grid["mode_beats_global_at_every_k"], bool)
    assert not (grid["mode_beats_global_at_every_k"] and grid["global_beats_mode_at_every_k"])


def test_by_mode_partitions_the_maps() -> None:
    maps = _league(4)
    walk = ml.walk_forward(maps)
    rows = ml.by_mode(maps, walk.map_preds)
    assert sum(r["n_maps"] for r in rows) == len(maps)
    assert {r["mode"] for r in rows} == {m.mode for m in maps}
    assert all(math.isfinite(r["arms"]["global"]["brier"]) for r in rows)
    # "mode" is both a mode slug and an arm name; the slug must survive.
    assert all(isinstance(r["mode"], str) for r in rows)


# ------------------------------------------- the declaration against the data


ROTATION_MIN_SHARE = 0.95
ROTATION_MIN_MAPS = 25
# The pre-2017 circuit had no single rulebook: MLG, UMG, ESWC and Gfinity each
# ran their own map order, so a title's rotation holds across the era at a lower
# share than a league-mandated one does. The lowest measured slot is Black Ops 3
# map 2 at 0.944, where Uplink took the slot at 5.2% of series.
PRE_2017_MIN_SHARE = 0.90
PRE_2017_TITLES = frozenset({"BO2", "GHO", "AW", "BO3"})


@pytest.fixture
def db_conn() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("DATABASE_URL", "postgres://cdlhub:cdlhub@localhost:54329/cdlhub")
    try:
        conn = psycopg.connect(dsn, connect_timeout=2)
    except Exception:  # noqa: BLE001 - any connection failure means no DB here
        pytest.skip("no database reachable")
    try:
        yield conn
    finally:
        conn.close()


def test_every_declared_rotation_is_what_the_league_played(db_conn: Any) -> None:
    """A declared constant is only trustworthy if something holds it to the
    archive. The rotation still may not be *derived* from the series being
    predicted — that would leak the result — so it is declared above and checked
    here, which is the same split the metric layer draws between an availability
    it measures and a rotation both teams knew before the series started."""
    rows = db_conn.execute(
        "SELECT t.short_name, g.ordinal, gm.slug, count(*)"
        " FROM games g"
        " JOIN series s ON s.id = g.series_id"
        " JOIN events ev ON ev.id = s.event_id"
        " JOIN seasons se ON se.id = ev.season_id"
        " JOIN titles t ON t.id = se.title_id"
        " JOIN game_modes gm ON gm.id = g.mode_id"
        " WHERE g.ordinal <= %s GROUP BY 1, 2, 3",
        (2 * ml.SERIES_WINS_NEEDED - 1,),
    ).fetchall()
    if not rows:
        pytest.skip("no games loaded")

    played: dict[tuple[str, int], dict[str, int]] = defaultdict(dict)
    for title, ordinal, slug, n in rows:
        played[(title, ordinal)][slug] = n

    # Every title with maps in the archive has to be declared, or the rollup
    # silently skips its whole era — which is the defect this test exists for.
    assert {t for t, _ in played} <= set(ml.ROTATION) | ml.NO_FIXED_ROTATION

    for (title, ordinal), modes in sorted(played.items()):
        total = sum(modes.values())
        if total < ROTATION_MIN_MAPS:  # a handful of maps decides nothing
            continue
        if title in ml.NO_FIXED_ROTATION:  # declared as having no order to hold to
            continue
        declared = ml.ROTATION[title][ordinal - 1]
        share = modes.get(declared, 0) / total
        floor = PRE_2017_MIN_SHARE if title in PRE_2017_TITLES else ROTATION_MIN_SHARE
        assert share >= floor, (
            f"{title} map {ordinal}: declared {declared} at {share:.3f} of {total} maps,"
            f" played {sorted(modes.items(), key=lambda kv: -kv[1])}"
        )
