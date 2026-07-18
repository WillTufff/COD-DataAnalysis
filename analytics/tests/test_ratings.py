import math

from cdlhub_analytics.ratings.elo import Elo, expected
from cdlhub_analytics.ratings.glicko2 import Glicko2, TeamState, rate


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
