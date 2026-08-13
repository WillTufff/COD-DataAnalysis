"""One test per resample whose population used to be ordered by a surrogate key.

Every one of these has the same shape: run the estimator, renumber or reorder
the rows underneath without changing a single number in them, run it again, and
require the published interval to be identical. That is what a reload of a
source does to this database, and until this sweep it was enough to move
intervals whose point estimates never moved.

`significance` and `roundwp` were fixed when the metric-diff harness found them
and carry their own assertions in their own files.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np

from cdlhub_analytics import resample, seriesdyn, style
from cdlhub_analytics.backtest import Prediction
from cdlhub_analytics.ratings import hierarchical as hier
from cdlhub_analytics.ratings import maplevel as ml
from cdlhub_analytics.ratings import player_rating as pr
from cdlhub_analytics.ratings.holdout import _brier_contrasts, persistence_columns
from cdlhub_analytics.regress import LogisticFit

# ------------------------------------------------------------------ the helper


def test_the_order_puts_the_first_column_first() -> None:
    """`lexsort` reads its keys last-first; the helper exists so nobody has to."""
    primary = [2.0, 1.0, 1.0]
    secondary = [0.0, 9.0, 3.0]
    assert list(resample.order([primary, secondary])) == [2, 1, 0]


def test_the_order_is_a_permutation_of_every_row() -> None:
    rng = np.random.default_rng(4)
    columns = [rng.standard_normal(50) for _ in range(3)]
    assert sorted(resample.order(columns).tolist()) == list(range(50))


def test_an_empty_population_orders_without_raising() -> None:
    assert resample.order([]).size == 0


def test_a_stream_is_the_same_for_the_same_contents_and_not_for_others() -> None:
    a = np.arange(10.0)
    assert list(resample.stream(7, a).integers(0, 100, 5)) == list(
        resample.stream(7, a.copy()).integers(0, 100, 5)
    )
    assert list(resample.stream(7, a).integers(0, 100, 5)) != list(
        resample.stream(7, a + 1.0).integers(0, 100, 5)
    )
    # And the run's seed still reaches the stream.
    assert list(resample.stream(7, a).integers(0, 100, 5)) != list(
        resample.stream(8, a).integers(0, 100, 5)
    )


def test_a_stream_does_not_move_when_the_last_bit_of_the_data_does() -> None:
    """The failure this guards is not hypothetical: `normal(loc, scale)` scales
    its draws with a fused multiply-add on arm64 and without one on x86_64, so
    the same seed builds data one ulp apart on a laptop and in CI. A seed read
    from exact float bytes turned that into an unrelated set of draws and an
    interval that moved by its own sampling spread."""
    rng = np.random.default_rng(12)
    exact = rng.standard_normal(200) * 3.0 + 20.0
    nudged = np.nextafter(exact, np.inf)
    assert not np.array_equal(exact, nudged)

    draws = resample.stream(3, exact).integers(0, 500, 40)
    assert list(resample.stream(3, nudged).integers(0, 500, 40)) == list(draws)


def test_a_stream_still_moves_when_the_data_genuinely_does() -> None:
    """Coarsening the seed must not blind it. A change any estimator could see
    has to reach the draws, or two different groups share a bootstrap."""
    base = np.random.default_rng(13).standard_normal(200)
    shifted = base.copy()
    shifted[7] += 1e-4

    assert list(resample.stream(3, base).integers(0, 500, 40)) != list(
        resample.stream(3, shifted).integers(0, 500, 40)
    )


def test_a_stream_does_not_move_when_a_neighbouring_group_appears() -> None:
    """The property a single threaded generator cannot have."""
    mine = np.arange(6.0)
    theirs = np.arange(20.0, 26.0)
    alone = resample.stream(1, mine).integers(0, 50, 8)
    for _ in range(3):
        resample.stream(1, theirs).integers(0, 50, 8)
    assert list(resample.stream(1, mine).integers(0, 50, 8)) == list(alone)


# --------------------------------------------------------- holdout persistence


def _persistence_input(
    seed: int = 5, n: int = 120
) -> tuple[dict[tuple[int, int], tuple[int, float]], dict[tuple[int, int], tuple[int, float]]]:
    rng = np.random.default_rng(seed)
    rating: dict[tuple[int, int], tuple[int, float]] = {}
    kd: dict[tuple[int, int], tuple[int, float]] = {}
    for pid in range(1, n + 1):
        skill = rng.standard_normal()
        for season in (1, 2):
            rating[(pid, season)] = (30, 1.0 + 0.1 * (skill + rng.standard_normal()))
            kd[(pid, season)] = (30, skill + rng.standard_normal())
    return rating, kd


def _renumber(
    table: dict[tuple[int, int], tuple[int, float]], mapping: dict[int, int]
) -> dict[tuple[int, int], tuple[int, float]]:
    return {(mapping[pid], season): v for (pid, season), v in table.items()}


def test_the_persistence_columns_do_not_move_when_players_are_renumbered() -> None:
    rating, kd = _persistence_input()
    rng = np.random.default_rng(0)
    ids = list(range(1, 121))
    shuffled = list(rng.permutation(ids))
    mapping = dict(zip(ids, (int(v) for v in shuffled), strict=True))

    before, counts = persistence_columns(rating, kd, [(1, 2)])
    after, counts_after = persistence_columns(
        _renumber(rating, mapping), _renumber(kd, mapping), [(1, 2)]
    )
    assert counts == counts_after
    assert before == after


# ------------------------------------------------------ holdout brier contrasts


def _predictions(seed: int = 6, n: int = 200) -> dict[str, dict[int, Prediction]]:
    rng = np.random.default_rng(seed)
    out: dict[str, dict[int, Prediction]] = {"rating": {}, "kd": {}}
    for game_id in range(1, n + 1):
        won = bool(rng.random() < 0.5)
        when = date(2020, 1, 24)
        out["rating"][game_id] = Prediction(
            p=float(
                np.clip(0.5 + (0.15 if won else -0.15) + 0.1 * rng.standard_normal(), 0.01, 0.99)
            ),
            won=won,
            when=when,
        )
        out["kd"][game_id] = Prediction(
            p=float(
                np.clip(0.5 + (0.05 if won else -0.05) + 0.2 * rng.standard_normal(), 0.01, 0.99)
            ),
            won=won,
            when=when,
        )
    return out


def test_the_brier_contrasts_do_not_move_when_the_games_are_renumbered() -> None:
    preds = _predictions()
    rng = np.random.default_rng(1)
    ids = sorted(preds["rating"])
    mapping = dict(zip(ids, (int(v) for v in rng.permutation(ids)), strict=True))
    renumbered = {name: {mapping[g]: p for g, p in rows.items()} for name, rows in preds.items()}

    before: dict[str, Any] = _brier_contrasts(preds)
    after: dict[str, Any] = _brier_contrasts(renumbered)
    assert before["available"] and before == after


# ---------------------------------------------------------------- the ratings


def _aggs(n_players: int = 40, maps: int = 12, seed: int = 3) -> list[pr.PlayerModeAgg]:
    rng = np.random.default_rng(seed)
    out: list[pr.PlayerModeAgg] = []
    for pid in range(1, n_players + 1):
        obs = rng.standard_normal((maps, 1)) + 0.3 * rng.standard_normal()
        out.append(
            pr.PlayerModeAgg(
                player_id=pid,
                season_id=1,
                mode_id=1,
                maps=maps,
                feats=np.asarray(obs.sum(axis=0) / maps),
                numerators=np.asarray(obs),
                denominators=np.ones((maps, 1)),
            )
        )
    return out


def _unit_fit() -> pr.ModeFit:
    return pr.ModeFit(
        n_games=100,
        mu=np.array([0.0]),
        sd=np.array([1.0]),
        fit=LogisticFit(intercept=0.0, weights=np.array([1.0]), converged=True, n_iter=1),
    )


def _unit_scale(members: list[pr.PlayerModeAgg]) -> pr.CohortScale:
    return pr.CohortScale(
        feat_mu=np.array([0.0]),
        feat_sd=np.array([1.0]),
        score_mu=0.0,
        score_sd=1.0,
        shrink_maps=10.0,
        within_var=1.0,
        between_var=1.0,
        n_players=len(members),
        n_maps=sum(a.maps for a in members),
        shrink_estimated=True,
    )


def test_a_rating_sd_does_not_move_when_a_players_maps_arrive_in_another_order() -> None:
    aggs = _aggs()
    fits = {(1, 1): _unit_fit()}
    scales = {(1, 1): _unit_scale(aggs)}
    rng = np.random.default_rng(2)

    def blended(rows: list[pr.SeasonRating]) -> dict[int, float]:
        return {
            r.player_id: r.rating_sd for r in rows if r.mode_id is None and r.rating_sd is not None
        }

    before = blended(pr.compute_ratings(aggs, fits, scales))
    # The same maps, emitted in a different order, and the players renumbered
    # into the bargain: neither is a fact about how anybody played.
    shuffled: list[pr.PlayerModeAgg] = []
    for a in aggs:
        take = rng.permutation(a.maps)
        shuffled.append(
            replace(
                a,
                player_id=a.player_id + 1000,
                numerators=a.numerators[take],
                denominators=a.denominators[take],
            )
        )
    after = blended(pr.compute_ratings(list(rng.permutation(shuffled)), fits, scales))  # type: ignore[arg-type]
    assert before and len(before) == len(after)
    for pid, sd in before.items():
        assert after[pid + 1000] == sd


# ------------------------------------------------------------- the mode weights


def _cohort_diffs(
    n: int = 400, seed: int = 8
) -> tuple[dict[tuple[int, int], list[pr.GameDiff]], dict[tuple[int, int], pr.Cohort]]:
    rng = np.random.default_rng(seed)
    feature = pr.Feature(
        key="kd",
        label="K/D",
        numerator=lambda r: 0.0,
        denominator=lambda r: 1.0,
        denom_kind="maps",
        sources=("box",),
        eligibility="always",
    )
    diffs = []
    for i in range(n):
        d = rng.standard_normal(1)
        diffs.append(
            pr.GameDiff(
                game_id=i + 1,
                event_id=1,
                when=date(2020, 1, 24),
                diff=np.asarray(d),
                a_won=bool(d[0] + rng.standard_normal() > 0),
            )
        )
    cohort = pr.Cohort(
        season_id=1, mode_id=1, mode_slug="hardpoint", title="MW", features=(feature,)
    )
    return {(1, 1): diffs}, {(1, 1): cohort}


def test_a_weight_interval_does_not_move_when_the_maps_arrive_in_another_order() -> None:
    diffs, cohorts = _cohort_diffs()
    rng = np.random.default_rng(12)
    before = pr.bootstrap_mode_weights(diffs, cohorts, b=60)
    shuffled = {(1, 1): [diffs[(1, 1)][i] for i in rng.permutation(len(diffs[(1, 1)]))]}
    after = pr.bootstrap_mode_weights(shuffled, cohorts, b=60)
    assert before[(1, 1)].weights == after[(1, 1)].weights
    assert before[(1, 1)].ratio == after[(1, 1)].ratio


def test_a_cohort_interval_does_not_move_when_a_second_cohort_is_fitted_first() -> None:
    """The defect one shared generator threaded through a loop always has."""
    diffs, cohorts = _cohort_diffs()
    other_diffs, other_cohorts = _cohort_diffs(seed=99)
    alone = pr.bootstrap_mode_weights(diffs, cohorts, b=60)
    together = pr.bootstrap_mode_weights(
        {(1, 1): diffs[(1, 1)], (0, 1): other_diffs[(1, 1)]},
        {(1, 1): cohorts[(1, 1)], (0, 1): replace(other_cohorts[(1, 1)], season_id=0)},
        b=60,
    )
    assert alone[(1, 1)].weights == together[(1, 1)].weights
    assert alone[(1, 1)].ratio == together[(1, 1)].ratio


# ------------------------------------------------- the observation calibration


def test_the_calibration_factor_does_not_move_when_players_are_reordered() -> None:
    members = _aggs(n_players=60, maps=8, seed=21)
    scale = _unit_scale(members)
    fit = _unit_fit()
    rng = np.random.default_rng(30)

    factor, _report = hier.calibrate(members, scale, fit, sigma2=1.0, b=80)
    renumbered = [
        replace(a, player_id=int(v) + 500)
        for a, v in zip(members, rng.permutation(60), strict=True)
    ]
    again, _report2 = hier.calibrate(renumbered, scale, fit, sigma2=1.0, b=80)
    assert factor == again


# ------------------------------------------------------------ series dynamics


def test_a_momentum_interval_does_not_move_when_the_series_are_renumbered() -> None:
    rng = np.random.default_rng(17)
    rows = []
    for i in range(300):
        path = [bool(rng.random() < 0.55) for _ in range(3)]
        if sum(path) == 2:
            path.append(bool(rng.random() < 0.55))
        series = [_series_row(path, sid=i + 1, day=i)]
        rows.extend(seriesdyn.map_rows(series, _frozen(series, 0.35 + 0.3 * rng.random())))

    before = seriesdyn.fit_specs(rows, "test", specs=("strength_prev",))
    shuffled = [replace(r, series_id=r.series_id + 10_000) for r in rng.permutation(rows)]  # type: ignore[arg-type]
    after = seriesdyn.fit_specs(shuffled, "test", specs=("strength_prev",))
    assert before["available"]
    assert before["specs"][0]["terms"] == after["specs"][0]["terms"]


def _series_row(path: list[bool], sid: int, day: int) -> seriesdyn.Series:
    maps = [
        seriesdyn.SeriesMap(ordinal=i + 1, mode="hardpoint", team1_won=won)
        for i, won in enumerate(path)
    ]
    return seriesdyn.Series(
        id=sid,
        team1=1,
        team2=2,
        played_at=datetime(2020, 1, 24, tzinfo=UTC) + timedelta(days=day),
        event_id=1,
        title="MW",
        year=2020,
        wins_needed=3,
        maps=tuple(maps),
    )


def _frozen(series: list[seriesdyn.Series], p: float) -> seriesdyn.Frozen:
    return seriesdyn.Frozen(
        rotation_ps={s.id: (p,) * 5 for s in series},
        played_ps={s.id: (p,) * len(s.maps) for s in series},
        n_no_rotation=0,
    )


# ------------------------------------------------------------- specialization


def test_the_specialization_null_does_not_move_when_events_are_renumbered() -> None:
    maps = _mode_league()
    rng = np.random.default_rng(44)
    events = sorted({m.event_id for m in maps})
    mapping = dict(zip(events, (int(v) for v in rng.permutation(events)), strict=True))
    renumbered = [replace(m, event_id=mapping[m.event_id]) for m in maps]

    before = ml.specialization(maps, n_permutations=30)
    after = ml.specialization(renumbered, n_permutations=30)
    assert before["available"]
    assert before["null_mean_sd"] == after["null_mean_sd"]
    assert before["p_value"] == after["p_value"]


def _mode_league(n_events: int = 12) -> list[ml.MapResult]:
    maps: list[ml.MapResult] = []
    gid = sid = 0
    for e in range(n_events):
        for a, b in ((1, 2), (1, 3), (1, 4), (2, 3), (2, 4), (3, 4)):
            sid += 1
            for i, mode in enumerate(ml.ROTATION["WWII"]):
                gid += 1
                if mode == "hardpoint":
                    won = a == 1
                elif mode == "search-and-destroy":
                    won = b == 1
                else:
                    won = (gid % 2) == 0
                maps.append(
                    ml.MapResult(
                        game_id=gid,
                        series_id=sid,
                        team1=a,
                        team2=b,
                        team1_won=won,
                        mode=mode,
                        title="WWII",
                        played_at=datetime(2018, 1, 20, tzinfo=UTC) + timedelta(days=e),
                        event_id=e + 1,
                        ordinal=i + 1,
                    )
                )
    return maps


# -------------------------------------------------------------------- the style


def test_the_style_basis_is_the_same_cloud_whatever_order_it_is_loaded_in() -> None:
    rng = np.random.default_rng(23)
    n = 200
    values = rng.standard_normal((n, 3))
    values[:, 1] += 0.8 * values[:, 0]
    columns = [style.Column(mode_id=0, metric=name) for name in ("kd", "engage", "obj")]
    seasons = [1 + (i % 2) for i in range(n)]

    def grid_of(order: list[int]) -> style.Grid:
        return style.Grid(
            subjects=[style.Subject(player_id=i + 1, season_id=seasons[i]) for i in order],
            columns=columns,
            values=values[order],
            season_of=np.array([seasons[i] for i in order]),
            rating=np.zeros(n),
            league="CDL",
            year_of={1: 2020, 2: 2021},
        )

    forward = style.fit_basis(style.build_basis(grid_of(list(range(n))), "core", (0,)))
    backward = style.fit_basis(style.build_basis(grid_of(list(rng.permutation(n))), "core", (0,)))
    assert forward.components.n_retained == backward.components.n_retained
    assert forward.surviving_k == backward.surviving_k
    # And a player keeps their score, which is what the reorder must not break.
    mine = {
        s.player_id: forward.components.scores[i, 0] for i, s in enumerate(forward.basis.subjects)
    }
    theirs = {
        s.player_id: backward.components.scores[i, 0] for i, s in enumerate(backward.basis.subjects)
    }
    assert all(math.isclose(mine[p], theirs[p], abs_tol=1e-9) for p in mine)


# --------------------------------------------------- the opponent adjustment


def _opponent_panel(*, offset: int) -> Any:
    """A small cohort whose loader-assigned ids are shifted by `offset`.

    The map keys and every number are identical between two calls; only the
    surrogate ids move, which is exactly what a reload does.
    """
    from cdlhub_analytics.ratings import opponent as op

    lines = []
    rng = np.random.default_rng(4)
    for game in range(60):
        picked = rng.permutation(16)[:8]
        left, right = picked[:4], picked[4:]
        for own, other in ((left, right), (right, left)):
            for player in own:
                value = float(10.0 + player - other.sum() / 8.0 + rng.normal(0, 1))
                lines.append(
                    op.Line(
                        player_id=int(player),
                        team_id=0 if own is left else 1,
                        game_id=game + offset,
                        series_id=game // 3 + offset,
                        event_id=offset,
                        season_id=0,
                        mode_id=0,
                        duration_s=600.0,
                        # The natural key does not move with the offset.
                        map_key=f"evt-{game // 3:03d}#{game % 3 + 1}",
                        opponents=tuple(sorted(int(p) for p in other)),
                        teammates=tuple(sorted(int(p) for p in own if p != player)),
                        opp_rating=1500.0 + float(other.sum()),
                        values={"stat": (value, 1.0)},
                    )
                )
    return op.Panel(
        season_id=0,
        mode_id=0,
        mode_slug="synthetic",
        title="synthetic",
        side=4,
        lines=tuple(lines),
        features=(),
    )


def test_the_placebo_does_not_move_when_the_loader_renumbers() -> None:
    from cdlhub_analytics.ratings import opponent as op

    results = []
    for offset in (0, 9_000):
        panel = _opponent_panel(offset=offset)
        columns = op.build_columns(panel, teammates=False)
        results.append(op.placebo(panel, "stat", columns, op.design(panel, columns), draws=4))
    assert results[0] == results[1]


def test_the_correction_interval_does_not_move_when_the_loader_renumbers() -> None:
    from cdlhub_analytics.ratings import opponent as op

    results = []
    for offset in (0, 12_345):
        panel = _opponent_panel(offset=offset)
        columns = op.build_columns(panel, teammates=False)
        results.append(
            op.bootstrap_correction(panel, "stat", columns, op.design(panel, columns), draws=25)
        )
    assert results[0] == results[1]


def test_the_split_halves_do_not_move_when_the_loader_renumbers() -> None:
    from cdlhub_analytics.ratings import opponent as op

    halves = [op.split_halves(_opponent_panel(offset=offset)) for offset in (0, 777)]
    assert halves[0] == halves[1]


# ----------------------------------------------- the evaluation harness (PE)


def _eval_panel(offset: int = 0, n: int = 240) -> list[Any]:
    """Transitions for two-per-player clusters, with the player ids shifted."""
    from cdlhub_analytics.ratings.evaluate import Observation

    rng = np.random.default_rng(3)
    out = []
    for i in range(n):
        kd = float(rng.standard_normal())
        out.append(
            Observation(
                player_id=offset + 1 + i // 2,
                season_a=1,
                season_b=2,
                title_a="WWII",
                year_a=2018,
                composite=0.4 * kd + float(rng.standard_normal()),
                kd=kd,
                openskill=0.2 * kd + float(rng.standard_normal()),
                skill=None,
                kd_next=0.6 * kd + 0.8 * float(rng.standard_normal()),
                composite_next=kd,
                moved_team=False,
                rookie=True,
                events_a=frozenset({1}),
            )
        )
    return out


def test_the_persistence_clusters_do_not_move_when_players_are_renumbered() -> None:
    from cdlhub_analytics.ratings import evaluate as ev

    results = [
        ev._persistence_stats(ev._ordered(_eval_panel(offset=offset)), by_cluster=True)
        for offset in (0, 5_000)
    ]
    assert results[0] == results[1]


def test_the_persistence_clusters_do_not_move_when_the_rows_arrive_reordered() -> None:
    from cdlhub_analytics.ratings import evaluate as ev

    panel = _eval_panel()
    shuffled = [panel[i] for i in np.random.default_rng(11).permutation(len(panel))]
    before = ev._persistence_stats(ev._ordered(panel), by_cluster=True)
    after = ev._persistence_stats(ev._ordered(shuffled), by_cluster=True)
    assert before == after


def test_the_openskill_pass_does_not_move_when_the_games_are_renumbered() -> None:
    """The walk-forward is ordered by `map_key`, never by the loader's game id."""
    from cdlhub_analytics.maprows import MapRow as Row
    from cdlhub_analytics.ratings import skillbase

    rows: list[Row] = []
    for g in range(120):
        winner = 100 if g % 3 else 200
        for team_id, members in ((100, (1, 2, 3, 4)), (200, (5, 6, 7, 8))):
            for pid in members:
                rows.append(
                    Row(
                        player_id=pid,
                        team_id=team_id,
                        game_id=g + 1,
                        series_id=g + 1,
                        season_id=1,
                        mode_id=1,
                        mode_slug="hardpoint",
                        title="WWII",
                        event_id=1,
                        played_at=date(2018, 1, 1) + timedelta(days=g),
                        duration_s=600.0,
                        winner_team_id=winner,
                        values={"kills": 20.0, "deaths": 20.0},
                        team_kills=80.0,
                        team_hill_time=100.0,
                        map_key=f"s{g + 1:04d}#1",
                    )
                )
    shifted = [Row(**{**r.__dict__, "game_id": r.game_id + 9_000}) for r in rows]
    assert skillbase.fit_walk_forward(rows).final == skillbase.fit_walk_forward(shifted).final
