"""Fixture tests for segment win probability. No database required.

Every map here is hand-built, so what is asserted is what the module claims in
its docstring rather than whatever the CDL era happens to contain: the anomaly
rules, the antisymmetry of the table, the exactness of the race baseline, and
the walk-forward split never letting a map score itself.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from cdlhub_analytics import segmentwp
from cdlhub_analytics.segmentwp import (
    HILL_TARGET,
    KIND_CONTROL,
    KIND_HILL,
    KIND_SND,
    RACE,
    RaceBaseline,
    SegmentMap,
    StateTable,
    _build_hill,
    _build_rounds,
    _race_grid,
    cell,
    competitiveness,
    is_terminal,
    state_rows,
    two_era_snd,
    walk_forward,
    win_type_artifact,
)

DAY0 = date(2024, 1, 1)
TEAM_A, TEAM_B = 10, 20


def rounds_map(
    winners: list[int],
    *,
    kind: str = KIND_SND,
    game_id: int = 1,
    event_id: int = 1,
    day: int = 0,
    win_types: list[str | None] | None = None,
) -> SegmentMap:
    """A round map from the sequence of side indices that won each round."""
    score = [0, 0]
    steps = []
    for side in winners:
        score[side] += 1
        steps.append((score[0], score[1]))
    return SegmentMap(
        game_id=game_id,
        kind=kind,
        year=2024,
        event_id=event_id,
        played_at=DAY0 + timedelta(days=day),
        teams=(TEAM_A, TEAM_B),
        steps=tuple(steps),
        win_types=tuple(win_types or [None] * len(winners)),
        winners=tuple(winners),
        won=1.0 if score[0] > score[1] else 0.0,
    )


def hill_map(pairs: list[tuple[int, int]], *, game_id: int = 1, day: int = 0) -> SegmentMap:
    return SegmentMap(
        game_id=game_id,
        kind=KIND_HILL,
        year=2024,
        event_id=1,
        played_at=DAY0 + timedelta(days=day),
        teams=(TEAM_A, TEAM_B),
        steps=tuple(pairs),
        win_types=(None,) * len(pairs),
        winners=(),
        won=1.0 if pairs[-1][0] > pairs[-1][1] else 0.0,
    )


def seg(
    team: int, *, score: int | None = None, won: bool | None = None, wt: str | None = None
) -> tuple[int, int | None, bool | None, str | None]:
    return (team, score, won, wt)


# --- the anomaly rules -------------------------------------------------------


def test_a_round_needs_both_sides_and_exactly_one_winner() -> None:
    ordinals = {
        1: [seg(TEAM_A, won=True), seg(TEAM_B, won=False)],
        2: [seg(TEAM_A, won=False), seg(TEAM_B, won=True)],
        3: [seg(TEAM_A, won=True)],  # one-sided: the map stops here
        4: [seg(TEAM_A, won=False), seg(TEAM_B, won=True)],
    }
    built = _build_rounds(KIND_SND, ordinals)
    assert not isinstance(built, str)
    _teams, steps, _types, _winners, dropped = built
    assert steps == ((1, 0), (1, 1))
    assert dropped == 2


def test_a_round_claimed_by_neither_side_truncates_the_map() -> None:
    ordinals = {
        1: [seg(TEAM_A, won=True), seg(TEAM_B, won=False)],
        2: [seg(TEAM_A, won=False), seg(TEAM_B, won=False)],
    }
    built = _build_rounds(KIND_SND, ordinals)
    assert not isinstance(built, str)
    assert built[1] == ((1, 0),)
    assert built[4] == 1


def test_a_map_with_only_one_team_is_rejected_by_its_own_reason() -> None:
    ordinals = {1: [seg(TEAM_A, won=True)], 2: [seg(TEAM_A, won=True)]}
    assert _build_rounds(KIND_SND, ordinals) == "only_one_team_in_the_source"


def test_a_decreasing_cumulative_score_is_rejected() -> None:
    ordinals = {
        1: [seg(TEAM_A, score=60), seg(TEAM_B, score=0)],
        2: [seg(TEAM_A, score=40), seg(TEAM_B, score=55)],
    }
    assert _build_hill(KIND_HILL, ordinals) == "the_cumulative_score_decreases"


def test_a_hill_score_past_the_target_is_rejected() -> None:
    ordinals = {1: [seg(TEAM_A, score=HILL_TARGET + 1), seg(TEAM_B, score=0)]}
    assert _build_hill(KIND_HILL, ordinals) == "the_cumulative_score_passes_250"


def test_a_hill_carries_the_score_of_a_side_that_did_not_move() -> None:
    ordinals = {
        1: [seg(TEAM_A, score=60), seg(TEAM_B, score=0)],
        2: [seg(TEAM_A, score=60), seg(TEAM_B, score=58)],
    }
    built = _build_hill(KIND_HILL, ordinals)
    assert not isinstance(built, str)
    assert built[1] == ((60, 0), (60, 58))


# --- the state table ---------------------------------------------------------


def test_the_table_is_antisymmetric_and_ties_are_a_coin_flip() -> None:
    maps = [rounds_map([0, 1, 0, 1, 0, 1, 0, 0, 0], game_id=i) for i in range(20)]
    maps += [rounds_map([1, 0, 1, 0, 1, 0, 1, 1, 1], game_id=100 + i) for i in range(20)]
    table = StateTable(KIND_SND, state_rows(maps, KIND_SND))
    for own in range(RACE[KIND_SND]):
        assert table.p(own, own) == 0.5
    for own in range(RACE[KIND_SND]):
        for opp in range(RACE[KIND_SND]):
            assert table.p(own, opp) + table.p(opp, own) == 1.0


def test_terminal_states_are_certain_and_excluded_from_training() -> None:
    maps = [rounds_map([0] * 6)]
    rows = state_rows(maps, KIND_SND)
    assert all(not is_terminal(KIND_SND, own, opp) for _c, own, opp, _y in rows)
    table = StateTable(KIND_SND, rows)
    assert table.p(6, 3) == 1.0
    assert table.p(3, 6) == 0.0


def test_hill_states_bucket_to_the_declared_widths() -> None:
    assert cell(KIND_HILL, 0, 0) == (0, 0)
    assert cell(KIND_HILL, 249, 0) == (9, 5)
    # The gap clips at 100, so a 150-point lead lands in the same cell as a 100.
    assert cell(KIND_HILL, 160, 10) == cell(KIND_HILL, 155, 55)
    # A round kind is its own cell and is never bucketed.
    assert cell(KIND_SND, 5, 4) == (5, 4)


# --- the race baseline -------------------------------------------------------


def test_the_race_baseline_matches_the_arithmetic_it_claims() -> None:
    grid = _race_grid(3)
    assert grid[0, 0] == 0.5
    assert grid[2, 2] == 0.5
    # One round from the map against a side that has none: only three straight
    # losses take it away, so 1 - 0.5**3.
    assert grid[2, 0] == 1.0 - 0.5**3
    assert grid[0, 2] == 0.5**3


def test_the_race_baseline_is_antisymmetric_at_every_state() -> None:
    for target in (3, 6):
        grid = _race_grid(target)
        for own in range(target):
            for opp in range(target):
                assert abs(grid[own, opp] + grid[opp, own] - 1.0) < 1e-12


def test_the_hill_baseline_reaches_certainty_at_the_target() -> None:
    maps = [hill_map([(60, 40), (120, 90), (190, 150), (250, 200)], game_id=i) for i in range(5)]
    baseline = RaceBaseline(KIND_HILL, maps)
    assert baseline.p(HILL_TARGET, 100) == 1.0
    assert baseline.p(100, HILL_TARGET) == 0.0
    assert abs(baseline.p(100, 100) - 0.5) < 1e-9


def test_the_hill_baseline_gives_a_lead_more_than_a_coin_flip() -> None:
    maps = [hill_map([(60, 40), (120, 90), (190, 150), (250, 200)], game_id=i) for i in range(5)]
    baseline = RaceBaseline(KIND_HILL, maps)
    assert baseline.p(200, 100) > baseline.p(150, 100) > 0.5


# --- the walk-forward split --------------------------------------------------


def test_no_map_is_ever_scored_by_a_table_that_saw_it() -> None:
    """The first event trains and is never scored, and later events never train
    on themselves.

    The second event is built to contradict the first: every map runs to 5-0 and
    is then lost 6-5. A table fitted on the first event says a 5-0 lead wins, so
    it is wrong about every state of the second — unless it saw those maps, in
    which case it is right and the leak shows up as a Brier better than a coin.
    A mirror-image archive cannot test this, because recording both sides of
    every map makes the mirror of an event something the table already knows.
    """
    maps = []
    for i in range(30):
        maps.append(rounds_map([0] * 6, game_id=i, event_id=1, day=0))
    for i in range(30):
        maps.append(rounds_map([0] * 5 + [1] * 6, game_id=100 + i, event_id=2, day=5))
    result = walk_forward(maps, KIND_SND)
    assert result["available"]
    assert result["n_events_scored"] == 1
    assert result["n_maps"] == 30
    scored = {m["model"]: m["brier"] for m in result["models"]}
    assert scored["state_table"] > scored["coin_flip"]


def test_a_single_event_cannot_be_backtested() -> None:
    maps = [rounds_map([0] * 6, game_id=i, event_id=1) for i in range(5)]
    result = walk_forward(maps, KIND_SND)
    assert result["available"] is False


def test_the_backtest_is_reproducible_across_runs() -> None:
    rng = np.random.default_rng(7)
    maps = []
    for i in range(120):
        winners = [int(rng.integers(0, 2)) for _ in range(9)]
        score = [winners.count(0), winners.count(1)]
        while max(score) < RACE[KIND_SND]:
            side = int(rng.integers(0, 2))
            winners.append(side)
            score[side] += 1
        maps.append(rounds_map(winners, game_id=i, event_id=i // 40, day=i // 40))
    first = walk_forward(maps, KIND_SND)
    second = walk_forward(list(reversed(maps)), KIND_SND)
    assert first["models"] == second["models"]


# --- the published splits ----------------------------------------------------


def test_win_types_are_counted_verbatim_and_unreported_is_its_own_bucket() -> None:
    maps = [
        rounds_map(
            [0, 1, 0, 1, 0, 0, 0],
            game_id=1,
            win_types=["kills", "bomb_defuse", "kills", None, "time", "kills", "kills"],
        )
    ]
    art = win_type_artifact(maps, KIND_SND)
    counts = {t["win_type"]: t["n"] for t in art["types"]}
    assert counts == {"kills": 4, "bomb_defuse": 1, "unreported": 1, "time": 1}
    assert sum(counts.values()) == art["n_rounds"]


def test_competitiveness_separates_a_blowout_from_a_decider() -> None:
    blowout = rounds_map([0] * 6, game_id=1)
    decider = rounds_map([0, 1, 0, 1, 0, 1, 0, 1, 0, 1, 0], game_id=2)
    art = competitiveness([blowout, decider])
    weights = {r["game_id"]: r["weight"] for r in art["maps"]}
    assert weights[1] > weights[2]


def test_the_two_era_comparison_names_the_maps_it_left_out() -> None:
    modern = [rounds_map([0, 1] * 3 + [0] * 3, game_id=i) for i in range(10)]
    feed = [rounds_map([0, 1] * 3 + [0] * 3, game_id=100 + i) for i in range(10)]
    out = two_era_snd(modern, feed, {"excluded": 93, "race_reached_by_season": {"2017": {"5": 92}}})
    assert out["feed"]["excluded_for_a_different_race"] == 93
    assert all(abs(c["delta"]) < 1e-9 for c in out["cells"])


def test_the_module_declares_every_constant_it_was_configured_with() -> None:
    params = segmentwp.params()
    assert params["race"] == RACE
    assert params["hill_target"] == HILL_TARGET
    assert set(params["kinds"]) == {KIND_SND, KIND_HILL, KIND_CONTROL}
