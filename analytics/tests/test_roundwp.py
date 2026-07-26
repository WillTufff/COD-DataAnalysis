"""Fixture tests for round win probability. No database required.

Every round here is hand-built, so the invariants being asserted are the ones
the module claims in its docstring rather than whatever the archive happens to
contain: antisymmetry, the telescoping of win probability added, and the
refusal to score a round whose feed contradicts itself.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from cdlhub_analytics.roundwp import (
    LAPLACE,
    RoundTimeline,
    StateTable,
    features,
    state_rows,
    timeline_artifact,
    timeline_steps,
    trade_latencies,
    walk_forward,
    wpa,
)

DAY0 = date(2018, 1, 1)
TEAM_A, TEAM_B = 10, 20
# Four a side; A is players 1-4, B is 5-8.
ROSTER = {1: TEAM_A, 2: TEAM_A, 3: TEAM_A, 4: TEAM_A, 5: TEAM_B, 6: TEAM_B, 7: TEAM_B, 8: TEAM_B}
SIDE_OF = {p: (0 if t == TEAM_A else 1) for p, t in ROSTER.items()}


def build_round(
    deaths: list[tuple[int, int, int | None]],
    winner: int,
    *,
    game_id: int = 1,
    rnd: int = 1,
    event_id: int = 1,
    day: int = 0,
    end_ms: int | None = None,
) -> RoundTimeline:
    steps = timeline_steps(deaths, (4, 4), SIDE_OF)
    assert steps is not None
    return RoundTimeline(
        game_id=game_id,
        round=rnd,
        event_id=event_id,
        played_at=DAY0 + timedelta(days=day),
        teams=(TEAM_A, TEAM_B),
        winner=winner,
        roster=dict(ROSTER),
        steps=steps,
        # Default to a round that ends with its last death, which is what a wipe
        # looks like; tests that care pass their own span.
        end_ms=steps[-1].t_ms if end_ms is None else end_ms,
    )


def flat_table() -> StateTable:
    """Every non-terminal state at exactly 0.5, so a curve that moves is moving
    because the states moved and not because the table has opinions."""
    return StateTable([(a, b, 0, 0.5) for a in range(1, 5) for b in range(1, 5)])


# ===== timeline construction =====


def test_alive_counts_follow_the_deaths() -> None:
    steps = timeline_steps([(1000, 5, 1), (2000, 6, 1), (3000, 2, 7)], (4, 4), SIDE_OF)
    assert steps is not None
    assert [s.alive for s in steps] == [(4, 4), (4, 3), (4, 2), (3, 2)]
    assert steps[0].killer is None and steps[0].victim is None
    assert steps[3].killer == 7 and steps[3].victim == 2


def test_a_death_on_an_empty_side_is_refused() -> None:
    """SnD is one life each, so a fifth death for a four-player side means the
    feed disagrees with itself. The round is dropped, never patched."""
    deaths = [(1000, 5, 1), (2000, 6, 1), (3000, 7, 1), (4000, 8, 1), (5000, 5, 2)]
    assert timeline_steps(deaths, (4, 4), SIDE_OF) is None


def test_an_unknown_victim_is_refused() -> None:
    assert timeline_steps([(1000, 99, 1)], (4, 4), SIDE_OF) is None


# ===== the target is antisymmetric by construction =====


def test_both_perspectives_are_recorded_and_terminal_states_are_not() -> None:
    rnd = build_round([(1000, 5, 1), (2000, 6, 1), (3000, 7, 1), (4000, 8, 1)], winner=TEAM_A)
    rows = state_rows([rnd])
    # (4,4) (4,3) (4,2) (4,1) from A's side and the mirror from B's; the wipeout
    # instant (4,0) has nothing left to predict.
    assert sorted({(a, b) for a, b, _t, _y in rows}) == [
        (1, 4),
        (2, 4),
        (3, 4),
        (4, 1),
        (4, 2),
        (4, 3),
        (4, 4),
    ]


def test_even_states_are_exactly_a_coin_flip() -> None:
    """Counting both sides of every instant forces P(n vs n) = 0.5 exactly. If
    that ever drifts, the mirroring has been broken somewhere."""
    rounds = [
        build_round([(1000, 5, 1), (2000, 2, 6)], winner=TEAM_A, game_id=1),
        build_round([(1000, 1, 5), (2000, 6, 2)], winner=TEAM_B, game_id=2),
        build_round([(1000, 5, 1)], winner=TEAM_A, game_id=3),
    ]
    table = StateTable(state_rows(rounds))
    assert table.p(4, 4) == 0.5
    assert table.p(3, 3) == 0.5


def test_the_fitted_intercept_is_zero_on_mirrored_data() -> None:
    from cdlhub_analytics.regress import fit_logistic_l2

    rounds = [
        build_round([(1000, 5, 1), (2000, 6, 2)], winner=TEAM_A, game_id=1),
        build_round([(1000, 1, 5), (2000, 2, 6)], winner=TEAM_B, game_id=2),
        build_round([(1000, 5, 1), (2000, 2, 6), (3000, 6, 3)], winner=TEAM_A, game_id=3),
    ]
    rows = state_rows(rounds)
    fit = fit_logistic_l2(
        features(rows, "survivors"), np.array([r[3] for r in rows], dtype=float), l2=1.0
    )
    assert abs(fit.intercept) < 1e-6


# ===== the table =====


def test_terminal_states_are_certain() -> None:
    table = StateTable([])
    assert table.p(3, 0) == 1.0
    assert table.p(0, 3) == 0.0


def test_an_unseen_state_falls_back_to_a_coin_flip() -> None:
    table = StateTable([])
    assert table.p(4, 3) == 0.5


def test_laplace_pulls_a_thin_cell_toward_the_middle() -> None:
    """One observed round should not publish a 100% state."""
    table = StateTable([(4, 3, 0, 1.0)])
    assert table.p(4, 3) == (1.0 + LAPLACE) / (1.0 + 2.0 * LAPLACE)
    assert table.p(4, 3) < 1.0


def test_a_lopsided_state_is_learned() -> None:
    rows = [(4, 1, 0, 1.0)] * 50 + [(1, 4, 0, 0.0)] * 50
    table = StateTable(rows)
    assert table.p(4, 1) > 0.9
    assert table.p(1, 4) < 0.1


# ===== win probability added =====


def test_wpa_telescopes_to_the_round_result() -> None:
    """Every kill's credit is a step in one walk from the opening state to the
    finish, so summing them in one team's frame has to land on that team's final
    win probability. This is what makes WPA an accounting of the round rather
    than a score attached to it.
    """
    table = StateTable([(a, b, 0, 0.5) for a in range(1, 5) for b in range(1, 5)])
    rnd = build_round(
        [(1000, 5, 1), (2000, 2, 6), (3000, 6, 3), (4000, 7, 3), (5000, 8, 3)],
        winner=TEAM_A,
    )
    # A-frame total must be p(final) - p(start).
    total = 0.0
    for before, after in zip(rnd.steps, rnd.steps[1:], strict=False):
        killer = after.killer
        assert killer is not None
        s = rnd.side(ROSTER[killer])
        delta = table.p(after.alive[s], after.alive[1 - s]) - table.p(
            before.alive[s], before.alive[1 - s]
        )
        total += delta if s == 0 else -delta
    start, final = rnd.steps[0].alive, rnd.steps[-1].alive
    assert abs(total - (table.p(*final) - table.p(*start))) < 1e-12


def test_a_player_who_takes_every_kill_gains_probability() -> None:
    table = StateTable([(a, b, 0, 0.5) for a in range(1, 5) for b in range(1, 5)])
    rounds = [
        build_round(
            [(1000, 5, 1), (2000, 6, 1), (3000, 7, 1), (4000, 8, 1)], winner=TEAM_A, game_id=g
        )
        for g in range(1, 61)
    ]
    out = wpa(rounds, table)
    assert out["n_rounds"] == 60
    # min_rounds gates the leaderboard, so nobody qualifies off 60 rounds.
    assert out["leaders"] == []
    assert not out["reliability"]["available"]


def test_reliability_is_withheld_rather_than_guessed_at_on_a_thin_sample() -> None:
    table = StateTable([(a, b, 0, 0.5) for a in range(1, 5) for b in range(1, 5)])
    out = wpa([build_round([(1000, 5, 1)], winner=TEAM_A)], table)
    assert out["reliability"]["available"] is False


# ===== the backtest =====


def test_walk_forward_needs_a_prior_event() -> None:
    rounds = [build_round([(1000, 5, 1)], winner=TEAM_A, game_id=g, event_id=1) for g in range(4)]
    out = walk_forward(rounds)
    assert out["available"] is False


def test_a_state_model_beats_a_coin_flip_when_state_decides_the_round() -> None:
    """Rounds where whoever draws first blood wins. Any model reading the alive
    counts should separate from 0.5; if it does not, the walk-forward split is
    leaking or the features are not reaching the fit."""
    rounds: list[RoundTimeline] = []
    for g in range(40):
        a_first = g % 2 == 0
        deaths: list[tuple[int, int, int | None]] = (
            [(1000, 5, 1), (2000, 6, 2), (3000, 7, 3)]
            if a_first
            else [(1000, 1, 5), (2000, 2, 6), (3000, 3, 7)]
        )
        rounds.append(
            build_round(
                deaths,
                winner=TEAM_A if a_first else TEAM_B,
                game_id=g,
                event_id=1 + g // 20,
                day=g // 20,
            )
        )
    out = walk_forward(rounds)
    assert out["available"] is True
    scores = {m["model"]: m["brier"] for m in out["models"]}
    assert scores["coin_flip"] == 0.25
    assert scores["state_table"] < 0.2
    assert scores["survivors"] < 0.25
    # The nested add-a-feature tests are always reported, so the two nulls the
    # module publishes can never silently stop being computed.
    assert {(p["a"], p["b"]) for p in out["nested"]} == {
        ("survivors_time", "survivors"),
        ("survivors_bomb", "survivors"),
    }


# ===== the time-resolved description =====


def test_the_survivor_curve_recovers_a_planted_shape() -> None:
    """One death every ten seconds, always on B. At 25 s exactly two of them have
    landed, so the winner's mean is 4.0 and the loser's 2.0 — no averaging over a
    ragged population to hide behind."""
    rounds = [
        build_round(
            [(10_000, 5, 1), (20_000, 6, 1), (30_000, 7, 1)],
            winner=TEAM_A,
            game_id=g,
            end_ms=40_000,
        )
        for g in range(20)
    ]
    art = timeline_artifact(rounds, flat_table())
    at = {b["t_s"]: b for b in art["bins"]}
    assert at[0]["winner_alive"] == 4.0 and at[0]["loser_alive"] == 4.0
    assert at[25]["winner_alive"] == 4.0 and at[25]["loser_alive"] == 2.0
    assert at[35]["loser_alive"] == 1.0
    # The round is still live at 40 s (its recorded end) and gone by 45 s.
    assert at[40]["n_live"] == 20
    assert at[45]["n_live"] == 0
    assert at[45]["p_winner"] is None


def test_live_share_starts_whole_and_only_falls() -> None:
    rounds = [
        build_round([(5_000 * (g + 1), 5, 1)], winner=TEAM_A, game_id=g, end_ms=5_000 * (g + 2))
        for g in range(10)
    ]
    art = timeline_artifact(rounds, flat_table())
    shares = [b["live_share"] for b in art["bins"]]
    assert shares[0] == 1.0
    assert all(b <= a for a, b in zip(shares[:-1], shares[1:], strict=True))
    assert shares[-1] == 0.0


def test_a_span_that_contradicts_the_feed_is_dropped_from_the_state_views_only() -> None:
    """The round with no usable span still has deaths, and the trade figure does
    not need to know when the round stopped — so it keeps them. The survivor and
    win-probability curves do, and exclude it."""
    good = build_round([(10_000, 5, 1), (12_000, 1, 6)], winner=TEAM_B, game_id=1, end_ms=20_000)
    bad = RoundTimeline(
        game_id=2,
        round=1,
        event_id=1,
        played_at=DAY0,
        teams=(TEAM_A, TEAM_B),
        winner=TEAM_B,
        roster=dict(ROSTER),
        steps=good.steps,
        end_ms=None,
    )
    art = timeline_artifact([good, bad], flat_table())
    assert art["n_rounds"] == 2
    assert art["n_rounds_spanned"] == 1
    assert art["n_rounds_span_conflict"] == 1
    at = {b["t_s"]: b for b in art["bins"]}
    assert at[0]["n_live"] == 1  # only the round with a usable span
    # Both rounds' deaths reach the trade view: two answerable deaths each.
    assert art["trade_latency"]["n_deaths"] == 4


def test_win_probability_starts_at_a_coin_flip_and_the_model_free_series_agrees() -> None:
    """Four a side is 0.5 by construction. Then plant rounds the leader always
    wins: both the table's curve and the model-free 'was the leader ahead' series
    have to climb, and the second one owes nothing to the first."""
    rounds = [
        build_round([(10_000, 5, 1), (20_000, 6, 2)], winner=TEAM_A, game_id=g, end_ms=30_000)
        for g in range(40)
    ]
    table = StateTable(state_rows(rounds))
    art = timeline_artifact(rounds, table)
    at = {b["t_s"]: b for b in art["bins"]}
    assert at[0]["p_winner"] == 0.5
    assert at[0]["leader_n"] == 0  # 4v4: nobody is ahead, so nothing to count
    assert at[25]["leader_n"] == 40
    assert at[25]["leader_wins"] == 1.0
    assert at[25]["p_winner"] is not None and at[25]["p_winner"] > 0.9


def test_the_model_free_series_can_contradict_the_table() -> None:
    """Rounds the trailing side always wins. leader_wins must read 0.0 — it is
    counted from outcomes, so no table can talk it into agreeing."""
    rounds = [
        build_round([(10_000, 5, 1), (20_000, 6, 2)], winner=TEAM_B, game_id=g, end_ms=30_000)
        for g in range(40)
    ]
    art = timeline_artifact(rounds, flat_table())
    at = {b["t_s"]: b for b in art["bins"]}
    assert at[25]["leader_n"] == 40
    assert at[25]["leader_wins"] == 0.0
    assert at[25]["p_winner"] == 0.5  # the flat table, unmoved


def test_trade_latency_is_unwindowed_and_the_window_is_reported_against_it() -> None:
    """A 7 s answer is a real answer that the 5 s convention excludes. It has to
    show up in the latency distribution and stay out of the traded share."""
    inside = build_round([(10_000, 5, 1), (13_000, 1, 6)], winner=TEAM_B, game_id=1, end_ms=20_000)
    outside = build_round([(10_000, 5, 1), (17_000, 1, 6)], winner=TEAM_B, game_id=2, end_ms=20_000)
    art = timeline_artifact([inside, outside], flat_table())
    lat = art["trade_latency"]
    assert lat["n_answered"] == 2
    assert lat["n_within_window"] == 1
    by_lo = {b["lo_s"]: b for b in lat["bins"]}
    assert by_lo[3.0]["n"] == 1 and by_lo[3.0]["in_window"] is True
    assert by_lo[7.0]["n"] == 1 and by_lo[7.0]["in_window"] is False
    # The 10-15 s bin holds all four deaths bar the 17 s one, and exactly one of
    # them was answered inside the window.
    at = {b["t_s"]: b for b in art["bins"]}
    assert at[10]["n_deaths"] == 3
    assert at[10]["traded_share"] == round(1 / 3, 4)
    assert at[15]["n_deaths"] == 1 and at[15]["traded_share"] == 0.0


def test_an_answer_from_the_wrong_side_is_not_a_trade() -> None:
    """Player 1 (team A) is killed by 5; 5 then dies to 6, their own teammate.
    Nobody on A answered, so the death is untraded and has no latency."""
    rnd = build_round([(10_000, 1, 5), (12_000, 5, 6)], winner=TEAM_B, end_ms=20_000)
    assert trade_latencies([rnd]) == [None, None]
    art = timeline_artifact([rnd], flat_table())
    assert art["trade_latency"]["n_answered"] == 0
    assert art["trade_latency"]["never"]["n"] == 2


def test_a_killer_who_survives_the_round_never_answers() -> None:
    rnd = build_round([(10_000, 5, 1), (20_000, 6, 1)], winner=TEAM_A, end_ms=30_000)
    assert trade_latencies([rnd]) == [None, None]


def test_the_traded_count_matches_the_shipped_trade_definition() -> None:
    """The figure must be the same 'traded' the metric layer publishes, not a
    lookalike. Same feed through both code paths, same count."""
    from cdlhub_analytics.metrics import KF_TRADED, compute_map_trades

    deaths: list[tuple[int, int, int | None]] = [
        (10_000, 5, 1),  # A's 1 kills B's 5 …
        (13_000, 1, 6),  # … and B answers in 3 s: that death is traded
        (30_000, 6, 2),  # A's 2 kills B's 6 …
        (40_000, 2, 7),  # … answered in 10 s, which the window excludes
    ]
    rnd = build_round(deaths, winner=TEAM_A, end_ms=60_000)
    feed = [(1, t, i, victim, killer) for i, (t, victim, killer) in enumerate(deaths)]
    per_player = compute_map_trades(feed, ROSTER)
    shipped = sum(c[KF_TRADED] for c in per_player.values())

    art = timeline_artifact([rnd], flat_table())
    drawn = sum(
        round(b["traded_share"] * b["n_deaths"])
        for b in art["bins"]
        if b["traded_share"] is not None
    )
    assert drawn == shipped == 1
    # Three of the four deaths were answered eventually; the window keeps one.
    # That difference is the whole reason the latency figure exists.
    lat = art["trade_latency"]
    assert (lat["n_deaths"], lat["n_answered"], lat["n_within_window"]) == (4, 3, 1)


def test_the_latency_bins_and_the_tails_account_for_every_death() -> None:
    rounds = [
        build_round([(10_000, 5, 1), (13_000, 1, 6)], winner=TEAM_B, game_id=g, end_ms=20_000)
        for g in range(5)
    ]
    lat = timeline_artifact(rounds, flat_table())["trade_latency"]
    counted = sum(b["n"] for b in lat["bins"]) + lat["beyond"]["n"] + lat["never"]["n"]
    assert counted == lat["n_deaths"]
