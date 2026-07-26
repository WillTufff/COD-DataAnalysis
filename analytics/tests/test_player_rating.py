"""Fixture tests for the rating engine: weight recovery on synthetic maps,
walk-forward hygiene, shrinkage, rating ordering, and per-cohort feature
resolution. No database required."""

from datetime import date, timedelta
from typing import Any, cast

import numpy as np
import pytest

from cdlhub_analytics.maprows import (
    MODE_HARDPOINT,
    MODE_SND,
    Coverage,
    KeyCoverage,
    MapRow,
)
from cdlhub_analytics.ratings.player_rating import (
    BOOTSTRAP_B,
    MIN_TRAIN_GAMES,
    SHRINK_FALLBACK,
    Cohort,
    ModeFit,
    PlayerModeAgg,
    _estimate_shrinkage,
    _shrink,
    aggregate_players,
    backtest_weights,
    bootstrap_mode_weights,
    build_cohort_scales,
    build_cohorts,
    build_game_diffs,
    compute_ratings,
    fit_mode_weights,
    resolve_features,
    rest_vs_slay,
    weights_artifact,
)
from cdlhub_analytics.regress import LogisticFit

# Player skill: (mean kills per map). Team 1 = {11, 12}, team 2 = {21, 22}.
SKILL = {11: 30.0, 12: 20.0, 21: 20.0, 22: 15.0}

TRACKED = KeyCoverage(rows=1000, present=1000, nonzero=1000)
UNTRACKED = KeyCoverage(rows=1000, present=1000, nonzero=0)

V1_COLUMNS = ("kills", "deaths", "assists", "hill_time")


def coverage_for(title: str, columns: tuple[str, ...], missing: tuple[str, ...] = ()) -> Coverage:
    return {title: {c: (UNTRACKED if c in missing else TRACKED) for c in columns}}


def synthetic_rows(n_games: int = 80, seed: int = 5) -> list[MapRow]:
    """Two teams, one season-mode cohort, two events. Kills and deaths carry
    independent signal about who wins the map; assists and objective are pure
    noise — so the regression has real structure to find, with no collinear
    degeneracy hiding it."""
    rng = np.random.default_rng(seed)
    rows: list[MapRow] = []
    day0 = date(2018, 1, 1)
    for g in range(n_games):
        kills = {p: max(0.0, rng.normal(mu, 3.0)) for p, mu in SKILL.items()}
        deaths = {p: max(0.0, rng.normal(20.0, 3.0)) for p in SKILL}
        latent = (kills[11] + kills[12] - kills[21] - kills[22]) - (
            deaths[11] + deaths[12] - deaths[21] - deaths[22]
        )
        team1_won = bool(latent + rng.normal(0.0, 2.0) > 0.0)
        event_id = 1 if g < n_games // 2 else 2
        for p in SKILL:
            team = 1 if p < 20 else 2
            rows.append(
                MapRow(
                    player_id=p,
                    team_id=team,
                    game_id=g,
                    season_id=1,
                    mode_id=1,
                    mode_slug=MODE_HARDPOINT,
                    title="WWII",
                    event_id=event_id,
                    played_at=day0 + timedelta(days=g),
                    duration_s=600.0,
                    winner_team_id=1 if team1_won else 2,
                    values={
                        "kills": kills[p],
                        "deaths": deaths[p],
                        "assists": max(0.0, rng.normal(5.0, 1.0)),
                        "hill_time": max(0.0, rng.normal(10.0, 3.0)),
                    },
                    team_kills=0.0,
                    team_hill_time=0.0,
                )
            )
    return rows


def v1_setup(rows: list[MapRow]) -> dict[tuple[int, int], Cohort]:
    return build_cohorts(rows, coverage_for("WWII", V1_COLUMNS), "1.0.0")


def test_game_diffs_shape_and_label() -> None:
    rows = synthetic_rows()
    diffs = build_game_diffs(rows, v1_setup(rows))
    assert set(diffs) == {(1, 1)}
    assert len(diffs[(1, 1)]) == 80
    # Team A is the lower id (1); its diff must correlate with winning.
    d = diffs[(1, 1)][0]
    game0 = [r for r in rows if r.game_id == 0]
    assert d.a_won == next(r.won for r in game0 if r.team_id == 1)


