import math

from cdlhub_analytics.ratings.elo import Elo, expected
from cdlhub_analytics.ratings.glicko2 import INITIAL_RD, Glicko2, TeamState, rate


def test_elo_expected_symmetry() -> None:
    assert expected(1500, 1500) == 0.5
    assert math.isclose(expected(1600, 1400), 1 - expected(1400, 1600))


def test_elo_golden_update() -> None:
    # Golden values: 1500 vs 1500, K=32 -> winner 1516, loser 1484.
    elo = Elo(k=32.0)
    p, ra, rb = elo.update(1, 2, a_won=True)
    assert p == 0.5
    assert ra == 1516.0 and rb == 1484.0
    # 1613.6 favorite beats 1388.5 underdog: small gain (Elo 1978 example shape)
    elo.ratings = {1: 1613.0, 2: 1388.0}
    p, ra, rb = elo.update(1, 2, a_won=True)
    assert p > 0.75
    assert 1613.0 < ra < 1621.0


def test_glicko2_paper_example() -> None:
    # Glickman (2013), the worked example: r=1500 RD=200 player beats 1400/30,
    # loses to 1550/100, loses to 1700/300, tau=0.5.
    player = TeamState(r=1500.0, rd=200.0, sigma=0.06)
    opponents = [
        (TeamState(1400.0, 30.0, 0.06), 1.0),
        (TeamState(1550.0, 100.0, 0.06), 0.0),
        (TeamState(1700.0, 300.0, 0.06), 0.0),
    ]
    new = rate(player, opponents, tau=0.5)
    assert math.isclose(new.r, 1464.06, abs_tol=0.05)
    assert math.isclose(new.rd, 151.52, abs_tol=0.05)
    assert math.isclose(new.sigma, 0.05999, abs_tol=0.0005)


def test_glicko2_rd_grows_when_idle() -> None:
    player = TeamState(r=1500.0, rd=50.0, sigma=0.06)
    idle = rate(player, [], tau=0.5)
    assert idle.rd > 50.0
    assert idle.r == 1500.0


def test_glicko2_predict_walk_forward() -> None:
    g = Glicko2(tau=0.5)
    assert g.predict(1, 2) == 0.5  # both unrated
    p = g.update(1, 2, a_won=True)
    assert p == 0.5  # prediction computed before the update
    assert g.state(1).r > 1500.0 > g.state(2).r
    assert g.predict(1, 2) > 0.5  # updated ratings inform the next prediction


def test_glicko2_rd_is_capped_at_the_uninformative_value() -> None:
    """350 already means 'nothing is known'; idling forever cannot mean less."""
    player = TeamState(r=1500.0, rd=349.0, sigma=0.06)
    for _ in range(50):
        player = rate(player, [], tau=0.5)
    assert player.rd == INITIAL_RD


def test_advance_inflates_the_idle_and_updates_the_active() -> None:
    """The bug this replaced: only the two teams in a series were ever advanced,
    so a team's deviation tracked games played and never time elapsed."""
    g = Glicko2(tau=0.5)
    for t in (1, 2, 3):
        g.state(t)
    g.advance({1: [(2, 1.0)], 2: [(1, 0.0)]}, roster=(1, 2, 3))
    # 3 sat out: rating unmoved, deviation grown.
    assert g.state(3).r == 1500.0
    assert g.state(3).rd > 350.0 - 1e-9 or g.state(3).rd == INITIAL_RD
    # 1 and 2 played: ratings moved apart, deviations shrunk.
    assert g.state(1).r > 1500.0 > g.state(2).r
    assert g.state(1).rd < 350.0


def test_advance_scores_a_period_against_pre_period_ratings() -> None:
    """Two results in one period must not see each other; a period is defined by
    every result in it being scored against the state at its open."""
    both_at_once = Glicko2(tau=0.5)
    for t in (1, 2, 3):
        both_at_once.state(t)
    both_at_once.advance({1: [(2, 1.0), (3, 1.0)], 2: [(1, 0.0)], 3: [(1, 0.0)]}, roster=(1, 2, 3))

    # Same two wins fed as one paper-style rating period, opponents frozen.
    manual = rate(
        TeamState(),
        [(TeamState(), 1.0), (TeamState(), 1.0)],
        tau=0.5,
    )
    assert math.isclose(both_at_once.state(1).r, manual.r, abs_tol=1e-9)
    assert math.isclose(both_at_once.state(1).rd, manual.rd, abs_tol=1e-9)


def test_advance_is_order_independent() -> None:
    """Iteration order over a period's teams cannot change the result."""
    results = {1: [(2, 1.0)], 2: [(1, 0.0)], 3: [(4, 0.0)], 4: [(3, 1.0)]}
    a = Glicko2(tau=0.5)
    a.advance(results, roster=(1, 2, 3, 4))
    b = Glicko2(tau=0.5)
    b.advance(dict(reversed(list(results.items()))), roster=(4, 3, 2, 1))
    assert {t: (s.r, s.rd) for t, s in a.teams.items()} == {
        t: (s.r, s.rd) for t, s in b.teams.items()
    }
