"""Phase B (kill-feed) tests: trade, clutch and man-advantage.

Three layers:
  * pure timeline functions on hand-scripted feeds with known answers;
  * catalog metrics on hand-built aggregates, incl. zero-denominator and
    missing-title (no-feed) cases, and a sum-then-divide property;
  * DB-backed checks (reconciliation gate, the kill_answered cross-tier
    assertion, an independent SQL spot-check) that skip when no database is
    reachable, so they validate locally and in any DB-backed CI without
    breaking the DB-free unit run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from cdlhub_analytics import metrics
from cdlhub_analytics.metrics import (
    CATALOG,
    KF_ANSWERED,
    KF_CLUTCH_ATT,
    KF_CLUTCH_WIN,
    KF_DEATHS,
    KF_KILLS,
    KF_THROWN,
    KF_TRADE_KILLS,
    KF_TRADED,
    KF_UNTRADED,
    Aggregate,
    Coverage,
    KeyCoverage,
    Metric,
    compute_map_clutch_adv,
    compute_map_trades,
    resolve_round_winners,
)

# ---------- helpers ----------

# Two teams of four for the clutch/advantage timelines.
TEAM_A = {1, 2, 3, 4}
TEAM_B = {5, 6, 7, 8}
TEAM_OF = {p: 10 for p in TEAM_A} | {p: 20 for p in TEAM_B}
ROSTER = {10: set(TEAM_A), 20: set(TEAM_B)}


def d(
    round_: int, time_ms: int, seq: int, victim: int, killer: int | None
) -> tuple[int, int, int, int, int | None]:
    return (round_, time_ms, seq, victim, killer)


def metric_by_key(key: str) -> Metric:
    return next(m for m in CATALOG if m.key == key)


def feed_agg(
    sums: dict[str, float] | None = None,
    *,
    title: str = "WWII",
    mode_slug: str = "search-and-destroy",
    mode_id: int | None = 5,
    feed_maps: int = 20,
    feed_duration_s: float = 12000.0,
) -> Aggregate:
    agg = Aggregate(player_id=1, season_id=2, mode_id=mode_id, mode_slug=mode_slug, title=title)
    agg.feed_maps = feed_maps
    agg.feed_duration_s = feed_duration_s
    for key, value in (sums or {}).items():
        agg.sums[key] = value
    return agg


def compute_ok(key: str, agg: Aggregate) -> tuple[float, float]:
    result = metric_by_key(key).compute(agg)
    assert result is not None
    return result


def feed_coverage(feed_titles: tuple[str, ...] = ("IW", "WWII")) -> Coverage:
    """Coverage where the kill-feed keys are tracked only for feed_titles."""
    keys = (*metrics.KF_KEYS, *metrics.CLUTCH_KEYS)
    coverage: Coverage = {}
    for title in metrics.TITLE_ORDER:
        nonzero = 1000 if title in feed_titles else 0
        coverage[title] = {k: KeyCoverage(rows=1000, present=1000, nonzero=nonzero) for k in keys}
    return coverage


# ---------- pure timeline: trades ----------


def test_trade_avenged_within_window() -> None:
    # V=1 killed by K=5, then K=5 killed by V's teammate 2 within 5s -> traded.
    deaths = [d(1, 1000, 0, 1, 5), d(1, 3000, 1, 5, 2)]
    c = compute_map_trades(deaths, TEAM_OF)
    assert c[1][KF_TRADED] == 1 and c[1][KF_UNTRADED] == 0
    assert c[2][KF_TRADE_KILLS] == 1
    assert c[5][KF_ANSWERED] == 1  # K's kill was answered


def test_trade_outside_window_is_untraded() -> None:
    deaths = [d(1, 1000, 0, 1, 5), d(1, 6001, 1, 5, 2)]  # 5001 ms later
    c = compute_map_trades(deaths, TEAM_OF)
    assert c[1][KF_UNTRADED] == 1 and c[1][KF_TRADED] == 0
    assert c[2][KF_TRADE_KILLS] == 0
    assert c[5][KF_ANSWERED] == 0


def test_trade_does_not_cross_round_boundary() -> None:
    deaths = [d(1, 1000, 0, 1, 5), d(2, 1200, 1, 5, 2)]  # next round, close in game clock
    c = compute_map_trades(deaths, TEAM_OF)
    assert c[1][KF_UNTRADED] == 1


def test_answered_counts_any_killer_trade_needs_victim_team() -> None:
    # K=5 killed by 6 (K's own teammate cannot kill K; use an enemy of K = team A).
    # Here K=5 is killed by player 3 who is a teammate of V=1 -> traded.
    deaths = [d(1, 1000, 0, 1, 5), d(1, 2000, 1, 5, 3)]
    c = compute_map_trades(deaths, TEAM_OF)
    assert c[1][KF_TRADED] == 1
    # answered credited to the original killer K regardless
    assert c[5][KF_ANSWERED] == 1


def test_first_death_of_round_flagged() -> None:
    deaths = [d(1, 1000, 0, 1, 5), d(1, 9000, 1, 2, 6)]  # 1 is first death, untraded
    c = compute_map_trades(deaths, TEAM_OF)
    assert c[1][metrics.KF_FIRST_DEATHS] == 1
    assert c[1][metrics.KF_FIRST_UNTRADED] == 1
    assert c[2][metrics.KF_FIRST_DEATHS] == 0  # not the first death


# ---------- pure timeline: clutch and advantage ----------


def test_clutch_1v3_won() -> None:
    # A gets first blood (kills 5), loses 1,2,3; player 4 clutches 1v3 and wins.
    deaths = [
        d(1, 100, 0, 5, 1),
        d(1, 200, 1, 1, 6),
        d(1, 300, 2, 2, 7),
        d(1, 400, 3, 3, 8),
        d(1, 500, 4, 6, 4),
        d(1, 600, 5, 7, 4),
        d(1, 700, 6, 8, 4),
    ]
    c = compute_map_clutch_adv({1: [(v, k) for _, _, _, v, k in deaths]}, ROSTER, TEAM_OF, {1: 10})
    assert c[4][metrics._clutch_att(3)] == 1 and c[4][metrics._clutch_win(3)] == 1
    assert c[4][KF_CLUTCH_ATT] == 1 and c[4][KF_CLUTCH_WIN] == 1


def test_advantage_conversion_and_thrown_death() -> None:
    # A takes first blood (5 dies), then player 1 dies while A is up a man (thrown),
    # A still wins the round.
    deaths = [(5, 1), (1, 6)]
    c = compute_map_clutch_adv({1: deaths}, ROSTER, TEAM_OF, {1: 10})
    # team A opened up a man and won
    assert c[1][metrics.KF_ADV_ROUNDS] == 1 and c[1][metrics.KF_ADV_WINS] == 1
    # team B opened down a man and lost (no steal)
    assert c[5][metrics.KF_DISADV_ROUNDS] == 1 and c[5][metrics.KF_DISADV_WINS] == 0
    # player 1 died while up a man -> thrown death
    assert c[1][KF_THROWN] == 1


def test_disadvantage_steal() -> None:
    # B concedes first blood (5 dies -> A up), but B wins the round -> steal for B.
    deaths = [(5, 1)]
    c = compute_map_clutch_adv({1: deaths}, ROSTER, TEAM_OF, {1: 20})
    assert c[5][metrics.KF_DISADV_ROUNDS] == 1 and c[5][metrics.KF_DISADV_WINS] == 1
    assert c[1][metrics.KF_ADV_ROUNDS] == 1 and c[1][metrics.KF_ADV_WINS] == 0


def test_clutch_and_advantage_skipped_without_winner() -> None:
    deaths = [(5, 1), (1, 6), (2, 7), (3, 8)]  # A down to player 4, but winner unknown
    c = compute_map_clutch_adv({1: deaths}, ROSTER, TEAM_OF, {})  # no winner
    assert c[4][KF_CLUTCH_ATT] == 0
    assert c[1][metrics.KF_ADV_ROUNDS] == 0
    # participation and thrown deaths still counted
    assert c[1][metrics.KF_SND_ROUNDS] == 1


# ---------- pure: round-winner resolution ----------


def test_resolve_round_winners_direct_with_reset_decider() -> None:
    rounds = [(1, 1, 0, 1), (2, 1, 1, 2), (3, 0, 0, None)]  # decider resets to (0,0)
    w = resolve_round_winners(rounds, 100, 200, 2, 1, 100)
    assert w == {1: 100, 2: 200, 3: 100}


def test_resolve_round_winners_swap_orientation() -> None:
    rounds = [(1, 1, 0, 1), (2, 2, 0, 1), (3, 2, 1, 2)]  # box (1,2) forces side1 -> team2
    w = resolve_round_winners(rounds, 100, 200, 1, 2, 200)
    assert w == {1: 200, 2: 200, 3: 100}


def test_resolve_round_winners_unresolvable_returns_empty() -> None:
    assert resolve_round_winners([(1, 1, 0, 1)], 100, 200, 5, 5, 100) == {}


# ---------- catalog metrics on aggregates ----------


def test_untraded_death_rate_value_and_denom() -> None:
    agg = feed_agg({KF_UNTRADED: 150.0, KF_DEATHS: 200.0})
    value, denom = compute_ok("untraded_death_rate", agg)
    assert value == pytest.approx(0.75)
    assert denom == 200.0


def test_trade_kills_p10_uses_feed_duration() -> None:
    agg = feed_agg({KF_TRADE_KILLS: 20.0}, feed_maps=10, feed_duration_s=6000.0)  # 100 min
    value, denom = compute_ok("trade_kills_p10", agg)
    assert value == pytest.approx(2.0)  # 20 kills / 100 min * 10
    assert denom == 10.0


def test_clutch_win_rate_and_per_n() -> None:
    agg = feed_agg(
        {
            KF_CLUTCH_WIN: 6.0,
            KF_CLUTCH_ATT: 20.0,
            metrics._clutch_win(1): 4.0,
            metrics._clutch_att(1): 10.0,
        }
    )
    value, denom = compute_ok("clutch_win_rate", agg)
    assert value == pytest.approx(0.3)
    assert denom == 20.0
    v1, d1 = compute_ok("clutch_1v1_win_rate", agg)
    assert v1 == pytest.approx(0.4)
    assert d1 == 10.0  # denom is attempts -> W-L on the card


def test_advantage_rounds_lost_is_complement() -> None:
    agg = feed_agg({metrics.KF_ADV_WINS: 70.0, metrics.KF_ADV_ROUNDS: 100.0})
    conv, _ = compute_ok("snd_adv_conversion", agg)
    lost, denom = compute_ok("snd_adv_rounds_lost", agg)
    assert conv == pytest.approx(0.7)
    assert lost == pytest.approx(0.3)
    assert denom == 100.0


@pytest.mark.parametrize(
    "key",
    [
        "untraded_death_rate",
        "kill_answered_rate",
        "trade_kills_p10",
        "clutch_win_rate",
        "snd_adv_conversion",
        "snd_disadv_steal_rate",
        "snd_adv_thrown_deaths_pr",
    ],
)
def test_zero_denominator_returns_none(key: str) -> None:
    agg = feed_agg(feed_maps=0, feed_duration_s=0.0)  # no feed data at all
    assert metric_by_key(key).compute(agg) is None


@pytest.mark.parametrize(
    "key",
    [
        "untraded_death_rate",
        "trade_kills_p10",
        "kill_answered_rate",
        "first_death_untraded_rate",
        "clutch_win_rate",
        "snd_adv_conversion",
        "snd_disadv_steal_rate",
        "snd_adv_thrown_deaths_pr",
    ],
)
def test_feed_metrics_excluded_for_no_feed_title(key: str) -> None:
    """BO4 has box scores but no events, so no kill-feed metric may publish for it."""
    coverage = feed_coverage(("IW", "WWII"))
    titles = metric_by_key(key).titles(coverage)
    assert "BO4" not in titles
    assert set(titles) == {"IW", "WWII"}


def test_sum_then_divide_not_mean_of_ratios() -> None:
    # Two player-maps: 1/1 and 1/99 untraded. Mean of ratios = 0.505; the correct
    # pooled rate is 2/100 = 0.02. The aggregate sums numerator and denominator.
    agg = feed_agg({KF_UNTRADED: 2.0, KF_DEATHS: 100.0})
    value, _ = compute_ok("untraded_death_rate", agg)
    assert value == pytest.approx(0.02)


# ---------- DB-backed checks (skip when no database) ----------


@pytest.fixture
def db_conn() -> Iterator[Any]:
    psycopg = pytest.importorskip("psycopg")
    dsn = os.environ.get("DATABASE_URL", "postgres://cdlhub:cdlhub@localhost:54329/cdlhub")
    try:
        conn = psycopg.connect(dsn, connect_timeout=2)
    except Exception:  # noqa: BLE001 - any connection failure means no DB here
        pytest.skip("no database reachable")
    has_feed = conn.execute("SELECT count(*) FROM kill_events").fetchone()
    if not has_feed or not has_feed[0]:
        conn.close()
        pytest.skip("kill_events is empty; import the snapshot first")
    try:
        yield conn
    finally:
        conn.close()


def test_reconciliation_wwii_fully_reconciled(db_conn: Any) -> None:
    bad = db_conn.execute(
        "SELECT count(*) FROM kill_feed_recon WHERE title = 'WWII' AND NOT reconciled"
    ).fetchone()
    assert bad[0] == 0


def test_kill_answered_cross_tier_tracks_box(db_conn: Any) -> None:
    """Feed kill_answered_rate must closely track 1 - kills_stayed_alive/kills (WWII)."""
    from cdlhub_analytics import metrics as m

    loaded = m.load(db_conn)
    m.augment_with_kill_feed(db_conn, loaded)
    title_of = {
        r[0]: r[1]
        for r in db_conn.execute(
            "SELECT se.id, t.short_name FROM seasons se JOIN titles t ON t.id = se.title_id"
        )
    }
    diffs = []
    for a in loaded.aggregates:
        if a.mode_id is not None or title_of.get(a.season_id) != "WWII":
            continue
        kills, answered = a.total(KF_KILLS), a.total(KF_ANSWERED)
        ksa = a.total("kills_stayed_alive")
        if kills < 100 or not a.has("kills_stayed_alive"):
            continue
        feed_rate = answered / kills
        box_complement = 1 - ksa / kills
        diffs.append(abs(feed_rate - box_complement))
    assert diffs, "no qualified WWII player-seasons found"
    assert sum(diffs) / len(diffs) < 0.03  # tracks the box within a few percent


def test_untraded_death_rate_spot_check(db_conn: Any) -> None:
    """Independent SQL recompute of one player-season equals the augmented aggregate."""
    from cdlhub_analytics import metrics as m

    loaded = m.load(db_conn)
    m.augment_with_kill_feed(db_conn, loaded)
    # pick the SnD aggregate with the most feed deaths
    snd = (
        a
        for a in loaded.aggregates
        if a.mode_slug == "search-and-destroy" and a.total(KF_DEATHS) > 100
    )
    best = max(snd, key=lambda a: a.total(KF_DEATHS), default=None)
    assert best is not None
    games = [
        r[0]
        for r in db_conn.execute(
            """
            SELECT r.game_id FROM kill_feed_recon r
            JOIN games g ON g.id = r.game_id JOIN game_modes gm ON gm.id = g.mode_id
            JOIN series s ON s.id = g.series_id JOIN events e ON e.id = s.event_id
            WHERE r.player_id = %s AND r.reconciled AND gm.slug = 'search-and-destroy'
              AND e.season_id = %s
            """,
            (best.player_id, best.season_id),
        )
    ]
    total = untraded = 0
    window = m.TRADE_WINDOW_MS
    for gid in games:
        team = {
            p: t
            for p, t in db_conn.execute(
                "SELECT player_id, team_id FROM game_player_stats WHERE game_id = %s", (gid,)
            )
        }
        evs = db_conn.execute(
            "SELECT round, time_ms, seq, victim_id, killer_id FROM kill_events "
            "WHERE game_id = %s AND death_kind = 'normal' ORDER BY round, time_ms, seq",
            (gid,),
        ).fetchall()
        for i, (rnd, t, _s, v, k) in enumerate(evs):
            if v != best.player_id:
                continue
            total += 1
            traded = False
            if k is not None and t is not None:
                for r2, t2, _s2, v2, k2 in evs[i + 1 :]:
                    if r2 != rnd or t2 is None or t2 - t > window:
                        break
                    if v2 == k:
                        traded = k2 is not None and team.get(k2) == team.get(v)
                        break
            untraded += 0 if traded else 1
    assert total == pytest.approx(best.total(KF_DEATHS))
    assert untraded == pytest.approx(best.total(KF_UNTRADED))
