import json
import math

import pytest

from cdlhub_analytics import metrics
from cdlhub_analytics.metrics import (
    ALL_MODES,
    CATALOG,
    Aggregate,
    Coverage,
    KeyCoverage,
    Loaded,
    Metric,
)


def full_coverage(*, exclude: dict[str, set[str]] | None = None) -> Coverage:
    """Every column tracked for every title, minus any named exclusions."""
    excluded = exclude or {}
    columns = (
        *metrics.NUMERIC_EXTRAS,
        "avg_kill_dist_m",
        "kills",
        "deaths",
        "assists",
        "hill_time",
        "first_bloods",
        "plants",
        "defuses",
        "damage",
    )
    coverage: Coverage = {}
    for title in metrics.TITLE_ORDER:
        coverage[title] = {
            col: KeyCoverage(
                rows=1000, present=1000, nonzero=0 if col in excluded.get(title, set()) else 1000
            )
            for col in columns
        }
    return coverage


def make_agg(
    *,
    mode_slug: str = ALL_MODES,
    title: str = "BO4",
    maps: int = 10,
    duration_s: float = 6000.0,
    team_hill_time: float = 0.0,
    player_id: int = 1,
    season_id: int = 12,
    mode_id: int | None = None,
    **sums: float,
) -> Aggregate:
    agg = Aggregate(
        player_id=player_id,
        season_id=season_id,
        mode_id=mode_id,
        mode_slug=mode_slug,
        title=title,
        maps=maps,
        duration_s=duration_s,
        team_hill_time=team_hill_time,
    )
    for key, value in sums.items():
        agg.sums[key] = value
        agg.present_maps[key] = maps
    return agg


def metric_by_key(key: str) -> Metric:
    return next(m for m in CATALOG if m.key == key)


def compute(key: str, agg: Aggregate) -> tuple[float, float]:
    result = metric_by_key(key).compute(agg)
    assert result is not None
    return result


# ---------- individual metrics ----------


def test_kills_p10() -> None:
    agg = make_agg(duration_s=1200.0, kills=40.0)  # 20 minutes
    value, denom = compute("kills_p10", agg)
    assert value == pytest.approx(20.0)
    assert denom == 10.0  # maps


def test_snd_fb_rate() -> None:
    agg = make_agg(mode_slug="search-and-destroy", first_bloods=30.0, snd_rounds=120.0)
    value, denom = compute("snd_fb_rate", agg)
    assert value == pytest.approx(0.25)
    assert denom == 120.0


def test_snd_fb_net_pr_can_go_negative() -> None:
    agg = make_agg(
        mode_slug="search-and-destroy",
        first_bloods=10.0,
        snd_firstdeaths=30.0,
        snd_rounds=100.0,
    )
    value, _ = compute("snd_fb_net_pr", agg)
    assert value == pytest.approx(-0.2)


def test_hill_time_share_uses_team_total_and_reports_maps() -> None:
    agg = make_agg(mode_slug="hardpoint", hill_time=300.0, team_hill_time=1200.0)
    value, denom = compute("hill_time_share", agg)
    assert value == pytest.approx(0.25)
    assert denom == 10.0  # qualification is in maps, not seconds


def test_clean_kill_rate() -> None:
    agg = make_agg(kills=200.0, kills_stayed_alive=150.0)
    value, denom = compute("clean_kill_rate", agg)
    assert value == pytest.approx(0.75)
    assert denom == 200.0


# ---------- degenerate inputs ----------


@pytest.mark.parametrize(
    ("key", "agg"),
    [
        ("kills_p10", make_agg(duration_s=0.0, kills=10.0)),
        ("snd_fb_rate", make_agg(mode_slug="search-and-destroy", first_bloods=5.0)),
        ("hill_time_share", make_agg(mode_slug="hardpoint", hill_time=10.0)),
        ("clean_kill_rate", make_agg(kills_stayed_alive=5.0)),
    ],
)
def test_zero_denominator_emits_nothing(key: str, agg: Aggregate) -> None:
    assert metric_by_key(key).compute(agg) is None