def test_weights_recover_structure() -> None:
    rows = synthetic_rows()
    cohorts = v1_setup(rows)
    fits = fit_mode_weights(build_game_diffs(rows, cohorts))
    w = dict(zip(("kills", "deaths", "assists", "obj"), fits[(1, 1)].weights, strict=True))
    assert w["kills"] > 0.0, "kill edge must raise win odds"
    assert w["deaths"] < 0.0, "death edge must lower win odds"
    assert w["kills"] > abs(w["assists"]), "assists are noise here"


def test_backtest_is_walk_forward_only() -> None:
    rows = synthetic_rows()
    preds = backtest_weights(build_game_diffs(rows, v1_setup(rows)))
    # Event 1 has no history; only event 2's 40 maps get predictions.
    assert len(preds) == 40
    assert all(0.0 < p.p < 1.0 for p in preds)
    hit = sum(1 for p in preds if (p.p >= 0.5) == p.won) / len(preds)
    assert hit > 0.6, "kills decide these maps; the model must beat coin flips"


def test_min_train_gate() -> None:
    rows = synthetic_rows(n_games=MIN_TRAIN_GAMES)  # each event below the gate
    assert backtest_weights(build_game_diffs(rows, v1_setup(rows))) == []


def test_ratings_order_scale_and_uncertainty() -> None:
    rows = synthetic_rows()
    cohorts = v1_setup(rows)
    diffs = build_game_diffs(rows, cohorts)
    fits = fit_mode_weights(diffs)
    aggs = aggregate_players(rows, cohorts)
    scales = build_cohort_scales(aggs, fits)
    ratings = compute_ratings(aggs, fits, scales)

    blended = {r.player_id: r for r in ratings if r.mode_id is None}
    assert len(blended) == 4
    # The 30-kill player must outrate everyone, and clearly outrate the
    # 15-kill player. (Exact ranks between the two 20-kill-ish players are
    # sampling noise at 80 maps; the model shouldn't pretend otherwise.)
    ordered = sorted(blended.values(), key=lambda r: r.rating, reverse=True)
    assert ordered[0].player_id == 11
    assert blended[11].rating > blended[22].rating + 0.05
    # Normalized: cohort centers on 1.0.
    mean = float(np.mean([r.rating for r in blended.values()]))
    assert abs(mean - 1.0) < 0.05
    # Bootstrap uncertainty exists on blended rows and stays bounded.
    # (A 4-player cohort standardizes coarsely, so sds run larger here than
    # in real dozens-of-players cohorts.)
    assert all(r.rating_sd is not None and 0.0 < r.rating_sd < 0.5 for r in blended.values())
    # Per-mode rows exist too, without their own sd.
    assert sum(1 for r in ratings if r.mode_id == 1) == 4


def test_shrinkage_pulls_small_samples_to_league_mean() -> None:
    k = SHRINK_FALLBACK
    assert abs(_shrink(2.0, 8, k)) < abs(_shrink(2.0, 80, k))  # fewer maps, more pooling
    assert abs(_shrink(2.0, 10**6, k) - 2.0) < 1e-3  # huge samples keep their signal
    assert _shrink(-2.0, 8, k) > -2.0  # shrinks from both sides
    assert _shrink(1.0, 20, 40.0) < _shrink(1.0, 20, 10.0)  # a stronger prior pools harder


# ------------------------------------------------------- empirical-Bayes prior


def one_feature_cohort(
    n_players: int, n_maps: int, sigma: float, tau: float, seed: int = 11
) -> list[PlayerModeAgg]:
    """A cohort with a known variance ratio: true scores drawn with SD `tau`,
    per-map observations scattered around them with SD `sigma`, one feature that
    is the score itself. The estimator should recover k = sigma^2 / tau^2."""
    rng = np.random.default_rng(seed)
    out: list[PlayerModeAgg] = []
    for pid in range(n_players):
        truth = rng.normal(0.0, tau)
        obs = rng.normal(truth, sigma, size=n_maps).reshape(n_maps, 1)
        out.append(
            PlayerModeAgg(
                player_id=pid,
                season_id=1,
                mode_id=1,
                maps=n_maps,
                feats=np.asarray(obs.mean(axis=0)),
                numerators=obs,
                denominators=np.ones((n_maps, 1)),
            )
        )
    return out


UNIT = (np.zeros(1), np.ones(1), np.ones(1))  # feat_mu, feat_sd, weights


def _unit_fit() -> ModeFit:
    """A one-feature fit whose weight is 1, so the score is the feature itself."""
    return ModeFit(
        n_games=0,
        mu=np.zeros(1),
        sd=np.ones(1),
        fit=LogisticFit(intercept=0.0, weights=np.ones(1), converged=True, n_iter=1),
    )


def test_estimate_shrinkage_recovers_a_known_variance_ratio() -> None:
    members = one_feature_cohort(n_players=250, n_maps=30, sigma=3.0, tau=1.0)
    k, within, between, n_players, n_maps, estimated = _estimate_shrinkage(members, *UNIT)
    assert estimated
    assert (n_players, n_maps) == (250, 7500)
    assert abs(within - 9.0) < 0.5  # sigma^2
    assert abs(between - 1.0) < 0.25  # tau^2
    assert 7.0 < k < 11.0  # sigma^2 / tau^2 = 9


def test_estimate_shrinkage_scales_with_the_noise() -> None:
    quiet = _estimate_shrinkage(one_feature_cohort(200, 30, sigma=1.0, tau=1.0), *UNIT)[0]
    noisy = _estimate_shrinkage(one_feature_cohort(200, 30, sigma=4.0, tau=1.0), *UNIT)[0]
    assert noisy > 8.0 * quiet, "a noisier mode must demand far more maps"


def test_estimate_shrinkage_flattens_a_cohort_that_does_not_differ() -> None:
    """Every player has the same true score, so all the observed spread is
    per-map noise. tau^2 estimates zero and is as likely to land just below it
    as just above; either way the cohort must end up almost fully pooled, so
    assert the shrinkage rather than the sign that produced it."""
    members = one_feature_cohort(n_players=120, n_maps=25, sigma=2.0, tau=0.0)
    k, _within, between, _n, _m, estimated = _estimate_shrinkage(members, *UNIT)
    assert abs(between) < 0.05
    if estimated:
        assert _shrink(1.0, 50, k) < 0.15, "a 50-map season should keep almost nothing"
    else:
        assert k == SHRINK_FALLBACK


def test_estimate_shrinkage_needs_replication() -> None:
    members = one_feature_cohort(n_players=40, n_maps=1, sigma=2.0, tau=1.0)
    assert _estimate_shrinkage(members, *UNIT) == (SHRINK_FALLBACK, 0.0, 0.0, 40, 40, False)


def test_cohort_scale_carries_the_prior_and_falls_back_on_four_players() -> None:
    """The synthetic cohort is deliberately tiny, and four players cannot
    separate true spread from per-map noise: tau^2 comes out negative and the
    scale says so. That is the guard working, and it is why the golden ratings
    below are the fallback's."""
    rows = synthetic_rows()
    cohorts = v1_setup(rows)
    fits = fit_mode_weights(build_game_diffs(rows, cohorts))
    scale = build_cohort_scales(aggregate_players(rows, cohorts), fits)[(1, 1)]
    assert (scale.n_players, scale.n_maps) == (4, 320)
    assert not scale.shrink_estimated
    assert scale.between_var < 0.0
    assert scale.shrink_maps == SHRINK_FALLBACK


def test_cohort_scale_estimates_the_prior_when_the_cohort_supports_it() -> None:
    members = one_feature_cohort(n_players=150, n_maps=20, sigma=2.0, tau=1.0)
    scale = build_cohort_scales(members, {(1, 1): _unit_fit()})[(1, 1)]
    assert scale.shrink_estimated
    assert scale.shrink_maps == pytest.approx(scale.within_var / scale.between_var)
    assert 2.5 < scale.shrink_maps < 6.0  # sigma^2 / tau^2 = 4


# ---------------------------------------------------------------- resolution

HP_COLUMNS = ("kills", "deaths", "hill_time", "hill_captures", "time_alive_s", "num_lives")
SND_COLUMNS = (
    "kills",
    "deaths",
    "first_bloods",
    "snd_firstdeaths",
    "snd_survives",
    "plants",
    "defuses",
    "snd_rounds",
)