def test_titles_derived_from_which_columns_carry_data() -> None:
    cov = full_coverage(exclude={"IW": {"snd_firstdeaths"}, "BO4": {"kills_stayed_alive"}})
    assert metric_by_key("snd_fb_net_pr").titles(cov) == ("WWII", "BO4")
    assert metric_by_key("clean_kill_rate").titles(cov) == ("IW", "WWII")
    assert metric_by_key("kills_p10").titles(cov) == ("IW", "WWII", "BO4")


def test_column_present_but_never_populated_is_not_published() -> None:
    """A column of all zeros must not produce a metric for that title."""
    cov = full_coverage()
    cov["BO4"]["kills_stayed_alive"] = KeyCoverage(rows=19120, present=19120, nonzero=0)
    ckr = metric_by_key("clean_kill_rate")
    assert "BO4" not in ckr.titles(cov)
    assert not ckr.applies_to(make_agg(title="BO4"), cov)
    assert ckr.applies_to(make_agg(title="WWII"), cov)


def test_rare_but_real_column_stays_published() -> None:
    """Aces are rare, not untracked; a handful of stray rows are untracked."""
    assert KeyCoverage(rows=23048, present=23048, nonzero=244).tracked
    assert not KeyCoverage(rows=19120, present=19120, nonzero=5).tracked


def test_mode_scoped_metric_skips_other_modes() -> None:
    cov = full_coverage()
    fb = metric_by_key("snd_fb_rate")
    assert fb.applies_to(make_agg(mode_slug="search-and-destroy"), cov)
    assert not fb.applies_to(make_agg(mode_slug="hardpoint"), cov)
    assert not fb.applies_to(make_agg(mode_slug=ALL_MODES), cov)


def test_all_modes_metric_applies_to_every_slice() -> None:
    cov = full_coverage()
    kills = metric_by_key("kills_p10")
    for slug in ("hardpoint", "search-and-destroy", "control", ALL_MODES):
        assert kills.applies_to(make_agg(mode_slug=slug), cov)


# ---------- aggregation semantics ----------


def test_sum_then_divide_not_mean_of_per_map_ratios() -> None:
    """A 1-kill 20-minute map and a 20-kill 5-minute map: the honest rate is
    total kills over total time, not the average of the two map rates."""
    combined = make_agg(maps=2, duration_s=1500.0, kills=21.0)
    value, _ = compute("kills_p10", combined)
    assert value == pytest.approx(21.0 / 2.5)

    per_map_rates = [1.0 / 2.0, 20.0 / 0.5]  # 0.5 and 40.0 per 10 min
    mean_of_ratios = sum(per_map_rates) / 2
    assert not math.isclose(value, mean_of_ratios)


def test_build_rows_marks_qualification_by_denominator() -> None:
    """Below-threshold players still get rows, scored against the qualified cohort."""
    aggs = [
        make_agg(
            player_id=i,
            mode_slug="search-and-destroy",
            mode_id=2,
            first_bloods=float(i),
            snd_rounds=100.0,
        )
        for i in range(1, 6)
    ]
    aggs.append(
        make_agg(
            player_id=99,
            mode_slug="search-and-destroy",
            mode_id=2,
            first_bloods=9.0,
            snd_rounds=10.0,
        )  # under the 50-round minimum
    )
    rows = metrics.build_rows(1, Loaded(aggs, full_coverage()), [metric_by_key("snd_fb_rate")])
    assert len(rows) == 6
    by_player = {r[1]: r for r in rows}
    assert by_player[99][9] is False
    assert all(by_player[i][9] is True for i in range(1, 6))
    # The unqualified player is still scored, and sits above the cohort.
    assert by_player[99][7] is not None
    assert by_player[99][7] > 0