def test_untracked_column_drops_only_its_own_feature() -> None:
    """WWII never populated hill_captures; BO4 never populated time_alive_s.
    Each cohort loses exactly the feature that reads the missing column."""
    wwii = resolve_features(
        "2.0.0",
        MODE_HARDPOINT,
        coverage_for("WWII", HP_COLUMNS, missing=("hill_captures",)),
        "WWII",
    )
    bo4 = resolve_features(
        "2.0.0",
        MODE_HARDPOINT,
        coverage_for("BO4", HP_COLUMNS, missing=("time_alive_s",)),
        "BO4",
    )
    assert [f.key for f in wwii] == ["kills_p10", "deaths_p10", "hill_time_p10", "time_per_life_s"]
    assert [f.key for f in bo4] == [
        "kills_p10",
        "deaths_p10",
        "hill_time_p10",
        "hill_captures_p10",
    ]


def test_iw_snd_drops_the_first_death_family() -> None:
    """IW 2017 tracked no first deaths and no survivals."""
    iw = resolve_features(
        "2.0.0",
        MODE_SND,
        coverage_for("IW", SND_COLUMNS, missing=("snd_firstdeaths", "snd_survives")),
        "IW",
    )
    assert [f.key for f in iw] == ["snd_kpr", "snd_dpr", "snd_fb_rate", "snd_bomb_pr"]


def test_feed_features_need_a_feed() -> None:
    """A title with no kill feed publishes none of the trade features, and 2.1.0
    degrades to exactly the 2.0.0 set rather than emitting zeros."""
    without = resolve_features("2.1.0", MODE_SND, coverage_for("BO4", SND_COLUMNS), "BO4")
    plain = resolve_features("2.0.0", MODE_SND, coverage_for("BO4", SND_COLUMNS), "BO4")
    assert [f.key for f in without] == [f.key for f in plain]
    assert not any(f.needs_feed for f in without)


def test_feed_cohort_rejects_unreconciled_maps() -> None:
    """Absent feed columns mean 'not reconciled', not 'zero' — those maps must
    leave the cohort instead of counting as maps where nothing was traded."""
    feed_columns = (*SND_COLUMNS, "kf_deaths", "kf_untraded_deaths", "kf_trade_kills")
    rows = synthetic_rows(n_games=4)
    cohorts = build_cohorts(rows, coverage_for("WWII", feed_columns), "2.1.0")
    cohort = cohorts[(1, 1)]
    assert cohort.needs_feed
    assert not cohort.accepts(rows[0])  # synthetic rows carry no feed marker


@pytest.mark.parametrize("version", ["1.0.0", "2.0.0", "2.1.0"])
def test_every_feature_declares_its_denominator_sources(version: str) -> None:
    """A denominator column is as much a source as a numerator column: if it is
    untracked the rate cannot be formed, so coverage has to gate on it too."""
    from cdlhub_analytics.ratings.player_rating import VERSIONS

    for features in VERSIONS[version].values():
        for f in features:
            if f.denom_kind == "rounds":
                assert any(s.endswith("rounds") for s in f.sources), f.key
            if f.denom_kind == "lives":
                assert "num_lives" in f.sources, f.key


# ------------------------------------------------------- weight uncertainty


def test_rest_vs_slay_reads_the_boundary_the_model_defines() -> None:
    w = {"kills_p10": 0.6, "deaths_p10": -0.4, "obj_p10": 1.0}
    # slaying mean = 0.5, rest mean = 1.0
    assert rest_vs_slay(w, ["kills_p10", "deaths_p10"]) == pytest.approx(2.0)


def test_rest_vs_slay_is_none_when_one_side_is_empty() -> None:
    """A cohort with nothing beyond the gunfight has no ratio, not a ratio of 0."""
    assert rest_vs_slay({"kills_p10": 0.6, "deaths_p10": -0.4}, ["kills_p10", "deaths_p10"]) is None
    assert rest_vs_slay({"obj_p10": 1.0}, []) is None


def test_rest_vs_slay_is_none_when_the_gunfight_carries_no_weight() -> None:
    assert rest_vs_slay({"kills_p10": 0.0, "obj_p10": 1.0}, ["kills_p10"]) is None


def test_weight_intervals_bracket_the_full_sample_fit() -> None:
    rows = synthetic_rows(n_games=200, seed=11)
    cohorts = v1_setup(rows)
    diffs = build_game_diffs(rows, cohorts)
    fits = fit_mode_weights(diffs)
    cis = bootstrap_mode_weights(diffs, cohorts, b=60)

    ci = cis[(1, 1)]
    assert ci.draws == 60
    for key, weight in zip(cohorts[(1, 1)].feature_keys, fits[(1, 1)].weights, strict=True):
        lo, hi = ci.weights[key]
        assert lo < hi, key
        assert lo <= weight <= hi, key