def test_build_rows_cohorts_do_not_cross_seasons() -> None:
    aggs = [
        make_agg(
            player_id=1,
            season_id=2,
            mode_slug="search-and-destroy",
            mode_id=2,
            first_bloods=10.0,
            snd_rounds=100.0,
        ),
        make_agg(
            player_id=2,
            season_id=2,
            mode_slug="search-and-destroy",
            mode_id=2,
            first_bloods=30.0,
            snd_rounds=100.0,
        ),
        make_agg(
            player_id=3,
            season_id=12,
            mode_slug="search-and-destroy",
            mode_id=2,
            first_bloods=10.0,
            snd_rounds=100.0,
        ),
        make_agg(
            player_id=4,
            season_id=12,
            mode_slug="search-and-destroy",
            mode_id=2,
            first_bloods=30.0,
            snd_rounds=100.0,
        ),
    ]
    rows = metrics.build_rows(1, Loaded(aggs, full_coverage()), [metric_by_key("snd_fb_rate")])
    z_by_player = {r[1]: r[7] for r in rows}
    # Identical values in each season get identical z-scores.
    assert z_by_player[1] == pytest.approx(z_by_player[3])
    assert z_by_player[2] == pytest.approx(z_by_player[4])


def test_extras_number_rejects_non_numeric() -> None:
    assert metrics._extras_number({"a": 5}, "a") == 5.0
    assert metrics._extras_number({"a": "5.5"}, "a") == 5.5
    assert metrics._extras_number({"a": "MP40"}, "a") is None
    assert metrics._extras_number({"a": True}, "a") is None
    assert metrics._extras_number({}, "a") is None
    assert metrics._extras_number({"a": None}, "a") is None


# ---------- catalog integrity ----------


def test_catalog_keys_unique() -> None:
    keys = [m.key for m in CATALOG]
    assert len(keys) == len(set(keys))


def test_catalog_entries_well_formed() -> None:
    cov = full_coverage()
    for m in CATALOG:
        assert m.tier in {"gold", "standard", "fun", "gold-fun"}
        assert m.sources
        assert set(m.titles(cov)) <= {"IW", "WWII", "BO4"}
        assert m.modes
        assert m.min_denom > 0
        assert m.label and m.unit and m.formula


def test_catalog_payload_serializable() -> None:
    payload = json.loads(json.dumps(metrics.catalog_payload(full_coverage())))
    assert payload["version"] == metrics.VERSION
    assert len(payload["metrics"]) == len(CATALOG)


def test_catalog_payload_reports_untracked_columns() -> None:
    cov = full_coverage()
    cov["BO4"]["kills_stayed_alive"] = KeyCoverage(rows=19120, present=19120, nonzero=0)
    payload = metrics.catalog_payload(cov)
    untracked = [u for u in payload["untracked_columns"] if u["title"] == "BO4"]
    assert {
        "title": "BO4",
        "column": "kills_stayed_alive",
        "rows": 19120,
        "nonzero": 0,
    } in untracked


# ---------- team metrics ----------


def make_team_map(
    *,
    team_id: int = 1,
    season_id: int = 2,
    mode_id: int = 1,
    mode_slug: str = "hardpoint",
    won: bool | None = True,
    score: int | None = None,
    opp_score: int | None = None,
    kills: dict[int, float] | None = None,
    hill: dict[int, float] | None = None,
    fb: dict[int, float] | None = None,
) -> metrics.TeamMap:
    return metrics.TeamMap(
        team_id=team_id,
        season_id=season_id,
        mode_id=mode_id,
        mode_slug=mode_slug,
        won=won,
        score=score,
        opp_score=opp_score,
        kills_by_player=kills or {},
        hill_by_player=hill or {},
        fb_by_player=fb or {},
    )


def test_gini_zero_when_load_is_even() -> None:
    assert metrics._gini([100.0, 100.0, 100.0, 100.0]) == pytest.approx(0.0)