def test_ratio_interval_excludes_one_when_the_gunfight_decides() -> None:
    """On these maps kills and deaths carry all the signal and assists and the
    objective are noise, so the interval must land wholly below 1x — the chart
    and the finding both hang on which side of 1.0 it sits."""
    rows = synthetic_rows(n_games=200, seed=11)
    cohorts = v1_setup(rows)
    cis = bootstrap_mode_weights(build_game_diffs(rows, cohorts), cohorts, b=60)

    ratio = cis[(1, 1)].ratio
    assert ratio is not None
    lo, hi = ratio
    assert 0.0 < lo < hi < 1.0


def test_bootstrap_skips_cohorts_below_the_training_gate() -> None:
    """The same floor fit_mode_weights uses: no fit, so nothing to put an
    interval on."""
    rows = synthetic_rows(n_games=MIN_TRAIN_GAMES - 1)
    cohorts = v1_setup(rows)
    assert bootstrap_mode_weights(build_game_diffs(rows, cohorts), cohorts, b=10) == {}


class _LabelConn:
    """Stand-in for the two label lookups weights_artifact makes."""

    def __init__(self) -> None:
        self._rows: list[tuple[object, ...]] = []

    def execute(self, sql: str, *_args: object) -> "_LabelConn":
        self._rows = [(1, 2018, "WWII")] if "FROM seasons" in sql else [(1, "Hardpoint")]
        return self

    def fetchall(self) -> list[tuple[object, ...]]:
        return self._rows


def test_weights_artifact_publishes_the_ratio_with_its_interval() -> None:
    rows = synthetic_rows(n_games=200, seed=11)
    cohorts = v1_setup(rows)
    diffs = build_game_diffs(rows, cohorts)
    fits = fit_mode_weights(diffs)
    cis = bootstrap_mode_weights(diffs, cohorts, b=40)

    payload = weights_artifact(cast(Any, _LabelConn()), fits, cohorts, "1.0.0", cis)
    (entry,) = payload["cohorts"]
    assert set(entry["weight_ci"]) == set(entry["weights"])
    lo, hi = entry["rest_vs_slay_ci"]
    assert lo < entry["rest_vs_slay"] < hi
    assert entry["ci_draws"] == 40
    assert payload["bootstrap_b"] == BOOTSTRAP_B


def test_weights_artifact_omits_the_interval_when_none_was_computed() -> None:
    """Consumers must be able to tell 'not published' from 'wide'."""
    rows = synthetic_rows(n_games=200, seed=11)
    cohorts = v1_setup(rows)
    fits = fit_mode_weights(build_game_diffs(rows, cohorts))

    payload = weights_artifact(cast(Any, _LabelConn()), fits, cohorts, "1.0.0")
    (entry,) = payload["cohorts"]
    assert "rest_vs_slay" in entry
    assert "rest_vs_slay_ci" not in entry
    assert "weight_ci" not in entry


# ------------------------------------------------------------------ golden

# Pinned end-to-end output on the synthetic cohort. Any change to aggregation,
# standardization, shrinkage or the bootstrap moves these; that is the point.
# Update deliberately, never to make a failing run pass.
GOLDEN_V1 = {
    11: (1.141589, 0.218364),
    12: (1.007431, 0.237894),
    21: (0.834219, 0.177857),
    22: (1.016761, 0.208196),
}


def test_golden_ratings_do_not_drift() -> None:
    rows = synthetic_rows()
    cohorts = v1_setup(rows)
    diffs = build_game_diffs(rows, cohorts)
    fits = fit_mode_weights(diffs)
    aggs = aggregate_players(rows, cohorts)
    ratings = compute_ratings(aggs, fits, build_cohort_scales(aggs, fits))

    blended = {r.player_id: r for r in ratings if r.mode_id is None}
    for player_id, (rating, sd) in GOLDEN_V1.items():
        assert abs(blended[player_id].rating - rating) < 1e-6, player_id
        actual_sd = blended[player_id].rating_sd
        assert actual_sd is not None and abs(actual_sd - sd) < 1e-6, player_id