def test_gini_rises_when_one_player_carries() -> None:
    even = metrics._gini([100.0, 100.0, 100.0, 100.0])
    mule = metrics._gini([400.0, 0.0, 0.0, 0.0])
    assert even is not None and mule is not None
    assert mule > even
    assert mule == pytest.approx(0.75)  # (n-1)/n for a single carrier


def test_gini_undefined_without_load() -> None:
    assert metrics._gini([0.0, 0.0, 0.0, 0.0]) is None
    assert metrics._gini([5.0]) is None


def test_herfindahl_even_split_is_one_over_n() -> None:
    assert metrics._herfindahl([1.0, 1.0, 1.0, 1.0]) == pytest.approx(0.25)
    assert metrics._herfindahl([10.0, 0.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_stddev_matches_sample_definition() -> None:
    assert metrics._stddev([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]) == pytest.approx(
        2.13808993, rel=1e-6
    )
    assert metrics._stddev([3.0]) is None


def test_team_map_win_rate_ignores_undecided_maps() -> None:
    maps = [
        make_team_map(won=True),
        make_team_map(won=False),
        make_team_map(won=None),
    ]
    assert metrics._team_metric_value("map_win_rate", maps) == pytest.approx(0.5)


def test_team_snd_round_win_rate_sums_rounds_not_maps() -> None:
    maps = [
        make_team_map(mode_slug="search-and-destroy", score=6, opp_score=0),
        make_team_map(mode_slug="search-and-destroy", score=2, opp_score=6),
    ]
    # 8 of 14 rounds, not the mean of 1.0 and 0.25.
    assert metrics._team_metric_value("snd_round_win_rate", maps) == pytest.approx(8 / 14)


def test_team_hp_margin_uses_signed_difference() -> None:
    maps = [
        make_team_map(score=250, opp_score=200),
        make_team_map(score=180, opp_score=250),
    ]
    assert metrics._team_metric_value("hp_avg_margin", maps) == pytest.approx(-10.0)


def test_team_metric_absent_when_inputs_missing() -> None:
    assert metrics._team_metric_value("hp_avg_margin", [make_team_map()]) is None
    assert metrics._team_metric_value("map_win_rate", [make_team_map(won=None)]) is None


def test_build_team_rows_scopes_mode_specific_metrics() -> None:
    maps = [
        make_team_map(team_id=1, mode_id=1, mode_slug="hardpoint", score=250, opp_score=100)
        for _ in range(10)
    ] + [
        make_team_map(team_id=2, mode_id=1, mode_slug="hardpoint", score=100, opp_score=250)
        for _ in range(10)
    ]
    rows = metrics.build_team_rows(1, maps)
    margins = [r for r in rows if r[4] == "hp_avg_margin"]
    assert margins and all(r[3] is not None for r in margins)
    # The all-modes slice must not produce a Hardpoint-only metric.
    assert all(r[3] is not None for r in rows if r[4] == "hp_avg_margin")


# ---------- catalog shape at full size ----------


def test_no_duplicate_keys_across_player_and_team_catalogs() -> None:
    player_keys = [m.key for m in CATALOG] + [metrics.SPLIT_METRIC.key]
    assert len(player_keys) == len(set(player_keys))
    team_keys = [m.key for m in metrics.TEAM_CATALOG]
    assert len(team_keys) == len(set(team_keys))


def test_every_metric_names_sources_that_exist() -> None:
    known = (
        set(metrics.NUMERIC_EXTRAS)
        | set(metrics.KF_KEYS)
        | set(metrics.CLUTCH_KEYS)
        | {
            "avg_kill_dist_m",
            "kills",
            "deaths",
            "assists",
            "hill_time",
            "first_bloods",
            "plants",
            "defuses",
            "damage",
        }
    )
    for m in CATALOG:
        assert set(m.sources) <= known, f"{m.key} names an unknown source"


def test_snd_round_shares_cover_zero_through_four() -> None:
    keys = {m.key for m in CATALOG}
    for n in range(5):
        assert f"snd_rounds_{n}k_share" in keys
